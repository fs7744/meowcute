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
import threading
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


def _rgb_to_hsv_u8(rgb: np.ndarray) -> np.ndarray:
    """RGB(0-255) → OpenCV 量纲的 HSV（H 0-180、S/V 0-255），纯 numpy。

    与 cv2.cvtColor(rgb, COLOR_RGB2HSV) 等价（uint8 量纲下 H 折半）。
    自己算而不是 import cv2：没装 OpenCV 的环境（包括本机）跑不了色相判定，
    而溢色清洗只是抠图链上「锦上添花」的一步，不该把整条链带崩——
    以前这里直接 import cv2，绿 / 蓝底素材一进 despill 就整单失败。
    """
    r = rgb[..., 0].astype(np.float32) / 255.0
    g = rgb[..., 1].astype(np.float32) / 255.0
    b = rgb[..., 2].astype(np.float32) / 255.0
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    diff = mx - mn
    h = np.zeros_like(mx)
    mask = diff > 1e-6
    idx = (mx == r) & mask
    h[idx] = ((g[idx] - b[idx]) / diff[idx]) % 6.0
    idx = (mx == g) & mask
    h[idx] = ((b[idx] - r[idx]) / diff[idx]) + 2.0
    idx = (mx == b) & mask
    h[idx] = ((r[idx] - g[idx]) / diff[idx]) + 4.0
    # h 是「六分之一圈」（0-6）：×60 得角度（0-360），再 ÷2 得 OpenCV 的
    # uint8 量纲（0-180）。**这里曾经写成 *30/2（等于 ×15）**，所有色相被拦腰
    # 砍半：纯绿算出 H=30（应为 60），于是 _hue_spill 的目标表一个都匹配不上
    # （|30-60|=30 > 容差 26）——绿幕/蓝幕的暗部溢色整套判定静默失效，
    # residualKeyPixels 还虚报「很干净」。现在与 cv2.cvtColor 逐像素比对过。
    h = h * 30.0
    s = np.where(mx > 1e-6, diff / np.maximum(mx, 1e-6), 0.0)
    hsv = np.stack([h, s * 255.0, mx * 255.0], axis=-1)
    return np.clip(hsv, 0, 255).astype(np.uint8)


def _hue_spill(rgb: np.ndarray, channel: int, hue_tol: int = 26,
               sat_min: float = 0.25, val_min: float = 0.05) -> np.ndarray:
    """按**色相**认出「被底色染过」的像素（不只是 RGB 距离近的）。

    RGB 距离判定的盲区在暗部：头发里一颗深绿溢出 (20,80,30) 到纯绿 (0,255,0)
    的 RGB 距离约 178，任何合理容差都抓不到，于是留着一条绿丝。色相 + 饱和度
    + 明度下限三条件能抓到它，同时排除「深色但不同相」的描边像素。

    量纲与 OpenCV 对齐：uint8 图 H 是 0-180（float 图才是 0-360），
    目标色相按这个量纲写死（绿 120 → 60）。
    """
    hsv = _rgb_to_hsv_u8(np.clip(rgb, 0, 255).astype(np.uint8))
    target = {0: 0, 1: 60, 2: 120}.get(channel, 60)
    dist = np.abs(hsv[..., 0].astype(np.int16) - target)
    dist = np.minimum(dist, 180 - dist)
    return (dist <= hue_tol) & (hsv[..., 1] >= int(sat_min * 255)) & (hsv[..., 2] >= int(val_min * 255))


