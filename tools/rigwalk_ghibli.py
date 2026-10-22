# -*- coding: utf-8 -*-
"""rigwalk_ghibli：Ghibli 角色化部件（含镜像补件）→ 套行走骨架 → GIF。

读取 exp_exploded_parts.py --ghibli --enhance --mirror 产出的 parts.json
（role→PNG 映射）与角色化 PNG，自动从部件几何推导骨架锚点：

  层序（远→近）：腿×2（藏躯干后）→ 躯干 → 臂×2 → 头
  锚点全部由躯干/部件尺寸按比例推导，换角色无需手调坐标。

用法：
  .venv/Scripts/python.exe tools/rigwalk_ghibli.py [部件目录]
  默认目录：.verify/ghibli/exploded-enhanced
产出：.verify/ghibli/rig/{idle.png, walk.gif, walk-strip.png}
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / ".verify" / "ghibli" / "exploded-enhanced"
if len(sys.argv) > 1:
    SRC = Path(sys.argv[1])
OUT = ROOT / ".verify" / "ghibli" / "rig"
OUT.mkdir(parents=True, exist_ok=True)

CANVAS = (520, 700)
SCALE = 0.82
LEG_SWING = 18
ARM_SWING = 12
BOB = 5
N_FRAMES = 8
ROLE_ORDER = ["head", "torso", "arm_l", "arm_r", "leg_l", "leg_r"]
ROLE_CN = {"head": "头", "torso": "躯干", "arm_l": "左臂",
           "arm_r": "右臂", "leg_l": "左腿", "leg_r": "右腿"}


def load_role_parts(src):
    meta = json.loads((src / "parts.json").read_text(encoding="utf-8"))["roles"]
    parts = {}
    for r in ROLE_ORDER:
        if r not in meta:
            raise SystemExit(f"缺少角色部件：{ROLE_CN[r]}（{r}）")
        im = Image.open(src / meta[r]["file"]).convert("RGBA")
        w, h = im.size
        parts[r] = im.resize((int(w * SCALE), int(h * SCALE)), Image.LANCZOS)
    return parts, meta


def paste_rot(canvas, img, center, angle_deg):
    im = img.rotate(angle_deg, resample=Image.NEAREST, expand=True)
    canvas.alpha_composite(im, (int(center[0] - im.width / 2),
                                int(center[1] - im.height / 2)))


def frame(parts, f, anchors, bob_dir=1):
    ph = 2 * math.pi * f / N_FRAMES
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    bob = int(round(BOB * math.sin(2 * ph)) * bob_dir)
    leg_a = LEG_SWING * math.sin(ph)
    arm_a = ARM_SWING * math.sin(ph + math.pi)

    torso = parts["torso"]
    tw, th = torso.size
    tx, ty = anchors["torso"]
    # 腿（后层，藏躯干后）
    leg = parts["leg_l"]
    leg2 = parts["leg_r"]
    leg_dy = leg.height * 0.42
    paste_rot(canvas, leg, (tx - anchors["hip_dx"], ty + anchors["hip_y"] + leg_dy + bob),
              leg_a)
    paste_rot(canvas, leg2, (tx + anchors["hip_dx"], ty + anchors["hip_y"] + leg_dy - bob),
              -leg_a)
    # 躯干
    paste_rot(canvas, torso, (tx, ty + bob), 0)
    # 臂
    arm = parts["arm_l"]
    arm2 = parts["arm_r"]
    arm_dy = arm.height * 0.42
    paste_rot(canvas, arm, (tx - anchors["sh_dx"], ty + anchors["sh_y"] + arm_dy + bob),
              arm_a)
    paste_rot(canvas, arm2, (tx + anchors["sh_dx"], ty + anchors["sh_y"] + arm_dy + bob),
              -arm_a)
    # 头（最上层）
    head = parts["head"]
    paste_rot(canvas, head, (tx, ty - anchors["head_up"] + bob), 0)
    return canvas


def main():
    parts, meta = load_role_parts(SRC)
    torso = parts["torso"]
    tw, th = torso.size
    tx, ty = CANVAS[0] // 2, int(CANVAS[1] * 0.52)
    anchors = {
        "torso": (tx, ty),
        "head_up": int(th * 0.46),          # 头中心在躯干中心上方多少（颈重叠）
        "sh_y": int(th * 0.16),             # 肩轴相对躯干中心向下
        "sh_dx": int(tw * 0.40),            # 肩左右偏移
        "hip_y": int(th * 0.42),            # 髋轴相对躯干中心向下
        "hip_dx": int(tw * 0.17),           # 髋左右偏移
    }
    mirrored = [ROLE_CN[r] for r in ROLE_ORDER if meta.get(r, {}).get("mirrored")]
    print("[i] 部件尺寸：", {ROLE_CN[r]: parts[r].size for r in ROLE_ORDER})
    print(f"[i] 镜像补件：{mirrored or '无'}", flush=True)
    print(f"[i] 锚点推导：{anchors}", flush=True)

    idle = frame(parts, 0, anchors)
    idle.convert("RGB").save(OUT / "idle.png")

    frames = [frame(parts, f, anchors) for f in range(N_FRAMES)]
    frames[0].convert("RGB").save(
        OUT / "walk.gif", save_all=True,
        append_images=[f.convert("RGB") for f in frames[1:]],
        duration=140, loop=0)

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
