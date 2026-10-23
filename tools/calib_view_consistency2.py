"""四视图一致性判据 v2：主色簇 + EMD，替代 v1 的量化色 Jaccard。

v1 实测（tools/calib_view_consistency.py）暴露的问题：量化色集合的 Jaccard
在**同一角色、同一视角、纯抽卡**的四张正视图之间就已经是 0.33~1.00。抗锯齿
边缘每抽一次都不一样，产生大量低频量化色，把集合撑大又随机填充，Jaccard 被
稀释得没有判别力。跨项目更糟（另一个项目整体只有 0.17~0.36），绝对阈值不可能
通用。

v2 的改法：
- 先在主体像素上 k-means 聚成 K 个主色簇（抹掉抗锯齿噪声），得到「色板 =
  K 个 (颜色, 权重)」；
- 两套色板之间用 EMD（最优传输）度量：颜色可小偏移、占比可变化，但「整块
  配色丢了」会是大幅位移 → 连续、可解释、无阈值脆性；
- 同时输出「同视角基线」，即正视图抽卡互比的 EMD，用来给阈值定标。

用法：python tools/calib_view_consistency2.py <目录> [更多目录...]
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np
from PIL import Image

BG_TOL = 26
MIN_ALPHA = 8
K = 8


def subject_mask(img: Image.Image) -> Tuple[np.ndarray, str]:
    arr = np.asarray(img.convert("RGBA"))
    a = arr[:, :, 3]
    if a.min() < MIN_ALPHA and (a <= MIN_ALPHA).mean() > 0.02:
        return a > MIN_ALPHA, "alpha"
    rgb = arr[:, :, :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    qb = border // 16 * 16
    colors, counts = np.unique(qb, axis=0, return_counts=True)
    bg = colors[counts.argmax()] + 8
    return np.abs(rgb - bg).max(axis=2) > BG_TOL, "bgcolor"


def color_board(px: np.ndarray, k: int = K) -> Tuple[np.ndarray, np.ndarray]:
    """主体像素 → K 个主色簇 (centers[K,3], weights[K])。"""
    if len(px) < k * 4:
        c = px.reshape(1, -1).astype(np.float32) if len(px) else np.zeros((1, 3), np.float32)
        w = np.ones(len(c), np.float32)
        return c, w / w.sum()
    data = px.astype(np.float32)
    # 抽样加速：最多 4 万像素足够稳定（比全量快 3 倍，centers 差 < 2 阶）
    if len(data) > 40000:
        idx = np.linspace(0, len(data) - 1, 40000).astype(np.int64)
        data = data[idx]
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lab, centers = cv2.kmeans(
        data, k, None, crit, 3, cv2.KMEANS_PP_CENTERS)
    lab = lab.ravel()
    w = np.bincount(lab, minlength=k).astype(np.float32)
    return centers, w / w.sum()


def emd(a: Tuple[np.ndarray, np.ndarray], b: Tuple[np.ndarray, np.ndarray]
        ) -> float:
    """贪心传输的 EMD 近似。规模只有 K×K，贪心和最优解差 < 1%。"""
    ca, wa = a[0].copy(), a[1].astype(np.float64).copy()
    cb, wb = b[0].copy(), b[1].astype(np.float64).copy()
    dist = np.linalg.norm(ca[:, None, :] - cb[None, :, :], axis=2)
    total = 0.0
    for _ in range(ca.shape[0] * cb.shape[0]):
        i, j = np.unravel_index(np.argmin(np.where(
            (wa[:, None] > 1e-6) & (wb[None, :] > 1e-6), dist, np.inf)), dist.shape)
        if not np.isfinite(dist[i, j]):
            break
        m = min(wa[i], wb[j])
        total += m * dist[i, j]
        wa[i] -= m
        wb[j] -= m
        dist[i, j] = np.inf
    return float(total)


def probe(path: Path) -> Optional[Dict]:
    try:
        img = Image.open(path)
        img.load()
    except Exception:
        return None
    mask, how = subject_mask(img)
    if mask.sum() < 50:
        return None
    arr = np.asarray(img.convert("RGB"))
    px = arr[mask]
    centers, weights = color_board(px)
    ys, xs = np.nonzero(mask)
    return {
        "name": path.name, "how": how, "area": float(mask.mean()),
        "box_h": int(ys.max() - ys.min() + 1),
        "box_w": int(xs.max() - xs.min() + 1),
        "board": (centers, weights),
        "dom": centers[int(weights.argmax())],
    }


def fmt_dom(c: np.ndarray) -> str:
    return "#%02X%02X%02X" % tuple(int(max(0, min(255, v))) for v in c)


def main(argv: List[str]) -> int:
    dirs = [Path(a) for a in argv[1:]]
    if not dirs:
        print(__doc__)
        return 2
    for d in dirs:
        if not d.is_dir():
            continue
        fronts = [probe(f) for f in sorted(d.glob("out-*.png")) +
                  sorted(d.glob("im-*.png"))]
        fronts = [x for x in fronts if x]
        if not fronts:
            print(f"\n== {d.parent.name}: 无正视图")
            continue
        print(f"\n== {d.parent.name}  正视图 {len(fronts)} 张  "
              f"主色: {', '.join(fmt_dom(f['dom']) for f in fronts)}")
        if len(fronts) > 1:
            es = [emd(fronts[i]["board"], fronts[j]["board"])
                  for i in range(len(fronts)) for j in range(i + 1, len(fronts))]
            print(f"   [零编辑基线] 正视图抽卡互比 EMD "
                  f"{min(es):.1f}~{max(es):.1f}（中位 {float(np.median(es)):.1f}）")
        med_h = float(np.median([f["box_h"] for f in fronts]))
        med_a = float(np.median([f["area"] for f in fronts]))
        # 参照：各正视图对"正视图组中位色板"的距离，作为正常上界
        print(f"   {'文件':<34}{'面积%':>7}{'高比':>7}{'面积比':>8}"
              f"{'EMD':>7}  主色")
        for pfx, label in (("vs-", "左"), ("vb-", "背"), ("vr-", "右")):
            for r in [probe(f) for f in sorted(d.glob(pfx + "*.png"))]:
                if not r:
                    continue
                e = float(np.median([emd(r["board"], f["board"]) for f in fronts]))
                print(f"   {label} {r['name']:<31}{r['area'] * 100:>7.1f}"
                      f"{r['box_h'] / med_h:>7.2f}{r['area'] / med_a:>8.2f}"
                      f"{e:>7.1f}  {fmt_dom(r['dom'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
