"""骨骼行走渲染：6 个部件 → 8 帧行走循环（纯 CPU，确定性，无扩散采样）。

这层解决的是「视频扩散做不到的事」：帧间一致性。同一组部件 + 同一段相位，
渲染结果永远一样，不存在闪烁/肢体变形/角色漂移。

关键实现（这两点各自都踩过坑）：
  1. **绕关节旋转**，不是绕部件中心。部件垫到 2 倍高透明画布上，让关节落在画布
     几何中心，旋转（expand）后中心不动 → 肩/髋/颈取部件顶端，头取底端。
     早期版本按中心摆，手臂整段压在胸前、摆起来是 X 形。
  2. **髋点跟着躯干同向起伏**，且前摆腿额外抬起。早先让两条腿反向错动，摆动侧
     髋部会露出翘边（腿根和躯干错位）。
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

ROLES = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r")
ROLE_CN = {"head": "头", "torso": "躯干", "arm_l": "左臂",
           "arm_r": "右臂", "leg_l": "左腿", "leg_r": "右腿"}
# 旋转中心：肢体绕近端关节（顶端），头绕颈（底端），躯干绕颈根（顶端）
PIVOT = {"head": "bottom", "torso": "top", "arm_l": "top",
         "arm_r": "top", "leg_l": "top", "leg_r": "top"}

# 方向 profile：不同视角的**绘制层级**和**视差**。
#   正面：腿在躯干后、臂在躯干前（常见的正面 cutout 排法）
#   背面：臂在躯干后（看到的是后背，手臂被躯干挡住）
#   侧面：远侧肢体（_r）先画、再躯干、再近侧（_l）；两侧肢体横向几乎重合，
#         只留一点视差，否则会看出"两条手臂并排"（正面视角的破功点）
LAYER_ORDER = {
    "front": ("leg_l", "leg_r", "torso", "arm_l", "arm_r", "head"),
    "back": ("arm_l", "arm_r", "leg_l", "leg_r", "torso", "head"),
    "side": ("leg_r", "arm_r", "torso", "leg_l", "arm_l", "head"),
}
# 侧视时肩/髋的横向分离系数（1 = 正面全展开；0.22 ≈ 侧面几乎重合）
SIDE_SQUEEZE = 0.22
# 侧视时远侧肢体相对躯干的额外横向视差（占躯干宽度比例）
SIDE_PARALLAX = 0.055


def load_parts(parts_dir: Path, scale: float = 0.82) -> Tuple[Dict[str, Image.Image], Dict[str, Any]]:
    """读 parts.json + 各部件 PNG。缺件直接报错（不静默降级）。"""
    import json

    meta_path = Path(parts_dir) / "parts.json"
    if not meta_path.is_file():
        raise RuntimeError(f"缺少 parts.json：{meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8")).get("roles") or {}
    parts, missing = {}, []
    for r in ROLES:
        if r not in meta:
            missing.append(ROLE_CN[r])
            continue
        im = Image.open(Path(parts_dir) / meta[r]["file"]).convert("RGBA")
        w, h = im.size
        parts[r] = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    if missing:
        raise RuntimeError(f"缺少角色部件：{'、'.join(missing)}")
    return parts, meta


def load_weapon(parts_dir: Path, meta: Dict[str, Any], scale: float):
    """武器是独立层（see-through 的 objects），按 PSD 原点贴回手上。

    不贴回去的话，正面就是「双臂抱胸」——因为原立绘的姿势是持枪。
    返回 (缩放后的图, 相对躯干原点的偏移) 或 None。
    """
    w = (meta or {}).get("weapon")
    if not w or not w.get("file"):
        return None
    fp = Path(parts_dir) / w["file"]
    if not fp.is_file():
        return None
    img = Image.open(fp).convert("RGBA")
    if scale != 1.0:
        img = img.resize((max(1, int(img.width * scale)), max(1, int(img.height * scale))),
                         Image.LANCZOS)
    to = (meta.get("torso") or {}).get("origin") or [0, 0]
    wo = w.get("origin") or [0, 0]
    return img, ((wo[0] - to[0]) * scale, (wo[1] - to[1]) * scale)


def paste_joint(canvas: Image.Image, img: Image.Image, joint, angle: float, pivot: str) -> None:
    """绕「关节」旋转后贴到画布上。

    把部件垫到 2 倍高的透明画布上，让目标关节落在画布几何中心；旋转后中心不动，
    再把中心对齐到 joint 坐标。这是「顶端/底端锚点」的实现方式。
    """
    w, h = img.size
    if pivot == "center":
        rot = img.rotate(angle, resample=Image.NEAREST, expand=True)
    else:
        pad = Image.new("RGBA", (w, 2 * h), (0, 0, 0, 0))
        pad.alpha_composite(img, (0, h if pivot == "top" else 0))
        rot = pad.rotate(angle, resample=Image.NEAREST, expand=True)
    canvas.alpha_composite(rot, (int(round(joint[0] - rot.width / 2)),
                                 int(round(joint[1] - rot.height / 2))))


def _squash(img: Image.Image, ratio: float) -> Image.Image:
    """纵向压缩（顶端固定）：摆动腿略缩短，模拟屈膝并把腿根收进髋部。"""
    if ratio <= 0:
        return img
    w, h = img.size
    out = img.resize((w, max(1, int(h * (1 - ratio)))), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.alpha_composite(out, (0, 0))
    return canvas


def derive_anchors(parts: Dict[str, Image.Image], canvas: Tuple[int, int]) -> Dict[str, Any]:
    """从躯干尺寸按比例推关节位置——换角色不用手调坐标。"""
    tw, th = parts["torso"].size
    cx = canvas[0] / 2
    neck_y = canvas[1] * 0.30
    return {
        "cx": cx, "neck": (cx, neck_y),
        # 肩宽 0.50（原 0.44）：0.44 时手臂落在背心内侧、视觉上"抱胸"
        "shoulder": (neck_y + th * 0.10, tw * 0.50),
        "hip": (neck_y + th * 0.56, tw * 0.16),
        "head_lift": th * 0.02,
        "tw": tw, "th": th,
    }


def build_frame(parts, f: int, n: int, a: Dict[str, Any], hold: bool = False,
                direction: str = "front") -> Image.Image:
    ph = 2 * math.pi * f / n
    canvas = Image.new("RGBA", a["canvas"], (0, 0, 0, 0))
    bob = a["bob"] * math.sin(2 * ph)
    leg_a = a["leg"] * math.sin(ph)
    # hold：双臂几乎同步小晃（双手握把不脱手）；否则正常反向摆
    arm_a = (a["arm"] * 0.25 * math.sin(ph)) if hold else (a["arm"] * math.sin(ph + math.pi))

    cx = a["cx"]
    sh_y, sh_dx = a["shoulder"]
    hip_y, hip_dx = a["hip"]
    side = direction == "side"
    if side:
        sh_dx *= SIDE_SQUEEZE
        hip_dx *= SIDE_SQUEEZE
    para = a["tw"] * SIDE_PARALLAX if side else 0.0

    lift = a.get("lift", 0.0)
    lift_l = lift * max(0.0, math.sin(ph))
    lift_r = lift * max(0.0, -math.sin(ph))
    sq = a.get("squash", 0.0) * abs(math.sin(ph))

    # 每个部件的贴图位置与角度；按 LAYER_ORDER 决定先后（远近）
    placed = {
        # 侧面：近侧腿（_l）走全幅、远侧腿（_r）反向 0.9 幅，两腿横向重合只留视差
        # → 才是"侧向跨步"的观感；远侧手臂几乎不可见，角度压到 0 免得露出手
        "leg_l": (_squash(parts["leg_l"], sq), (cx - hip_dx + para, hip_y + bob - lift_l),
                  leg_a),
        "leg_r": (_squash(parts["leg_r"], sq), (cx + hip_dx - para, hip_y + bob - lift_r),
                  -leg_a * 0.9 if side else -leg_a),
        "torso": (parts["torso"], (cx, a["neck"][1] + bob), 0.0),
        "arm_l": (parts["arm_l"], (cx - sh_dx + para, sh_y + bob),
                  arm_a * 0.7 if side else arm_a),
        "arm_r": (parts["arm_r"], (cx + sh_dx - para, sh_y + bob),
                  0.0 if side else (arm_a if hold else -arm_a)),
        "head": (parts["head"], (cx, a["neck"][1] + bob + a["head_lift"]), 0.0),
    }
    for role in LAYER_ORDER.get(direction, LAYER_ORDER["front"]):
        img, joint, ang = placed[role]
        paste_joint(canvas, img, joint, ang, PIVOT[role])

    # 武器最后画（在手上）：位置 = 躯干左上角 + PSD 里的相对偏移
    wp = a.get("weapon")
    if wp:
        wimg, (dx, dy) = wp
        tx = cx - a["tw"] / 2 + dx
        ty = a["neck"][1] + bob + dy
        canvas.alpha_composite(wimg, (int(round(tx)), int(round(ty))))
    return canvas


def measure(frames: List[Image.Image]) -> Dict[str, Any]:
    """确定性渲染的可量化指标：面积稳定性 / 步幅 / 首尾闭合 / 帧间差。"""
    arrs = [np.asarray(f.convert("L").resize((160, 200)), dtype=np.float32) for f in frames]
    alphas = [np.asarray(f.split()[-1].resize((160, 200)), dtype=np.float32) > 40 for f in frames]
    areas = np.array([m.sum() for m in alphas], dtype=np.float32)
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
    return {
        "areaMean": int(areas.mean()),
        "areaStdPct": round(float(areas.std() / max(1.0, areas.mean()) * 100), 2),
        "stridePx": int(spread),
        "loopGap": round(float(np.abs(arrs[-1] - arrs[0]).mean()), 2),
        "motion": round(float(np.mean([np.abs(arrs[i + 1] - arrs[i]).mean()
                                       for i in range(len(arrs) - 1)])), 2),
    }


def render(parts_dir: Path, *, frames: int = 8, swing: float = 13.0,
           arm: float = 12.0, bob: float = 5.0, lift: float = 7.0,
           squash: float = 0.06, scale: float = 0.82, hold: bool = False,
           direction: str = "front",
           canvas: Optional[Tuple[int, int]] = None) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """渲染一段行走循环，返回（帧列表, 指标）。"""
    import time

    t0 = time.time()
    parts, _meta = load_parts(parts_dir, scale)
    weapon = load_weapon(parts_dir, _meta, scale)
    tw, th = parts["torso"].size
    cvs = canvas or (int(tw * 2.6), int(th * 2.6))
    anchors = derive_anchors(parts, cvs)
    anchors.update(canvas=cvs, leg=swing, arm=arm, bob=bob, lift=lift, squash=squash,
                   weapon=weapon)

    fr = [build_frame(parts, i, frames, anchors, hold, direction) for i in range(frames)]
    bbox = None
    for f in fr:
        b = f.split()[-1].getbbox()
        if b:
            bbox = b if bbox is None else (min(bbox[0], b[0]), min(bbox[1], b[1]),
                                           max(bbox[2], b[2]), max(bbox[3], b[3]))
    if bbox:
        pad = 8
        crop = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
                bbox[2] + pad, bbox[3] + pad)
        fr = [f.crop(crop) for f in fr]

    m = measure(fr)
    m.update(frames=frames, hold=bool(hold), swing=swing, arm=arm, direction=direction,
             renderSecs=round(time.time() - t0, 3))
    return fr, m
