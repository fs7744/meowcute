"""HunyuanVideo-1.5 I2V 冒烟测试（两阶段加载，适配 32G 内存）。

阶段1 文本编码：tools/hy15_assemble.py --encode（或本脚本自动补跑）
阶段2 生成：只加载 DiT + VAE + SigLIP，喂预计算 embeds。

用法：
    python tools/hy15_smoke.py                              # 默认 61 帧 / 12 步
    python tools/hy15_smoke.py --frames 121 --steps 8
    python tools/hy15_smoke.py --guidance 6.0
"""

from __future__ import annotations

import argparse
import gc
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "models" / "hunyuanvideo-1.5"
OUT = ROOT / ".verify" / "hy15"
CELL = OUT / "cell0.png"

DEFAULT_PROMPT = (
    "A panda special forces soldier holding a rifle walks forward with a natural, "
    "steady walking cycle: legs alternate stepping, arms swing gently, torso stays "
    "upright. Full body visible, static camera, plain light-gray background, "
    "consistent character and outfit throughout."
)


def ensure_embeds(prompt: str, negative: str) -> Path:
    """阶段1：编码 prompt/negative 并缓存（已存在则复用）。"""
    sys.path.insert(0, str(ROOT / "tools"))
    from hy15_assemble import EMBEDS, encode  # noqa: PLC0415

    key = __import__("hashlib").sha1(f"{prompt}\x00{negative}".encode("utf-8")).hexdigest()[:12]
    cached = EMBEDS / f"{key}.pt"
    if cached.exists():
        print(f"复用文本编码缓存 {cached.name}")
        return cached
    return encode(prompt, negative)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=21, help="61 帧会超线性变慢（25k tokens = 47s/步），21 帧约 2.4s/步")
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--guidance", type=float, default=1.0, help="step-distilled 官方值 1.0（无 CFG）")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--prompt", default=DEFAULT_PROMPT)
    ap.add_argument("--negative", default="")
    ap.add_argument("--image", default=str(CELL))
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)

    # ---- 阶段 1：文本编码（CPU，编完即释放） ----
    t0 = time.time()
    emb_path = ensure_embeds(args.prompt, args.negative)
    blob = torch.load(emb_path, map_location="cpu", weights_only=False)
    (pe, pem, pe2, pem2) = blob["pos"]
    (ne, nem, ne2, nem2) = blob["neg"]
    print(f"文本编码就绪 [{emb_path.name}] {time.time()-t0:.1f}s", flush=True)

    # ---- 阶段 2：加载主权重并生成 ----
    from diffusers import (
        AutoencoderKLHunyuanVideo15,
        ClassifierFreeGuidance,
        FlowMatchEulerDiscreteScheduler,
        HunyuanVideo15ImageToVideoPipeline,
        HunyuanVideo15Transformer3DModel,
    )
    from transformers import SiglipImageProcessor, SiglipVisionModel

    dtype = torch.bfloat16
    print("loading transformer ...", flush=True)
    transformer = HunyuanVideo15Transformer3DModel.from_pretrained(
        CKPT / "transformer-diffusers", torch_dtype=dtype
    )
    print("loading vae + image_encoder ...", flush=True)
    vae = AutoencoderKLHunyuanVideo15.from_pretrained(CKPT / "vae-diffusers", torch_dtype=dtype)
    siglip_dir = CKPT / "vision_encoder" / "siglip"
    siglip_enc = siglip_dir / "image_encoder" if (siglip_dir / "image_encoder" / "config.json").exists() else siglip_dir
    siglip_pp = siglip_dir / "feature_extractor" if (siglip_dir / "feature_extractor" / "preprocessor_config.json").exists() else siglip_dir
    image_encoder = SiglipVisionModel.from_pretrained(siglip_enc, torch_dtype=dtype)
    feature_extractor = SiglipImageProcessor.from_pretrained(siglip_pp)

    pipe = HunyuanVideo15ImageToVideoPipeline(
        text_encoder=None,
        tokenizer=None,
        transformer=transformer,
        vae=vae,
        scheduler=FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=7.0, use_dynamic_shifting=False),
        text_encoder_2=None,
        tokenizer_2=None,
        guider=ClassifierFreeGuidance(guidance_scale=args.guidance, enabled=args.guidance != 1.0),
        image_encoder=image_encoder,
        feature_extractor=feature_extractor,
    )
    # 必须 offload：DiT 常驻 15.5G + VAE 解码峰值 12G > 24G，会触发显存颠簸（卡死）。
    # offload 每步搬运 15.5G 权重约 1.2s，换来解码前自动腾空显存。
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    print(f"pipeline ready {time.time()-t0:.1f}s", flush=True)

    # 输入图：透明底角色 -> 铺浅灰底（do_convert_rgb 会把透明转黑，先合成）
    im = Image.open(args.image).convert("RGBA")
    bg = Image.new("RGBA", im.size, (235, 235, 235, 255))
    im = Image.alpha_composite(bg, im).convert("RGB")
    print(f"input: {im.size}", flush=True)

    generator = torch.Generator(device="cuda").manual_seed(args.seed)
    t1 = time.time()
    print(f"generating: frames={args.frames} steps={args.steps} guidance={args.guidance} seed={args.seed}", flush=True)
    result = pipe(
        image=im,
        prompt=None,  # 传 embeds 时 prompt 必须为 None（check_inputs 会拦）
        prompt_embeds=pe,
        prompt_embeds_mask=pem,
        prompt_embeds_2=pe2,
        prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne,
        negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2,
        negative_prompt_embeds_mask_2=nem2,
        num_frames=args.frames,
        num_inference_steps=args.steps,
        generator=generator,
        output_type="pil",
    )
    frames = result.frames[0]
    dt = time.time() - t1
    peak = torch.cuda.max_memory_allocated() / 1024**3 if torch.cuda.is_available() else 0
    print(f"done in {dt:.1f}s, peak VRAM {peak:.1f} GB", flush=True)

    tag = f"f{args.frames}s{args.steps}g{args.guidance}seed{args.seed}"
    fdir = OUT / f"frames-{tag}"
    fdir.mkdir(exist_ok=True)
    for i, fr in enumerate(frames):
        fr.save(fdir / f"{i:03d}.png")
    print(f"frames -> {fdir} ({len(frames)} frames)")

    step = max(1, len(frames) // 8)
    picks = list(range(0, len(frames), step))[:8]
    tw = 220
    thumbs = [frames[i].resize((tw, int(frames[i].size[1] * tw / frames[i].size[0]))) for i in picks]
    th = max(t.size[1] for t in thumbs)
    sheet = Image.new("RGB", (tw * len(thumbs), th), (20, 20, 20))
    for j, t in enumerate(thumbs):
        sheet.paste(t, (j * tw, (th - t.size[1]) // 2))
    sheet.save(OUT / f"contact-{tag}.png")
    print(f"contact sheet -> {OUT / f'contact-{tag}.png'}")

    try:
        from diffusers.utils import export_to_video

        export_to_video(frames, str(OUT / f"walk-{tag}.mp4"), fps=24)
        print(f"mp4 -> {OUT / f'walk-{tag}.mp4'}")
    except Exception as e:  # noqa: BLE001
        print(f"mp4 导出跳过: {e}")

    print(f"RESULT tag={tag} secs={dt:.1f} peak_gb={peak:.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
