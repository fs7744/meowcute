"""下载「原版 Qwen-Image 20B」的可跑子集，用于 ControlNet 可行性验证。

为什么不是一个 snapshot_download 全下：整个 repo 约 58GB，其中 transformer bf16
占 41GB，而本机只有 32GB 内存——bf16 权重根本装不进内存去量化。所以拆成两路：
  · 除 transformer 以外的全部（text_encoder / tokenizer / processor /
    scheduler / vae / model_index，约 17GB），走 HF 镜像；
  · transformer 单独取 ComfyUI 的 fp8_e4m3fn 单文件（20GB），由
    tools/convert_qwen20b.py 转成 diffusers 目录。

跑到 models/qwen-image-20b/ 下，结构对 diffusers 的 QwenImageControlNetPipeline
是可加载的（transformer 由转换脚本产出）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ["HF_HUB_DISABLE_XET"] = "1"   # xet 通道在镜像上会 502（项目踩过）

from huggingface_hub import snapshot_download  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "models" / "qwen-image-20b"
DEST.mkdir(parents=True, exist_ok=True)

WANT = ["model_index.json", "scheduler/*", "tokenizer/*", "processor/*",
        "vae/*", "text_encoder/*"]

if __name__ == "__main__":
    p = snapshot_download(
        "Qwen/Qwen-Image",
        local_dir=str(DEST),
        allow_patterns=WANT,
        max_workers=4,
        ignore_patterns=["*.md", "*.gitattributes", "assets/*"],
    )
    print("DONE", p, flush=True)
    total = sum(f.stat().st_size for f in DEST.rglob("*") if f.is_file())
    print(f"{total / 2**30:.1f}GB", flush=True)
