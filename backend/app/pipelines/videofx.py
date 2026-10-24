# -*- coding: utf-8 -*-
"""videofx.py — 视频出片后处理：插帧（光流）+ 放大 2×。

为什么放在这里而不是下游节点：这两个操作都只依赖帧序列本身（不依赖角色、
不依赖方向），放「文本生视频 / 行走视频」节点的出口最直接；下游序列帧 /
精灵表节点直接吃到处理后的帧，不用各自再实现一遍。

插帧（interpolate2x）：OpenCV Farneback 稠密光流 + 双向变形混合。
  - 纯本地、确定性、零权重——github 不通、HF 权重拉不稳的环境下唯一可靠选项；
  - 小中幅运动（行走循环的主力场景）效果可用；大幅运动/快速遮挡会有伪影，
    UI 上如实标注，不冒充 AI 插帧（RIFE 类需要拉权重，当前网络不可行）。
  - 帧数翻倍（21 → 41），GIF 播放帧率不变时观感直接变顺。

放大（upscale2x）：2× 放大。
  - 像素素材：最近邻整数放大——像素图的「正确超分」，硬边不糊；
  - 非像素素材：Lanczos + USM 锐化——诚实标注为非 AI 超分（真正的 AI 超分
    需要 Real-ESRGAN/SwinIR 类权重，体积 1-2GB，当前网络不可达）。
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from PIL import Image, ImageFilter


def _to_gray(arr: np.ndarray) -> np.ndarray:
    if arr.ndim == 3:
        return np.asarray(Image.fromarray(arr).convert("L"))
    return arr


def _warp(img: np.ndarray, flow: np.ndarray) -> np.ndarray:
    """按 flow（dx, dy）变形 img。"""
    h, w = flow.shape[:2]
    xs, ys = np.meshgrid(np.arange(w, dtype=np.float32),
                         np.arange(h, dtype=np.float32))
    map_x = (xs + flow[..., 0]).astype(np.float32)
    map_y = (ys + flow[..., 1]).astype(np.float32)
    import cv2
    return cv2.remap(img, map_x, map_y, cv2.INTER_LINEAR,
                     borderMode=cv2.BORDER_REPLICATE)


def interpolate2x(frames: List[Image.Image]) -> List[Image.Image]:
    """帧序列 2× 插帧（光流双向变形混合）。输入至少 2 帧才动得了。"""
    import cv2

    if len(frames) < 2:
        return list(frames)
    out: List[Image.Image] = []
    for i in range(len(frames) - 1):
        a = frames[i].convert("RGB")
        b = frames[i + 1].convert("RGB")
        out.append(a)
        ga = np.asarray(_to_gray(np.asarray(a)), dtype=np.float32)
        gb = np.asarray(_to_gray(np.asarray(b)), dtype=np.float32)
        # 双向光流：a→b 与 b→a
        fab = cv2.calcOpticalFlowFarneback(ga, gb, None, 0.5, 3, 21, 3, 5,
                                           1.2, cv2.OPTFLOW_FARNEBACK_GAUSSIAN)
        fba = cv2.calcOpticalFlowFarneback(gb, ga, None, 0.5, 3, 21, 3, 5,
                                           1.2, cv2.OPTFLOW_FARNEBACK_GAUSSIAN)
        # 半程变形：a 前推 0.5 与 b 后推 0.5 加权混合
        wa = _warp(np.asarray(a), fab * 0.5).astype(np.float32)
        wb = _warp(np.asarray(b), fba * 0.5).astype(np.float32)
        # 一致性置信：两向流量互相矛盾处（遮挡/暴露）降低对应侧权重
        conf_a = np.exp(-np.abs(fab + _warp(fba, fab * 0.5)).sum(axis=2, keepdims=True) / 24.0)
        conf_b = np.exp(-np.abs(fba + _warp(fab, fba * 0.5)).sum(axis=2, keepdims=True) / 24.0)
        mid = (wa * conf_a + wb * conf_b) / np.maximum(conf_a + conf_b, 1e-6)
        out.append(Image.fromarray(np.clip(mid, 0, 255).astype(np.uint8), "RGB"))
    out.append(frames[-1].convert("RGB"))
    return out


def upscale2x(frames: List[Image.Image], pixel_art: bool = False) -> List[Image.Image]:
    """2× 放大：像素图最近邻整数放大，其余 Lanczos + USM 锐化。"""
    out: List[Image.Image] = []
    for f in frames:
        im = f.convert("RGB")
        w, h = im.size
        if pixel_art:
            out.append(im.resize((w * 2, h * 2), Image.NEAREST))
        else:
            big = im.resize((w * 2, h * 2), Image.LANCZOS)
            out.append(big.filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2)))
    return out


def postprocess(
    frames: List[Image.Image],
    *,
    interpolate: bool = False,
    upscale: bool = False,
    pixel_art: bool = False,
) -> Tuple[List[Image.Image], dict]:
    """按开关依次做后处理，返回 (帧序列, 处理记录 meta)。"""
    info = {"in": len(frames)}
    out = list(frames)
    if interpolate and len(out) >= 2:
        out = interpolate2x(out)
        info["interpolated"] = True
    if upscale:
        out = upscale2x(out, pixel_art=pixel_art)
        info["upscaled"] = True
    info["out"] = len(out)
    if out:
        info["size"] = list(out[0].size)
    return out, info
