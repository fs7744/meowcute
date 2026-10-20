"""调色板：提取（频次加权的最远点采样）与应用（最近色映射）。

为什么要单独一层：
- 跨多张结果共享同一块色板 —— 逐张各自量化时每张色板都不同，同一个肤色
  逐张偏色，放进同一套素材里一眼就能看出不是一套；
- 色板必须是**固定输入**才能谈「限定 16 色」这个验收指标。量化完再数
  颜色数只能叫「事后统计」，先定色板再映射才叫「受控」。

为什么不用中位切分 / k-means：两者都按「像素数量」分箱，像素画里背景
（纯白）常占 60%+，于是 16 个色位会有十几个都被白色系占掉，真正需要的
中间灰、描边黑、点缀红反而分不到——实测就是这样把白色映射成红色。
这里改成「频次加权的最远点采样」：先取最高频色，之后每次挑「离已选色板
最远、同时出现得不算少」的颜色，保证有限色位覆盖到真正不同的色相。
"""
from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

RGB = Tuple[int, int, int]

# 唯一固定色板：GameBoy DMG（4 色）—— 像素风验收时最容易被拿来对照的经典
FIXED_PALETTES = {
    "gameboy": [(15, 56, 15), (48, 98, 48), (139, 172, 15), (155, 188, 15)],
    "nes": [(0, 0, 0), (252, 252, 252), (188, 188, 188), (124, 124, 124),
            (164, 228, 252), (60, 188, 252), (0, 120, 248), (0, 0, 252),
            (104, 136, 252), (0, 88, 248), (0, 0, 188), (248, 184, 248),
            (248, 120, 248), (248, 88, 152), (216, 0, 204), (248, 164, 192),
            (240, 208, 176), (248, 120, 88), (248, 56, 0), (228, 92, 16),
            (252, 160, 68), (248, 184, 0), (252, 224, 168), (248, 216, 120),
            (184, 248, 24), (88, 216, 84), (0, 168, 0), (0, 232, 216)],
}


