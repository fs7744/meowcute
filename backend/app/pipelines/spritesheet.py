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


def _normalize_shared(frames: List[Image.Image], *, size: int, anchor: str,
                      pad: int, scale: Optional[float]
                      ) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """统一裁剪窗口 + 统一变换：所有帧用**同一个 crop 矩形**和**同一个缩放**落格。

    为什么必须这样（2026-10-26 实测踩到）：逐帧按自己的 bbox 居中，会让「衣摆/
    袖子摆动」变成整幅画面的左右位移——素材里头的横向量程只有 3.8px，逐帧居中
    之后放大成 **96.5px（25 倍）**，精灵图看起来就是角色在原地左右乱跳。
    根因是 bbox 中心随摆幅变化，而居中是拿它当参考的。

    换成「取所有帧 bbox 的并集当裁剪窗口」后：每帧裁的是同一块区域、缩放同一个
    系数、落在同一个位置——**逐帧重算这一步被彻底去掉**，角色在格子里的位移只
    来自素材本身（真实动作），不再被放大。

    代价：并集比任何单帧都大一点，角色在格子里会略小（换来的是不抖）。
    """
    inner = max(1, size - pad * 2)
    boxes = []
    for f in frames:
        b = f.convert("RGBA").split()[-1].getbbox()
        boxes.append(b)
    valid = [b for b in boxes if b]
    if not valid:
        return [Image.new("RGBA", (size, size), (0, 0, 0, 0)) for _ in frames], {
            "size": [size, size], "frames": len(frames), "scaleRange": [],
            "sharedScale": True, "anchor": anchor, "pivot": "shared"}
    ux0 = min(b[0] for b in valid)
    uy0 = min(b[1] for b in valid)
    ux1 = max(b[2] for b in valid)
    uy1 = max(b[3] for b in valid)
    uw, uh = max(1, ux1 - ux0), max(1, uy1 - uy0)
    # 外部给的 scale 是按**单帧**包围盒算的；换成并集窗口后可能超出内容区，
    # 此时等比回缩——绝不非等比拉伸，像素角色一变形就毁了。
    common = float(scale) if scale else min(inner / uw, inner / uh)
    if uw * common > inner or uh * common > inner:
        common = min(inner / uw, inner / uh)
    nw = max(1, int(round(uw * common)))
    nh = max(1, int(round(uh * common)))
    if nw > inner or nh > inner:
        f2 = inner / max(nw, nh)
        nw, nh = max(1, int(nw * f2)), max(1, int(nh * f2))
    x = (size - nw) // 2
    if anchor == "center":
        y = (size - nh) // 2
    elif anchor == "top":
        y = pad
    else:
        y = size - nh - pad

    outs: List[Image.Image] = []
    for f in frames:
        crop = f.convert("RGBA").crop((ux0, uy0, ux1, uy1))
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(crop.resize((nw, nh), Image.NEAREST), (x, y))
        outs.append(canvas)
    return outs, {
        "size": [size, size], "frames": len(outs),
        "scaleRange": [round(common, 3), round(common, 3)],
        "sharedScale": True, "anchor": anchor, "pivot": "shared",
        # 并集窗口在**源坐标系**里，导出/调试时能对照原视频定位
        "cropBox": [int(ux0), int(uy0), int(ux1), int(uy1)],
        "place": [int(x), int(y), int(nw), int(nh)],
    }


