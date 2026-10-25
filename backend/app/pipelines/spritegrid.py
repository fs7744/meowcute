"""任意帧 → 精灵图（网格拼板 + 引擎索引）。

和 `spritesheet.py` 的区别：那条是八方向行走的专用拼板（按方向分组、每方向
一张横条、外加八方向总览）。这里是通用拼板：**一串用户自己挑出来的帧** →
一张网格精灵图 + 一份索引 JSON。

复用 `spritesheet` 里三个已经踩过坑的实现（共享缩放 / 透明区 RGB 清零 /
交付前逐格自检），补上通用拼板需要的两件事：

1. **帧间距与画布外边距**分开。市面工具（ezgif / TexturePacker）都这么做，
   原因不是审美：Unity 的 Sprite Editor「Automatic」切片靠**透明间隔**判断
   边界，帧紧贴时会把两帧并成一张。默认仍是 0（最省内存），但界面上要能
   一键给 2px。
2. **索引 JSON 双格式**。自带字段够简单（行列/单元格/帧表），同时附一段
   Phaser 3 (JSON Array) 兼容的 `textures`——这是市面图集工具事实上的通用
   交换格式，TexturePacker 导出的就是这个，Unity/Godot 也都有现成脚本吃它。

不做 extrude/bleed：那是紧密打包（MaxRects/旋转）才需要的东西，我们是规整
网格 + 单元格内固定 2px 透明边，本身不会串色。多一层推算只会让 jitter 更大。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod
from .spritesheet import (
    FRAME_PAD,
    frame_warnings,
    normalize_frames,
    zero_transparent_rgb,
)

ANCHOR_PIVOT = {"bottom": (0.5, 1.0), "center": (0.5, 0.5), "top": (0.5, 0.0)}
ANCHORS = tuple(ANCHOR_PIVOT)


def _place_cell(img: Image.Image, cell: int) -> Image.Image:
    """把已规整的帧贴到 cell×cell 单元格（尺寸不符时居中放，不缩放）。"""
    if img.size == (cell, cell):
        return img
    canvas = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
    canvas.alpha_composite(img, ((cell - img.width) // 2, (cell - img.height) // 2))
    return canvas


def auto_cols(n: int) -> int:
    """自动列数：让整图接近正方形（cols × rows ≈ 单元格数）。

    为什么不默认一行排完：21 帧 128px 的横条是 2736×136，纹理在 GPU 上的
    有效采样率极低（长宽比 20:1），引擎还得按 UV 偏移切；方形网格（5×5）
    的显存占用一样但采样友好。要横排精灵表（Godot hframes / Unity 单行）
    就在节点上显式给列数。
    """
    n = max(1, int(n))
    import math
    return max(1, min(n, math.ceil(math.sqrt(n))))


def frame_rects(
    count: int,
    *,
    cell: int,
    cols: int,
    padding: int = 0,
    margin: int = 0,
) -> List[Dict[str, int]]:
    """按网格布局算出每帧在图集里的矩形（与 compose_grid 同一套公式）。

    为什么单独抽出来：引擎导出（Godot SpriteFrames 的 region、Unity 切片）
    手上只有「精灵图 + 拼板参数」，没有 PIL 帧对象。要么让它自己按公式反算，
    要么重跑一次拼板——后者等于把同一张图再算一遍，还多一个走偏的机会。
    公式只写这一处，拼板和导出都从这里取，改布局不会出现两套不一致。
    """
    n = max(0, int(count))
    if not n:
        return []
    cell = max(1, int(cell))
    cols = max(1, min(int(cols) if cols and cols > 0 else auto_cols(n), n))
    pad = max(0, int(padding))
    mar = max(0, int(margin))
    out: List[Dict[str, int]] = []
    for i in range(n):
        c, r = i % cols, i // cols
        out.append({"i": i, "x": mar + c * (cell + pad),
                    "y": mar + r * (cell + pad), "w": cell, "h": cell})
    return out


def sheet_size(count: int, *, cell: int, cols: int, padding: int = 0,
               margin: int = 0) -> Tuple[int, int]:
    """同一套公式算整图尺寸（导出前用它校验精灵图真的对得上参数）。"""
    n = max(1, int(count))
    cols = max(1, min(int(cols) if cols and cols > 0 else auto_cols(n), n))
    rows = (n + cols - 1) // cols
    pad, mar = max(0, int(padding)), max(0, int(margin))
    return (cols * int(cell) + (cols - 1) * pad + mar * 2,
            rows * int(cell) + (rows - 1) * pad + mar * 2)


def compose_grid(
    frames: Sequence[Image.Image],
    *,
    cell: int,
    cols: int = 0,
    padding: int = 0,
    margin: int = 0,
) -> Tuple[Image.Image, List[Dict[str, Any]]]:
    """规整帧 → 网格拼板。返回 (sheet, 帧坐标表)。

    cols<=0 表示自动（接近正方形）；否则按列数折行。间距只出现在**相邻
    单元格之间**，外边距在整张图四周。
    """
    if not frames:
        raise ValueError("没有可合成的帧")
    n = len(frames)
    # cols<=0 = 自动（见 auto_cols）：整图接近正方形
    cols = int(cols) if cols and cols > 0 else auto_cols(n)
    cols = max(1, min(cols, n))
    w, h = sheet_size(n, cell=cell, cols=cols, padding=padding, margin=margin)
    sheet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    table = frame_rects(n, cell=cell, cols=cols, padding=padding, margin=margin)
    for i, f in enumerate(frames):
        t = table[i]
        sheet.alpha_composite(_place_cell(f, cell), (t["x"], t["y"]))
    return sheet, table


def verify_grid(
    sheet: Image.Image,
    frames: Sequence[Image.Image],
    *,
    cell: int,
    cols: int,
    padding: int = 0,
    margin: int = 0,
) -> Dict[str, Any]:
    """交付前自检：每格与源帧逐位一致，格子外（含间距/外边距）全透明。

    不复用 spritesheet.verify_composed：那个函数假设整表尺寸恰好是
    `单元格 × 行列`（没有间距/外边距），加了 padding 就会先撞「尺寸不符」
    直接返回 False——看着像拼板坏了，其实是检查器不认新布局。
    """
    n = len(frames)
    if not n:
        return {"cellsMatchFrames": False, "reason": "没有帧"}
    cols = max(1, min(int(cols), n))
    rows = (n + cols - 1) // cols
    pad, mar = max(0, int(padding)), max(0, int(margin))
    want_w = cols * cell + (cols - 1) * pad + mar * 2
    want_h = rows * cell + (rows - 1) * pad + mar * 2
    if sheet.size != (want_w, want_h):
        return {"cellsMatchFrames": False,
                "reason": f"尺寸不符：{sheet.size} != {(want_w, want_h)}"}
    s = np.asarray(sheet.convert("RGBA"))
    # 先确认「不该有内容的地方」确实空：整表 alpha 求和 == 各帧 alpha 求和
    total_a = int(s[:, :, 3].astype(np.int64).sum())
    src_a = sum(int(np.asarray(f.convert("RGBA"))[:, :, 3].astype(np.int64).sum())
                for f in frames)
    for i, f in enumerate(frames):
        c, r = i % cols, i // cols
        x = mar + c * (cell + pad)
        y = mar + r * (cell + pad)
        got = s[y:y + cell, x:x + cell]
        src = np.asarray(f.convert("RGBA"))
        if src.shape[0] != cell or src.shape[1] != cell:
            return {"cellsMatchFrames": False, "mismatchAt": i,
                    "reason": "源帧尺寸与单元格不一致（规整阶段漏了）"}
        if not np.array_equal(got, src):
            return {"cellsMatchFrames": False, "mismatchAt": i}
    if total_a != src_a:
        return {"cellsMatchFrames": False,
                "reason": f"格子外有多余像素：alpha 合计 {total_a} != {src_a}"}
    return {"cellsMatchFrames": True, "cells": n}


def build_index(
    table: List[Dict[str, Any]],
    *,
    cell: int,
    cols: int,
    rows: int,
    padding: int,
    margin: int,
    sheet: Image.Image,
    anchor: str,
    fps: float,
    image_name: str,
    trimmed: bool = False,
) -> Dict[str, Any]:
    """索引 JSON：自有字段 + Phaser 3 (JSON Array) 兼容段。"""
    px, py = ANCHOR_PIVOT.get(anchor, (0.5, 1.0))
    frames = []
    for t in table:
        frames.append({
            "filename": f"{t['i']}.png",
            "frame": {"x": t["x"], "y": t["y"], "w": t["w"], "h": t["h"]},
            "rotated": False,
            "trimmed": bool(trimmed),
            "spriteSourceSize": {"x": 0, "y": 0, "w": t["w"], "h": t["h"]},
            "sourceSize": {"w": t["w"], "h": t["h"]},
            "pivot": {"x": px, "y": py},
        })
    return {
        "version": "1.1",
        "generator": "pixelforge spritegrid",
        "image": image_name,
        "sheetSize": {"w": sheet.width, "h": sheet.height},
        "frameSize": {"w": cell, "h": cell},
        "grid": {"cols": cols, "rows": rows, "count": len(table),
                 "padding": padding, "margin": margin},
        "anchor": anchor,
        "pivot": {"x": px, "y": py},
        "fps": float(fps),
        "frames": table,
        # Phaser 3 / TexturePacker(JSON Array) 可直接 load.atlas 的段
        "textures": [{
            "image": image_name,
            "format": "RGBA8888",
            "size": {"w": sheet.width, "h": sheet.height},
            "scale": 1,
            "frames": frames,
        }],
    }


def run_grid(
    frames: Sequence[Image.Image],
    *,
    cell: int = 128,
    cols: int = 0,
    padding: int = 0,
    margin: int = 0,
    anchor: str = "bottom",
    shared_scale: bool = True,
    fps: float = 12.0,
    image_name: str = "spritesheet.png",
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any], Dict[str, Any]]:
    """一串帧 → (精灵图, 索引 JSON, 统计)。

    cell 是单元格边长；帧会按 cell 内部固定 2px 留白做共享缩放（抬腿的帧不
    会被放大），因此单元格内容不会贴边——Unity 自动切片时单元格之间即使
    padding=0 也有透明缝。
    """
    progress_mod.check_cancel(task_id)
    if not frames:
        raise RuntimeError("没有选中任何帧：先在上一步挑帧")
    cell = max(8, int(cell))
    anchor = anchor if anchor in ANCHORS else "bottom"

    progress_mod.report(task_id, 0, 3, "规整帧…")
    norm, nmeta = normalize_frames(
        list(frames), shared_scale=bool(shared_scale), size=cell,
        anchor=anchor, pad=FRAME_PAD)
    # 清零只做一次：compose / verify 都信任这个结果（见 spritesheet 注释）
    norm = [zero_transparent_rgb(f) for f in norm]

    progress_mod.check_cancel(task_id)
    progress_mod.report(task_id, 1, 3, "拼板…")
    cols_n = max(1, min(int(cols) if cols and cols > 0 else auto_cols(len(norm)),
                        len(norm)))
    sheet, table = compose_grid(norm, cell=cell, cols=cols_n,
                                padding=padding, margin=margin)
    rows_n = (len(norm) + cols_n - 1) // cols_n

    progress_mod.report(task_id, 2, 3, "自检…")
    checks = verify_grid(sheet, norm, cell=cell, cols=cols_n,
                         padding=padding, margin=margin)
    warnings = frame_warnings(norm)

    index = build_index(table, cell=cell, cols=cols_n, rows=rows_n,
                        padding=max(0, int(padding)), margin=max(0, int(margin)),
                        sheet=sheet, anchor=anchor, fps=fps,
                        image_name=image_name)
    meta = {
        "cell": cell,
        "cols": cols_n,
        "rows": rows_n,
        "count": len(norm),
        "padding": max(0, int(padding)),
        "margin": max(0, int(margin)),
        "anchor": anchor,
        "sharedScale": bool(shared_scale),
        "scaleRange": nmeta.get("scaleRange"),
        "sheetSize": [sheet.width, sheet.height],
        "checks": checks,
        "warnings": warnings,
        "fps": float(fps),
        "pivot": index["pivot"],
    }
    progress_mod.report(task_id, 3, 3, "完成")
    return sheet, index, meta
