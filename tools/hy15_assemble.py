"""HunyuanVideo-1.5 文本编码缓存（两阶段加载的第一阶段）。

本机 32G 内存装不下全部组件（bf16 约 35G），所以：
  阶段1（本脚本）：只加载 Qwen2.5-VL-7B + byt5 编码 prompt -> 存 .pt -> 释放
  阶段2（hy15_smoke.py / 后续 provider）：只加载 DiT+VAE+SigLIP，喂预计算 embeds 生成

用法：
    python tools/hy15_assemble.py --check                 # 检查下载文件完整性
    python tools/hy15_assemble.py --encode                # 用默认提示词编编码
    python tools/hy15_assemble.py --encode --prompt "..." --negative "..."
输出：models/hunyuanvideo-1.5/embeds/<hash>.pt
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "models" / "hunyuanvideo-1.5"
EMBEDS = CKPT / "embeds"

NEEDED = [
    ("transformer/480p_i2v_step_distilled", ["diffusion_pytorch_model.safetensors", "config.json"], "DiT 480p I2V step-distilled"),
    ("vae", ["diffusion_pytorch_model.safetensors", "config.json"], "3D causal VAE"),
    ("scheduler", ["scheduler_config.json"], "scheduler 配置"),
    ("text_encoder/llm", ["config.json", "tokenizer.json"], "Qwen2.5-VL-7B 文本编码器"),
    # byt5 不需要 spiece.model：transformers 5.x 的 ByT5Tokenizer 是纯字节映射
    # （id = utf8字节+3），且管线只编码提示词引号内的文字，没有引号文字时用零向量
    ("text_encoder/byt5-small", ["config.json"], "byt5 glyph 编码器"),
]


def check() -> bool:
    ok = True
    for sub, files, desc in NEEDED:
        for f in files:
            p = CKPT / sub / f
            if not p.exists():
                ok = False
                print(f"  [MISS] {sub}/{f}  ({desc})")
    for cand in ("vision_encoder/siglip/image_encoder", "vision_encoder/siglip"):
        if (CKPT / cand / "config.json").exists():
            break
    else:
        print("  [MISS] vision_encoder/siglip/  (SigLIP 图像编码器)")
        ok = False
    print("文件检查:" + ("全部就绪" if ok else "有缺失"))
    return ok


def encode(prompt: str, negative: str) -> Path:
    """只加载两个文本编码器编码 prompt/negative，不建管线。

    _get_mllm_prompt_embeds / _get_byt5_prompt_embeds 都是 @staticmethod，
    直接调用可以避开「用 None 组件建管线」时 __init__ 里 hasattr(None) 的坑。
    """
    from diffusers.pipelines.hunyuan_video1_5.pipeline_hunyuan_video1_5_image2video import (
        HunyuanVideo15ImageToVideoPipeline as P,
    )
    from transformers import (
        ByT5Tokenizer,
        Qwen2_5_VLTextModel,
        Qwen2Tokenizer,
        T5EncoderModel,
    )

    dtype = torch.bfloat16
    device = torch.device("cpu")
    print("loading text encoders ...", flush=True)
    text_encoder = Qwen2_5_VLTextModel.from_pretrained(CKPT / "text_encoder" / "llm", torch_dtype=dtype)
    tokenizer = Qwen2Tokenizer.from_pretrained(CKPT / "text_encoder" / "llm")
    text_encoder_2 = T5EncoderModel.from_pretrained(CKPT / "text_encoder" / "byt5-small", torch_dtype=dtype)
    # 纯字节分词器：ModelScope 的 byt5 镜像缺 spiece.model，但 transformers 5.x 的
    # ByT5Tokenizer 是纯 Python 字节映射（id = utf8 字节 + 3），不需要词表文件
    tokenizer_2 = ByT5Tokenizer()

    def _enc(text: str):
        pe, pem = P._get_mllm_prompt_embeds(
            tokenizer=tokenizer,
            text_encoder=text_encoder,
            prompt=[text],
            device=device,
            tokenizer_max_length=1000,
        )
        pe2, pem2 = P._get_byt5_prompt_embeds(
            tokenizer=tokenizer_2,
            text_encoder=text_encoder_2,
            prompt=[text],
            device=device,
            tokenizer_max_length=256,
        )
        return (pe, pem, pe2, pem2)

    print("encoding prompt ...", flush=True)
    pos = _enc(prompt)
    print("encoding negative prompt ...", flush=True)
    neg = _enc(negative)
    del text_encoder, text_encoder_2
    import gc

    gc.collect()

    key = hashlib.sha1(f"{prompt}\x00{negative}".encode("utf-8")).hexdigest()[:12]
    EMBEDS.mkdir(parents=True, exist_ok=True)
    out = EMBEDS / f"{key}.pt"
    torch.save(
        {
            "prompt": prompt,
            "negative": negative,
            "pos": [t.contiguous() for t in pos],
            "neg": [t.contiguous() for t in neg],
        },
        out,
    )
    print(f"saved -> {out}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--encode", action="store_true")
    ap.add_argument("--prompt", default="")
    ap.add_argument("--negative", default="")
    args = ap.parse_args()

    if args.check:
        return 0 if check() else 1
    if args.encode:
        encode(args.prompt, args.negative)
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
