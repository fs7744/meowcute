# -*- coding: utf-8 -*-
"""RGBA 的「脏 alpha」清理：近透明区 alpha 归零、RGB 清零。

为什么需要这个模块（2026-10-23 实测）：透明底生成的图（Qwen RGBA 输出）
背景区的 alpha 常常不是 0，而是 1-8，RGB 是 VAE 留下的垃圾值。实测一张
四视图拼板里 **51.3% 的像素是「alpha 1-8 + 品红 RGB」**，alpha=0 的区是黑色。
任何下游只要丢掉 alpha 通道（`convert("RGB")`、存 JPEG、用别的工具打开、
喂给只吃 RGB 的模型），这片垃圾就变成可见的品红/黑底——用户看到的就是
「视频背景莫名发紫」「拼板下载后打开是花的」。

清零之后，alpha=0 的像素在任何观图方式下都一致地表示「什么都没有」。
只动 alpha<8 的像素（不透明度 <3%，肉眼不可见），实心区与边缘抗锯齿不动。
"""
from typing import Any

import numpy as np

# alpha 低于此值一律归零（8/255 ≈ 3% 不透明度，肉眼不可见）
ALPHA_FLOOR = 8
# 高于此值一律拉满，避免 254 这种「差一点不透明」在合成时露出底色
ALPHA_CEIL = 248


def sanitize_alpha(img: Any) -> Any:
    """清理一张 RGBA 图的脏 alpha。非 RGBA 输入原样返回（零开销）。"""
    from PIL import Image

    if img is None or img.mode != "RGBA":
        return img
    a = np.asarray(img)
    if a.shape[2] < 4:
        return img
    alpha = a[:, :, 3]
    low = alpha < ALPHA_FLOOR
    high = alpha > ALPHA_CEIL
    if not low.any() and not high.any():
        return img
    a = a.copy()
    if low.any():
        # RGB 一起清零：透明区的颜色没有任何意义，留着就是泄漏源
        a[low, 0:3] = 0
        a[low, 3] = 0
    if high.any():
        a[high, 3] = 255
    return Image.fromarray(a, "RGBA")


def transparent_ratio(img: Any) -> float:
    """透明像素占比（0~1）。用于判断「这张图有没有真背景」。"""
    if img is None:
        return 0.0
    rgba = img if img.mode == "RGBA" else img.convert("RGBA")
    return float((np.asarray(rgba)[:, :, 3] < 16).mean())
