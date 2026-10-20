"""背景扣除（抠图）：优先语义分割（u2net），退化到 GrabCut，最后才是颜色键。

为什么不能只靠颜色键：AI 出图常见「灰色渐变底 + 灰白毛色角色」，背景与白甲 /
白毛的 RGB 距离很近，纯阈值无论怎么调都是二选一——要么背景留一大片，要么把
角色白甲抠成透明洞。

三级路径：
- **u2net**（默认，需 rembg + 本地权重）：显著性分割，灰底灰角色、软投影都能处理。
- **grabcut**（需 opencv）：边框作背景种子、中心作前景种子做图割；第二遍把
  脚底「低饱和且明显暗于局部底色」的像素标成可能背景，压掉地板软投影。
- **key**（最终兜底）：四角色板 + 连通域洪泛 + 有限梯度生长，仅靠颜色。

三条路径最终都走「硬边 alpha + 边缘去污（despill）」：
- 像素画的边只能全不透明 / 全透明。给 alpha 做高斯羽化会让半透明像素在浅色
  底上显成一圈白晕，模糊半径还会把脸部 / 细肢等窄结构啃出洞。
- 紧贴背景的一圈像素颜色是背景与角色的混合，直接留下会形成白 / 绿描边；
  这里把「仍接近背景色」的轮廓像素改写为相邻内部像素的颜色。
"""
from __future__ import annotations

import os
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image


# --------------------------------------------------------------------------- #
# 通用小工具
# --------------------------------------------------------------------------- #
_NEIGHBOURS = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (1, -1), (-1, 1), (-1, -1))


