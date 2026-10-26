"""序列帧精灵图 → 逐帧拆解（自动识别网格）。

为什么单独一条管线、不套用 clipframes：那条链的输入是「按时间排列的视频帧」，
它的两把筛子（跳帧步长 / 近似度去重 / 循环闭合）解决的是「视频帧太多」；
精灵图的帧数已经被网格定死了，要的是「按格切开 + 勾选」。硬共用两边都别扭。

**关键取舍：每帧保留格子原尺寸，不裁到自己的内容 bbox。**
2026-10-26 已经踩过一次反方向的坑：精灵图落格那一步曾逐帧 trim_alpha 到各自
bbox 再居中，衣摆一摆 bbox 中心就横移，整幅（连纹丝不动的头）被反向挪回去，
头本来只晃 3.8px，被放大成 16px。拆解这头如果也裁到 bbox，等于提前把「帧与帧
之间的相对位置」扔掉，下游再怎么统一落格都补不回来。所以这里裁的是**格子**。

网格识别三级策略（按可靠性排序，全部可被用户显式参数覆盖）：
  1. 用户给了 cols / rows / cell → 直接用，不做任何猜测
  2. 投影法：前景在 x / y 上存在整条全空的列 / 行 → 那就是格间空隙
  3. 等分探测：枚举所有能整除画布的 (cols, rows)，给每种切法打分——每格必须
     有内容、内容不贴满格、格内连通块数≈1（一个角色）、内容大致居中。
     紧排精灵图（帧与帧直接相接、没有透明缝）只有这一条路走得通。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod

MIN_ALPHA = 8        # alpha 高于此值才算前景（与 matte / sequence 的口径一致）
MIN_SEG = 8          # 一格至少这么宽/高才可能是帧，更窄的当投影噪声
MIN_FRAME_AREA = 12  # 一帧里至少这么多前景像素，否则视为空格
MAX_SPLIT = 16       # 单方向最多切 16 格（256 格已经远超任何人类动作素材）


def _fg_mask(img: Image.Image) -> Tuple[np.ndarray, bool]:
    """前景掩膜 + 「这张图带不带 alpha」。

    有 alpha 且确实存在透明区 → 用 alpha（最准，不受底色与角色同色干扰）。
    否则按四角中位色当底色做色差。四角取中位而不是取左上角：单角很可能正好
    落在角色的脚/高光上，那样整张图的前景会全错。
    """
    a = np.asarray(img.convert("RGBA"))
    alpha = a[:, :, 3]
    transparent_ratio = float((alpha < MIN_ALPHA).mean())
    if transparent_ratio > 0.01:
        return alpha >= MIN_ALPHA, True
    rgb = a[:, :, :3].astype(np.int32)
    corners = np.array([rgb[0, 0], rgb[0, -1], rgb[-1, 0], rgb[-1, -1]])
    bg = np.median(corners, axis=0)
    return np.abs(rgb - bg).max(axis=2) > 24, False


def _segments(flags: np.ndarray, min_seg: int = MIN_SEG) -> List[Tuple[int, int]]:
    """把布尔序列切成「连续为真」的段，丢掉过窄的（投影噪声）。"""
    segs: List[Tuple[int, int]] = []
    start: Optional[int] = None
    for i, v in enumerate(flags):
        if v and start is None:
            start = i
        elif not v and start is not None:
            if i - start >= min_seg:
                segs.append((start, i))
            start = None
    if start is not None and len(flags) - start >= min_seg:
        segs.append((start, len(flags)))
    return segs


def _count_parts(sub: np.ndarray) -> int:
    """格内「够大的」连通块数。每格应该只有 1 个角色（带分离武器时 2 个）。"""
    try:
        from scipy import ndimage
    except Exception:
        return 1
    lab, n = ndimage.label(sub, structure=np.array([[0, 1, 0],
                                                    [1, 1, 1],
                                                    [0, 1, 0]]))
    if n <= 1:
        return int(n)
    areas = ndimage.sum(sub, lab, range(1, n + 1))
    floor = max(4.0, 0.005 * float(sub.size))
    return int(sum(1 for a in areas if a >= floor))


def _score_grid(mask: np.ndarray, cols: int, rows: int) -> float:
    """给「按 cols×rows 等分」这种切法打分，-1 = 不可能。

    判据全部来自实测形态学（2026-10-26 用 spritegrid 产物做环路测试时校准）：
      ①每格**最多一个**角色：格内够大的连通块数 >2 → 这一格里塞了好几帧
      ②角色不能贴格边：正确切分下内容 bbox 与格边界留有余量；切在角色中间时
        内容必然延伸到格子边缘（这条是抑制「切太细」最有效的一条）
      ③内容大致居中、内容量彼此接近
      ④**尾部空格是合法的**：帧数不是完整矩形时（3 帧排 2×2、8 帧排 3×3），
        行优先顺序的末尾会出现空格子。但空格只能出现在末尾，中间空一格说明
        切法错了。这一条是环路测试发现的真 bug：最初要求「每格都有内容」，
        于是 8 帧被判成 3×2，白白丢了最后一帧。
    """
    h, w = mask.shape
    if cols < 1 or rows < 1 or w % cols or h % rows:
        return -1.0
    cw, ch = w // cols, h // rows
    if cw < MIN_SEG or ch < MIN_SEG:
        return -1.0
    area = float(cw * ch)
    ratios: List[float] = []
    offsets: List[float] = []
    extra_parts = 0
    touches = 0
    used = 0
    total = cols * rows
    seen_empty = False
    for r in range(rows):
        for c in range(cols):
            sub = mask[r * ch:(r + 1) * ch, c * cw:(c + 1) * cw]
            n = int(sub.sum())
            if n < MIN_FRAME_AREA:
                seen_empty = True
                continue
            if seen_empty:
                return -1.0                  # 空格子后面又有内容 → 中间开洞，切错了
            used += 1
            ratio = n / area
            if ratio > 0.95:
                return -1.0                  # 贴满整格 → 一格里画满了，不是单帧
            ys, xs = np.nonzero(sub)
            x0, x1 = int(xs.min()), int(xs.max())
            y0, y1 = int(ys.min()), int(ys.max())
            # 只统计「触到**内部格线**」的次数：贴到画布最外沿是正常的
            # （AI 出图经常让角色踩到画面边），那不是被格线切断的证据。
            # 第一版没排除外沿，结果单张立绘（bbox 正好顶到右/下边）被判成
            # 「没有网格」直接 fallback —— 用户传一张单人图就报错。
            touches += ((x0 <= 0 and c > 0)
                        + (x1 >= cw - 1 and c < cols - 1)
                        + (y0 <= 0 and r > 0)
                        + (y1 >= ch - 1 and r < rows - 1))
            offsets.append(abs(float(xs.mean()) - cw / 2.0) / cw
                           + abs(float(ys.mean()) - ch / 2.0) / ch)
            parts = _count_parts(sub)
            if parts > 2:
                return -1.0                  # 一格 3 个角色 → 明显合并了多帧
            extra_parts += max(0, parts - 1)
            ratios.append(ratio)
    if used < max(1, int(0.34 * total)):
        return -1.0                          # 大部分格子空着 → 切太细
    mean_off = float(np.mean(offsets))
    if mean_off > 0.22:
        return -1.0                          # 内容不居中 → 切歪了
    cv = float(np.std(ratios) / max(1e-6, float(np.mean(ratios))))
    touch_rate = touches / max(1, used)
    return (1.0 - 1.6 * mean_off - 0.5 * min(1.0, cv)
            - 0.08 * extra_parts - 0.55 * touch_rate)


def detect_grid(img: Image.Image, *, cols: int = 0, rows: int = 0,
                cell: int = 0) -> Dict[str, Any]:
    """识别网格。返回 cols / rows / cellW / cellH / method / warnings。"""
    w, h = img.size
    warnings: List[str] = []

    # ---- 1) 用户说了算 ----
    if cell and cell > 0:
        cols = max(1, w // int(cell))
        rows = max(1, h // int(cell))
        return {"cols": cols, "rows": rows, "cellW": w // cols,
                "cellH": h // rows, "method": "cell", "warnings": warnings}
    if cols and rows:
        cols, rows = max(1, int(cols)), max(1, int(rows))
        return {"cols": cols, "rows": rows, "cellW": w // cols,
                "cellH": h // rows, "method": "manual", "warnings": warnings}
    if cols and not rows:
        cols = max(1, int(cols))
        rows = max(1, int(round(h / (w / cols))))
        return {"cols": cols, "rows": rows, "cellW": w // cols,
                "cellH": h // rows, "method": "manual", "warnings": warnings}
    if rows and not cols:
        rows = max(1, int(rows))
        cols = max(1, int(round(w / (h / rows))))
        return {"cols": cols, "rows": rows, "cellW": w // cols,
                "cellH": h // rows, "method": "manual", "warnings": warnings}

    mask, _ = _fg_mask(img)
    if not mask.any():
        return {"cols": 1, "rows": 1, "cellW": w, "cellH": h, "method": "empty",
                "warnings": ["这张图里找不到前景（全透明或全同色），无法拆帧"]}

    # ---- 2) 枚举所有能整除画布的切法，用 _score_grid 统一打分取最高 ----
    # 不用「投影法先定段数」那条捷径：投影法只能处理帧之间有明显空隙的精灵图，
    # 而且「最后一格是空的」时它会少算一行（8 帧排 3×3 会被算成 3×2）。
    # 打分函数里已经包含连通块数判据，所以 1×1 面对多帧图会因为「一格里有
    # 好几个角色」被自动排除，不需要额外特判。
    best: Optional[Tuple[float, int, int]] = None
    for c in range(1, min(MAX_SPLIT, w // MIN_SEG) + 1):
        if w % c:
            continue
        for r in range(1, min(MAX_SPLIT, h // MIN_SEG) + 1):
            if h % r:
                continue
            s = _score_grid(mask, c, r)
            if s <= 0:
                continue
            # 同分时偏好「更细的切法」：精灵图的意义就是把多帧排在一起，
            # 若 1×1 和 3×3 打平，正确的一定是后者
            s += 1e-6 * (c * r)
            if best is None or s > best[0]:
                best = (s, c, r)

    if best is None:
        # 整张图只有一个角色 → 这本来就不是精灵图，按「单帧」处理并说清楚，
        # 而不是丢一句「没认出网格」让用户以为功能坏了
        if _count_parts(mask) <= 3:
            warnings.append("这张图看起来只有一帧（没检测到多格网格）："
                            "如果你传的确实是精灵图，请手动填「列数 / 行数」")
            return {"cols": 1, "rows": 1, "cellW": w, "cellH": h,
                    "method": "single", "warnings": warnings}
        warnings.append("没能自动认出网格：请手动填「列数 / 行数」或单元格边长")
        return {"cols": 1, "rows": 1, "cellW": w, "cellH": h,
                "method": "fallback", "warnings": warnings}

    _, c, r = best
    if c * r == 1:
        warnings.append("这张图看起来只有一帧（没有检测到多格网格）")
    return {"cols": c, "rows": r, "cellW": w // c, "cellH": h // r,
            "method": "auto", "warnings": warnings}


def _cell_bbox(img: Image.Image) -> Optional[Tuple[int, int, int, int]]:
    """格子内容的前景包围盒（相对格子坐标）。空则 None。"""
    mask, _ = _fg_mask(img)
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def run_split(
    img: Image.Image,
    *,
    cols: int = 0,
    rows: int = 0,
    cell: int = 0,
    count: int = 0,
    matte: bool = False,
    tolerance: int = 40,
    task_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """精灵图 → 逐帧（可选去底）。

    返回 (frames, meta)。frames 元素：
        {i, image, bbox, alphaRatio, hash, diff}
    i 是**按网格顺序**的帧号（0 起，行优先），也是用户勾选时的稳定标识。
    """
    from .clipframes import diff_percent, frame_hash

    grid = detect_grid(img, cols=cols, rows=rows, cell=cell)
    n_cols, n_rows = int(grid["cols"]), int(grid["rows"])
    cw, ch = int(grid["cellW"]), int(grid["cellH"])
    total = n_cols * n_rows
    limit = total if not count or count <= 0 else min(total, int(count))

    progress_mod.check_cancel(task_id)
    if matte:
        progress_mod.report(task_id, 0, max(1, limit), "拆格 + 逐帧去底…")
    else:
        progress_mod.report(task_id, 0, max(1, limit), "拆格中…")

    out: List[Dict[str, Any]] = []
    punched = 0
    for idx in range(limit):
        progress_mod.check_cancel(task_id)
        r, c = divmod(idx, n_cols)
        box = (c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)
        piece = img.convert("RGBA").crop(box)

        if matte:
            # 逐帧抠而不是整图一次抠：整图里 9 个小人，语义分割只认「最显著的
            # 一个」，很容易整排丢帧；单帧里只有一个角色，u2net/BiRefNet 最准。
            from . import matte as matte_pipe
            piece, mm = matte_pipe.remove_background(
                piece, tolerance=int(tolerance), feather=0, hard=True,
                method="auto", despill=True)
            punched += int(mm.get("bgHolesPunched") or 0)

        bbox = _cell_bbox(piece)
        alpha = np.asarray(piece)[:, :, 3]
        frame = {
            "i": idx,
            "row": r,
            "col": c,
            "image": piece,
            "bbox": list(bbox) if bbox else [0, 0, cw, ch],
            "alphaRatio": round(float((alpha >= MIN_ALPHA).mean()), 4),
            "hash": frame_hash(piece),
        }
        out.append(frame)
        progress_mod.report(task_id, idx + 1, max(1, limit),
                            f"拆格 {idx + 1}/{limit}"
                            + ("（去底）" if matte else ""))

    # 帧间差异：前端「近似度过滤」滑杆的默认值要照这个口径来（与拆视频一致）
    diffs = [0.0]
    for k in range(1, len(out)):
        diffs.append(round(diff_percent(out[k - 1]["hash"], out[k]["hash"]), 2))
    for f, d in zip(out, diffs):
        f["diff"] = d

    empty = [f["i"] for f in out if f["alphaRatio"] <= 0.0005]
    warnings = list(grid.get("warnings") or [])
    if empty:
        warnings.append(
            f"有 {len(empty)} 格是空的（第 {'、'.join(str(i + 1) for i in empty[:6])}"
            f"{' 等' if len(empty) > 6 else ''} 格）：如果这些格子本该有内容，"
            "说明网格识别错了，请手动填列数 / 行数")

    meta: Dict[str, Any] = {
        "cols": n_cols, "rows": n_rows, "cellW": cw, "cellH": ch,
        "count": len(out), "gridMethod": grid["method"],
        "sourceSize": [img.width, img.height],
        "matte": bool(matte),
        "bgHolesPunched": punched,
        "diffMedian": round(float(np.median(diffs[1:])), 2) if len(diffs) > 1 else 0.0,
        "diffMax": round(float(max(diffs[1:], default=0.0)), 2),
        "warnings": warnings,
    }
    if total != len(out):
        meta["truncated"] = total - len(out)
    return out, meta
