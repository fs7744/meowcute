"""正面行（源 0-2 帧）单独扫：侧面行和背面行都能跟住，只有正面行会漂。

怀疑原因：正面剪影左右对称、信息量低，控制网给不出方向信号，模型就自由发挥。
这里对 0-2 帧各跑两种候选（灰剪影 x1.0 / 黑剪影 x2.4），看能不能三帧都出正面。

用法：.venv/Scripts/python.exe tools/probe_front.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc                      # noqa: E402
from app.pipelines.qwen20_edit import (DEFAULT_NEGATIVE, DEFAULT_PROMPT,  # noqa: E402
                                       Qwen20EditControl)
from tools_path import board_cells, sheet_cells                 # noqa: E402

OUT = ROOT / "out"
ROW_CELL = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]


def main() -> None:
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]
    eng = Qwen20EditControl(size=512, steps=30, cfg=4.0)
    eng.ensure()
    cache = eng.warm_prompts([DEFAULT_PROMPT, DEFAULT_NEGATIVE])
    pos, pos_mask = cache[DEFAULT_PROMPT]
    neg, neg_mask = cache[DEFAULT_NEGATIVE]
    embeds = {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}
    for k in (0, 1, 2):
        for kind, sc in (("sil", 1.0), ("black", 2.4)):
            img, meta = eng.run_frame(control=cells[k], ref=refs[ROW_CELL[k]],
                                      kind=kind, cn_scale=sc, embeds=embeds, seed=1000 + k)
            img.save(OUT / f"fr_{kind}_{sc}_{k:02d}.png")
            print(f"[front] {kind} x{sc} 帧{k} {meta['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
