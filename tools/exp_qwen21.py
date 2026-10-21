# -*- coding: utf-8 -*-
"""实验 B：Qwen-Image-2.1 的三项实测（改图忠实度 / 原生透明 / 显存耗时）。

背景与判定门槛见 docs/模型选型调研-2026-10-21.md 第五节。核心是第一条：
「只改朝向、其余以参考图为准」的忠实度能不能达到 Qwen-Image-Edit-2511 的水平。

准备：模型放在 models/qwen-image-2.1（官方 diffusers 格式，约 31GB）。
内存注意：transformer 13.25GB 与 text encoder 16.33GB 是**分别**加载、量化后再
组装管线的——一次性 from_pretrained 整个仓库要 31GB 内存，本机（31.8GB）扛不住。

用法：.venv/Scripts/python.exe tools/exp_qwen21.py [--edit-only]
"""
from __future__ import annotations

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

from backend.app import storage  # noqa: E402
from backend.app.pipelines.walkvideo import direction_instruction  # noqa: E402
from backend.app.providers.diffusers_local import (  # noqa: E402
    DiffusersProvider, _enable_offload, _release_vram,
)

PID = "p-e1fd855eb0f5"
BASE = "out-20261021-212829-1-4f76.png"
MODEL_DIR = ROOT / "models" / "qwen-image-2.1"
OUT_DIR = ROOT / ".verify" / "exp-b"


def _peak_gb() -> float:
    import torch
    return round(torch.cuda.max_memory_allocated() / 2**30, 2) if torch.cuda.is_available() else -1


def build_pipeline():
    """按组件分别加载 + fp8 量化，再组装管线（避免 31GB 一次性进内存）。"""
    import torch
    from diffusers import QwenImage21Pipeline, QwenImage21Transformer2DModel
    from transformers import Qwen3VLForConditionalGeneration

    if not MODEL_DIR.is_dir():
        raise SystemExit(f"模型不存在：{MODEL_DIR}")

    provider = DiffusersProvider({"model": str(MODEL_DIR)})
    print("加载并量化 transformer（13.25GB bf16 → fp8）…", flush=True)
    t0 = time.time()
    tf = provider._load_component_fp8(
        str(MODEL_DIR), "transformer", QwenImage21Transformer2DModel,
        "qwen21.transformer")
    print(f"  transformer {time.time() - t0:.0f}s", flush=True)

    print("加载并量化 text_encoder（16.33GB bf16 → fp8）…", flush=True)
    t0 = time.time()
    te = Qwen3VLForConditionalGeneration.from_pretrained(
        str(MODEL_DIR / "text_encoder"), torch_dtype=torch.bfloat16)
    provider._quantize_fp8(te, "qwen21.text_encoder")
    print(f"  text_encoder {time.time() - t0:.0f}s", flush=True)

    pipe = QwenImage21Pipeline.from_pretrained(
        str(MODEL_DIR), transformer=tf, text_encoder=te, torch_dtype=torch.bfloat16)
    _enable_offload(pipe, "qwen21")
    pipe.set_progress_bar_config(disable=True)
    return pipe


async def main() -> int:
    import torch
    ap = argparse.ArgumentParser()
    ap.add_argument("--edit-only", action="store_true", help="只跑改图忠实度，不跑透明测试")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    base = storage.load_image(PID, "images", BASE)
    if base is None:
        raise SystemExit("基准图不存在")
    pipe = build_pipeline()

    # ---- 1) 改图忠实度：与 Qwen-Image-Edit-2511 同一张图、同一句指令 ----
    for tag, cfg in (("cfg1", 1.0), ("cfg4", 4.0)):
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        out = pipe(
            image=base.convert("RGB"),
            prompt=direction_instruction("left"),
            width=384, height=512, output_resolution=512,
            num_inference_steps=40, true_cfg_scale=cfg,
            generator=torch.Generator("cpu").manual_seed(20261021),
        ).images[0]
        dt = time.time() - t0
        p = OUT_DIR / f"left-qwen21-{tag}.png"
        out.save(p)
        print(f"[2.1 改图 true_cfg={cfg}] {dt:5.1f}s 峰值 {_peak_gb():5.2f}GB "
              f"输出 {out.size} → {p.name}", flush=True)
        _release_vram()

    if args.edit_only:
        return 0

    # ---- 2) 原生透明：官方推荐的 RGBA 提示词格式 ----
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    out = pipe(
        prompt=("This is an RGBA image with transparency. 像素风小猫少女，"
                "三头身，正面站立，红色短披风。The image has alpha channel and "
                "the background is transparent."),
        width=512, height=512, output_resolution=512,
        num_inference_steps=40, true_cfg_scale=1.0,
        generator=torch.Generator("cpu").manual_seed(7),
    ).images[0]
    dt = time.time() - t0
    p = OUT_DIR / "alpha-test.png"
    out.save(p)
    has_alpha = out.mode in ("RGBA", "LA") or (out.mode == "P" and "transparency" in out.info)
    alpha_min = out.getchannel("A").getextrema() if has_alpha else None
    print(f"[2.1 透明] {dt:5.1f}s 峰值 {_peak_gb():5.2f}GB mode={out.mode} "
          f"alpha范围={alpha_min} → {p.name}", flush=True)
    print("透明结论：" + ("有 alpha 通道，可直接当抠图结果用"
                       if has_alpha and alpha_min and alpha_min[0] < 250
                       else "没有真正透明（alpha 不透明或通道缺失）"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