def _estimate_bg_color(rgb_img: Image.Image) -> Optional[np.ndarray]:
    """四角采样估计纯色背景；四角不一致（场景/透明底）返回 None。

    像素主体多数是居中构图，四角是背景。只有四块区域的中位色足够接近时
    才认定「这是纯色背景」，避免把场景图/渐变底的像素也当成背景剔除。
    """
    arr = np.asarray(rgb_img, dtype=np.uint8)
    h, w = arr.shape[:2]
    s = max(1, min(h, w) // 16)
    patches = [arr[:s, :s], arr[:s, w - s:], arr[h - s:, :s], arr[h - s:, w - s:]]
    meds = np.stack([np.median(p.reshape(-1, 3), axis=0) for p in patches])
    spread = float(np.abs(meds - meds.mean(axis=0)).max())
    if spread <= 18.0:
        return meds.mean(axis=0)
    return None


def _collect_pixels(images: Sequence[Image.Image], max_side: int = 384,
                    max_total: int = 300_000,
                    ignore_background: bool = True) -> np.ndarray:
    """把若干张图缩到小尺寸后拼成一维 RGB 像素数组（可复现，不随机采样）。

    ignore_background：纯色背景（中灰/绿幕）占画面大头，若让它参与色板提取，
    频次加权会分掉大量色位给背景灰，主体（尤其半透明主体的色阶）只剩几档。
    这里按四角估计背景色并剔除近背景像素，让色板集中在主体上。
    """
    chunks: List[np.ndarray] = []
    for im in images:
        rgba = im.convert("RGBA")
        rgb = rgba.convert("RGB")
        w, h = rgb.size
        scale = min(1.0, max_side / max(w, h))
        a_img = rgba.getchannel("A")
        if scale < 1.0:
            new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
            rgb = rgb.resize(new_size, Image.LANCZOS)
            # alpha 必须跟着 RGB 一起缩，否则 arr 与掩码长度不一致会越界
            a_img = a_img.resize(new_size, Image.NEAREST)
        arr = np.asarray(rgb, dtype=np.uint8).reshape(-1, 3)
        # 透明像素不参与色板：它们会被置黑，混进来会把色板拉向纯黑
        a = np.asarray(a_img, dtype=np.uint8).reshape(-1)
        arr = arr[a > 8]
        if ignore_background and len(arr):
            bg = _estimate_bg_color(rgb)
            if bg is not None:
                # 只剔「几乎就是背景色」的像素（单通道差 ≤14），避免误伤主体阴影
                diff = np.abs(arr.astype(np.int32) - bg.astype(np.int32)).max(axis=1)
                arr = arr[diff > 14]
        chunks.append(arr)
    if not chunks:
        return np.zeros((0, 3), dtype=np.uint8)
    px = np.concatenate(chunks, axis=0)
    if len(px) > max_total:
        idx = np.linspace(0, len(px) - 1, max_total).astype(np.int64)
        px = px[idx]
    return px


def _weighted_dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """感知加权距离（R2 G4 B3）：人眼对绿色最敏感。"""
    d = a.astype(np.int32) - b.astype(np.int32)
    return (d[..., 0] ** 2 * 2 + d[..., 1] ** 2 * 4 + d[..., 2] ** 2 * 3).astype(np.float32)


def extract_palette(images: Sequence[Image.Image], colors: int,
                    ignore_background: bool = True) -> List[RGB]:
    """从一批图里提取共享色板：频次加权的最远点采样（确定性，无随机）。"""
    n = max(2, min(int(colors), 256))
    px = _collect_pixels(images, ignore_background=ignore_background)
    if len(px) == 0:
        return [(0, 0, 0), (255, 255, 255)]

    # 1) 粗量化合并噪声（AI 出的「纯色」常带 ±3 抖动，不合并会占满色位）。
    # 桶宽随目标色数走：48 色这类高色数如果还用 8 级桶（32 档），半透明主体
    # 的中间色阶会被并掉，通透感直接断层；4 级桶（64 档）能保住细腻色阶。
    step = 4 if n >= 32 else 8
    q = ((px.astype(np.int32) // step) * step + step // 2).clip(0, 255).astype(np.uint8)
    uniq, counts = np.unique(q, axis=0, return_counts=True)
    uniq = uniq.astype(np.int32)
    counts = counts.astype(np.float32)

    # 2) 先取最高频色（通常是背景），再逐次挑「远且不算少」的颜色
    order = np.argsort(-counts)
    chosen: List[int] = [int(order[0])]
    min_dist = _weighted_dist(uniq, uniq[chosen[0]])
    freq_w = np.sqrt(counts / counts.max())

    while len(chosen) < n:
        score = min_dist * freq_w
        # 已经覆盖到的颜色不计（距离为 0 的地方分数必为 0）
        score[chosen] = -1.0
        i = int(np.argmax(score))
        if score[i] <= 1.0:
            break
        chosen.append(i)
        min_dist = np.minimum(min_dist, _weighted_dist(uniq, uniq[i]))

    palette: List[RGB] = []
    for i in chosen:
        c = (int(uniq[i][0]), int(uniq[i][1]), int(uniq[i][2]))
        if c not in palette:
            palette.append(c)
    return palette or [(0, 0, 0), (255, 255, 255)]


def apply_palette(rgb: Image.Image, palette: Sequence[RGB]) -> Image.Image:
    """把每个像素映射到最近色板色（感知加权距离）。

    注意 dtype：差值平方最大 255²×4 ≈ 26 万，int16（上限 32767）会溢出成
    负数，导致「最近色」算错——实测白色被映射成红色就是这条。必须 int32。
    """
    if not palette:
        return rgb.convert("RGB")
    arr = np.asarray(rgb.convert("RGB"), dtype=np.uint8)
    pal = np.asarray(palette, dtype=np.int32)  # (K, 3)
    h, w, _ = arr.shape
    flat = arr.reshape(-1, 3).astype(np.int32)
    out = np.empty_like(flat)
    block = 65536
    for s in range(0, len(flat), block):
        chunk = flat[s:s + block]
        d = ((chunk[:, None, 0] - pal[None, :, 0]) ** 2 * 2
             + (chunk[:, None, 1] - pal[None, :, 1]) ** 2 * 4
             + (chunk[:, None, 2] - pal[None, :, 2]) ** 2 * 3)
        idx = np.argmin(d, axis=1)
        out[s:s + block] = pal[idx]
    return Image.fromarray(out.astype(np.uint8).reshape(h, w, 3), "RGB")


def count_out_of_palette(rgb: Image.Image, palette: Sequence[RGB]) -> int:
    """统计色板外颜色数（验收指标，量化后必须为 0）。"""
    if not palette:
        return 0
    colors = rgb.convert("RGB").getcolors(maxcolors=1 << 24) or []
    pal_set = {(int(r), int(g), int(b)) for r, g, b in palette}
    return sum(1 for _cnt, c in colors if c not in pal_set)


def binarize_alpha(rgba: Image.Image, threshold: int = 128) -> Image.Image:
    """alpha 二值化：像素素材不用半透明（抗锯齿边会被引擎采样出色晕）。"""
    a = rgba.getchannel("A").point(lambda v: 255 if v >= threshold else 0)
    out = rgba.copy()
    out.putalpha(a)
    # 全透明像素 RGB 置 0：防止引擎双线性采样把底色晕进边缘
    arr = np.asarray(out).copy()
    mask = np.asarray(a) == 0
    arr[mask] = 0
    return Image.fromarray(arr, "RGBA")


def alpha_binary_ratio(rgba: Image.Image) -> float:
    """非 0/255 alpha 像素占比（验收指标，处理后应为 0）。"""
    a = np.asarray(rgba.getchannel("A"), dtype=np.uint8).reshape(-1)
    if len(a) == 0:
        return 0.0
    return round(float(np.count_nonzero((a != 0) & (a != 255)) / len(a)), 4)


def resolve_palette(
    palette_name: str,
    colors: int,
    images: Optional[Sequence[Image.Image]] = None,
    ignore_background: bool = True,
) -> Tuple[str, List[RGB]]:
    """解析色板选择：固定色板（gameboy/nes）或从图里提取（auto）。"""
    name = (palette_name or "auto").strip().lower()
    if name in FIXED_PALETTES:
        return name, list(FIXED_PALETTES[name])
    imgs = list(images or [])
    if not imgs:
        return "auto", []
    return "auto", extract_palette(imgs, colors, ignore_background=ignore_background)
