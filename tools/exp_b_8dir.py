# -*- coding: utf-8 -*-
"""实验 B 追加：八方向一致性对跑（Qwen-Image-2.1 vs Qwen-Image-Edit-2511）。

设计（保守口径）：7 个方向全部从**同一正面锚点** + 全量方向指令推导。
生产管线实际用「最近锚点接力」（front/side/back 三锚点，旋转量 0~90°），
比本实验更稳；本实验刻意用更难的单锚点全量旋转（含 135°/180°），
若 2.1 仍不输 edit-2511，则接力策略下只会更好。

锚点：生产管线的正面视图 .verify/exp-b/walkdown-frame0.png（384x512，
walk-down GIF 首帧；像素画经 GIF 调色板损失极小）。
方向：7 个（down 是锚点自身，生产逻辑 passthrough 不跑）。
设置对齐 exp_qwen21.py：40 步 / true_cfg 4.0 / 种子 20261021 / 384x512 /
negative_prompt="" 显式传入（修复 true_cfg 静默失效的坑）。

产出：.verify/exp-b/dirs8-<model>/<dir>.png + dirs8-results.json
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

from PIL import Image  # noqa: E402

from backend.app.pipelines.walkvideo import direction_instruction  # noqa: E402
from backend.app.providers.diffusers_local import (  # noqa: E402
    DiffusersProvider, _enable_offload, _release_vram,
)

ANCHOR = ROOT / ".verify" / "exp-b" / "walkdown-frame0.png"
OUT = ROOT / ".verify" / "exp-b"
DIRS = ["down-left", "left", "up-left", "up", "up-right", "right", "down-right"]
SEED = 20261021
W, H = 384, 512
MODEL_21 = ROOT / "models" / "qwen-image-2.1"
MODEL_EDIT = str(ROOT / "models" / "qwen-image-edit-2511")


def build_qwen21():
    import torch
    from diffusers import QwenImage21Pipeline, QwenImage21Transformer2DModel
    from transformers import Qwen3VLForConditionalGeneration

    provider = DiffusersProvider({"model": str(MODEL_21)})
    tf = provider._load_component_fp8(
        str(MODEL_21), "transformer", QwenImage21Transformer2DModel,
        "qwen21.transformer")
    te = Qwen3VLForConditionalGeneration.from_pretrained(
        str(MODEL_21 / "text_encoder"), torch_dtype=torch.bfloat16)
    provider._quantize_fp8(te, "qwen21.text_encoder")
    pipe = QwenImage21Pipeline.from_pretrained(
        str(MODEL_21), transformer=tf, text_encoder=te, torch_dtype=torch.bfloat16)
    _enable_offload(pipe, "qwen21")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def build_edit2511():
    import torch
    from diffusers import QwenImageEditPlusPipeline

    provider = DiffusersProvider({})
    transformer = provider._load_qwen_transformer_fp8(MODEL_EDIT)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        MODEL_EDIT, torch_dtype=torch.bfloat16, transformer=transformer)
    _enable_offload(pipe, "qwen_edit")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def run_one(pipe, model, anchor, direction):
    import torch
    prompt = direction_instruction(direction, "front")
    gen = torch.Generator("cpu").manual_seed(SEED)
    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    kw = dict(image=anchor.convert("RGB"), prompt=prompt, negative_prompt="",
              num_inference_steps=40, true_cfg_scale=4.0,
              width=W, height=H, generator=gen)
    if model == "qwen21":
        kw["output_resolution"] = 512
    out = pipe(**kw).images[0]
    dt = time.time() - t0
    peak = (torch.cuda.max_memory_allocated() / 2**30
            if torch.cuda.is_available() else -1)
    return out, dt, peak, prompt


def main():
    anchor = Image.open(ANCHOR).convert("RGB")
    print(f"[anchor] {ANCHOR.name} {anchor.size}", flush=True)
    results = []
    builders = {"qwen21": build_qwen21, "edit2511": build_edit2511}
    for model, build in builders.items():
        t0 = time.time()
        pipe = build()
        print(f"[{model}] 管线就绪 {time.time() - t0:.0f}s", flush=True)
        out_dir = OUT / f"dirs8-{model}"
        out_dir.mkdir(parents=True, exist_ok=True)
        for d in DIRS:
            img, dt, peak, prompt = run_one(pipe, model, anchor, d)
            p = out_dir / f"{d}.png"
            img.save(p)
            results.append({"model": model, "direction": d, "seconds": round(dt, 1),
                            "peakGB": round(peak, 2), "path": str(p)})
            print(f"[{model}] {d:11s} {dt:6.1f}s 峰值 {peak:5.2f}GB → {p.name}",
                  flush=True)
            _release_vram()
        del pipe
        _release_vram()
    (OUT / "dirs8-results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[done] {len(results)} 张方向图 → dirs8-results.json", flush=True)


if __name__ == "__main__":
    main()
