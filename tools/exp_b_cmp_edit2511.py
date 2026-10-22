# -*- coding: utf-8 -*-
"""实验 B 补充：edit-2511 在同基准图（矮人）上的朝向改写对照。

背景：docs/模型选型调研-2026-10-21.md 第五节原计划对比
.verify/exp-a/left-base40.png，但该图基准是另一张角色（橙猫），与实验 B 的
矮人基准（out-20261021-212829-1-4f76.png）不符，对比对象错位。
本脚本用与 exp_qwen21.py 完全相同的设置补跑 edit-2511：
同图 + 同指令 + 40 步 + true_cfg 4.0 + 同种子(20261021) + 同尺寸(384x512)。
"""
from __future__ import annotations

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
MODEL = str(ROOT / "models" / "qwen-image-edit-2511")
OUT_DIR = ROOT / ".verify" / "exp-b"


def main() -> int:
    import torch
    from diffusers import QwenImageEditPlusPipeline

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = storage.load_image(PID, "images", BASE)
    if base is None:
        raise SystemExit("基准图不存在")

    prov = DiffusersProvider({})
    t0 = time.time()
    transformer = prov._load_qwen_transformer_fp8(MODEL)
    print(f"[t] fp8 transformer {time.time() - t0:.0f}s", flush=True)
    pipe = QwenImageEditPlusPipeline.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, transformer=transformer)
    _enable_offload(pipe, "qwen_edit")
    pipe.set_progress_bar_config(disable=True)
    print(f"[t] 管线就绪 {time.time() - t0:.0f}s", flush=True)

    torch.cuda.reset_peak_memory_stats()
    t1 = time.time()
    out = pipe(
        image=base.convert("RGB"),
        prompt=direction_instruction("left"),
        negative_prompt="",
        num_inference_steps=40,
        true_cfg_scale=4.0,
        width=384, height=512,
        generator=torch.Generator("cpu").manual_seed(20261021),
    ).images[0]
    dt = time.time() - t1
    peak = torch.cuda.max_memory_allocated() / 2**30
    p = OUT_DIR / "left-edit2511.png"
    out.save(p)
    print(f"[edit-2511 改图] {dt:5.1f}s 峰值 {peak:5.2f}GB 输出 {out.size} → {p.name}",
          flush=True)
    _release_vram()
    return 0


if __name__ == "__main__":
    sys.exit(main())
