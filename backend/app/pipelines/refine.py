"""像素精修：把 AI 出的「像素风」收成真正的像素图（本项目质量护城河）。

流程（全部确定性，不碰模型）：
    网格检测/吸附（可选） → 每格中位色采样 → 共享调色板量化
    → alpha 二值化 → 输出**原生像素尺寸**图

采样为什么用中位色而不是多数表决：合成图实测（8px 格 + 抗锯齿 + 噪声），
median 的重建误差 2.4、色数 550；majority（k-means k=2）误差 5.0、色数 1378；
center 直接取格中心点最差（误差 11.9）。median 对格内噪声更稳，且在网格
偏移 2px 时仍保持最低误差。

为什么要网格对齐而不是简单降采样：AI 图的「像素块」边界通常不在整齐网格上
（512 图里 37.3px 一格很常见），直接 resize 到 64 会把边界切在块中间，
混色成脏斑。FFT 检测真实网格 + Sobel 吸附网格线 + 每格取主色，才能
「每格一色、边界对齐」——这是像素画与「低分辨率图」的区别。

输出尺寸的取舍：只存原生像素尺寸（如 64×64），不预放大 8 倍。前端用
``image-rendering: pixelated`` 最近邻显示即可，放大交给渲染端——存放大版
等于把「8 倍」这个显示参数焊死在素材里，之后想要 4 倍或 16 倍还得重算。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from PIL import Image

from . import palette as palette_pipe
from . import perfect_pixel


def _to_array(img: Image.Image) -> np.ndarray:
    """RGBA 图 → (RGB array, alpha array)。"""
    rgba = img.convert("RGBA")
    arr = np.asarray(rgba, dtype=np.uint8)
    return arr[..., :3], arr[..., 3]


def _from_array(rgb: np.ndarray, alpha: Optional[np.ndarray]) -> Image.Image:
    if alpha is None:
        return Image.fromarray(rgb.astype(np.uint8), "RGB")
    out = np.dstack([rgb.astype(np.uint8), alpha.astype(np.uint8)])
    return Image.fromarray(out, "RGBA")


def _box_downsample(rgb: np.ndarray, alpha: Optional[np.ndarray],
                    target_w: int, target_h: int):
    """无网格对齐时的退路：区域平均降采样（避免最近邻随机取点的脏斑）。

    RGB 用 BOX（区域平均后再量化），alpha 用最近邻（保持硬边）。
    """
    h, w = rgb.shape[:2]
    target_w = max(1, min(int(target_w), w))
    target_h = max(1, min(int(target_h), h))
    rgb_img = Image.fromarray(rgb, "RGB").resize((target_w, target_h), Image.BOX)
    arr = np.asarray(rgb_img)
    a_small = None
    if alpha is not None:
        a_img = Image.fromarray(alpha, "L").resize((target_w, target_h), Image.NEAREST)
        a_small = np.asarray(a_img)
    return arr, a_small


def _clean_specks(rgb: np.ndarray, max_diff: int = 40) -> np.ndarray:
    """像素画收尾清理：去掉孤立噪点、填 1 像素孔洞。

    只在「8 邻域没有任何同色像素」且「与邻域多数色的差异不超过 max_diff」
    时替换。后一个条件很关键：单像素眼睛/高光与周围差异大，必须保留；
    量化残留的近似色噪点差异小，才清掉。1 像素描边通常有 2 个以上同色
    邻居，不会被误删。
    """
    h, w = rgb.shape[:2]
    if h < 3 or w < 3:
        return rgb
    padded = np.pad(rgb, ((1, 1), (1, 1), (0, 0)), mode="edge")
    nbrs = []
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            nbrs.append(padded[1 + dy:1 + dy + h, 1 + dx:1 + dx + w])
    stack = np.stack(nbrs, axis=0)                  # (8, h, w, 3)
    same = (stack == rgb[None]).all(axis=-1)        # (8, h, w)
    isolated = same.sum(axis=0) == 0

    best_color = rgb.copy()
    best_count = np.zeros((h, w), dtype=np.int32)
    for i in range(8):
        cnt = (stack == stack[i][None]).all(axis=-1).sum(axis=0)
        better = cnt > best_count
        best_count = np.where(better, cnt, best_count)
        best_color = np.where(better[..., None], stack[i], best_color)

    diff = np.abs(rgb.astype(np.int32) - best_color.astype(np.int32)).max(axis=-1)
    fill = isolated & (best_count >= 6) & (diff <= max_diff)
    return np.where(fill[..., None], best_color, rgb)


def refine_one(
    img: Image.Image,
    *,
    grid_snap: bool = True,
    grid_size: Optional[Sequence[int]] = None,
    target_size: Optional[Sequence[int]] = None,
    sample_method: str = "median",
    palette: Optional[Sequence[Sequence[int]]] = None,
    palette_name: str = "auto",
    transparent_bg: bool = False,
) -> Dict[str, Any]:
    """单张精修。palette 为空时不做调色板量化（只做网格对齐）。

    返回 {image, meta}：image 是原生像素尺寸的 RGBA 图。
    """
    rgb, alpha = _to_array(img)
    h, w = rgb.shape[:2]
    meta: Dict[str, Any] = {"sourceSize": [w, h]}

    detected = False
    grid: Optional[tuple] = None
    if grid_snap:
        gs = None
        if grid_size and len(grid_size) == 2:
            gs = (int(grid_size[0]), int(grid_size[1]))
        gw, gh, sampled, xs, ys = perfect_pixel.perfect_pixel(
            rgb, sample_method=sample_method, grid_size=gs,
            fix_square=False, return_coords=True,
        )
        if gw is None or gh is None:
            meta["gridFailed"] = True
        else:
            rgb = sampled
            detected = gs is None
            grid = (gw, gh)
            if alpha is not None:
                # alpha 用**同一批**网格坐标采样（中位数 + 二值化阈值）：
                # 单独再 refine 一次会因舍入差出 1 格，拼回 RGBA 时形状对不上
                a_cells = perfect_pixel.sample_median(alpha[..., None], xs, ys)[..., 0]
                alpha = np.where(a_cells >= 128, 255, 0).astype(np.uint8)
                # 手动指定网格时 RGB 可能被硬对齐过，alpha 跟着同尺寸走
                if alpha.shape != rgb.shape[:2]:
                    ah, aw = alpha.shape
                    th, tw = rgb.shape[:2]
                    sy = (np.arange(th) * ah / th).astype(np.int32)
                    sx = (np.arange(tw) * aw / tw).astype(np.int32)
                    alpha = alpha[sy][:, sx]

    if grid is None:
        # 没检测到网格 / 关闭网格对齐：按目标尺寸或原尺寸的 1/8 降采样
        if target_size and len(target_size) == 2:
            tw, th = int(target_size[0]), int(target_size[1])
        else:
            tw, th = max(1, w // 8), max(1, h // 8)
        rgb, alpha = _box_downsample(rgb, alpha, tw, th)
        grid = (rgb.shape[1], rgb.shape[0])

    # 目标尺寸：检测结果与用户要求不一致时缩到目标
    if target_size and len(target_size) == 2:
        tw, th = int(target_size[0]), int(target_size[1])
        if (tw, th) != (rgb.shape[1], rgb.shape[0]):
            # 缩小用 BOX（块平均）而不是 NEAREST：128→64 的 NEAREST 只取
            # 每 2×2 块的左上角，等于丢掉四分之三的像素；块平均保住整体
            # 色量，随后调色板量化会把中间色收回去。alpha 仍用 NEAREST 保硬边。
            rgb_img = Image.fromarray(rgb, "RGB").resize((tw, th), Image.BOX)
            rgb = np.asarray(rgb_img)
            if alpha is not None:
                alpha = np.asarray(Image.fromarray(alpha, "L").resize(
                    (tw, th), Image.NEAREST))
            grid = (tw, th)

    # 调色板量化（共享色板在 batch 层解析后传进来）
    if palette:
        pal_list = [(int(c[0]), int(c[1]), int(c[2])) for c in palette]
        rgb_img = palette_pipe.apply_palette(Image.fromarray(rgb, "RGB"), pal_list)
        rgb = np.asarray(rgb_img)

    # 收尾清理：量化后容易出现「孤立噪点」和「1 像素孔洞」，清一遍再输出
    rgb = _clean_specks(rgb)

    # 背景透明：按四角估计纯色背景，把接近背景的像素置为透明。
    # 阈值 14 很保守，只清「几乎就是背景色」的像素，避免啃掉主体阴影。
    if transparent_bg:
        bg = palette_pipe._estimate_bg_color(Image.fromarray(rgb, "RGB"))
        if bg is not None:
            diff = np.abs(rgb.astype(np.int32) - bg.astype(np.int32)).max(axis=2)
            mask = diff <= 14
            if alpha is None:
                alpha = np.full(rgb.shape[:2], 255, dtype=np.uint8)
            alpha = np.where(mask, 0, alpha).astype(np.uint8)

    out = _from_array(rgb, alpha)
    if out.mode == "RGBA":
        out = palette_pipe.binarize_alpha(out)

    colors_used = len(out.convert("RGB").getcolors(maxcolors=1 << 24) or [])
    meta.update({
        "grid": [int(grid[0]), int(grid[1])],
        "detected": detected,
        "palette": palette_name,
        "paletteSize": len(palette or []),
        "colors": colors_used,
        "offPalette": palette_pipe.count_out_of_palette(out, palette) if palette else 0,
        "alphaBinary": palette_pipe.alpha_binary_ratio(out) if out.mode == "RGBA" else 0.0,
        "size": [out.width, out.height],
    })
    return {"image": out, "meta": meta}


def refine_batch(
    images: Sequence[Image.Image],
    *,
    grid_snap: bool = True,
    grid_size: Optional[Sequence[int]] = None,
    target_size: Optional[Sequence[int]] = None,
    sample_method: str = "median",
    colors: int = 16,
    palette_name: str = "auto",
    transparent_bg: bool = False,
) -> Dict[str, Any]:
    """一批图统一精修：共享色板 + 逐张网格对齐。

    共享色板在 batch 层解析：先各自网格对齐，再汇总所有结果提取一块色板，
    最后统一映射。逐张各自取色板会显著偏色（见 palette.py 注释）。
    """
    # 第一遍：网格对齐（不带色板），拿到像素级中间结果
    interim = [
        refine_one(im, grid_snap=grid_snap, grid_size=grid_size,
                   target_size=target_size, sample_method=sample_method,
                   palette=None, palette_name=palette_name,
                   transparent_bg=transparent_bg)
        for im in images
    ]
    if palette_name in ("auto", "") and colors:
        name, pal = palette_pipe.resolve_palette(
            palette_name, colors, [r["image"] for r in interim])
    else:
        name, pal = palette_pipe.resolve_palette(palette_name, colors, None)

    out: List[Dict[str, Any]] = []
    for r in interim:
        src = r["image"]
        im = src
        if pal:
            # apply_palette 只处理 RGB，alpha 需要从原图拼回后重新二值化
            rgb_img = palette_pipe.apply_palette(src.convert("RGB"), pal)
            # 量化映射后可能又冒出新孤立色，再清一次
            rgb_img = Image.fromarray(_clean_specks(np.asarray(rgb_img)), "RGB")
            if src.mode == "RGBA":
                im = rgb_img.convert("RGBA")
                im.putalpha(src.getchannel("A"))
                im = palette_pipe.binarize_alpha(im)
            else:
                im = rgb_img
            m = r["meta"]
            m.update({
                "palette": name,
                "paletteSize": len(pal),
                "colors": len(im.convert("RGB").getcolors(maxcolors=1 << 24) or []),
                "offPalette": palette_pipe.count_out_of_palette(im, pal),
            })
        out.append({"image": im, "meta": r["meta"]})
    return {"items": out, "palette": pal, "paletteName": name}
