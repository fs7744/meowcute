"""HunyuanVideo-1.5 行走动作调参扫描。

一次进程内跑多组配置（避免重复加载权重），每组输出：
  - 帧序列 PNG
  - 客观指标：动作能量 / 周期与周期强度 / 角色漂移 / 闪烁尖峰
  - 接触印版

用法：
    python tools/hy15_sweep.py                # 跑预设 6 组
    python tools/hy15_sweep.py --only A,C      # 只跑指定组
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "models" / "hunyuanvideo-1.5"
OUT = ROOT / ".verify" / "hy15" / "sweep"
CELL = ROOT / ".verify" / "hy15" / "cell0.png"

sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))
from app.pipelines.sequence import detect_cycle_period  # noqa: E402

CYCLE_PROMPT = (
    "One complete walking cycle performed in place. The soldier lifts the left knee, "
    "swings the left leg forward and plants the foot; the body rises slightly; then he "
    "lifts the right knee, swings the right leg forward and plants it, returning to the "
    "starting pose. Arms swing in opposition to the legs. He keeps stepping on the same "
    "spot at the same size and position, facing the camera. Static camera, no zoom, no "
    "pan, plain light-gray background, identical outfit and weapon throughout."
)

PRESETS = {
    "A": dict(frames=21, steps=12, shift=7.0, guidance=1.0, note="基线 21f/12步"),
    "B": dict(frames=33, steps=12, shift=7.0, guidance=1.0, note="长片 33f/12步"),
    "C": dict(frames=21, steps=12, shift=5.0, guidance=1.0, note="低 shift 5.0（动作通常更大）"),
    "D": dict(frames=21, steps=16, shift=7.0, guidance=1.0, note="多步 16 步"),
    "E": dict(frames=21, steps=12, shift=7.0, guidance=2.0, note="开 CFG g=2.0"),
    "F": dict(frames=17, steps=12, shift=7.0, guidance=1.0, note="短片 17f（约一周期）"),
}


def prep_image(path: Path) -> Image.Image:
    im = Image.open(path).convert("RGBA")
    bg = Image.new("RGBA", im.size, (235, 235, 235, 255))
    return Image.alpha_composite(bg, im).convert("RGB")


def metrics(frames: list[Image.Image]) -> dict:
    n = len(frames)
    small = [np.asarray(f.convert("L").resize((160, 150), Image.BILINEAR), dtype=np.float32) for f in frames]
    diffs = np.array([np.abs(small[i + 1] - small[i]).mean() for i in range(n - 1)])
    # 前景区域帧差（背景 235 左右，只算角色轮廓内）
    masks = [a < 220 for a in small]
    fg = [float(np.abs(small[i + 1] - small[i])[masks[i] & masks[i + 1]].mean())
          for i in range(n - 1) if (masks[i] & masks[i + 1]).any()]
    # 角色包围盒漂移：位置/尺寸该稳定（原地踏步）
    cx, cy, hs = [], [], []
    for a, m in zip(small, masks):
        ys, xs = np.nonzero(m)
        if len(xs) == 0:
            continue
        cx.append(xs.mean()); cy.append(ys.mean()); hs.append(ys.max() - ys.min() + 1)
    drift = float(np.std(cx) + np.std(cy)) if cx else 0.0
    size_var = float(np.std(hs) / (np.mean(hs) + 1e-6) * 100) if hs else 0.0
    period = detect_cycle_period(frames)
    # 周期强度：在周期处 vs 其他偏移的平均差，越大越像真循环
    gain = 0.0
    if period and n > period + 2:
        at = float(np.mean([np.abs(small[i] - small[i + period]).mean() for i in range(n - period)]))
        others = [float(np.mean([np.abs(small[i] - small[i + p]).mean()
                                 for i in range(0, n - p, 2)])) for p in range(max(4, n // 6), n - 4)
                  if p != period]
        if others:
            gain = float(np.mean(others) - at)
    flicker = float(diffs.max() / (np.median(diffs) + 1e-6)) if len(diffs) else 0.0
    return dict(motion=float(diffs.mean()), fg_motion=float(np.mean(fg)) if fg else 0.0,
                period=period, period_gain=round(gain, 3), drift=round(drift, 2),
                size_var=round(size_var, 2), flicker=round(flicker, 2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default="", help="逗号分隔，如 A,C")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--prompt", default=CYCLE_PROMPT)
    ap.add_argument("--image", default=str(CELL))
    args = ap.parse_args()
    keys = [k.strip() for k in args.only.split(",") if k.strip()] or list(PRESETS)
    OUT.mkdir(parents=True, exist_ok=True)

    # ---------- 阶段1：文本编码（一次加载，编完释放）----------
    from hy15_assemble import encode  # noqa: PLC0415

    t0 = time.time()
    ckpt = encode(args.prompt, "")  # 复用已有缓存则秒返回
    blob = torch.load(ckpt, map_location="cpu", weights_only=False)
    (pe, pem, pe2, pem2) = blob["pos"]
    (ne, nem, ne2, nem2) = blob["neg"]
    print(f"文本编码就绪 {time.time()-t0:.1f}s", flush=True)

    # ---------- 阶段2：主权重 ----------
    from diffusers import (  # noqa: PLC0415
        AutoencoderKLHunyuanVideo15,
        ClassifierFreeGuidance,
        FlowMatchEulerDiscreteScheduler,
        HunyuanVideo15ImageToVideoPipeline,
        HunyuanVideo15Transformer3DModel,
    )
    from transformers import SiglipImageProcessor, SiglipVisionModel  # noqa: PLC0415

    dtype = torch.bfloat16
    print("loading transformer ...", flush=True)
    transformer = HunyuanVideo15Transformer3DModel.from_pretrained(CKPT / "transformer-diffusers", torch_dtype=dtype)
    vae = AutoencoderKLHunyuanVideo15.from_pretrained(CKPT / "vae-diffusers", torch_dtype=dtype)
    siglip_dir = CKPT / "vision_encoder" / "siglip"
    enc_dir = siglip_dir / "image_encoder" if (siglip_dir / "image_encoder" / "config.json").exists() else siglip_dir
    pp_dir = siglip_dir / "feature_extractor" if (siglip_dir / "feature_extractor" / "preprocessor_config.json").exists() else siglip_dir
    image_encoder = SiglipVisionModel.from_pretrained(enc_dir, torch_dtype=dtype)
    feature_extractor = SiglipImageProcessor.from_pretrained(pp_dir)

    im = prep_image(Path(args.image))
    results = {}
    for key in keys:
        cfg = PRESETS[key]
        print(f"\n=== {key}: {cfg['note']} frames={cfg['frames']} steps={cfg['steps']} "
              f"shift={cfg['shift']} g={cfg['guidance']} ===", flush=True)
        pipe = HunyuanVideo15ImageToVideoPipeline(
            text_encoder=None, tokenizer=None, transformer=transformer, vae=vae,
            scheduler=FlowMatchEulerDiscreteScheduler(
                num_train_timesteps=1000, shift=cfg["shift"], use_dynamic_shifting=False),
            text_encoder_2=None, tokenizer_2=None,
            guider=ClassifierFreeGuidance(guidance_scale=cfg["guidance"],
                                         enabled=cfg["guidance"] != 1.0),
            image_encoder=image_encoder, feature_extractor=feature_extractor,
        )
        pipe.enable_model_cpu_offload()
        pipe.vae.enable_tiling()

        gen = torch.Generator(device="cuda").manual_seed(args.seed)
        t1 = time.time()
        out = pipe(
            image=im, prompt=None,
            prompt_embeds=pe, prompt_embeds_mask=pem,
            prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
            negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
            negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
            num_frames=cfg["frames"], num_inference_steps=cfg["steps"],
            generator=gen, output_type="pil",
        )
        frames = out.frames[0]
        dt = time.time() - t1
        peak = torch.cuda.max_memory_allocated() / 1024**3
        torch.cuda.reset_peak_memory_stats()

        d = OUT / key
        d.mkdir(exist_ok=True)
        for i, f in enumerate(frames):
            f.save(d / f"{i:03d}.png")
        m = metrics(frames)
        m.update(secs=round(dt, 1), peak_gb=round(peak, 1), frames=cfg["frames"],
                 steps=cfg["steps"], shift=cfg["shift"], guidance=cfg["guidance"], note=cfg["note"])
        results[key] = m
        print(f"  {dt:.1f}s peak={peak:.1f}G | motion={m['motion']:.2f} fg={m['fg_motion']:.2f} "
              f"period={m['period']} gain={m['period_gain']} drift={m['drift']} "
              f"size_var={m['size_var']}% flicker={m['flicker']}", flush=True)
        del pipe
        gc.collect()
        torch.cuda.empty_cache()

    # ---------- 汇总 ----------
    import json

    (OUT / "metrics.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # 对照大图：每组一行
    rows = [(k, results[k]) for k in keys if k in results]
    TW, TH = 190, 180
    COLS = 6
    W = 12 + COLS * TW + 12
    H = 24 + len(rows) * (TH + 34)
    sheet = Image.new("RGB", (W, H), (252, 252, 252))
    dr = ImageDraw.Draw(sheet)
    dr.text((12, 6), "walk-cycle sweep (same seed, same prompt, frames sampled evenly)", fill=(20, 20, 20))
    for r, (k, m) in enumerate(rows):
        y = 24 + r * (TH + 34)
        dr.text((12, y), f"{k}: {m['frames']}f/{m['steps']}步 shift={m['shift']} g={m['guidance']}  "
                         f"motion={m['motion']:.2f} period={m['period']} gain={m['period_gain']} "
                         f"drift={m['drift']} flicker={m['flicker']}", fill=(10, 10, 10))
        files = sorted((OUT / k).glob("*.png"))
        picks = [files[int(round(i * (len(files) - 1) / (COLS - 1)))] for i in range(COLS)]
        for j, fp in enumerate(picks):
            t = Image.open(fp).convert("RGB")
            t.thumbnail((TW, TH))
            sheet.paste(t, (12 + j * TW, y + 22))
    sheet.save(OUT / "sheet.png")
    print(f"\n汇总 -> {OUT/'sheet.png'}\n指标 -> {OUT/'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
