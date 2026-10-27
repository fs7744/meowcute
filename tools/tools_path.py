"""实验脚本共用的素材读取：动作精灵图切格 + 四视图板切格。

放在单独模块里，避免每个探针脚本各抄一份格子坐标（改一次格子就得改五处）。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc  # noqa: E402

SHEET = ROOT / "data/projects/p-dbdb6b3dbc19/refs/ref-20261026-224308-1796cfc8.png"
BOARD = ROOT / "data/projects/p-dbdb6b3dbc19/refs/ref-20261026-224318-a94709d4.png"
COLS, ROWS = 3, 4


def sheet_cells() -> List[Image.Image]:
    img = Image.open(SHEET).convert("RGBA")
    cw, ch = img.width // COLS, img.height // ROWS
    return [img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            for r in range(ROWS) for c in range(COLS)]


def board_cells() -> List[Image.Image]:
    board = pc.repack_board(Image.open(BOARD).convert("RGBA"))[0]
    return pc.split_board_cells(board)
