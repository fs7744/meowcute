"""四视图一致性判据的离线标定（不占 GPU）。

目的：给「拼板前该不该拦下这张」找一组可量化的判据和阈值，而不是拍脑袋定
直方图相似度。三个刻意的设计：

1. **在主体像素上算，不在全图上算**。拼板前各视角的底色占比本来就会变
   （角色形状变了，背景面积就变了），全图统计量里混着这份噪声。
2. **用色域覆盖（Jaccard）而不是主色直方图相似度**。直方图对「占比变化」
   敏感、对「缺失某色」不敏感，正好和需求相反：背视图披风从 5.6% 长到 10.4%
   是正确结果（实测），而「红披风整块丢了」才是事故。
3. **先量同视角的自然波动**。同一角色的多张正视图互比，得到的是「零视角
   变化下的基线离散度」；视角图与正视图比，得到的是「视角变化 + 漂移」。
   阈值只有落在两者之间才有判别力——这一步不量，阈值就一定定错。

用法：
  python tools/calib_view_consistency.py <项目图片目录> [更多目录...]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

Q = 8            # 颜色量化步长（256/Q = 32 级/通道）
BG_TOL = 26      # 与底色的 L∞ 距离超过这个值算主体
MIN_ALPHA = 8    # alpha 低于这个值算透明


def subject_mask(img: Image.Image) -> Tuple[np.ndarray, str]:
    """返回主体掩码。优先后 alpha（透明底产物），否则按边框底色抠。"""
    arr = np.asarray(img.convert("RGBA"))
    a = arr[:, :, 3]
    if a.min() < MIN_ALPHA and (a <= MIN_ALPHA).mean() > 0.02:
        return a > MIN_ALPHA, "alpha"
    rgb = arr[:, :, :3].astype(np.int16)
    border = np.concatenate([
        rgb[0, :, :], rgb[-1, :, :], rgb[:, 0, :], rgb[:, -1, :],
    ])
    qb = border // 16 * 16
    colors, counts = np.unique(qb, axis=0, return_counts=True)
    bg = colors[counts.argmax()] + 8
    dist = np.abs(rgb - bg).max(axis=2)
    return dist > BG_TOL, "bgcolor"


def palette(img: Image.Image, mask: np.ndarray, min_share: float = 0.015
            ) -> Tuple[set, np.ndarray]:
    """主体像素的量化色集合（占比够的才留），以及主体像素数组。"""
    arr = np.asarray(img.convert("RGB")).astype(np.uint8)
    px = arr[mask]
    if px.size == 0:
        return set(), px
    qpx = (px // Q).astype(np.uint8)
    keys = (qpx[:, 0].astype(np.int32) << 16) | (qpx[:, 1].astype(np.int32) << 8) | qpx[:, 2]
    uniq, cnt = np.unique(keys, return_counts=True)
    keep = cnt >= max(1, int(min_share * len(px)))
    return set(int(k) for k in uniq[keep]), px


def jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b)


def probe(path: Path) -> Optional[Dict]:
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    mask, how = subject_mask(img)
    if mask.sum() < 50:
        return None
    cols, px = palette(img, mask)
    ys, xs = np.nonzero(mask)
    h, w = mask.shape
    return {
        "name": path.name,
        "size": (img.width, img.height),
        "how": how,
        "area": float(mask.mean()),
        "box_h": int(ys.max() - ys.min() + 1),
        "box_w": int(xs.max() - xs.min() + 1),
        "cols": cols,
        "mean_rgb": px.mean(axis=0) if px.size else np.zeros(3),
    }


def group(files: List[Path], prefixes: List[str]) -> Dict[str, List[Path]]:
    out: Dict[str, List[Path]] = {p: [] for p in prefixes}
    for f in sorted(files):
        for p in prefixes:
            if f.name.startswith(p):
                out[p].append(f)
    return out


def main(argv: List[str]) -> int:
    dirs = [Path(a) for a in argv[1:]]
    if not dirs:
        print(__doc__)
        return 2
    prefixes = ["out-", "vs-", "vb-", "vr-", "im-"]
    for d in dirs:
        if not d.is_dir():
            print(f"跳过（不是目录）：{d}")
            continue
        g = group(list(d.glob("*.png")), prefixes)
        front = [probe(f) for f in g["out-"] + g["im-"]]
        front = [x for x in front if x]
        if not front:
            print(f"\n== {d.name}: 没有正视图，跳过")
            continue
        print(f"\n== {d.name}  正视图 {len(front)} 张")
        base_cols = set()
        for f in front:
            base_cols |= f["cols"]
        # 正视图之间的自然波动（同一视角，零编辑）
        if len(front) > 1:
            js = [jaccard(front[i]["cols"], front[j]["cols"])
                  for i in range(len(front)) for j in range(i + 1, len(front))]
            ha = [f["box_h"] for f in front]
            print(f"   [基线] 正视图互比色域 Jaccard {min(js):.2f}~{max(js):.2f}"
                  f"（中位 {float(np.median(js)):.2f}），主体高 {min(ha)}~{max(ha)}px")
        else:
            print("   [基线] 只有一张正视图，无同视角基线")
        ref_h = float(np.median([f["box_h"] for f in front]))
        ref_area = float(np.median([f["area"] for f in front]))
        print(f"   {'文件':<34}{'底':>5}{'面积%':>7}{'高':>6}{'宽':>6}"
              f"{'高比':>7}{'面积比':>8}{'色域J':>7}")
        for pfx, label in (("vs-", "左"), ("vb-", "背"), ("vr-", "右")):
            rows = [probe(f) for f in g[pfx]]
            for r in rows:
                if not r:
                    continue
                j = jaccard(base_cols, r["cols"])
                print(f"   {label} {r['name']:<31}{r['how'][:4]:>5}"
                      f"{r['area'] * 100:>7.1f}{r['box_h']:>6}{r['box_w']:>6}"
                      f"{r['box_h'] / ref_h:>7.2f}{r['area'] / ref_area:>8.2f}{j:>7.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
