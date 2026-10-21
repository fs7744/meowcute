"""八方向行走 · 阶段三：精灵整图（规整 + 拼板 + 自检）。

输入是序列帧节点的每方向透明底帧，输出是每方向一张横排精灵表 + 一张
八方向总览图。

移植自归档项目的 sprite/sheet 两个模块，三个关键设计：

1. **共享缩放（shared_scale）**：先量所有帧的最大包围盒，再统一按它算缩放
   系数。逐帧各自撑满画布会让「腿抬起来」的帧包围盒更高、被放大，角色在
   整段循环里忽大忽小——一播就露馅。缩放系数在**所有方向之间也统一**：
   正面矮胖、侧面瘦高，各方向各自撑满会让转身时角色变形。
2. **透明区 RGB 清零**：alpha_composite 前把全透明像素的 RGB 置 0。透明区
   携带底色时，引擎做双线性采样或预乘 alpha 会渲出一层色晕，图集单元格
   之间也会互相串色。
3. **交付前自检**：合成表每个单元格与源帧逐位一致、空白区必须全透明。
   alpha_composite 是确定性的，正常情况下不可能不一致；真出现不一致说明
   有 bug——宁可在这里炸掉，也不把错表交出去。

锚点默认底部居中：行走循环里角色的脚要踩在同一条线上，上下漂移会像在
「飘」。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod
from .walkvideo import DIR_LABELS

# 总览图的方向排列顺序（顺时针）
OVERVIEW_ORDER = ("down", "down-left", "left", "up-left", "up", "up-right", "right", "down-right")


# --------------------------------------------------------------------------- #
# 规整
# --------------------------------------------------------------------------- #
def trim_alpha(img: Image.Image) -> Tuple[Image.Image, Tuple[int, int, int, int]]:
    """裁掉全透明边缘，返回裁剪图与包围盒。"""
    rgba = img.convert("RGBA")
    bbox = rgba.split()[-1].getbbox()
    if not bbox:
        return rgba, (0, 0, rgba.width, rgba.height)
    return rgba.crop(bbox), bbox


def _place(trimmed: Image.Image, size: int, scale: float, *,
           anchor: str = "bottom", pad: int = 2) -> Image.Image:
    """按给定缩放把裁剪图贴进 size×size 画布（最近邻，像素素材不能插值）。"""
    inner = max(1, size - pad * 2)
    nw = max(1, int(round(trimmed.width * scale)))
    nh = max(1, int(round(trimmed.height * scale)))
    nw, nh = min(nw, inner), min(nh, inner)
    scaled = trimmed.resize((nw, nh), Image.NEAREST)

    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x = (size - nw) // 2
    if anchor == "center":
        y = (size - nh) // 2
    elif anchor == "top":
        y = pad
    else:
        y = size - nh - pad
    canvas.alpha_composite(scaled, (x, y))
    return canvas


def normalize_frames(
    frames: List[Image.Image],
    *,
    shared_scale: bool = True,
    size: int = 128,
    anchor: str = "bottom",
    pad: int = 2,
    scale: Optional[float] = None,
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """把一摞透明帧规整成同尺寸精灵帧（裁空白 → 缩放 → 锚点对齐）。

    scale：外部给定的统一缩放（run_sheets 算的**全方向**系数）。给了就用它，
    不再按本方向的包围盒各自撑满——八个方向如果各算各的，转身时角色会忽大
    忽小（正面矮胖、侧面瘦高，各自撑满单元格就变形了）。
    """
    if not frames:
        return [], {"size": [size, size], "frames": 0}
    prepared = [trim_alpha(f) for f in frames]
    inner = max(1, size - pad * 2)

    if scale:
        common = float(scale)
    elif shared_scale:
        max_w = max((t.width for t, _ in prepared), default=1)
        max_h = max((t.height for t, _ in prepared), default=1)
        common = min(inner / max(1, max_w), inner / max(1, max_h))
    else:
        common = 0.0

    outs: List[Image.Image] = []
    scales: List[float] = []
    for trimmed, _bbox in prepared:
        if trimmed.width < 1 or trimmed.height < 1:
            outs.append(Image.new("RGBA", (size, size), (0, 0, 0, 0)))
            scales.append(0.0)
            continue
        scale = common or min(inner / trimmed.width, inner / trimmed.height)
        outs.append(_place(trimmed, size, scale, anchor=anchor, pad=pad))
        scales.append(float(scale))

    meta = {
        "size": [size, size],
        "frames": len(outs),
        "scaleRange": [round(min(scales), 3), round(max(scales), 3)] if scales else [],
        "sharedScale": bool(shared_scale),
        "anchor": anchor,
    }
    return outs, meta


# --------------------------------------------------------------------------- #
# 拼板（移植自归档 sheet.py）
# --------------------------------------------------------------------------- #
def zero_transparent_rgb(img: Image.Image) -> Image.Image:
    """把全透明像素的 RGB 置 0（alpha 不动）。没有透明像素时原样返回。"""
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    arr = np.asarray(rgba)
    if not (arr[:, :, 3] == 0).any():
        return rgba
    arr = arr.copy()
    arr[arr[:, :, 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def compose_sheet(frames: List[Image.Image], cols: int) -> Tuple[Image.Image, Dict[str, Any]]:
    """横排拼板 + 引擎索引 JSON（帧下标/坐标/尺寸）。"""
    if not frames:
        raise ValueError("没有可合成的帧")
    cols = max(1, int(cols))
    fw = max(f.width for f in frames)
    fh = max(f.height for f in frames)
    rows = (len(frames) + cols - 1) // cols
    sheet = Image.new("RGBA", (fw * cols, fh * rows), (0, 0, 0, 0))
    index = []
    for i, f in enumerate(frames):
        c, r = i % cols, i // cols
        frame = zero_transparent_rgb(f)
        sheet.alpha_composite(frame, (c * fw, r * fh))
        index.append({"i": i, "x": c * fw, "y": r * fh, "w": fw, "h": fh})
    return sheet, {
        "version": "1.0",
        "frame_size": {"w": fw, "h": fh},
        "sheet_size": {"w": sheet.width, "h": sheet.height},
        "grid": {"cols": cols, "rows": rows},
        "frames": index,
    }


def verify_composed(sheet: Image.Image, frames: List[Image.Image], cols: int) -> Dict[str, Any]:
    """自检合成表：每个单元格与源帧逐位一致，空白区全透明。"""
    cols = max(1, int(cols))
    if not frames:
        return {"cellsMatchFrames": False, "reason": "没有帧"}
    fw = max(f.width for f in frames)
    fh = max(f.height for f in frames)
    rows = (len(frames) + cols - 1) // cols
    if sheet.size != (fw * cols, fh * rows):
        return {"cellsMatchFrames": False, "reason": f"尺寸不符：{sheet.size}"}
    s = np.asarray(sheet.convert("RGBA"))
    for i, f in enumerate(frames):
        c, r = i % cols, i // cols
        cell = s[r * fh:(r + 1) * fh, c * fw:(c + 1) * fw]
        src = np.asarray(zero_transparent_rgb(f))
        if not np.array_equal(cell[:src.shape[0], :src.shape[1]], src):
            return {"cellsMatchFrames": False, "mismatchAt": i}
        rest = cell[src.shape[0]:, :]
        if rest.size and (rest[:, :, 3] != 0).any():
            return {"cellsMatchFrames": False, "mismatchAt": i, "reason": "空白区不透明"}
    return {"cellsMatchFrames": True, "cells": len(frames)}


# --------------------------------------------------------------------------- #
# 帧级警告（没有结论的指标等于没算，警告要能指到第几帧）
# --------------------------------------------------------------------------- #
def frame_warnings(frames: List[Image.Image]) -> List[str]:
    out: List[str] = []
    for i, f in enumerate(frames):
        a = np.asarray(f.convert("RGBA"))
        if not (a[:, :, 3] > 0).any():
            out.append(f"第 {i + 1} 帧轮廓为空：抠图把整帧抠没了，检查该帧")
            continue
        bbox = f.getchannel("A").getbbox()
        if bbox and (bbox[0] <= 0 or bbox[1] <= 0
                     or bbox[2] >= f.width or bbox[3] >= f.height):
            out.append(f"第 {i + 1} 帧角色贴到画布边缘：可能被裁切，加大单元格尺寸")
    if len(frames) == 1:
        out.append("只有一帧：这是静态图，不构成循环动画")
    return out


# --------------------------------------------------------------------------- #
# 总览图
# --------------------------------------------------------------------------- #
def overview(sheets: Dict[str, Image.Image], *, cols: int = 4, gap: int = 10,
             bg: Tuple[int, int, int] = (24, 26, 32)) -> Image.Image:
    """把各方向的精灵表排成一张总览（默认 4 列 × 2 行 = 八方向）。"""
    items = [(d, sheets[d]) for d in OVERVIEW_ORDER if d in sheets]
    items += [(d, s) for d, s in sheets.items() if d not in OVERVIEW_ORDER]
    if not items:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    cw = max(s.width for _, s in items)
    ch = max(s.height for _, s in items)
    rows = (len(items) + cols - 1) // cols
    out = Image.new("RGBA", (cw * cols + gap * (cols + 1), ch * rows + gap * (rows + 1)),
                    bg + (255,))
    for i, (_d, s) in enumerate(items):
        c, r = i % cols, i // cols
        x = gap + c * (cw + gap) + (cw - s.width) // 2
        y = gap + r * (ch + gap) + (ch - s.height) // 2
        out.alpha_composite(s, (x, y))
    return out


# --------------------------------------------------------------------------- #
# 阶段入口
# --------------------------------------------------------------------------- #
async def run_sheets(
    directions: List[Dict[str, Any]],
    *,
    size: int = 128,
    cols: int = 0,
    shared_scale: bool = True,
    anchor: str = "bottom",
    task_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """每方向：规整（共享缩放）→ 横排拼板 → 自检；外加一张总览图。

    cols <= 0 表示一行排完（每方向一张横条）。
    """
    total = len(directions)
    out: List[Dict[str, Any]] = []
    done = 0

    # 全方向统一缩放：先量所有方向所有帧的最大包围盒，算一个系数。逐方向
    # 各自撑满的话，转身时角色会忽大忽小——这是精灵表最容易被一眼看出的毛病。
    global_scale: Optional[float] = None
    if shared_scale:
        max_w = max_h = 1
        for d in directions:
            for f in (d.get("frames") or []):
                t, _ = trim_alpha(f)
                max_w = max(max_w, t.width)
                max_h = max(max_h, t.height)
        inner = max(1, int(size) - 4)          # 与 normalize_frames 的 pad=2 一致
        global_scale = min(inner / max(1, max_w), inner / max(1, max_h))

    for d in directions:
        progress_mod.check_cancel(task_id)
        frames = list(d.get("frames") or [])
        if not frames:
            raise RuntimeError(f"{DIR_LABELS.get(d.get('dir'), d.get('dir'))}方向没有序列帧")
        n_cols = cols if cols and cols > 0 else max(1, len(frames))
        progress_mod.report(task_id, done, total,
                            f"{d.get('label') or DIR_LABELS.get(d.get('dir'), '')}：拼板中…")
        norm, nmeta = normalize_frames(
            frames, shared_scale=shared_scale, size=int(size), anchor=anchor,
            scale=global_scale)
        norm = [zero_transparent_rgb(f) for f in norm]
        sheet, index = compose_sheet(norm, n_cols)
        warnings = frame_warnings(norm)
        checks = verify_composed(sheet, norm, n_cols)
        done += 1
        progress_mod.report(task_id, done, total,
                            f"{d.get('label') or DIR_LABELS.get(d.get('dir'), '')}：精灵表完成")
        out.append({
            **d,
            "sheet": sheet,
            "index": index,
            "normalize": nmeta,
            "warnings": warnings,
            "checks": checks,
            "frames": norm,
            "count": len(norm),
        })

    sheets = {d["dir"]: d["sheet"] for d in out}
    ov = overview(sheets)
    meta = {
        "size": int(size),
        "sharedScale": bool(shared_scale),
        "globalScale": round(global_scale, 4) if global_scale else None,
        "anchor": anchor,
        "directions": len(out),
        "overviewSize": list(ov.size),
        "warnings": [w for d in out for w in d["warnings"]],
        "checksOk": all(d["checks"].get("cellsMatchFrames") for d in out),
    }
    return out, meta