def normalize_frames(
    frames: List[Image.Image],
    *,
    shared_scale: bool = True,
    size: int = 128,
    anchor: str = "bottom",
    pad: int = 2,
    scale: Optional[float] = None,
    pivot: str = "shared",
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """把一摞透明帧规整成同尺寸精灵帧（裁空白 → 缩放 → 锚点对齐）。

    pivot：
      shared（默认，推荐）所有帧用同一个裁剪窗口与缩放，**逐帧不再重算居中**。
        角色在格子里的位移只来自素材本身的真实动作，不会因为摆幅变化被放大。
      per-frame  旧行为：每帧裁到自己的 bbox 再居中。角色被撑满格子，但衣摆/
        袖子一摆就会把整幅画面推来推去（实测放大 25 倍），只在「素材本身各帧
        完全静止、只想最大化利用率」时才用。

    scale：外部给定的统一缩放（run_sheets 算的**全方向**系数）。给了就用它，
    不再按本方向的包围盒各自撑满——八个方向如果各算各的，转身时角色会忽大
    忽小（正面矮胖、侧面瘦高，各自撑满单元格就变形了）。
    """
    if not frames:
        return [], {"size": [size, size], "frames": 0}
    same_canvas = len({f.size for f in frames}) == 1
    if pivot != "per-frame" and same_canvas:
        return _normalize_shared(frames, size=size, anchor=anchor, pad=pad, scale=scale)
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
# 单元格外沿留白（像素）：normalize_frames 按它算内容区，run_sheets 算全局
# 缩放系数时也要用同一个值。以前这里写死 `size - 4`，与 normalize_frames 的
# pad=2 相隔 170 行靠注释维持一致——任何一边改默认值，角色整体缩放会静默算错。
FRAME_PAD = 2


def zero_transparent_rgb(img: Image.Image) -> Image.Image:
    """把全透明像素的 RGB 置 0（alpha 不动）。没有透明像素时原样返回。"""
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    arr = np.asarray(rgba)
    if not (arr[:, :, 3] == 0).any():
        return rgba
    arr = arr.copy()
    arr[arr[:, :, 3] == 0, :3] = 0
    return Image.fromarray(arr, "RGBA")


def compose_sheet(frames: List[Image.Image], cols: int, *,
                  sanitized: bool = False) -> Tuple[Image.Image, Dict[str, Any]]:
    """横排拼板 + 引擎索引 JSON（帧下标/坐标/尺寸）。

    sanitized=True 表示调用方已经跑过 zero_transparent_rgb：run_sheets 在
    规整后清过一次，这里不再全图重扫（64 帧 × 3 遍全图拷贝纯属浪费）。
    """
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
        frame = f if sanitized else zero_transparent_rgb(f)
        sheet.alpha_composite(frame, (c * fw, r * fh))
        index.append({"i": i, "x": c * fw, "y": r * fh, "w": fw, "h": fh})
    return sheet, {
        "version": "1.0",
        "frame_size": {"w": fw, "h": fh},
        "sheet_size": {"w": sheet.width, "h": sheet.height},
        "grid": {"cols": cols, "rows": rows},
        "frames": index,
    }


def verify_composed(sheet: Image.Image, frames: List[Image.Image], cols: int, *,
                    sanitized: bool = False) -> Dict[str, Any]:
    """自检合成表：每个单元格与源帧逐位一致，空白区全透明。

    sanitized 同 compose_sheet：源帧已清零时直接比，不再扫第三遍。
    """
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
        src = np.asarray(f if sanitized else zero_transparent_rgb(f))
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
    cols = max(1, min(int(cols), len(items)))     # 方向少时别留一整排空白
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
def run_sheets(
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

    # 全方向统一缩放：先量所有方向所有帧的包围盒，算一个系数。逐方向
    # 各自撑满的话，转身时角色会忽大忽小——这是精灵表最容易被一眼看出的毛病。
    #
    # 用「每个方向的**并集**包围盒」而不是「单帧最大」：落格现在用统一裁剪窗口
    # （见 _normalize_shared），并集比任何单帧都大；若这里仍按单帧最大算系数，
    # 并集更大的方向会被迫等比回缩，于是各方向系数不再一致——等于把刚修好的
    # 「转身不变大小」又弄坏。
    global_scale: Optional[float] = None
    if shared_scale:
        inner = max(1, int(size) - FRAME_PAD * 2)
        union_w = union_h = 1
        for d in directions:
            boxes = [f.getchannel("A").getbbox() for f in (d.get("frames") or [])]
            boxes = [b for b in boxes if b]
            if not boxes:
                continue
            union_w = max(union_w, max(b[2] for b in boxes) - min(b[0] for b in boxes))
            union_h = max(union_h, max(b[3] for b in boxes) - min(b[1] for b in boxes))
        global_scale = min(inner / max(1, union_w), inner / max(1, union_h))

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
            scale=global_scale, pad=FRAME_PAD)
        # 清零只做一次，后面 compose / verify 都信任这个结果
        norm = [zero_transparent_rgb(f) for f in norm]
        sheet, index = compose_sheet(norm, n_cols, sanitized=True)
        warnings = frame_warnings(norm)
        checks = verify_composed(sheet, norm, n_cols, sanitized=True)
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
