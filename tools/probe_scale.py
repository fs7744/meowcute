"""正面行（源 0-2 帧是正面站桩）控制不住，怀疑是「剪影太糊 + 强度不够」。
这里对同一帧扫控制强度与控制图类型。

用法：.venv/Scripts/python.exe tools/probe_scale.py [帧号]
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
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]
    eng = Qwen20EditControl(size=512, steps=30, cfg=4.0)
    eng.ensure()
    cache = eng.warm_prompts([DEFAULT_PROMPT, DEFAULT_NEGATIVE])
    pos, pos_mask = cache[DEFAULT_PROMPT]
    neg, neg_mask = cache[DEFAULT_NEGATIVE]
    embeds = {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}
    plans = [("black", 1.0), ("black", 1.6), ("black", 2.4),
             ("sil", 1.0), ("src", 1.0), ("black", 1.6)]
    for i, (kind, sc) in enumerate(plans):
        img, meta = eng.run_frame(control=cells[k], ref=refs[ROW_CELL[k]],
                                  kind=kind, cn_scale=sc, embeds=embeds, seed=1000 + k)
        img.save(OUT / f"sc_{kind}_{sc}_{k:02d}.png")
        print(f"[scale] {kind} x{sc} 帧{k} {meta['seconds']}s", flush=True)


if __name__ == "__main__":
    main()
