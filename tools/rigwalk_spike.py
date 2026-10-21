#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
2D 纸娃娃(骨骼化)行走 Spike —— 证明"骨骼动画代替视频抽帧"能根治画面不稳。

思路(完全不含扩散采样，动画循环里只有确定性仿射变换)：
  1. 载入一张正面全身角色(RGBA/白底)，抠出角色(alpha)
  2. 用标准人体比例先验在 bbox 上估关节(肩/肘/腕/髋/膝/踝)
  3. 沿关节把角色切成部件(头/躯干/大小臂/大小腿)各带 alpha
  4. 建两层骨链: 大腿-小腿、大臂-小臂，每块部件绕近端关节旋转
  5. 参数化行走关键帧(腿摆±A、膝屈B、手臂反向摆、躯干上下颠)
  6. 渲染 N 帧 -> 精灵条 PNG + 循环 GIF

与当前 Wan 视频方案的本质区别: 每个部件图在帧间完全不变，
只有骨头角度在变 -> 零闪烁、零不一致、循环无缝。

依赖: Pillow, numpy (项目已有)
"""
import math
import os
import sys
import numpy as np
from PIL import Image, ImageChops

SRC = sys.argv[1] if len(sys.argv) > 1 else "backend/_gpu_out/raw.png"  # 正面全身角色
OUT = ".verify/rigwalk"
N_FRAMES = 16
DEG = math.pi / 180.0

# ---- 行走参数(弧度) ----
A_THIGH = 26 * DEG     # 大腿前后摆幅
B_SHIN = 26 * DEG      # 小腿(膝)屈曲幅
A_ARM = 12 * DEG       # 大臂反向摆幅
B_FORE = 10 * DEG      # 小臂屈曲幅
BOB = 0.018            # 躯干上下颠(占身高比例)


def load_character(path):
    """载入并抠出角色: 角落自适应取背景色 -> 透明 alpha。"""
    im = Image.open(path).convert("RGB")
    a = np.array(im).astype(np.float32)
    h, w, _ = a.shape
    corners = np.stack([a[5, 5], a[5, -6], a[-6, 5], a[-6, -6]])
    bg = corners.mean(0)
    dist = np.linalg.norm(a - bg, axis=2)
    alpha = ((dist > 32).astype(np.float32) * 255.0)[:, :, None]
    out = np.concatenate([a, alpha], axis=2).astype(np.uint8)
    img = Image.fromarray(out, "RGBA")
    return img


def fg_bbox(img):
    a = np.array(img)[:, :, 3]
    ys, xs = np.where(a > 10)
    if len(xs) == 0:
        raise RuntimeError("抠图失败: 没有前景")
    return xs.min(), xs.max(), ys.min(), ys.max()


def joints(xl, xr, yt, yb):
    """按标准人体比例估关节(fraction of height from top)。"""
    H = yb - yt
    cx = (xl + xr) / 2.0
    sh = 0.155 * H          # 半肩宽
    hp = 0.10 * H           # 半髋宽
    return {
        "head":   (cx, yt + 0.065 * H),
        "neck":   (cx, yt + 0.16 * H),
        "shoulderL": (cx - sh, yt + 0.18 * H),
        "shoulderR": (cx + sh, yt + 0.18 * H),
        "elbowL": (cx - sh * 1.05, yt + 0.33 * H),
        "elbowR": (cx + sh * 1.05, yt + 0.33 * H),
        "wristL": (cx - sh * 1.1, yt + 0.45 * H),
        "wristR": (cx + sh * 1.1, yt + 0.45 * H),
        "hipL":   (cx - hp, yt + 0.50 * H),
        "hipR":   (cx + hp, yt + 0.50 * H),
        "kneeL":  (cx - hp * 1.0, yt + 0.73 * H),
        "kneeR":  (cx + hp * 1.0, yt + 0.73 * H),
        "ankleL": (cx - hp * 0.95, yt + 0.93 * H),
        "ankleR": (cx + hp * 0.95, yt + 0.93 * H),
        "shoulderY": yt + 0.18 * H,
        "hipY": yt + 0.50 * H,
        "H": H, "cx": cx,
    }


def slice_part(img, pivot, box, pad=0.02):
    """从角色图裁出一块部件, 记录 pivot 在裁图局部坐标。
    box: (x0,y0,x1,y1) 部件大致范围; pivot: (px,py) 近端关节世界坐标。
    pad 只给小边距: 大 pad 会把邻居部件像素裹进来, 旋转时形成鬼影四肢。
    关节处的连续性由 box 本身向父部件方向多伸 0.03H 保证。
    返回 (crop RGBA, local_pivot)。"""
    H = img.height
    padpx = int(pad * H)
    x0 = max(0, int(box[0]) - padpx)
    y0 = max(0, int(box[1]) - padpx)
    x1 = min(img.width, int(box[2]) + padpx)
    y1 = min(img.height, int(box[3]) + padpx)
    crop = img.crop((x0, y0, x1, y1))
    lp = (pivot[0] - x0, pivot[1] - y0)
    return crop, lp


def rot_about(img, pivot, angle_deg):
    """把 img 绕局部 pivot 旋转 angle_deg, 返回旋转后图与 pivot 新坐标。
    用大画布法避免 expand 数学: 把裁图贴到大画布使 pivot 在中心,
    旋转整画布(extend=FALSE), 再算 pivot 仍在同一中心位置。"""
    W, Hh = img.size
    pad = max(W, Hh)  # 足够大避免裁剪
    canvas = Image.new("RGBA", (W + 2 * pad, Hh + 2 * pad), (0, 0, 0, 0))
    cx, cy = pivot[0] + pad, pivot[1] + pad
    canvas.paste(img, (pad, pad))
    rot = canvas.rotate(angle_deg, center=(cx, cy), resample=Image.BICUBIC)
    return rot, (cx, cy)


def composite(out, rot_img, world_pivot, local_pivot):
    """把 rot_img 的 local_pivot 对齐到 world_pivot 后合成到 out。"""
    ox = int(round(world_pivot[0] - local_pivot[0]))
    oy = int(round(world_pivot[1] - local_pivot[1]))
    out.alpha_composite(rot_img, (ox, oy))


def build_parts(img, J):
    """切出所有部件 (crop, local_pivot)。"""
    parts = {}
    H = J["H"]
    # 头
    parts["head"] = slice_part(img, J["neck"],
        (J["cx"] - 0.10 * H, J["head"][1] - 0.04 * H,
         J["cx"] + 0.10 * H, J["neck"][1] + 0.02 * H))
    # 躯干(肩到髋)
    parts["torso"] = slice_part(img, J["neck"],
        (J["cx"] - 0.18 * H, J["shoulderY"] - 0.02 * H,
         J["cx"] + 0.18 * H, J["hipY"] + 0.04 * H))
    # 手臂: 大臂(肩->肘) + 小臂(肘->腕)
    parts["uarmL"] = slice_part(img, J["shoulderL"],
        (J["shoulderL"][0] - 0.05 * H, J["shoulderY"] - 0.03 * H,
         J["elbowL"][0] + 0.05 * H, J["elbowL"][1] + 0.03 * H))
    parts["farmL"] = slice_part(img, J["elbowL"],
        (J["elbowL"][0] - 0.05 * H, J["elbowL"][1] - 0.03 * H,
         J["wristL"][0] + 0.05 * H, J["wristL"][1] + 0.03 * H))
    parts["uarmR"] = slice_part(img, J["shoulderR"],
        (J["elbowR"][0] - 0.05 * H, J["shoulderY"] - 0.03 * H,
         J["shoulderR"][0] + 0.05 * H, J["elbowR"][1] + 0.03 * H))
    parts["farmR"] = slice_part(img, J["elbowR"],
        (J["wristR"][0] - 0.05 * H, J["elbowR"][1] - 0.03 * H,
         J["elbowR"][0] + 0.05 * H, J["wristR"][1] + 0.03 * H))
    # 腿: 大腿(髋->膝) + 小腿(膝->踝)
    parts["thighL"] = slice_part(img, J["hipL"],
        (J["hipL"][0] - 0.06 * H, J["hipY"] - 0.02 * H,
         J["kneeL"][0] + 0.06 * H, J["kneeL"][1] + 0.03 * H))
    parts["shinL"] = slice_part(img, J["kneeL"],
        (J["kneeL"][0] - 0.06 * H, J["kneeL"][1] - 0.03 * H,
         J["ankleL"][0] + 0.06 * H, J["ankleL"][1] + 0.03 * H))
    parts["thighR"] = slice_part(img, J["hipR"],
        (J["kneeR"][0] - 0.06 * H, J["hipY"] - 0.02 * H,
         J["hipR"][0] + 0.06 * H, J["kneeR"][1] + 0.03 * H))
    parts["shinR"] = slice_part(img, J["kneeR"],
        (J["ankleR"][0] - 0.06 * H, J["kneeR"][1] - 0.03 * H,
         J["hipR"][0] + 0.06 * H, J["ankleR"][1] + 0.03 * H))
    return parts


def render_frame(parts, J, t):
    """渲染第 t 帧(相位 0..1)。返回 RGBA 画布。"""
    H = J["H"]
    # 各骨头角度(弧度)
    thighL = A_THIGH * math.sin(2 * math.pi * t)
    thighR = A_THIGH * math.sin(2 * math.pi * t + math.pi)
    # 膝: 抬腿(小腿向后屈)在摆动期最大
    shinL = -B_SHIN * (0.5 + 0.5 * math.sin(2 * math.pi * t))
    shinR = -B_SHIN * (0.5 + 0.5 * math.sin(2 * math.pi * t + math.pi))
    uarmL = -A_ARM * math.sin(2 * math.pi * t)          # 手臂反向摆
    uarmR = -A_ARM * math.sin(2 * math.pi * t + math.pi)
    foreL = -B_FORE * (0.5 + 0.5 * math.cos(2 * math.pi * t))
    foreR = -B_FORE * (0.5 + 0.5 * math.cos(2 * math.pi * t + math.pi))
    head = 4 * DEG * math.sin(2 * math.pi * t)          # 头微点
    bob = BOB * H * abs(math.sin(2 * math.pi * t))       # 躯干颠

    canvas = Image.new("RGBA", (int(J["cx"] * 2) + 80, int(J["H"] * 1.15)), (0, 0, 0, 0))
    baseY = -bob  # 上颠为正偏移(画布上移)

    def place(name, pivot_world, ang):
        crop, lp = parts[name]
        rot, lp2 = rot_about(crop, lp, ang / DEG)
        composite(canvas, rot, (pivot_world[0], pivot_world[1] + baseY), lp2)

    # z-order: 后侧肢体先画(此处正面, 左右对称, 按 左后右前 近似)
    # 腿(远端先)
    place("thighL", J["hipL"], thighL)
    place("shinL", J["kneeL"], thighL + shinL)   # 小腿相对世界 = 大腿角+膝角
    place("thighR", J["hipR"], thighR)
    place("shinR", J["kneeR"], thighR + shinR)
    # 躯干
    place("torso", J["neck"], 0)
    # 手臂
    place("uarmL", J["shoulderL"], uarmL)
    place("farmL", J["elbowL"], uarmL + foreL)
    place("uarmR", J["shoulderR"], uarmR)
    place("farmR", J["elbowR"], uarmR + foreR)
    # 头
    place("head", J["neck"], head)
    return canvas


def main():
    os.makedirs(OUT, exist_ok=True)
    img = load_character(SRC)
    xl, xr, yt, yb = fg_bbox(img)
    # 收紧一点, 去掉白边
    J = joints(xl + 4, xr - 4, yt + 2, yb - 2)
    parts = build_parts(img, J)

    frames = []
    for i in range(N_FRAMES):
        t = i / N_FRAMES
        f = render_frame(parts, J, t)
        frames.append(f)

    # 裁到统一 bbox
    bbs = [f.getbbox() for f in frames]
    minx = min(b[0] for b in bbs); miny = min(b[1] for b in bbs)
    maxx = max(b[2] for b in bbs); maxy = max(b[3] for b in bbs)
    fw = maxx - minx; fh = maxy - miny
    fr = [f.crop((minx, miny, maxx, maxy)) for f in frames]

    # 精灵条
    strip = Image.new("RGBA", (fw * N_FRAMES, fh), (0, 0, 0, 0))
    for i, f in enumerate(fr):
        strip.paste(f, (i * fw, 0), f)
    strip.save(os.path.join(OUT, "walk-strip.png"))

    # 循环 GIF(间隔 90ms)
    gif = [f.convert("RGBA") for f in fr]
    gif[0].save(os.path.join(OUT, "walk.gif"), save_all=True,
                append_images=gif[1:] + [gif[0]], duration=90, loop=0,
                disposal=2)
    print("OK frames=%d strip=%s gif=%s" % (
        N_FRAMES, os.path.join(OUT, "walk-strip.png"),
        os.path.join(OUT, "walk.gif")))


if __name__ == "__main__":
    main()