def _neighbour_shift(mask: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """把 bool mask 按 (dy,dx) 平移，越界填 False。"""
    out = np.zeros_like(mask)
    h, w = mask.shape
    ys, ye = (dy, h) if dy >= 0 else (0, h + dy)
    xs, xe = (dx, w) if dx >= 0 else (0, w + dx)
    out[ys:ye, xs:xe] = mask[ys - dy:ye - dy, xs - dx:xe - dx]
    return out


def _shift_block(arr: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """把任意通道数组按 (dy,dx) 平移，越界填 0。用于取邻居颜色。"""
    out = np.zeros_like(arr)
    h, w = arr.shape[:2]
    ys, ye = (dy, h) if dy >= 0 else (0, h + dy)
    xs, xe = (dx, w) if dx >= 0 else (0, w + dx)
    out[ys:ye, xs:xe] = arr[ys - dy:ye - dy, xs - dx:xe - dx]
    return out


def _erode(mask: np.ndarray) -> np.ndarray:
    out = mask.copy()
    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        out &= _neighbour_shift(mask, dy, dx)
    return out


def _border_palette(arr: np.ndarray, bucket: int = 24, limit: int = 4) -> List[np.ndarray]:
    """四角取样得到背景色板（只取四角，避免把角色描边算成背景）。"""
    h, w = arr.shape[:2]
    pad = max(4, min(16, h // 8, w // 8))
    patches = [
        arr[0:pad, 0:pad, :3],
        arr[0:pad, w - pad:w, :3],
        arr[h - pad:h, 0:pad, :3],
        arr[h - pad:h, w - pad:w, :3],
    ]
    votes: dict = {}
    sums: dict = {}
    for patch in patches:
        px = patch.reshape(-1, 3).astype(np.int32)
        q = (px // bucket) * bucket
        uniq, counts = np.unique(q, axis=0, return_counts=True)
        for i in np.argsort(-counts)[:2]:
            key = tuple(int(v) for v in uniq[i])
            votes[key] = votes.get(key, 0) + 1
            sums.setdefault(key, []).append(uniq[i].astype(np.int32) + bucket // 2)

    ranked = sorted(votes.items(), key=lambda kv: -kv[1])
    out: List[np.ndarray] = []
    for key, v in ranked:
        if v < 2 and out:
            continue
        out.append(np.mean(sums[key], axis=0).astype(np.int32))
        if len(out) >= limit:
            break
    if not out:
        out.append(arr[0, 0, :3].astype(np.int32))
    return out


def _palette_dist(rgb: np.ndarray, palette: List[np.ndarray]) -> np.ndarray:
    h, w = rgb.shape[:2]
    dist = np.full((h, w), 1e9, dtype=np.float32)
    for c in palette:
        d = np.sqrt(((rgb - c) ** 2).sum(axis=2)).astype(np.float32)
        dist = np.minimum(dist, d)
    return dist


def _row_bg(rgb: np.ndarray, strip: int = 8) -> np.ndarray:
    """用左右边框估计每一行的背景色（竖向渐变 / 暗角都能跟上）。"""
    h = rgb.shape[0]
    out = np.zeros((h, 3), dtype=np.float32)
    for y in range(h):
        side = np.concatenate([rgb[y, :strip], rgb[y, -strip:]], axis=0)
        out[y] = np.median(side, axis=0)
    return out


def _saturation(rgb: np.ndarray) -> np.ndarray:
    mx = rgb.max(axis=2).astype(np.float32)
    mn = rgb.min(axis=2).astype(np.float32)
    return np.where(mx > 0, (mx - mn) / np.maximum(mx, 1), 0.0)


# --------------------------------------------------------------------------- #
# 边缘去污
# --------------------------------------------------------------------------- #
def _chroma_channel(palette: List[np.ndarray]) -> Optional[int]:
    """底色里有没有「可色键的纯色」——某通道领先另两个 40+ 的才算。

    绿幕 (0,255,0)、蓝幕 (0,0,255) 都满足；灰底、白底、花色背景不满足，
    此时不走色相判定，退回 RGB 距离判定（避免把任何带颜色的角色像素都当成溢色）。
    """
    for c in palette:
        arr = np.asarray(c, dtype=np.int32)
        ch = int(arr.argmax())
        if arr[ch] - max(int(np.delete(arr, ch)[0]), int(np.delete(arr, ch)[1])) >= 40:
            return ch
    return None


def _hue_spill(rgb: np.ndarray, channel: int, hue_tol: int = 26,
               sat_min: float = 0.25, val_min: float = 0.05) -> np.ndarray:
    """按**色相**认出「被底色染过」的像素（不只是 RGB 距离近的）。

    RGB 距离判定的盲区在暗部：头发里一颗深绿溢出 (20,80,30) 到纯绿 (0,255,0)
    的 RGB 距离约 178，任何合理容差都抓不到，于是留着一条绿丝。色相 + 饱和度
    + 明度下限三条件能抓到它，同时排除「深色但不同相」的描边像素。

    注意 OpenCV 的 HSV 色相量纲随输入 dtype 变：float 图 H 是 0-360，uint8 图
    才是 0-180。这里显式转 uint8，否则目标色相会整体差一倍（绿 120 被当成 60
    = 红），判定会静默失效。
    """
    import cv2

    u8 = (np.clip(rgb, 0, 255)).astype(np.uint8)
    hsv = cv2.cvtColor(u8, cv2.COLOR_RGB2HSV)
    target = {0: 0, 1: 60, 2: 120}.get(channel, 60)
    dist = np.abs(hsv[..., 0].astype(np.int16) - target)
    dist = np.minimum(dist, 180 - dist)
    return (dist <= hue_tol) & (hsv[..., 1] >= int(sat_min * 255)) & (hsv[..., 2] >= int(val_min * 255))


def _despill(arr: np.ndarray, kept: np.ndarray, palette: List[np.ndarray], tol: float) -> int:
    """把「紧贴透明区、颜色仍接近背景」的轮廓像素改成内侧角色像素的颜色。

    判定要同时看两个距离，只看「到背景的距离」会误伤浅色角色（白发 / 白甲在
    灰底上本来就离底色近）：
    - ``d_bg``：到背景色板的距离，越小越像背景；
    - ``d_core``：到内侧核心像素颜色的距离，越小越像角色本身。
    只有 ``d_bg`` 明显小于 ``d_core`` 且确实贴着底色，才认定是混边。
    """
    rgb = arr[:, :, :3].astype(np.int32)
    core = _erode(kept)
    if not core.any():           # 1px 细结构没有可信内侧参考，宁可不改
        return 0
    deeper = _erode(core)
    if deeper.any():
        core = deeper
    band = kept & ~core
    if not band.any():
        return 0

    d_bg_all = _palette_dist(rgb, palette)
    out = rgb.copy()
    changed = 0

    # 底色是可色键纯色时，轮廓环里「色相属于底色」的像素也算污染——专抓 RGB
    # 距离抓不到的暗部溢出（深绿丝、深蓝边）。只作用于轮廓环，不动画面内部。
    channel = _chroma_channel(palette)
    hue_bad = _hue_spill(rgb, channel) if channel is not None else None
    if hue_bad is not None:
        # 色相比 RGB 距离精确（暗部也认得出来），轮廓环再放宽 1px
        deep = _erode(core)
        if deep.any():
            core = deep
            band = kept & ~core

    # known 必须在 core 定稿之后再取：提前拷贝会把轮廓环里已被认定为角色色的
    # 像素（恰恰是溢色）设成参考起点，循环第一步就把它们跳过。
    known = core.copy()

    # 由内向外一圈圈推进：内圈的判定结果会成为外圈的参考色
    for _ in range(3):
        acc = np.zeros((rgb.shape[0], rgb.shape[1], 3), dtype=np.float64)
        cnt = np.zeros(kept.shape, dtype=np.float64)
        for dy, dx in _NEIGHBOURS:
            near = _neighbour_shift(known, dy, dx)
            acc += near[:, :, None] * _shift_block(out, dy, dx).astype(np.float64)
            cnt += near

        cand = band & ~known & (cnt > 0)
        if not cand.any():
            break
        ys, xs = np.nonzero(cand)
        mean = acc[ys, xs] / cnt[ys, xs][:, None]
        d_core = np.sqrt(((rgb[ys, xs] - mean) ** 2).sum(axis=1)).astype(np.float32)
        d_bg = d_bg_all[ys, xs]
        # 0.9 的相对余量：两边距离相等时（浅色角色压在浅色底上）都是合法颜色，
        # 不能因为浮点精度差就被判成污染。
        dirty = (d_bg <= tol * 1.25) & (d_bg < d_core * 0.9)
        if hue_bad is not None:
            dirty = dirty | hue_bad[ys, xs]
        if dirty.any():
            out[ys[dirty], xs[dirty]] = np.round(mean[dirty]).astype(np.int32)
            changed += int(dirty.sum())
        known[ys, xs] = True

    if changed:
        arr[:, :, :3] = out.astype(np.uint8)
    return changed


def _fill_small_holes(kept: np.ndarray, max_area_ratio: float = 0.002) -> Tuple[np.ndarray, int]:
    """填掉被角色完全包围的小透明空洞（分割偶发斑点）。大缝隙保留。"""
    try:
        import cv2
    except Exception:
        return kept, 0
    holes = (~kept).astype(np.uint8)
    n, lab = cv2.connectedComponents(holes, connectivity=4)
    if n <= 1:
        return kept, 0
    h, w = kept.shape
    max_area = int(max(4, max_area_ratio * h * w))
    filled = 0
    for i in range(1, n):
        comp = lab == i
        area = int(comp.sum())
        if area == 0 or area > max_area:
            continue
        ys, xs = np.nonzero(comp)
        if ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1:
            continue
        kept |= comp
        filled += area
    return kept, filled


def drop_bg_islands(cut: Image.Image, tol: float = 70.0) -> Image.Image:
    """丢掉 mask 里「孤立小块且整体都是背景色」的伪影岛。

    u2net 偶发把一小片背景当成显著目标保留——原图里那是纯净背景，抠完却成了
    一块扎眼的浮块。判定：连通域面积小（< 主体 2%，上限 3000px）且域内 90%+
    像素接近透明区主色 → alpha 置 0。连在主体上的伪影属于主体大连通域，不受影响。
    """
    arr = np.asarray(cut)
    kept = arr[:, :, 3] > 8
    if not kept.any():
        return cut
    try:
        import cv2
    except Exception:
        return cut

    rgb = arr[:, :, :3].astype(np.int32)
    trans = rgb[~kept]
    if len(trans) < 64:
        return cut
    q = trans // 16 * 16
    colors, counts = np.unique(q.reshape(-1, 3), axis=0, return_counts=True)
    refs = (colors[counts.argsort()[::-1][:3]] + 8).astype(np.int32)

    bg_like = np.zeros(kept.shape, dtype=bool)
    for c in refs:
        bg_like |= (np.abs(rgb - c).sum(axis=2) < tol)
    bg_like &= kept
    if not bg_like.any():
        return cut

    n, lab = cv2.connectedComponents(kept.astype(np.uint8), connectivity=4)
    if n <= 1:
        return cut
    subject_area = int(kept.sum())
    max_area = max(48, min(int(0.02 * subject_area), 3000))
    out = arr.copy()
    for i in range(1, n):
        comp = lab == i
        area = int(comp.sum())
        if area == 0 or area > max_area:
            continue
        if (bg_like & comp).sum() < 0.9 * area:
            continue
        out[comp, 3] = 0
    return Image.fromarray(out, "RGBA")


# --------------------------------------------------------------------------- #
# 路径 1：u2net 深度抠图
# --------------------------------------------------------------------------- #
_SESSION = None
_U2NET_ERR: Optional[str] = None
_U2NET_HOME = os.path.expanduser(os.getenv("U2NET_HOME", "~/.u2net"))
_U2NET_ONNX = os.path.join(_U2NET_HOME, "u2net.onnx")


def _u2net_session():
    global _SESSION, _U2NET_ERR
    if _SESSION is not None or _U2NET_ERR is not None:
        return _SESSION
    # 关键：权重文件不存在时 **绝不** 触发 rembg 的联网下载（会卡死数十分钟，
    # 直接拖垮整个请求）。只有本地权重就绪才尝试 new_session。
    if not os.path.exists(_U2NET_ONNX):
        _U2NET_ERR = f"u2net 权重缺失：{_U2NET_ONNX}"
        return None
    try:
        from rembg import new_session
        _SESSION = new_session("u2net")
    except Exception as e:  # 权重损坏 / onnxruntime 不可用
        _U2NET_ERR = f"{type(e).__name__}: {e}"
        _SESSION = None
    return _SESSION


def u2net_available() -> bool:
    return _u2net_session() is not None


def engine_status() -> Dict[str, Any]:
    """给前端的抠图引擎体检结果：哪条路径能用、现在会走哪条。"""
    sess = _u2net_session()
    try:
        import cv2  # noqa: F401
        cv2_ok = True
    except Exception:
        cv2_ok = False
    if sess is not None:
        primary, note = "u2net", "语义分割可用，任意识别主体，不受背景颜色影响"
    elif cv2_ok:
        primary, note = "grabcut", "未装 u2net 权重，走 GrabCut 图割，纯色/渐变底可用"
    else:
        primary, note = "key", "只有颜色键兜底，背景必须是接近纯色的底"
    if sess is None and _U2NET_ERR:
        note += f"（{_U2NET_ERR}）"
    return {"available": True, "primary": primary, "note": note,
            "u2net": sess is not None, "cv2": cv2_ok, "u2netError": _U2NET_ERR}


def _mask_keep(rgb: np.ndarray) -> np.ndarray:
    """显著性分割得到 kept 掩码（阈值二值化，像素素材不要软边）。"""
    from rembg import remove
    session = _u2net_session()
    if session is None:
        raise RuntimeError(_U2NET_ERR or "u2net session unavailable")
    mask = remove(Image.fromarray(rgb), session=session, only_mask=True)
    return np.array(mask) >= 128


# --------------------------------------------------------------------------- #
# 路径 2：GrabCut 图割
# --------------------------------------------------------------------------- #
def _grabcut_keep(rgb: np.ndarray, shadow_pass: bool = True, iters: int = 5) -> np.ndarray:
    """GrabCut 图割：返回 kept（前景）bool 掩码。

    外侧一圈为确定背景、中心为可能前景；第二遍把「角色 bbox 底部 30% 内、
    低饱和且明显暗于局部底色」的像素标成可能背景，压掉地板软投影
    （用 PR_BGD 而非 BGD：硬性种子会误切暗色腿甲）。
    """
    import cv2

    h, w = rgb.shape[:2]
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    m = max(3, int(round(min(h, w) * 0.015)))
    rect = (m, m, w - 2 * m, h - 2 * m)

    mask = np.full((h, w), cv2.GC_BGD, np.uint8)
    mask[m:h - m, m:w - m] = cv2.GC_PR_FGD
    c = m + 2
    mask[:c, :c] = cv2.GC_BGD
    mask[:c, -c:] = cv2.GC_BGD
    mask[-c:, :c] = cv2.GC_BGD
    mask[-c:, -c:] = cv2.GC_BGD

    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, mask, rect, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)

    if shadow_pass:
        kept = np.isin(mask, [cv2.GC_FGD, cv2.GC_PR_FGD])
        ys, xs = np.nonzero(kept)
        if len(ys):
            top, bot = int(ys.min()), int(ys.max())
            y0 = top + int((bot - top) * 0.70)
            sat = _saturation(rgb)
            luma = rgb.mean(axis=2)
            bgl = _row_bg(rgb).mean(axis=1)
            band = np.zeros((h, w), dtype=bool)
            band[y0:bot + 1, :] = True
            seed = band & kept & (sat < 0.14) & (luma < bgl[:, None] - 15)
            if seed.any():
                mask[seed] = cv2.GC_PR_BGD
                bgd = np.zeros((1, 65), np.float64)
                fgd = np.zeros((1, 65), np.float64)
                cv2.grabCut(bgr, mask, rect, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)

    return np.isin(mask, [cv2.GC_FGD, cv2.GC_PR_FGD])


# --------------------------------------------------------------------------- #
# 路径 3：颜色键兜底
# --------------------------------------------------------------------------- #
def _key_keep(rgb: np.ndarray, palette: List[np.ndarray], tolerance: float,
              edge_grow: int, grow_passes: int) -> Tuple[np.ndarray, int, int]:
    h, w = rgb.shape[:2]
    dist = _palette_dist(rgb, palette)
    match = dist <= float(tolerance)

    visited = np.zeros((h, w), dtype=bool)
    dq: deque = deque()
    for x in range(w):
        for y in (0, h - 1):
            if match[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))
    for y in range(h):
        for x in (0, w - 1):
            if match[y, x] and not visited[y, x]:
                visited[y, x] = True
                dq.append((y, x))

    removed = 0
    while dq:
        y, x = dq.popleft()
        removed += 1
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and match[ny, nx]:
                visited[ny, nx] = True
                dq.append((ny, nx))

    grown = 0
    if edge_grow > 0:
        near_bg = dist <= float(tolerance) * 1.5
        for _ in range(max(0, grow_passes)):
            added = np.zeros((h, w), dtype=bool)
            for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                added |= _neighbour_shift(visited, dy, dx) & ~visited & near_bg
            if not added.any():
                break
            visited |= added
            grown += int(added.sum())

    return ~visited, removed, grown


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def remove_background(
    img: Image.Image,
    tolerance: int = 80,
    feather: int = 0,
    sample: str = "auto",
    edge_grow: int = 10,
    grow_passes: int = 6,
    hard: bool = True,
    despill: bool = True,
    method: str = "auto",
) -> Tuple[Image.Image, dict]:
    """抠掉背景。

    hard=True（默认，像素素材）：二值 alpha + 边缘去污，无软边、无白晕。
    hard=False（照片 / 半透明素材）：保留高斯羽化，边缘更自然。
    method: auto | u2net | grabcut | key
    """
    rgba = img.convert("RGBA")
    arr = np.array(rgba)
    h, w = arr.shape[:2]
    rgb = arr[:, :, :3].astype(np.int32)

    palette = [rgb[0, 0]] if sample == "topleft" else _border_palette(arr)
    already_clear = arr[:, :, 3] < 8

    used = "key"
    chain: List[str] = []
    if method == "auto":
        chain = ["u2net", "grabcut", "key"] if hard else ["key"]
    else:
        chain = [method]

    kept = None
    removed = 0
    grown = 0
    holes_filled = 0
    last_err = None

    for cand in chain:
        if cand == "u2net":
            if not hard or min(h, w) < 64 or not u2net_available():
                continue
            try:
                kept = _mask_keep(arr[:, :, :3])
                used = "u2net"
            except Exception as e:
                last_err = f"u2net: {type(e).__name__}"
                kept = None
                continue
        elif cand == "grabcut":
            if not hard or min(h, w) < 96:
                continue
            try:
                import cv2  # noqa: F401
                kept = _grabcut_keep(arr[:, :, :3])
                used = "grabcut"
            except Exception as e:  # 无 cv2
                last_err = f"grabcut: {type(e).__name__}"
                kept = None
                continue
        else:
            kept, removed, grown = _key_keep(rgb, palette, tolerance, edge_grow, grow_passes)
            used = "key"
        break

    if kept is None:  # 兜底
        kept, removed, grown = _key_keep(rgb, palette, tolerance, edge_grow, grow_passes)
        used = "key"

    kept = kept & ~already_clear
    if hard and used != "key":
        kept, holes_filled = _fill_small_holes(kept)
    removed = int((~kept).sum())

    despilled = 0
    residual = 0
    alpha = np.where(kept, 255, 0).astype(np.uint8)

    if hard:
        if despill:
            despilled = _despill(arr, kept, palette, float(tolerance))
        arr[:, :, 3] = alpha
        # 去污之后还剩多少「底色色相」的像素：在下游使用前先量一次污染
        channel = _chroma_channel(palette)
        if channel is not None:
            residual = int((_hue_spill(arr[:, :, :3], channel) & kept).sum())
    else:
        if feather > 0:
            from PIL import ImageFilter

            softened = np.array(
                Image.fromarray(alpha).filter(
                    ImageFilter.GaussianBlur(max(0.5, feather / 2.0)))
            )
            alpha = np.where(softened > 250, 255, softened).astype(np.uint8)
        arr[:, :, 3] = alpha

    out = Image.fromarray(arr, "RGBA")
    if hard and used == "u2net":
        # 语义分割会把一小片背景当成显著目标保留，抠完变成扎眼的浮块
        out = drop_bg_islands(out)

    kept_ratio = round(int(kept.sum()) / float(h * w), 4)
    meta = {
        "method": used,
        "background": "#%02X%02X%02X" % tuple(int(v) for v in palette[0]),
        "tolerance": tolerance,
        "feather": feather,
        "hard": hard,
        "despilledPixels": despilled,
        "holesFilled": holes_filled,
        "edgeGrowPixels": grown,
        "removedRatio": round(removed / float(h * w), 4),
        "keptRatio": kept_ratio,
        "residualKeyPixels": residual,
    }
    if last_err:
        meta["fallbackReason"] = last_err
    # 几乎没抠掉 / 几乎全抠掉都说明这一趟大概率不对，前端要能直接提示
    if kept_ratio < 0.02:
        meta["warning"] = "几乎没留下主体：可能是背景识别错了，试试调低容差或换个方法"
    elif kept_ratio > 0.97:
        meta["warning"] = "几乎没抠掉背景：主体可能铺满画面或背景不够纯，试试提高容差"
    return out, meta


def trim_to_subject(img: Image.Image, pad: int = 0) -> Tuple[Image.Image, dict]:
    """按 alpha 包围盒裁到主体（可选留白），返回 (图, {"box": [...]})。"""
    arr = np.asarray(img)
    if arr.shape[2] < 4:
        return img, {"box": [0, 0, img.width, img.height]}
    kept = arr[:, :, 3] > 8
    if not kept.any():
        return img, {"box": [0, 0, img.width, img.height]}
    ys, xs = np.nonzero(kept)
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    if pad > 0:
        x0 = max(0, x0 - pad)
        y0 = max(0, y0 - pad)
        x1 = min(img.width, x1 + pad)
        y1 = min(img.height, y1 + pad)
    return img.crop((x0, y0, x1, y1)), {"box": [x0, y0, x1, y1]}
