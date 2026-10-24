# -*- coding: utf-8 -*-
"""rigwalk_ghibli：爆炸图部件 → 套行走骨架 → GIF（关节锚点版）。

历史与本次修正
--------------
  rigwalk_spike  单图估关节 + 矩形切件 —— 披风撕成斜条纹（失败）
  rigwalk2       FK 骨链 + 带状抠像 —— 静止完美，一动关节露缝
  rigwalk3       爆炸图部件 —— 切件干净，但腿/臂按「部件中心」摆放
  rigwalk_ghibli 同上 + 镜像补件 —— 画质被锚点语义卡住：
                 手臂整段压在胸前、摆起来是 X 形

**本版把「爆炸图部件」和「FK 关节锚点」拼起来**：部件来自爆炸图（切得干净），
旋转中心落在关节上（肩/髋 = 臂/腿的顶端中点，头 = 底端中点，躯干 = 顶端中点）。
这两件各自都已被验证过，组合后才是可用的纸娃娃。

另外加了 `--hold`：双手持枪/握把的角色不能大摆臂——两只手分属不同骨骼，
各转各的会"脱手"。hold 模式把摆幅压到 3°，并让双臂保持同向小晃动。

用法：
  python tools/rigwalk_ghibli.py [部件目录] [--hold] [--swing 18] [--arm 12]
  默认目录：.verify/ghibli/exploded-enhanced
产出：<OUT>/{idle.png, walk.gif, walk-strip.png, metrics.json}
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ROLE_ORDER = ["head", "torso", "arm_l", "arm_r", "leg_l", "leg_r"]
ROLE_CN = {"head": "头", "torso": "躯干", "arm_l": "左臂",
           "arm_r": "右臂", "leg_l": "左腿", "leg_r": "右腿"}
# 旋转中心：肢体绕近端关节（顶端），头绕颈（底端），躯干绕颈根（顶端）
PIVOT = {"head": "bottom", "torso": "top", "arm_l": "top",
         "arm_r": "top", "leg_l": "top", "leg_r": "top"}


def load_parts(src: Path, scale: float):
    meta_path = src / "parts.json"
    if not meta_path.is_file():
        raise SystemExit(f"缺少 parts.json：{meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))["roles"]
    parts, missing = {}, []
    for r in ROLE_ORDER:
        if r not in meta:
            missing.append(ROLE_CN[r])
            continue
        im = Image.open(src / meta[r]["file"]).convert("RGBA")
        w, h = im.size
        parts[r] = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    if missing:
        raise SystemExit(f"缺少角色部件：{'、'.join(missing)}（先跑 exp_exploded_parts.py）")
    return parts, meta


def paste_joint(canvas: Image.Image, img: Image.Image, joint, angle: float, pivot: str) -> None:
    """绕「关节」旋转后贴到画布上。

    做法：把部件垫到 2 倍高的透明画布上，让目标关节正好落在画布几何中心，
    旋转（expand）后中心不动，再把中心对齐到 joint 坐标。
    """
    w, h = img.size
    if pivot == "center":
        rot = img.rotate(angle, resample=Image.NEAREST, expand=True)
    else:
        pad = Image.new("RGBA", (w, 2 * h), (0, 0, 0, 0))
        # top: 部件贴下半 → pad 中心 = 部件顶端中点
        # bottom: 部件贴上半 → pad 中心 = 部件底端中点
        pad.alpha_composite(img, (0, h if pivot == "top" else 0))
        rot = pad.rotate(angle, resample=Image.NEAREST, expand=True)
    canvas.alpha_composite(rot, (int(round(joint[0] - rot.width / 2)),
                                 int(round(joint[1] - rot.height / 2))))


def derive_anchors(parts, canvas):
    """从躯干尺寸按比例推关节位置（换角色不用手调坐标）。"""
    tw, th = parts["torso"].size
    cx = canvas[0] / 2
    neck_y = canvas[1] * 0.30
    return {
        "cx": cx, "neck": (cx, neck_y),
        "shoulder": (neck_y + th * 0.10, tw * 0.44),
        "hip": (neck_y + th * 0.56, tw * 0.16),
        "head_lift": th * 0.02,
        "tw": tw, "th": th,
    }


def _squash(img: Image.Image, ratio: float) -> Image.Image:
    """纵向压缩（顶端固定）：摆动腿略缩短，模拟屈膝并把腿根收进髋部。"""
    if ratio <= 0:
        return img
    w, h = img.size
    out = img.resize((w, max(1, int(h * (1 - ratio)))), Image.LANCZOS)
    # resize 是缩放，顶端会跟着上移；补透明行让顶端位置不变
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(out, (0, 0))
    return canvas


def build_frame(parts, f, n, a, hold: bool) -> Image.Image:
    ph = 2 * math.pi * f / n
    canvas = Image.new("RGBA", a["canvas"], (0, 0, 0, 0))
    bob = a["bob"] * math.sin(2 * ph)
    leg_a = a["leg"] * math.sin(ph)
    # hold：双臂几乎同步小晃（握把不脱手）；否则正常反向摆
    arm_a = (a["arm"] * 0.25 * math.sin(ph)) if hold else (a["arm"] * math.sin(ph + math.pi))

    cx = a["cx"]
    sh_y, sh_dx = a["shoulder"]
    hip_y, hip_dx = a["hip"]

    # 腿（后层，藏躯干后）：绕髋。髋点必须**跟着躯干同向**起伏——
    # 早先让两条腿反向错动，摆动侧髋部会露出翘边（腿根和躯干错位）。
    # 迈步要抬脚：前摆那条腿整体上移 lift·|sin|。
    lift = a.get("lift", 0.0)
    lift_l = lift * max(0.0, math.sin(ph))
    lift_r = lift * max(0.0, -math.sin(ph))
    sq = a.get("squash", 0.0)
    leg_l_img = _squash(parts["leg_l"], sq * abs(math.sin(ph)))
    leg_r_img = _squash(parts["leg_r"], sq * abs(math.sin(ph)))
    paste_joint(canvas, leg_l_img, (cx - hip_dx, hip_y + bob - lift_l),
                leg_a, PIVOT["leg_l"])
    paste_joint(canvas, leg_r_img, (cx + hip_dx, hip_y + bob - lift_r),
                -leg_a, PIVOT["leg_r"])
    # 躯干：绕颈根
    paste_joint(canvas, parts["torso"], (cx, a["neck"][1] + bob), 0, PIVOT["torso"])
    # 臂：绕肩
    paste_joint(canvas, parts["arm_l"], (cx - sh_dx, sh_y + bob),
                arm_a, PIVOT["arm_l"])
    paste_joint(canvas, parts["arm_r"], (cx + sh_dx, sh_y + bob),
                arm_a if hold else -arm_a, PIVOT["arm_r"])
    # 头：绕颈（底端中点），略微下压形成重叠，避免脖子断开
    paste_joint(canvas, parts["head"], (cx, a["neck"][1] + bob + a["head_lift"]),
                0, PIVOT["head"])
    return canvas


def measure(frames):
    """评估：面积稳定性 / 步幅 / 首尾闭合。全是确定性渲染下的可量化指标。"""
    arrs = [np.asarray(f.convert("L").resize((160, 200)), dtype=np.float32) for f in frames]
    alphas = [np.asarray(f.split()[-1].resize((160, 200)), dtype=np.float32) > 40 for f in frames]
    areas = np.array([m.sum() for m in alphas], dtype=np.float32)
    # 步幅：每帧「脚底带」的最左/最右像素 x 坐标
    feet = []
    for m in alphas:
        rows = np.nonzero(m.any(axis=1))[0]
        if len(rows) == 0:
            feet.append((0, 0))
            continue
        band = m[max(0, rows.max() - 6):rows.max() + 1]
        cols = np.nonzero(band.any(axis=0))[0]
        feet.append((int(cols.min()), int(cols.max())) if len(cols) else (0, 0))
    spread = max(abs(f[1] - f[0]) for f in feet) - min(abs(f[1] - f[0]) for f in feet)
    loop = float(np.abs(arrs[-1] - arrs[0]).mean())
    return {
        "area_mean": int(areas.mean()),
        "area_std_pct": round(float(areas.std() / max(1.0, areas.mean()) * 100), 2),
        "stride_px": int(spread),
        "loop_gap": round(loop, 2),
        "motion": round(float(np.mean([np.abs(arrs[i + 1] - arrs[i]).mean()
                                       for i in range(len(arrs) - 1)])), 2),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", nargs="?", default=str(ROOT / ".verify" / "ghibli" / "exploded-enhanced"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--swing", type=float, default=18.0, help="腿摆角（度）")
    ap.add_argument("--arm", type=float, default=12.0, help="臂摆角（度）")
    ap.add_argument("--bob", type=float, default=5.0, help="身体起伏（像素）")
    ap.add_argument("--lift", type=float, default=7.0, help="前摆腿抬起高度（像素，模拟迈步离地）")
    ap.add_argument("--squash", type=float, default=0.06,
                    help="摆动时腿的纵向压缩比例（模拟屈膝，同时把腿根收进髋部）")
    ap.add_argument("--scale", type=float, default=0.82)
    ap.add_argument("--canvas", default=None, help="WxH，默认按躯干尺寸自适应")
    ap.add_argument("--hold", action="store_true", help="握持模式：双臂小摆（双手持枪不脱手）")
    args = ap.parse_args()

    src = Path(args.src)
    out = Path(args.out) if args.out else (ROOT / ".verify" / "ghibli" / "rig")
    out.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    parts, meta = load_parts(src, args.scale)
    tw, th = parts["torso"].size
    if args.canvas:
        w, h = (int(x) for x in args.canvas.lower().split("x"))
        canvas = (w, h)
    else:
        canvas = (int(tw * 2.6), int(th * 2.6))
    anchors = derive_anchors(parts, canvas)
    anchors.update(canvas=canvas, leg=args.swing, arm=args.arm, bob=args.bob, lift=args.lift, squash=args.squash)

    print(f"[i] 部件：{ {ROLE_CN[r]: parts[r].size for r in ROLE_ORDER} }", flush=True)
    print(f"[i] 画布 {canvas}；锚点 neck={tuple(round(v) for v in anchors['neck'])} "
          f"shoulder_y={anchors['shoulder'][0]:.0f} hip_y={anchors['hip'][0]:.0f} "
          f"sh_dx={anchors['shoulder'][1]:.0f} hip_dx={anchors['hip'][1]:.0f}", flush=True)
    mirrored = [ROLE_CN[r] for r in ROLE_ORDER if (meta.get(r) or {}).get("mirrored")]
    if mirrored:
        print(f"[i] 镜像补件：{mirrored}", flush=True)
    if args.hold:
        print("[i] 握持模式：臂摆 ×0.25 且双臂同向", flush=True)

    frames = [build_frame(parts, f, args.frames, anchors, args.hold) for f in range(args.frames)]
    dt = time.time() - t0

    bbox = None
    for fr in frames:
        b = fr.split()[-1].getbbox()
        if b:
            bbox = b if bbox is None else (min(bbox[0], b[0]), min(bbox[1], b[1]),
                                           max(bbox[2], b[2]), max(bbox[3], b[3]))
    if bbox:
        pad = 8
        crop = (max(0, bbox[0] - pad), max(0, bbox[1] - pad), bbox[2] + pad, bbox[3] + pad)
        frames = [fr.crop(crop) for fr in frames]

    m = measure(frames)
    m.update(frames=args.frames, hold=args.hold, swing=args.swing, arm=args.arm, lift=args.lift, squash=args.squash,
             render_secs=round(dt, 3))
    (out / "metrics.json").write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")

    frames[0].save(out / "idle.png")
    frames[0].save(out / "walk.gif", save_all=True, append_images=frames[1:],
                   duration=110, loop=0, disposal=2)
    W = sum(f.width for f in frames) + 4 * (len(frames) - 1)
    strip = Image.new("RGB", (W, frames[0].height), (40, 44, 52))
    x = 0
    for f in frames:
        strip.paste(f.convert("RGB"), (x, 0), f)
        x += f.width + 4
    strip.save(out / "walk-strip.png")

    print(f"[m] 面积 {m['area_mean']}±{m['area_std_pct']}% 步幅 {m['stride_px']}px "
          f"闭合差 {m['loop_gap']} 帧间差 {m['motion']} | 渲染 {m['render_secs']}s", flush=True)
    print(f"DONE {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
