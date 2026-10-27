"""12 帧全跑（Edit-2511 + ControlNet）：身份来自四视图，姿势来自动作帧。

每帧一次推理约 90s（含参考图 VAE 编码与 CFG 双通道），12 帧约 20 分钟。
进程内连跑会在第二帧顶到 24GB（PyTorch 分配器碎片），所以每帧之间
`gc.collect() + empty_cache()`；仍然卡死的话就用 EXIT_AFTER=1 走「每帧独立进程」。

用法：.venv/Scripts/python.exe tools/probe_edit_ctrl12.py [black] [帧号...]
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


def main() -> None:
    import probe_edit_ctrl as pe

    kind = sys.argv[1] if len(sys.argv) > 1 else "black"
    want = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else list(range(12))
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]

    if os.environ.get("SIZE"):
        pe.SIZE = int(os.environ["SIZE"])

    import diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus as _ep
    _ep.VAE_IMAGE_SIZE = 512 ** 2

    vae = pe.load_vae()
    if len(want) > 1:
        raise SystemExit("一次只跑一帧：进程内连跑会在第二帧顶满显存（见模块注释）")
    k = want[0]
    # 参考格必须跟着这一帧的朝向走：源的 12 帧按行是「正面 / 侧面 / 侧面 / 背面」，
    # 一直用正面格会让模型把每一帧都画成正面（实测 6 帧全站桩）。这里按行配格：
    # 0-2 正面格、3-8 侧面格、9-11 背面格。
    row_cell = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]
    ref_idx = int(os.environ.get("REF_CELLS", "").split(",")[k]) if os.environ.get("REF_CELLS") else row_cell[k]
    ref = board_cells()[ref_idx].convert("RGBA")
    ref = Image.alpha_composite(
        Image.new("RGBA", ref.size, (255, 255, 255, 255)), ref).convert("RGB")
    cond = pe.encode_control(vae, pe.control_image(cells[k], kind),
                             pe.SIZE, pe.SIZE, "cuda", vae.dtype)
    torch.cuda.empty_cache()
    pipe, controlnet = pe.build(vae)
    pe.attach_control(pipe, controlnet, cond, scale=1.0)

    t0 = time.time()
    out = pipe(
        image=[ref], prompt=pe.PROMPT, negative_prompt=pe.NEG,
        height=pe.SIZE, width=pe.SIZE, num_inference_steps=pe.STEPS,
        true_cfg_scale=4.0, generator=torch.Generator("cuda").manual_seed(1234),
    ).images[0].convert("RGB")
    out.save(OUT / f"ec12_{kind}_{k:02d}.png")
    print(f"帧 {k:02d} {time.time() - t0:.0f}s", flush=True)
    del pipe, controlnet, vae
    gc.collect()
    torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
