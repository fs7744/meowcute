"""批量稳定性的显存/时间诊断：同一个进程连跑 N 帧，看显存是不是逐帧往上爬。

背景：Edit+ControlNet 单帧 88s / 峰值 21.3GB 很稳，但**同一个进程跑第二帧就顶到
24GB 原地打转**。要先分清三种可能，再决定修法：
  a) 每帧真的漏了（引用没释放 → memory_allocated 单调上升）；
  b) 没漏但分配器碎片化（allocated 平稳、reserved 上涨）；
  c) 只是慢（两者都平稳，且每帧耗时也在涨）。

用法：.venv/Scripts/python.exe tools/diag_batch.py [帧数] [尺寸]
"""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc          # noqa: E402
from tools_path import board_cells, sheet_cells    # noqa: E402

OUT = ROOT / "out"


def mb(x: float) -> float:
    return round(x / 2**30, 2)


def main() -> None:
    import probe_edit_ctrl as pe

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 384
    pe.SIZE = size

    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    row_cell = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]

    def ref_cell(k: int) -> Image.Image:
        c = board_cells()[row_cell[k]].convert("RGBA")
        return Image.alpha_composite(
            Image.new("RGBA", c.size, (255, 255, 255, 255)), c).convert("RGB")

    import diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus as _ep
    _ep.VAE_IMAGE_SIZE = 512 ** 2

    vae = pe.load_vae()
    conds = [pe.encode_control(vae, pe.control_image(c, "black"), size, size,
                               "cuda", vae.dtype) for c in cells[:n]]
    torch.cuda.empty_cache()
    print(f"[diag] 开始：allocated {mb(torch.cuda.memory_allocated())}GB "
          f"reserved {mb(torch.cuda.memory_reserved())}GB", flush=True)

    pipe, controlnet = pe.build(vae)
    print(f"[diag] 管线就绪：allocated {mb(torch.cuda.memory_allocated())}GB "
          f"reserved {mb(torch.cuda.memory_reserved())}GB", flush=True)

    for k in range(n):
        pe.attach_control(pipe, controlnet, conds[k], scale=1.0)
        torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        out = pipe(
            image=[ref_cell(k)], prompt=pe.PROMPT, negative_prompt=pe.NEG,
            height=size, width=size, num_inference_steps=pe.STEPS,
            true_cfg_scale=4.0, generator=torch.Generator("cuda").manual_seed(1234),
        ).images[0]
        out.save(OUT / f"diag_{size}_{k:02d}.png")
        dt = time.time() - t0
        print(f"[diag] 帧 {k}: {dt:.0f}s  peak {mb(torch.cuda.max_memory_allocated())}GB  "
              f"allocated {mb(torch.cuda.memory_allocated())}GB  "
              f"reserved {mb(torch.cuda.memory_reserved())}GB", flush=True)
        del out
        gc.collect()
        torch.cuda.empty_cache()
        print(f"[diag]      清完 allocated {mb(torch.cuda.memory_allocated())}GB  "
              f"reserved {mb(torch.cuda.memory_reserved())}GB", flush=True)


if __name__ == "__main__":
    main()
