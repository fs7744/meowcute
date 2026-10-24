"""两步法优化行走动作：先用 Qwen 改出「迈步中」锚点图，再用 HunyuanVideo 续走。

动机：I2V 的运动模式被首帧姿态强约束——宽站姿参考图只会得到"原地屈膝/上下颠"。
先把首帧改成单腿在前、单腿在后的迈步姿态，模型才有步态可延续。

用法：
    python tools/hy15_anchor.py                 # 生成锚点 + 两条续走
    python tools/hy15_anchor.py --skip-anchor   # 复用已有锚点
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
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

CKPT = ROOT / "models" / "hunyuanvideo-1.5"
QWEN = ROOT / "models" / "qwen-image-2.1"
OUT = ROOT / ".verify" / "hy15" / "anchor"
CELL = ROOT / ".verify" / "hy15" / "cell0.png"

EDIT_PROMPT = (
    "Same character, same outfit, same weapon, same plain light-gray background, "
    "same size and framing. Change only the leg pose into a mid-stride walking pose: "
    "the left leg is stretched forward with the foot planted, the right leg is behind "
    "with the heel lifted, both arms swing in opposition, torso upright, head facing "
    "the camera. Full body visible."
)

WALK_PROMPTS = {
    "A": ("continue-walk", (
        "The soldier continues walking in place, completing one full stride cycle: the rear "
        "right leg lifts, swings forward past the planted left leg and plants; then the left "
        "leg lifts and swings forward. Natural alternating stride, arms swinging in opposition, "
        "torso upright. He keeps stepping on the same spot at the same size, facing the camera. "
        "Static camera, no zoom, plain light-gray background, identical outfit and weapon."
    )),
    "C": ("one-cycle", (
        "Over this clip the soldier completes exactly one full walking cycle and finishes in "
        "the same pose he starts in: the rear leg swings forward and plants, the other leg "
        "lifts and swings forward, then lifts and returns to the starting stance. Arms swing "
        "in opposition. He stays exactly the same distance from the camera and the same size "
        "in frame, never stepping closer or further, never zooming. Facing the camera, static "
        "camera, plain light-gray background, identical outfit and weapon."
    )),
    "D": ("cycle-33f", (
        "The soldier walks in place through a full stride cycle: rear leg swings forward and "
        "plants, front leg lifts and swings back, repeat, ending in the starting pose. Smooth "
        "alternating strides, arms swinging opposite the legs, torso upright. Same distance "
        "from camera and same size throughout, facing the camera, static camera, plain "
        "light-gray background, identical outfit and weapon."
    )),
    "B": ("treadmill", (
        "The soldier walks on a treadmill at a steady pace: legs alternate stretching forward "
        "and pushing back in a clean stride, arms swing naturally, body bobs slightly with each "
        "step. Same position and size in frame, facing the camera, static camera, plain "
        "light-gray background, identical outfit and weapon throughout."
    )),
}


def build_qwen():
    from diffusers import QwenImage21Pipeline, QwenImage21Transformer2DModel
    from transformers import Qwen3VLForConditionalGeneration

    from backend.app.providers.diffusers_local import DiffusersProvider, _enable_offload

    provider = DiffusersProvider({"model": str(QWEN)})
    print("loading qwen transformer (fp8) ...", flush=True)
    tf = provider._load_component_fp8(str(QWEN), "transformer", QwenImage21Transformer2DModel, "qwen21.transformer")
    print("loading qwen text encoder (fp8) ...", flush=True)
    te = Qwen3VLForConditionalGeneration.from_pretrained(str(QWEN / "text_encoder"), torch_dtype=torch.bfloat16)
    provider._quantize_fp8(te, "qwen21.text_encoder")
    pipe = QwenImage21Pipeline.from_pretrained(str(QWEN), transformer=tf, text_encoder=te, torch_dtype=torch.bfloat16)
    _enable_offload(pipe, "qwen21")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def make_anchor() -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    dst = OUT / "anchor-stride.png"
    if dst.exists():
        print(f"锚点已存在 {dst}")
        return dst
    im = Image.open(CELL).convert("RGBA")
    bg = Image.new("RGBA", im.size, (235, 235, 235, 255))
    im = Image.alpha_composite(bg, im).convert("RGB")

    pipe = build_qwen()
    t0 = time.time()
    out = pipe(
        image=im, prompt=EDIT_PROMPT,
        width=512, height=512, output_resolution=512,
        num_inference_steps=40, true_cfg_scale=4.0,
        generator=torch.Generator("cpu").manual_seed(20261024),
    ).images[0]
    print(f"锚点生成 {time.time()-t0:.1f}s", flush=True)
    out.save(dst)
    del pipe
    gc.collect()
    torch.cuda.empty_cache()
    from backend.app.providers.diffusers_local import _release_vram

    _release_vram()
    return dst


def run_walk(anchor: Path, key: str, frames: int = 21, steps: int = 12, seed: int = 7) -> None:
    from diffusers import (
        AutoencoderKLHunyuanVideo15, ClassifierFreeGuidance, FlowMatchEulerDiscreteScheduler,
        HunyuanVideo15ImageToVideoPipeline, HunyuanVideo15Transformer3DModel,
    )
    from transformers import SiglipImageProcessor, SiglipVisionModel

    from hy15_assemble import encode

    tag, prompt = WALK_PROMPTS[key]
    print(f"\n=== 续走 {tag} frames={frames} steps={steps} seed={seed} ===", flush=True)
    ckpt = encode(prompt, "")
    blob = torch.load(ckpt, map_location="cpu", weights_only=False)
    (pe, pem, pe2, pem2) = blob["pos"]
    (ne, nem, ne2, nem2) = blob["neg"]

    dtype = torch.bfloat16
    print("loading hy15 ...", flush=True)
    transformer = HunyuanVideo15Transformer3DModel.from_pretrained(CKPT / "transformer-diffusers", torch_dtype=dtype)
    vae = AutoencoderKLHunyuanVideo15.from_pretrained(CKPT / "vae-diffusers", torch_dtype=dtype)
    sd = CKPT / "vision_encoder" / "siglip"
    enc = sd / "image_encoder" if (sd / "image_encoder" / "config.json").exists() else sd
    pp = sd / "feature_extractor" if (sd / "feature_extractor" / "preprocessor_config.json").exists() else sd
    image_encoder = SiglipVisionModel.from_pretrained(enc, torch_dtype=dtype)
    feature_extractor = SiglipImageProcessor.from_pretrained(pp)

    pipe = HunyuanVideo15ImageToVideoPipeline(
        text_encoder=None, tokenizer=None, transformer=transformer, vae=vae,
        scheduler=FlowMatchEulerDiscreteScheduler(num_train_timesteps=1000, shift=7.0, use_dynamic_shifting=False),
        text_encoder_2=None, tokenizer_2=None,
        guider=ClassifierFreeGuidance(guidance_scale=1.0, enabled=False),
        image_encoder=image_encoder, feature_extractor=feature_extractor,
    )
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()

    img = Image.open(anchor).convert("RGB")
    t0 = time.time()
    out = pipe(
        image=img, prompt=None,
        prompt_embeds=pe, prompt_embeds_mask=pem, prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
        num_frames=frames, num_inference_steps=steps,
        generator=torch.Generator("cuda").manual_seed(seed), output_type="pil",
    )
    frames_list = out.frames[0]
    print(f"  {time.time()-t0:.1f}s, peak {torch.cuda.max_memory_allocated()/1024**3:.1f}G", flush=True)
    d = OUT / f"walk-{tag}"
    d.mkdir(exist_ok=True)
    for i, f in enumerate(frames_list):
        f.save(d / f"{i:03d}.png")
    print(f"  frames -> {d}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-anchor", action="store_true")
    ap.add_argument("--frames", type=int, default=21)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--keys", default="A,B")
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    anchor = OUT / "anchor-stride.png" if args.skip_anchor else make_anchor()
    print(f"锚点: {anchor}")
    for k in [x.strip() for x in args.keys.split(",") if x.strip()]:
        run_walk(anchor, k, args.frames, args.steps, seed=args.seed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
