"""Wan2.2-TI2V-5B 质量对照台：同一参考图 / 提示词 / seed，只改一个变量，
看画面到底在哪个环节变差。

四条能自证的判据（不靠主观「像不像」）：
- ref_l1：首帧与送进模型那张图的平均绝对差。I2V 的首帧 latent 是被条件硬替换的
  （latents=(1-mask)*condition+mask*latents），第一帧在数学上就该≈输入图；
  它一大，说明 VAE / 量化 / 尺寸对齐里有一个是坏的。
- bg_flicker：只统计非前景区域的帧间差。机位固定时背景必须几乎不动。
- motion_iou：相邻帧前景掩膜的 IoU 补数，衡量到底动没动。
- area_delta_pct / std_pct：前景面积抖动，衡量形体是否忽胖忽瘦。

用法：
  python tools/diag_wan_quality.py --sizes 768x768:fp8 1216x688:fp8
  python tools/diag_wan_quality.py --quant none --sizes 768x768
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = ROOT / "models" / "wan2.2-ti2v-5b"
DEFAULT_REF = (ROOT / "data/projects/p-4c0436d1798e/refs/"
               "ref-20261025-001057-39828871.png")
DEFAULT_PROMPT = (
    "严格参照提供的角色设定，角色正面朝向镜头，朝着摄影机直线奔跑，身体正面正对镜头，"
    "奔跑动态自然流畅，肢体动作连贯，重心向前，手臂前后摆动，腿部交替迈步；"
    "电影级运镜，机位固定，正面平视，人物主体居中，画面稳定，高细节"
)
OUT = ROOT / "out" / "wan_diag"


def _mask_of(arr: np.ndarray, bg: np.ndarray) -> np.ndarray:
    """前景：与背景色三维差的和 > 84。"""
    return (np.abs(arr - bg).sum(axis=-1) > 28 * 3)


def metrics(imgs: list[Image.Image], ref0: np.ndarray | None = None) -> dict:
    arr = np.stack([np.asarray(im.convert("RGB"), dtype=np.float32) for im in imgs])
    n, h, w, _ = arr.shape
    corners = np.concatenate([
        arr[:, :8, :8].reshape(n, -1, 3), arr[:, :8, -8:].reshape(n, -1, 3),
        arr[:, -8:, :8].reshape(n, -1, 3), arr[:, -8:, -8:].reshape(n, -1, 3),
    ], axis=1)
    bg = np.array(np.median(corners.reshape(-1, 3), axis=0), dtype=np.float32)
    diff = np.abs(np.diff(arr, axis=0))
    masks = np.stack([_mask_of(f, bg) for f in arr])
    area = masks.reshape(n, -1).sum(axis=1).astype(np.float64)
    ys, xs = np.mgrid[0:h, 0:w]
    cx = np.array([xs[m].mean() if m.any() else np.nan for m in masks])
    cy = np.array([ys[m].mean() if m.any() else np.nan for m in masks])
    drift = float(np.nanmean(np.hypot(cx - np.nanmean(cx), cy - np.nanmean(cy))))
    m0 = masks[:-1].reshape(-1, h * w).astype(np.float32)
    m1 = masks[1:].reshape(-1, h * w).astype(np.float32)
    inter = (m0 * m1).sum(axis=1)
    uni = np.maximum(m0.sum(axis=1) + m1.sum(axis=1), 1)
    motion = float(np.mean(1 - inter / uni))
    bmask = (~masks[:-1]) & (~masks[1:])
    bg_flicker = float((diff * bmask[..., None]).sum() / max(bmask.sum() * 3, 1))
    ref_l1 = None
    if ref0 is not None and ref0.shape == arr[0].shape:
        ref_l1 = round(float(np.abs(arr[0] - ref0).mean()), 2)
    return {
        "frames": int(n), "size": [int(w), int(h)], "bg": [int(x) for x in bg],
        "ref_l1": ref_l1,
        "flicker": round(float(diff.mean()), 2),
        "bg_flicker": round(bg_flicker, 2),
        "motion": round(motion, 4),
        "drift_px": round(drift, 1),
        "area_mean_pct": round(float(area.mean()) / (w * h) * 100, 2),
        "area_delta_pct": round(float(np.mean(np.abs(np.diff(area)) /
                                             max(area.mean(), 1))) * 100, 2),
        "std_pct": round(float(area.std()) / max(area.mean(), 1) * 100, 2),
    }


def grid(imgs: list[Image.Image], path: Path, cols: int = 3, cell: int = 320) -> None:
    k = min(cols * cols, len(imgs))
    idx = ([int(round(i * (len(imgs) - 1) / (k - 1))) for i in range(k)]
           if k > 1 else [0])
    ims = [imgs[i].convert("RGB").resize(
        (cell, max(1, int(cell * imgs[i].height / imgs[i].width)))) for i in idx]
    rows = (k + cols - 1) // cols
    g = Image.new("RGB", (cell * cols, ims[0].height * rows), (24, 24, 24))
    for i, im in enumerate(ims):
        g.paste(im, ((i % cols) * cell, (i // cols) * ims[0].height))
    path.parent.mkdir(parents=True, exist_ok=True)
    g.save(path)


def parse_spec(s: str) -> tuple[int, int, str]:
    size, _, tag = s.partition(":")
    w, h = size.lower().split("x")
    return int(w), int(h), (tag or f"{w}x{h}")


def canvas_of(ref: Image.Image, w: int, h: int) -> Image.Image:
    """等比 letterbox 铺到目标画布（不拉伸角色），底灰 128。"""
    tf = Image.new("RGB", (w, h), (128, 128, 128))
    s = min(w / ref.width, h / ref.height)
    r2 = ref.resize((max(1, int(ref.width * s)), max(1, int(ref.height * s))),
                    Image.LANCZOS)
    tf.paste(r2, ((w - r2.width) // 2, (h - r2.height) // 2))
    return tf


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--ref", default=str(DEFAULT_REF))
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--negative", default="扭曲肢体，畸形手脚，面部崩坏，画面抖动，模糊")
    ap.add_argument("--sizes", nargs="+", default=["768x768"])
    ap.add_argument("--quant", choices=["fp8", "none", "transformer"], default="fp8")
    ap.add_argument("--offload", type=int, default=1)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--cfg", type=float, default=5.0)
    ap.add_argument("--frames", nargs="+", type=int, default=[21])
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--max-seq", type=int, default=512)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    from diffusers import WanImageToVideoPipeline
    t0 = time.time()
    pipe = WanImageToVideoPipeline.from_pretrained(
        args.model, torch_dtype=torch.bfloat16)
    print(f"[load] bf16 组件 {time.time() - t0:.0f}s", flush=True)

    if args.quant in ("fp8", "transformer"):
        try:
            from torchao.quantization import Float8WeightOnlyConfig, quantize_
            q0 = time.time()
            quantize_(pipe.transformer, Float8WeightOnlyConfig())
            if args.quant == "fp8":
                quantize_(pipe.text_encoder, Float8WeightOnlyConfig())
            print(f"[load] torchao fp8（{args.quant}）{time.time() - q0:.0f}s", flush=True)
        except Exception as e:
            print(f"[load] fp8 失败（{e}），保持 bf16", flush=True)

    if args.offload:
        pipe.enable_model_cpu_offload()
    else:
        pipe.to("cuda")
    pipe.set_progress_bar_config(disable=True)

    ref = Image.open(args.ref).convert("RGBA")
    if ref.getchannel("A").getextrema()[0] < 250:
        bgc = Image.new("RGBA", ref.size, (128, 128, 128, 255))
        ref = Image.alpha_composite(bgc, ref)
    ref = ref.convert("RGB")
    OUT.mkdir(parents=True, exist_ok=True)

    results = []
    for spec in args.sizes:
        w, h, tag = parse_spec(spec)
        tf = canvas_of(ref, w, h)
        ref0 = np.asarray(tf, dtype=np.float32)
        for nf in args.frames:
            g = torch.Generator(device="cuda").manual_seed(args.seed)
            t1 = time.time()
            out = pipe(prompt=args.prompt, negative_prompt=args.negative or " ",
                       image=tf, width=w, height=h, num_frames=nf,
                       num_inference_steps=args.steps, guidance_scale=args.cfg,
                       generator=g, output_type="pil",
                       max_sequence_length=args.max_seq)
            dt = time.time() - t1
            fr = out.frames[0] if isinstance(out.frames[0], (list, tuple)) else out.frames
            frames = [f if isinstance(f, Image.Image) else Image.fromarray(np.asarray(f))
                      for f in fr]
            m = metrics(frames, ref0)
            m.update({"tag": tag, "frames_req": nf, "sec": round(dt, 1),
                      "steps": args.steps, "cfg": args.cfg, "quant": args.quant})
            results.append(m)
            print(f"[{tag}@{nf}f] {m['size']} {dt:.0f}s  refL1={m['ref_l1']} "
                  f"bgFlash={m['bg_flicker']} motion={m['motion']} "
                  f"drift={m['drift_px']}px areaΔ={m['area_delta_pct']}% "
                  f"std={m['std_pct']}%", flush=True)
            grid(frames, OUT / f"{args.tag}{tag}_{nf}f_grid.png")
            try:
                import imageio.v2 as iio
                iio.mimsave(str(OUT / f"{args.tag}{tag}_{nf}f.mp4"),
                            [np.asarray(f.convert("RGB")) for f in frames],
                            fps=max(1, round(len(frames) / 1.75)), macro_block_size=1)
            except Exception as e:
                print(f"   mp4 保存失败 {e}", flush=True)
        tf.save(OUT / f"{args.tag}{tag}_input.png")

    (OUT / f"report{args.tag}.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1, default=str))
    print("\n汇总：")
    hdr = f"{'tag':<16}{'size':<13}{'quant':<12}{'f':<5}{'refL1':<8}{'bgFlash':<9}{'motion':<9}{'drift':<8}{'areaΔ%'}"
    print("  " + hdr)
    for r in results:
        print(f"  {r['tag']:<16}{str(r['size']):<13}{r['quant']:<12}"
              f"{r['frames_req']:<5}{str(r['ref_l1']):<8}{r['bg_flicker']:<9}"
              f"{r['motion']:<9}{r['drift_px']:<8}{r['area_delta_pct']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
