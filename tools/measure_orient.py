"""量化「有没有跟住朝向」：剪影宽高比相关性。

为什么不用 IoU：源角色是矮胖的光头小人，生成的是青蓝长袍女剑客，两者体型
根本不是一回事，IoU 天然只有 0.4~0.6，看不出好坏。但**朝向**是可比的——
正面/背面比侧面宽得多（宽高比 0.5+ vs 0.3 左右）。所以比较源帧与生成帧
剪影的 w/h 序列，算 Pearson 相关系数：跟住了朝向就会显著正相关。

用法：.venv/Scripts/python.exe tools/measure_orient.py [black]
"""
from __future__ import annotations

import sys

import numpy as np
from PIL import Image

ROOT = __import__("pathlib").Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc  # noqa: E402
from tools_path import sheet_cells          # noqa: E402


def ratio(img: Image.Image) -> float:
    m = pc._fg_mask(img)
    if not m.any():
        return 0.0
    ys, xs = np.nonzero(m)
    h = max(1, ys.max() - ys.min() + 1)
    w = max(1, xs.max() - xs.min() + 1)
    return float(w / h)


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "black"
    cells = sheet_cells()
    rs, rg = [], []
    for k, c in enumerate(cells):
        try:
            gen = Image.open(ROOT / "out" / f"ctrl_{kind}_{k}.png").convert("RGBA")
        except FileNotFoundError:
            continue
        src, _ = pc._ensure_fg(c)
        rs.append(ratio(src))
        rg.append(ratio(gen))
    if len(rs) < 3:
        raise SystemExit("可用帧太少，先把 12 帧都跑出来")
    a, b = np.array(rs), np.array(rg)
    r = float(np.corrcoef(a, b)[0, 1])
    print(f"帧数 {len(a)}")
    print("源宽高比 ", " ".join(f"{v:.2f}" for v in a))
    print("生成宽高比", " ".join(f"{v:.2f}" for v in b))
    print(f"Pearson r = {r:.3f}")


if __name__ == "__main__":
    main()
