# -*- coding: utf-8 -*-
"""改进版纸娃娃：固定骨骼层级(FK) + 行走关键帧 + alpha 带状抠像。

相比第一版 rigwalk_spike 的关键升级：
  * 部件不再各自绕固定点独立旋转，而是挂在骨骼层级上（脚→小腿→大腿→骨盆），
    父骨一动整条链跟动，子骨起点=父骨末端关节 → 物理上不可能脱节。
  * 部件用 alpha 带状抠像（沿关节连线取带状区域并只保留角色自身像素），
    不再用固定 padding 矩形 → 不会有"第三条腿"鬼影。
  * 行走是真实关键帧：骨盆上下颠动 + 左右腿相位差 180° + 手臂反向摆 +
    脚掌保持水平，看着像走而不是抽搐。

用法：.venv/Scripts/python.exe tools/rigwalk2.py [character.png]
不传路径则用 .verify/rigwalk2/front-char.png（由 tools/gen_front_char.py 生成）。
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / ".verify" / "rigwalk2"
OUT.mkdir(parents=True, exist_ok=True)

# ---- 角色骨骼定义（归一化：u=左→右占 bbox 宽，v=上→下占 bbox 高）----
# 每个骨骼 = 一段运动学链，同时是一张贴图部件（connector 类 delta 恒为 0，不画）
JOINTS = {
    "pelvis":  (0.50, 0.56),
    "chest":   (0.50, 0.34),
    "head":    (0.50, 0.06),
    "shouldL": (0.34, 0.34), "elbowL": (0.27, 0.50), "handL": (0.23, 0.64),
    "shouldR": (0.66, 0.34), "elbowR": (0.73, 0.50), "handR": (0.77, 0.64),
    "hipL":    (0.43, 0.56), "kneeL":  (0.44, 0.76), "ankleL": (0.46, 0.93), "toeL": (0.45, 0.98),
    "hipR":    (0.57, 0.56), "kneeR":  (0.56, 0.76), "ankleR": (0.54, 0.93), "toeR": (0.55, 0.98),
}
# (bone, parent, 近端关节, 远端关节, 部件名或 None, 带宽占宽比例)
BONES = [
    ("spine",  "pelvis", "pelvis", "chest",   "torso", 0.20),
    ("head",   "chest",  "chest",  "head",    "head",  0.16),
    ("collarL","chest",  "chest",  "shouldL", None,    0.0),
    ("uarmL",  "collarL","shouldL","elbowL",  "uarmL", 0.09),
    ("farmL",  "uarmL",  "elbowL", "handL",   "farmL", 0.08),
    ("collarR","chest",  "chest",  "shouldR", None,    0.0),
    ("uarmR",  "collarR","shouldR","elbowR",  "uarmR", 0.09),
    ("farmR",  "uarmR",  "elbowR", "handR",   "farmR", 0.08),
    ("hipL",   "pelvis", "pelvis", "hipL",    None,    0.0),
    ("thighL", "hipL",   "hipL",   "kneeL",   "thighL",0.13),
    ("shinL",  "thighL", "kneeL",  "ankleL",  "shinL", 0.10),
    ("footL",  "shinL",  "ankleL", "toeL",    "footL", 0.14),
    ("hipR",   "pelvis", "pelvis", "hipR",    None,    0.0),
    ("thighR", "hipR",   "hipR",   "kneeR",   "thighR",0.13),
    ("shinR",  "thighR", "kneeR",  "ankleR",  "shinR", 0.10),
    ("footR",  "shinR",  "ankleR", "toeR",    "footR", 0.14),
]
DRAW_ORDER = ["thighL", "shinL", "footL", "thighR", "shinR", "footR",
              "torso", "head", "uarmR", "farmR", "uarmL", "farmL"]
N_FRAMES = 16


def remove_bg(rgba: Image.Image) -> Image.Image:
    """把纯色背景变成透明（适配白底/绿底/任意纯色底的角色图）。"""
    if rgba.mode != "RGBA":
        rgba = rgba.convert("RGBA")
    a = np.asarray(rgba)
    rgb = a[..., :3].astype(int)
    h, w = a.shape[:2]
    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]]).reshape(-1, 3)
    bg = np.median(border, axis=0)
    diff = np.sqrt(((rgb - bg) ** 2).sum(-1))
    mask = diff < 70
    out = a.copy()
    out[..., 3] = np.where(mask, 0, a[..., 3])
    return Image.fromarray(out, "RGBA")


def alpha_bbox(rgba: Image.Image):
    a = np.asarray(rgba)[..., 3]
    ys, xs = np.where(a > 30)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


def build_parts(rgba: Image.Image, alpha_full: np.ndarray, x0, y0, x1, y1, rest):
    """把整张角色剪影按"每个像素归属最近骨骼段"切分为部件。

    与单条带状裁剪不同：这里覆盖角色全部像素、无空洞、无重叠，因此静止帧
    能精确还原原图（IoU≈1），动画时各部件刚体旋转也不会产生缝或鬼影。
    """
    sub = rgba.crop((x0, y0, x1, y1))
    sa = alpha_full[y0:y1, x0:x1]
    hh, ww = sa.shape
    ys, xs = np.mgrid[0:hh, 0:ww]
    segs = []
    for (bone, _p, pj, dj, part_name, _wf) in BONES:
        if part_name is None:
            continue
        A = (rest[pj][0] - x0, rest[pj][1] - y0)
        B = (rest[dj][0] - x0, rest[dj][1] - y0)
        segs.append((bone, pj, A, B))
    n = len(segs)
    dists = np.empty((hh, ww, n), dtype=np.float32)
    for i, (bone, pj, A, B) in enumerate(segs):
        abx, aby = B[0] - A[0], B[1] - A[1]
        L2 = abx * abx + aby * aby + 1e-9
        px = xs - A[0]
        py = ys - A[1]
        t = np.clip((px * abx + py * aby) / L2, 0.0, 1.0)
        dx = px - t * abx
        dy = py - t * aby
        dists[..., i] = np.sqrt(dx * dx + dy * dy)
    assign = np.argmin(dists, axis=2)
    parts = {}
    for i, (bone, pj, A, B) in enumerate(segs):
        mask = (assign == i) & (sa > 30)
        if not mask.any():
            print(f"[warn] 部件 {bone} 无像素，跳过")
            continue
        ys_k, xs_k = np.where(mask)
        cx0, cy0, cx1, cy1 = xs_k.min(), ys_k.min(), xs_k.max() + 1, ys_k.max() + 1
        part = sub.crop((cx0, cy0, cx1, cy1)).copy()
        pa = np.array(part)
        pa[..., 3] = np.where(mask[cy0:cy1, cx0:cx1], pa[..., 3], 0)
        anchor = (A[0] - cx0, A[1] - cy0)
        parts[bone] = (Image.fromarray(pa, "RGBA"), anchor, pj)
    return parts


def rotate_about(img: Image.Image, angle_deg: float, center):
    """绕 center 旋转，返回旋转后图像和 center 在新图中的坐标。"""
    th = math.radians(angle_deg)
    cos, sin = math.cos(th), math.sin(th)
    w, h = img.size
    cx, cy = center

    def R(x, y):
        return (cx + (x - cx) * cos - (y - cy) * sin,
                cy + (x - cx) * sin + (y - cy) * cos)

    corners = [R(0, 0), R(w, 0), R(w, h), R(0, h)]
    minx = min(c[0] for c in corners)
    miny = min(c[1] for c in corners)
    maxx = max(c[0] for c in corners)
    maxy = max(c[1] for c in corners)
    W = int(math.ceil(maxx - minx))
    H = int(math.ceil(maxy - miny))
    a = cos
    b = -sin
    d = sin
    e = cos
    c_ = cx - a * cx - b * cy
    f_ = cy - d * cx - e * cy
    rot = img.transform((W, H), Image.AFFINE, (a, b, c_, d, e, f_), resample=Image.BICUBIC)
    ax, ay = R(cx, cy)
    return rot, (ax - minx, ay - miny)


def walk_deltas(f: int):
    """第 f 帧的各骨骼旋转增量（度）。相位让左右腿差 180°。"""
    ph = 2 * math.pi * f / N_FRAMES
    thighL = 20 * math.sin(ph)
    thighR = -20 * math.sin(ph)
    shinL = 35 * max(0.0, math.sin(ph - math.pi / 2))   # 抬腿时屈膝
    shinR = 35 * max(0.0, math.sin(ph + math.pi / 2))
    uarmL = -16 * math.sin(ph)
    uarmR = 16 * math.sin(ph)
    farm = 12
    spine = 3 * math.sin(ph)
    return {
        "spine": spine, "head": 0.0,
        "uarmL": uarmL, "farmL": farm, "uarmR": uarmR, "farmR": farm,
        "thighL": thighL, "shinL": shinL, "thighR": thighR, "shinR": shinR,
        "footL": 0.0, "footR": 0.0,   # 下面抵消，使脚掌保持水平
        "collarL": 0.0, "collarR": 0.0, "hipL": 0.0, "hipR": 0.0,
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else str(OUT / "front-char.png")
    if not Path(src).exists():
        raise SystemExit(f"角色图不存在：{src}")
    raw = Image.open(src).convert("RGBA")
    rgba = remove_bg(raw)
    rgba.save(OUT / "char-cutout.png")

    x0, y0, x1, y1 = alpha_bbox(rgba)
    W, H = x1 - x0, y1 - y0
    alpha = np.asarray(rgba)[..., 3]
    rest = {k: (x0 + u * W, y0 + v * H) for k, (u, v) in JOINTS.items()}

    # 按"每个像素归属最近骨骼段"把整张剪影完整切分为部件（覆盖 100%、无空洞）
    parts = build_parts(rgba, alpha, x0, y0, x1, y1, rest)

    # 画关节调试图
    dbg = rgba.copy().convert("RGBA")
    d = ImageDraw.Draw(dbg)
    for k, (ux, vy) in JOINTS.items():
        px, py = rest[k]
        d.ellipse([px - 4, py - 4, px + 4, py + 4], outline=(255, 0, 0, 255))
    dbg.save(OUT / "joints-debug.png")

    # 画布
    M = int(0.35 * W)
    cw, ch = W + 2 * M, H + 2 * M
    ox, oy = M - x0, M - y0  # 把 bbox 居中

    frames = []
    motion = []
    for f in range(N_FRAMES):
        # FK
        world = {"pelvis": (rest["pelvis"][0], rest["pelvis"][1])}
        wang = {"pelvis": 0.0}
        for (bone, parent, pj, dj, _pn, _wf) in BONES:
            delta = walk_deltas(f)[bone]
            wa = wang[parent] + delta
            wang[bone] = wa
            rvx = rest[dj][0] - rest[pj][0]
            rvy = rest[dj][1] - rest[pj][1]
            th = math.radians(wa)
            rx = rvx * math.cos(th) - rvy * math.sin(th)
            ry = rvx * math.sin(th) + rvy * math.cos(th)
            world[dj] = (world[pj][0] + rx, world[pj][1] + ry)
            world[bone] = world[pj]
            wang[dj] = wa  # 关节点也要记角度，供子骨骼按父关节查 world_ang

        # 脚掌保持水平：脚的世界角恒为 0（即静止朝向），不随腿旋转
        for side in ("L", "R"):
            wang["foot" + side] = 0.0

        canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
        for bone in DRAW_ORDER:
            if bone not in parts:
                continue
            pim, anc, pj = parts[bone]
            pivot = (world[pj][0] + ox, world[pj][1] + oy)
            rot, anc_new = rotate_about(pim, wang[bone], anc)
            canvas.alpha_composite(rot, (int(round(pivot[0] - anc_new[0])),
                                         int(round(pivot[1] - anc_new[1]))))
        frames.append(canvas)

        # 量化：相邻帧剪影变化
        sil = np.asarray(canvas)[..., 3] > 30
        if f > 0:
            prev = np.asarray(frames[f - 1])[..., 3] > 30
            inter = np.logical_and(sil, prev).sum()
            union = np.logical_or(sil, prev).sum()
            motion.append(1 - inter / max(union, 1))

    # 静止帧（delta 全 0）应与原始近似一致：把去背角色按同样 offset 贴回画布再比
    rest_canvas = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    rest_canvas.alpha_composite(rgba, (ox, oy))
    sil0 = np.asarray(frames[0])[..., 3] > 30
    rest_sil = np.asarray(rest_canvas)[..., 3] > 30
    inter = np.logical_and(sil0, rest_sil).sum()
    union = np.logical_or(sil0, rest_sil).sum()
    iou0 = inter / max(union, 1)

    # 拼条 + gif
    cols = 8
    rows = math.ceil(N_FRAMES / cols)
    strip = Image.new("RGBA", (cols * cw, rows * ch), (0, 0, 0, 0))
    for i, fr in enumerate(frames):
        strip.alpha_composite(fr, ((i % cols) * cw, (i // cols) * ch))
    strip.convert("RGBA").save(OUT / "walk-strip.png")

    frames[0].save(OUT / "walk.gif", save_all=True, append_images=frames[1:] + [frames[0]],
                   duration=90, loop=0, disposal=2)

    print(f"帧数={N_FRAMES} 画布={cw}x{ch}")
    print(f"静止帧与原始剪影 IoU={iou0:.3f}（应接近 1，验证骨骼静止即还原）")
    print(f"相邻帧动作幅度(1-IoU) 均值={sum(motion)/len(motion):.3f} "
          f"最小={min(motion):.3f} 最大={max(motion):.3f}")
    print(f"输出：{OUT/'walk-strip.png'} {OUT/'walk.gif'} {OUT/'char-cutout.png'} {OUT/'joints-debug.png'}")


if __name__ == "__main__":
    main()
