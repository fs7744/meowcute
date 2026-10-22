# -*- coding: utf-8 -*-
"""rigwalk3：爆炸图部件 → 套行走骨架 → GIF。

前情：
  rigwalk_spike  矩形裁切扣部件 —— 披风撕成斜条纹（失败）
  rigwalk2       最近骨骼段切割 —— 静止 100% 还原，一动关节露缝（到头了）
  exp_layered    Qwen-Image-Layered —— 拆的是合成层不是部件（此路不通）
  exp_exploded   edit-2511 爆炸图 —— 6 部件空间分离，连通域切块 ✓

本脚本把 .verify/exploded/ 的部件组装成行走动画：
  层序（远→近）：腿×2（藏躯干后）→ 躯干(含披风裤) → 臂×2 → 头
  躯干的白色裤腿当「裤子」，摆动腿件从裤脚后伸出（同色白，视觉连贯）。

产出：.verify/rigwalk3/idle.png + walk.gif + walk-strip.png
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

from PIL import Image

SRC = Path(__file__).resolve().parents[1] / ".verify" / "exploded"
OUT = Path(__file__).resolve().parents[1] / ".verify" / "rigwalk3"
OUT.mkdir(parents=True, exist_ok=True)

# ---- 布局参数（迭代调出来的，canvas 逻辑坐标） ----
CANVAS = (420, 460)
SCALE = 0.62          # 部件整体缩放
TORSO_POS = (210, 300)   # 躯干图块中心锚点（cape 上沿以下）
HEAD_POS = (210, 145)    # 头图块中心（底部与披风上沿轻微重叠）
HIP_Y = 318              # 腿摆动轴 y
HIP_DX = 46              # 腿轴相对中线的偏移
SHOULDER_Y = 178         # 臂摆动轴 y
SHOULDER_DX = 72
LEG_SWING = 20           # 腿摆幅（度）
ARM_SWING = 14           # 臂摆幅（度）
BOB = 4                  # 躯干+头上下起伏（px）
N_FRAMES = 8


def load_parts():
    parts = {}
    for i in range(10):
        p = SRC / f"part-{i}.png"
        if p.exists():
            parts[i] = Image.open(p).convert("RGBA")
    # 眼洞合并进头（眼睛 bbox 在头 bbox 内，按相对位置贴回）
    head = parts[1]
    hx0, hy0 = 249, 23          # 头在 exploded 原图里的 bbox
    for eye_i in (8, 9):
        if eye_i in parts:
            ex0, ey0, ex1, ey1 = (312, 126, 339, 174) if eye_i == 8 else (414, 126, 441, 174)
            eye = parts[eye_i].resize((ex1 - ex0, ey1 - ey0))
            head.alpha_composite(eye, (ex0 - hx0, ey0 - hy0))
    parts[1] = head
    return parts


def scaled(img):
    w, h = img.size
    return img.resize((int(w * SCALE), int(h * SCALE)), Image.LANCZOS)


def paste_rot(canvas, img, center, angle_deg):
    """以图块中心为轴旋转后贴到 canvas 的 center 处。"""
    im = img.rotate(angle_deg, resample=Image.NEAREST, expand=True)
    cw, ch = canvas.size
    w, h = im.size
    canvas.alpha_composite(im, (int(center[0] - w / 2), int(center[1] - h / 2)))


def frame(parts, f, strip=False):
    """f: 0..N_FRAMES-1。返回单帧 RGBA。"""
    ph = 2 * math.pi * f / N_FRAMES
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    bob = int(round(BOB * math.sin(2 * ph)))
    leg_a = LEG_SWING * math.sin(ph)
    arm_a = ARM_SWING * math.sin(ph + math.pi)

    # 腿（后层）：轴在髋部，图块挂在轴下方（中心在轴下 leg_len/2 处）
    leg_len = parts["leg"].size[1]
    leg_center_dy = leg_len * 0.42
    paste_rot(canvas, parts["leg"], (210 - HIP_DX, HIP_Y + leg_center_dy + bob), leg_a)
    paste_rot(canvas, parts["leg2"], (210 + HIP_DX, HIP_Y + leg_center_dy - bob), -leg_a)

    # 躯干
    paste_rot(canvas, parts["torso"], (TORSO_POS[0], TORSO_POS[1] + bob), 0)

    # 臂：挂肩部，中心在轴下臂长一半处
    arm_len = parts["arm"].size[1]
    arm_center_dy = arm_len * 0.42
    paste_rot(canvas, parts["arm"], (210 - SHOULDER_DX, SHOULDER_Y + arm_center_dy + bob), arm_a)
    paste_rot(canvas, parts["arm2"], (210 + SHOULDER_DX, SHOULDER_Y + arm_center_dy + bob), -arm_a)

    # 头（最上层）
    paste_rot(canvas, parts["head"], (HEAD_POS[0], HEAD_POS[1] + bob), 0)
    return canvas


def main():
    parts_raw = load_parts()
    torso = scaled(parts_raw[0])
    head = scaled(parts_raw[1])
    # 部件角色分配：part-2/4 最长 → 腿；part-3/5 → 臂
    leg, leg2 = scaled(parts_raw[2]), scaled(parts_raw[4])
    arm, arm2 = scaled(parts_raw[5]), scaled(parts_raw[3])

    parts = {"torso": torso, "head": head, "leg": leg, "leg2": leg2,
             "arm": arm, "arm2": arm2}
    print("[i] 部件尺寸：", {k: v.size for k, v in parts.items()})

    # 静止姿势调试图
    idle = frame(parts, 0)
    idle.convert("RGB").save(OUT / "idle.png")

    frames = [frame(parts, f) for f in range(N_FRAMES)]
    # 末帧回跳首帧的循环：N_FRAMES 帧已含一个完整正弦周期
    frames[0].convert("RGB").save(OUT / "walk.gif", save_all=True,
                                  append_images=[f.convert("RGB") for f in frames[1:]],
                                  duration=120, loop=0)

    # 帧条带
    W = sum(f.width for f in frames) + 4 * (len(frames) - 1)
    strip = Image.new("RGB", (W, frames[0].height), (40, 44, 52))
    x = 0
    for f in frames:
        strip.paste(f.convert("RGB"), (x, 0), f)
        x += f.width + 4
    strip.save(OUT / "walk-strip.png")
    print(f"DONE 输出 {OUT}")


if __name__ == "__main__":
    main()