def _despill(arr: np.ndarray, kept: np.ndarray, palette: List[np.ndarray], tol: float) -> int:
    """把「紧贴透明区、颜色是被背景冲淡的」轮廓像素改成内侧角色像素的颜色。

    判据是**混合比例**，不是绝对距离：

        边缘像素 C ≈ α·F + (1-α)·B（F=内侧角色色，B=背景色）
        ⇒ 它在 B→F 这条线上的位置 = |C-B| / |F-B|
        比例 < 0.85 说明混了 15% 以上的背景，是混边；实心角色像素比例 ≈ 1。

    为什么不能用绝对距离（以前是 `d_bg <= tol*1.25`）：白底 236、描边 40 时，
    一个 40% 混白的边缘像素到背景的距离约 200，任何按「色键容差」量出来的阈值
    （tol≈40 → 50）都抓不到它——用户看到的白边正是这一圈。而按比例判的话，
    同一套阈值在深底、浅底、彩色底上统一成立，不必为每种底色调参。

    也不能用「离背景更近还是离内侧更近」（以前是 `d_bg < d_core * 0.9`）：
    α≈0.5 的混色像素在**数学上两边等距**，越接近纯混边越判不出来，正好漏掉
    最该修的那部分。

    安全性：角色本身就是浅色（白毛/白甲）时，|F-B| 极小，比例判据要求近乎
    贴底的像素才会命中，不会把白毛洗成邻居色。只改 RGB 不动 alpha 形状。
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

    # 混边经常不是 1px：视频重采样会让抗锯齿糊成 2~3px（实测白底人物环宽 3px）。
    # 只在主体够大时才多剥一圈，避免把小图标 / 1px 线条整个剥成「边缘」。
    if int(kept.sum()) > 5000:
        wider = _erode(core)
        if wider.any() and int(wider.sum()) > int(kept.sum()) * 0.1:
            core = wider
            band = kept & ~core

    d_bg_all = _palette_dist(rgb, palette)
    bgv = np.asarray(palette[0], dtype=np.float64)
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
    for _ in range(4):
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
        d_bg = d_bg_all[ys, xs]
        # |F - B|：这条线有多长。≤6 说明内侧色与背景几乎同色，没法判断混没混，
        # 此时按「贴底」处理（比例判据会自然放行）。
        span = np.sqrt(((mean - bgv[None, :]) ** 2).sum(axis=1)).astype(np.float32)
        ratio = d_bg / np.maximum(span, 6.0)
        dirty = ratio < 0.85
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
    # 全向量化：面积/触边都用 bincount 一次算完，最后用 LUT 一次映射回掩码。
    # 旧写法 `for i: comp = lab == i` 是「每个域扫一遍全图」——u2net 掩码常有
    # 几千个噪点连通域，5000 域 × 25M 像素 = 10^9 级操作，实测把抠图拖到分钟级。
    # 且预筛面积**救不了**这种场景：噪点域恰恰都是小域，全都会通过预筛。
    areas = np.bincount(lab.ravel(), minlength=n)
    border = np.zeros(n, bool)
    border[np.unique(lab[0, :])] = True
    border[np.unique(lab[-1, :])] = True
    border[np.unique(lab[:, 0])] = True
    border[np.unique(lab[:, -1])] = True
    ok = (areas > 0) & (areas <= max_area) & ~border
    ok[0] = False
    filled = int(areas[ok].sum())
    kept |= ok[lab]
    return kept, filled


def _punch_bg_holes(rgb: np.ndarray, kept: np.ndarray, palette: List[np.ndarray],
                    tolerance: float = 24.0, min_area: int = 50,
                    max_area_ratio: float = 0.06, flat_std: float = 5.0,
                    reach: int = 4) -> Tuple[np.ndarray, int]:
    """挖掉「被轮廓包住、但其实是背景」的浅色舌状块（白底漏进缝隙的那一块）。

    为什么需要（2026-10-26 实测）：袍子和佩剑之间的缝只有 1~2px，白底从缝里
    露出来，语义分割把整个轮廓**填实**，这块白就留成不透明的一片——缩到 128/256
    的格子里特别扎眼，看起来像"白底没扣干净"。边界泛洪救不了：缝被前景连成一片，
    泛洪进不去（`_fill_small_holes` 方向正好相反，它填的是透明洞）。

    四个判据同时满足才动手（宁可留着，也不误伤白/浅色衣料）：
      ① 颜色接近边框底色：欧氏距离 ≤ tolerance（默认 24，比抠图容差紧得多）
      ② 局部平坦：5×5 灰度标准差 ≤ flat_std——背景没有褶皱与阴影，衣料必有
      ③ 面积在 [min_area, max_area_ratio×主体]：太小是噪点，太大可能是白袍本体
      ④ **贴着外部背景**：与「已抠掉的区域」膨胀 reach 像素后有交叠——说明它是
         从窄缝漏进来的一块，而不是身体内部的白色饰件（内衬、领口、白袜）
    """
    try:
        import cv2
    except Exception:
        return kept, 0
    if not kept.any():
        return kept, 0
    area_kept = float(kept.sum())
    dist = _palette_dist(rgb.astype(np.int32), palette)
    gray = cv2.cvtColor(rgb.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    mu = cv2.blur(gray, (5, 5))
    mu2 = cv2.blur(gray * gray, (5, 5))
    std = np.sqrt(np.maximum(0.0, mu2 - mu * mu))

    seed = kept & (dist <= float(tolerance)) & (std <= float(flat_std))
    if not seed.any():
        return kept, 0
    # ④ 只认「外部背景伸手可及」的块：先把已抠掉的区域膨胀一圈
    outside = (~kept).astype(np.uint8)
    near_bg = cv2.dilate(outside, np.ones((3, 3), np.uint8),
                         iterations=max(1, int(reach))).astype(bool)

    n, lab, stats, _ = cv2.connectedComponentsWithStats(seed.astype(np.uint8),
                                                        connectivity=4)
    if n <= 1:
        return kept, 0
    max_area = max(int(min_area), int(max_area_ratio * area_kept))
    # 全向量化（同 _fill_small_holes 的教训：逐域扫全图会被噪点数拖死）
    areas = stats[:, cv2.CC_STAT_AREA]
    touches = np.bincount(lab[near_bg].ravel(), minlength=n) > 0
    ok = (areas >= int(min_area)) & (areas <= max_area) & touches
    ok[0] = False
    if not ok.any():
        return kept, 0
    punched = int(areas[ok].sum())
    kept = kept & ~ok[lab]
    return kept, punched


def _decontaminate_edge(arr: np.ndarray, kept: np.ndarray,
                        bg: np.ndarray, depth: float = 2.5,
                        brighten: float = 8.0) -> int:
    """把轮廓最外 1~2px 的「半背景混合像素」洗回纯前景色（只改 RGB，不动 alpha）。

    为什么需要（2026-10-26 实测）：`_despill` 走的是**色相**污染（绿幕/蓝幕那种），
    白底没有任何色相，于是这条路径对白底完全无效——alpha 边界切在发丝中间时，
    留下的一圈「深色发丝 + 白底」的混合像素（如 172,183,187，内侧真色是 98,114,118）
    在深色背景上就是一条扎眼的白边，看着像"白底没抠掉"，其实背景早就干净了
    （该素材精确底色残留只有 27px/8 帧）。

    判据（三条同时满足才动）：
      ① 在轮廓最外 depth 像素内
      ② 比最近的内侧"深层"像素更亮（brighten 以上）——混了背景必然被提亮
      ③ 离背景色比内侧参考色更近（|p-B| < |F-B|）：说明它是被背景覆盖过的混合
         像素，而不是角色自己的高光（高光不会朝背景色方向走）
    命中就把 RGB 换成那个内侧参考色 F（alpha 保持二值不变），**形状一点不变**，
    只是把白边染回本色。幂等：第二次跑 p==F，②③ 不再成立。
    """
    try:
        import cv2
        from scipy import ndimage
    except Exception:
        return 0
    if not kept.any():
        return 0
    rgb = arr[:, :, :3].astype(np.float32)
    dist = cv2.distanceTransform(kept.astype(np.uint8), cv2.DIST_L2, 5)
    deep = kept & (dist > float(depth))
    if not deep.any():
        return 0
    # 每个边界像素找最近的「深层」像素当参考色（EDT 一次算完，别逐像素搜索）
    _, idx = ndimage.distance_transform_edt(~deep, return_indices=True)
    ref = rgb[idx[0], idx[1]]
    band = kept & (dist <= float(depth))
    if not band.any():
        return 0
    b = np.asarray(bg, dtype=np.float32).reshape(1, 1, 3)
    d_bg = np.abs(rgb - b).sum(axis=2)
    d_bg_ref = np.abs(ref - b).sum(axis=2)
    lum = rgb.mean(axis=2)
    lum_ref = ref.mean(axis=2)
    hit = band & (lum > lum_ref + float(brighten)) & (d_bg < d_bg_ref - 1.0)
    n = int(hit.sum())
    if not n:
        return 0
    arr[hit, 0:3] = ref[hit].astype(arr.dtype)
    return n


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
    # 全向量化（同 _fill_small_holes）：域内背景色像素数也用 bincount 统计，
    # 「90% 以上是背景色」按标签一次判完，最后 LUT 映射置 alpha。
    areas = np.bincount(lab.ravel(), minlength=n)
    bg_counts = np.bincount(lab[bg_like], minlength=n)
    ok = (areas > 0) & (areas <= max_area) & (bg_counts >= 0.9 * areas)
    ok[0] = False
    if not ok.any():
        return cut
    out = arr.copy()
    out[ok[lab], 3] = 0
    return Image.fromarray(out, "RGBA")


def clean_edge_spill(cut: Image.Image, bg: Tuple[int, int, int], depth: int = 2) -> Tuple[Image.Image, int]:
    """边缘溢色清洗（薄封装）：真正的算法在 `_despill`，这里只负责从 RGBA 反推
    前景掩码再调一次。

    为什么改成薄封装：原实现自己算「最外 depth 圈」，但那个圈是用
    `inner |= 平移(kept)`（膨胀）算的，`kept & ~膨胀` **恒为空**——整段是死代码，
    永远不会命中（实测 `edgePixelsRecolored` 一直是 0）。就算修好环的计算，
    它用「整个环的中位色」当替换色：红披风挨着灰毛时，中位色会把披风边缘涂成
    灰色。`_despill` 是逐像素从最近的内侧邻居取色，既正确又已经在主线里，没
    必要维护两套。

    幂等：`remove_background` 内已经跑过一次，这里再跑只会命中「补洞之后新
    暴露出来」的边界像素，不会反复涂抹同一圈。
    """
    arr = np.asarray(cut).copy()
    if arr.shape[2] < 4:
        return cut, 0
    kept = arr[:, :, 3] > 8
    if not kept.any():
        return cut, 0
    n = _despill(arr, kept, [np.asarray(bg, dtype=np.int32)], 70.0)
    if not n:
        return cut, 0
    return Image.fromarray(arr, "RGBA"), n


# --------------------------------------------------------------------------- #
# 路径 1：u2net 深度抠图
# --------------------------------------------------------------------------- #
_SESSION = None
_U2NET_ERR: Optional[str] = None
_U2NET_HOME = os.path.expanduser(os.getenv("U2NET_HOME", "~/.u2net"))
_U2NET_ONNX = os.path.join(_U2NET_HOME, "u2net.onnx")
# 懒加载锁：FastAPI 的 to_thread 会并发调 remove_background，两个请求同时
# 首次进入时都会通过 `_SESSION is None` 检查，然后各 new_session 一份
# 176MB 模型——内存翻倍、加载时间也翻倍。用一把锁把初始化串起来。
_U2NET_LOCK = threading.Lock()


def _u2net_session():
    global _SESSION, _U2NET_ERR
    if _SESSION is not None or _U2NET_ERR is not None:
        return _SESSION
    with _U2NET_LOCK:
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


def _clean_alpha_specks(kept: np.ndarray, passes: int = 2) -> Tuple[np.ndarray, int]:
    """去掉孤立的单像素前景噪点。

    u2net 会在主体外和抗锯齿边撒零散 1px 点，二值化后成了扎眼的碎屑。判据刻意只认
    「8 邻域里一个前景邻居都没有」：胡须、触角这类 1px 宽但有长度的结构，每颗像素
    至少还有一个同类邻居，不会被误删。
    """
    if not kept.any():
        return kept, 0
    out = kept.copy()
    removed = 0
    for _ in range(max(1, passes)):
        cnt = np.zeros(kept.shape, dtype=np.uint8)
        for dy, dx in _NEIGHBOURS:
            cnt += _neighbour_shift(out, dy, dx).astype(np.uint8)
        lonely = out & (cnt == 0)
        n = int(lonely.sum())
        if not n:
            break
        out &= ~lonely
        removed += n
    return out, removed


def _plausible(kept: np.ndarray) -> Tuple[bool, str]:
    """分割结果是否有基本合理性——用来决定要不要换条路径重来。

    两类失败最常见，且都不报错：把整幅图当主体（四边几乎全被保留）、什么都没留下。
    语义分割在这两种情况下会安静地给出一个「看起来跑了」的结果，用户只看到一张
    要么没抠、要么全空的图。这里先量一次，不合理就让调用方退到图割 / 颜色键。
    """
    if not kept.any():
        return False, "没有识别出主体"
    h, w = kept.shape
    ratio = float(kept.sum()) / float(h * w)
    if ratio < 0.015:
        return False, "主体面积过小"
    if ratio > 0.985:
        return False, "几乎整幅图都被当成主体"
    border = [kept[0, :].mean(), kept[-1, :].mean(), kept[:, 0].mean(), kept[:, -1].mean()]
    if min(border) > 0.55:
        return False, "主体铺满四条边（多半把背景也算进去了）"
    return True, ""


def _trim_bottom(kept: np.ndarray, ratio: float) -> Tuple[np.ndarray, int]:
    """从主体底部按比例切掉一条（0 < ratio <= 0.3），用来去掉接地投影 / 地面残影。

    为什么要给这么「粗暴」的旋钮：AI 出图常画一圈贴地软投影，而投影与浅色主体在
    颜色和几何上都可能重合（实测浅蓝史莱姆底 + 淡紫投影，试过按亮度、按饱和度、
    按到底色的距离、按列轮廓四种判据，都会把主体底部一起削掉）。与其上线一个
    会咬掉角色脚的东西，不如把这条边界交给用户：拖一下就能对齐主体的真实底边，
    结果完全可预测。
    """
    r = max(0.0, min(float(ratio), 0.3))
    if r <= 0:
        return kept, 0
    ys, _ = np.nonzero(kept)
    if not len(ys):
        return kept, 0
    y0, y1 = int(ys.min()), int(ys.max())
    subj_h = max(1, y1 - y0 + 1)
    cut_y = y1 - int(round(subj_h * r))
    if cut_y <= y0:
        return kept, 0
    removed = kept[cut_y + 1:, :]
    n = int(removed.sum())
    out = kept.copy()
    out[cut_y + 1:, :] = False
    return out, n


def _edge_offset(kept: np.ndarray, px: int) -> np.ndarray:
    """整块 alpha 的边界内缩（负）或外扩（正），单位像素。

    抠图边界永远有 1-2 像素的争议：硬边像素素材常常被啃掉一圈轮廓，照片类又容易
    留一圈底色残影。与其在阈值上猜，不如给一个可预测的旋钮——用户看到「啃边」就
    加、看到「留边」就减，效果立刻可见。
    """
    n = abs(int(px))
    if n == 0:
        return kept
    out = kept.copy()
    for _ in range(n):
        if px < 0:
            # 腐蚀必须取「所有邻居的交集」；用并集再与自身相与等于没做
            acc = np.ones_like(out)
            for dy, dx in _NEIGHBOURS:
                acc &= _neighbour_shift(out, dy, dx)
            out = acc
        else:
            acc = np.zeros_like(out)
            for dy, dx in _NEIGHBOURS:
                acc |= _neighbour_shift(out, dy, dx)
            out = out | acc
        if not out.any():
            break
    return out


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
def _flood_from_border(match: np.ndarray) -> Tuple[np.ndarray, int]:
    """从画面四边向内泛洪 `match`，返回 (与边框连通的像素, 个数)。

    等价于「四邻域 BFS，只走 match 像素」，但走连通域而不是逐像素 Python 循环：
    cv2.connectedComponents 是 C 实现，5000² 也是毫秒级；旧 BFS 在 2000² 上要
    140 万次解释器迭代（秒级）、5000² 上 2500 万次（分钟级），而没有 u2net 权重
    或用户明确选「颜色键」时，每次抠图都走这条路。
    没有 cv2 时退回原来的 deque BFS（结果完全一致）。
    """
    h, w = match.shape
    labels = np.zeros((h, w), np.uint8)
    try:
        import cv2
        n, lab = cv2.connectedComponents(match.astype(np.uint8), connectivity=4)
        if n <= 1:
            return labels.astype(bool), 0
        border = np.zeros(n, bool)
        for edge in (lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]):
            border[np.unique(edge)] = True
        border[0] = False           # 0 = 非 match 像素
        visited = border[lab]
        return visited, int(visited.sum())
    except Exception:
        pass

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
    while dq:
        y, x = dq.popleft()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and match[ny, nx]:
                visited[ny, nx] = True
                dq.append((ny, nx))
    return visited, int(visited.sum())


def _key_keep(rgb: np.ndarray, palette: List[np.ndarray], tolerance: float,
              edge_grow: int, grow_passes: int) -> Tuple[np.ndarray, int, int]:
    h, w = rgb.shape[:2]
    dist = _palette_dist(rgb, palette)
    match = dist <= float(tolerance)

    visited, removed = _flood_from_border(match)

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
    edge: int = 0,
    bottom_trim: float = 0.0,
) -> Tuple[Image.Image, dict]:
    """抠掉背景。

    hard=True（默认，像素素材）：二值 alpha + 边缘去污，无软边、无白晕。
    hard=False（照片 / 半透明素材）：保留高斯羽化，边缘更自然。
    method: auto | u2net | grabcut | key
    edge: 整块 alpha 的边界偏移，负数内缩、正数外扩，单位像素
    bottom_trim: 从主体底部切掉的比例（0-0.3），用于去掉接地投影
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
        # 软边（hard=False）也走语义分割：分割本身与是否硬边无关，羽化只是
        # 最后对 alpha 做一次模糊。以前这里 `else ["key"]` 把 u2net/grabcut
        # 整个跳过，用户对一张非纯色背景的照片选「羽化」，结果是什么都没抠掉
        # （只有 keptRatio>0.97 的 warning 间接提示，绝大多数人看不出）。
        chain = ["u2net", "grabcut", "key"]
    else:
        chain = [method]

    kept = None
    grown = 0
    holes_filled = 0
    bg_holes = 0
    specks_removed = 0
    last_err = None
    retry_reason = ""

    def _try(cand: str):
        if cand == "u2net":
            if min(h, w) < 32 or not u2net_available():
                return None
            return _mask_keep(arr[:, :, :3])
        if cand == "grabcut":
            if min(h, w) < 96:
                return None
            return _grabcut_keep(arr[:, :, :3])
        return None

    # 逐级尝试；每一级都过一遍合理性检查，不合格就换下一条更保守的路径。
    # first 记住第一个跑出来的结果：全都「看起来不对」时宁可用首选（语义分割）也不换成
    # 颜色键——满幅构图的图标本来就该四边都有前景，不能因为启发式不认就换条更差的路径。
    first = None
    for cand in chain:
        if cand == "key":
            got, _rem, _gr = _key_keep(rgb, palette, tolerance, edge_grow, grow_passes)
        else:
            try:
                got = _try(cand)
            except Exception as e:
                last_err = f"{cand}: {type(e).__name__}"
                continue
            rem = gr = 0
            if got is None:
                continue
        if first is None:
            first = (got, cand)
        ok, why = _plausible(got)
        if ok:
            kept, used = got, cand
            break
        # 不合理：记下原因，继续试下一条更保守的路径
        retry_reason = why
        last_err = f"{cand}: {why}"

    if kept is None:
        if first is not None:
            kept, used = first
            retry_reason = retry_reason or "分割结果不可靠，已保留首选结果"
        else:  # 一条路径都没跑起来
            kept, _rem, _gr = _key_keep(rgb, palette, tolerance, edge_grow, grow_passes)
            used = "key"

    kept = kept & ~already_clear
    if edge:
        before = int(kept.sum())
        kept = _edge_offset(kept, edge)
        grown += max(0, int(kept.sum()) - before)
    if bottom_trim > 0:
        kept, _cut = _trim_bottom(kept, bottom_trim)
    if hard:
        kept, specks_removed = _clean_alpha_specks(kept)
        if used != "key":
            kept, holes_filled = _fill_small_holes(kept)
        # 反向一步：把「背景色从窄缝漏进轮廓」的那几块挖掉。
        # 顺序放在补洞之后：先补掉噪点小洞，再把真正的背景舌状块打穿。
        kept, bg_holes = _punch_bg_holes(rgb, kept, palette,
                                         tolerance=min(24.0, float(tolerance)))
    removed = int((~kept).sum())

    despilled = 0
    decontaminated = 0
    residual = 0
    alpha = np.where(kept, 255, 0).astype(np.uint8)

    if hard:
        if despill:
            despilled = _despill(arr, kept, palette, float(tolerance))
            # 色相去污对**白/灰底**无效（没有色相可查），这里补一条按亮度的混合像素
            # 回洗：把轮廓最外 1~2px 被背景提亮的混合像素染回内侧本色（只改 RGB）
            decontaminated = _decontaminate_edge(
                arr, kept, np.asarray(palette[0], dtype=np.float32))
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
        # 白/灰底下靠亮度判据回洗的边缘混合像素数（色相去污对它无效）
        "edgeDecontaminated": decontaminated,
        "holesFilled": holes_filled,
        # 被挖掉的「背景漏进轮廓」像素数（白底舌状块）。非 0 说明这帧原本会残留白块
        "bgHolesPunched": bg_holes,
        "specksRemoved": specks_removed,
        "edgeOffset": int(edge),
        "bottomTrim": round(float(bottom_trim), 3),
        "edgeGrowPixels": grown,
        # 键名是透明像素占比（含输入图自带的透明区），不是「本次抠掉的比例」：
        # 早先两处 removed 赋值互相覆盖，报出来的数名字段对不上，读的人会误判。
        "transparentRatio": round(removed / float(h * w), 4),
        "keptRatio": kept_ratio,
        "residualKeyPixels": residual,
    }
    if last_err:
        meta["fallbackReason"] = last_err
    if retry_reason:
        meta["retryReason"] = retry_reason
    # 几乎没抠掉 / 几乎全抠掉都说明这一趟大概率不对，前端要能直接提示
    if kept_ratio < 0.02:
        meta["warning"] = "几乎没留下主体：多半把背景当成了主体，把方法改成「颜色键」并调低容差试试"
    elif kept_ratio > 0.97:
        meta["warning"] = "几乎没抠掉背景：主体可能铺满画面，或背景与主体太接近"
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
