# -*- coding: utf-8 -*-
"""pcs — Part Contract Sheet v1 参考实现。

设计立场（一句话）：**动画是数据，不是代码**。
骨架用 glTF 2.0 的节点语义（相对父节点的 T/R/S 级联），动作用 glTF 的
channels/samplers 关键帧结构。没有正弦公式、没有 per-frame 手写渲染逻辑——
渲染器只做三件事：算节点世界矩阵、按时间采样通道、把部件贴到骨头上。

由此得到两个能力：
1. 同一副骨架 + 同一份动作，服务所有角色（换部件不换动作）；
2. 骨架/动作可以直接导出成 glTF，Blender / three.js / 任意引擎能打开。

三种输入统一到同一张输出帧表：
- render  静态部件（AI 出图）+ 骨架 → 变形出帧
- compose 逐帧素材（LPC spritesheet）→ 按契约裁切合成
- 两者的产物都是 PCS 帧表（行 = 动作 × 朝向，对齐 LPC 行语义）

用法见 README.md。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent

# ---------------------------------------------------------------- 分类缺省规则
# AI 出图不可能人工标锚点。分类 -> (挂哪根骨, 部件内归一化锚点, 默认 zPos)。
# 锚点口径：x 0..1 由左到右，y 0..1 由上到下。
CATEGORY_RULES: Dict[str, Tuple[str, Tuple[float, float], int]] = {
    # 头/躯干：挂点在近端（脖子/髋），部件从挂点向外生长
    "head":  ("head",  (0.5, 1.0), 50),
    "hair":  ("head",  (0.5, 0.5), 55),
    "face":  ("head",  (0.5, 0.5), 52),
    "ear":   ("head",  (0.5, 0.5), 51),
    "neck":  ("neck",  (0.5, 1.0), 35),
    "torso": ("spine", (0.5, 1.0), 40),
    "cape":  ("spine", (0.5, 0.0), 10),
    "arm_l": ("arm_l", (0.5, 0.0), 45),
    "arm_r": ("arm_r", (0.5, 0.0), 45),
    "leg_l": ("leg_l", (0.5, 0.0), 5),
    "leg_r": ("leg_r", (0.5, 0.0), 5),
    "weapon":   ("arm_r", (0.5, 0.5), 70),
    "accessory": ("spine", (0.5, 0.5), 60),
}

# LPC 视觉约定：朝向行序 down/left/right/up（拼板行序沿用这个顺序）
DIRECTIONS = ("down", "left", "right", "up")
DIR_INDEX = {d: i for i, d in enumerate(DIRECTIONS)}


# ---------------------------------------------------------------- 矩阵（3x3 仿射）
Mat = List[List[float]]


def m_identity() -> Mat:
    return [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


def m_mul(a: Mat, b: Mat) -> Mat:
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def m_trans(tx: float, ty: float) -> Mat:
    return [[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]]


def m_scale(sx: float, sy: float) -> Mat:
    return [[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]]


def m_rot(deg: float) -> Mat:
    """屏幕坐标（y 向下）下顺时针为正。"""
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def m_apply(m: Mat, x: float, y: float) -> Tuple[float, float]:
    return (m[0][0] * x + m[0][1] * y + m[0][2],
            m[1][0] * x + m[1][1] * y + m[1][2])


def m_inv(m: Mat) -> Mat:
    a, b, c = m[0]
    d, e, f = m[1]
    det = a * e - b * d
    if abs(det) < 1e-12:
        raise ValueError("矩阵不可逆（缩放为 0？）")
    ia, ib, ic = e / det, -b / det, (b * f - c * e) / det
    id_, ie, if_ = -d / det, a / det, (c * d - a * f) / det
    return [[ia, ib, ic], [id_, ie, if_], [0.0, 0.0, 1.0]]


def m_trs(tx: float, ty: float, rot: float, sx: float, sy: float) -> Mat:
    """glTF 的 T * R * S 组合顺序。"""
    return m_mul(m_mul(m_trans(tx, ty), m_rot(rot)), m_scale(sx, sy))


# ---------------------------------------------------------------- 数据装载
def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


class Rig:
    """glTF 风格的节点树：每个节点只有相对父节点的局部位移/旋转。"""

    def __init__(self, data: Dict[str, Any]):
        self.id = str(data.get("id") or "rig")
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self._order: List[str] = []
        for n in data.get("nodes") or []:
            name = str(n["name"])
            self.nodes[name] = {
                "parent": n.get("parent"),
                "t": [float(n["translation"][0]), float(n["translation"][1])],
                "rot": float(n.get("rotation") or 0.0),
                "length": float(n.get("length") or 0.0),
                "depth": float(n.get("depth") or 0.0),
            }
        self.category_bone = data.get("categoryBone") or {}
        # 拓扑排序：父永远在子之前（渲染时按序遍历即可）
        seen: Dict[str, int] = {}

        def visit(name: str, stack: Tuple[str, ...] = ()) -> None:
            if name in seen:
                return
            if name in stack:
                raise ValueError(f"骨架有环：{' -> '.join(stack + (name,))}")
            node = self.nodes.get(name)
            if node is None:
                raise ValueError(f"骨架引用了不存在的节点：{name}")
            parent = node["parent"]
            if parent:
                visit(str(parent), stack + (name,))
            seen[name] = 1
            self._order.append(name)

        for name in self.nodes:
            visit(name)

    def rule_for(self, category: str) -> Tuple[str, Tuple[float, float], int]:
        """分类 -> (骨名, 锚点, zPos)。骨架可覆盖，缺省用内置表。"""
        bone, anchor, z = CATEGORY_RULES.get(category, ("spine", (0.5, 0.5), 40))
        over = self.category_bone.get(category) or {}
        if over.get("bone"):
            bone = str(over["bone"])
        if over.get("anchor"):
            anchor = (float(over["anchor"][0]), float(over["anchor"][1]))
        if over.get("zPos") is not None:
            z = int(over["zPos"])
        if bone not in self.nodes:
            raise ValueError(f"分类 {category} 指向的骨骼不存在：{bone}")
        return bone, anchor, z

    def fit_measurements(self, m: Dict[str, float]) -> Dict[str, float]:
        """按部件实测比例重排关节（拓扑不变，只挪位置）。

        为什么必须这么做：AI 出的部件不是按某套固定比例画的（LPC 的图是人按
        同一具身体对齐画出来的，AI 不是）。硬套固定关节，结果就是腿够不到地、
        头浮在躯干上方。这里用实测的头/躯干/腿高度把关节摆到真实比例上。
        动作不受影响——动作全是归一化旋转与相对位移，与骨长无关，这正是
        「拓扑固定 + 比例自适应」能同时成立的原因。
        """
        head = float(m.get("head") or 0.0)
        torso = float(m.get("torso") or 0.0)
        leg = float(m.get("leg") or 0.0)
        total = head + torso + leg
        if total <= 0:
            return {}
        torso_f = torso / total
        hip = (head + torso) / total          # 髋在头顶往下的比例位置
        arm_dx = float(m.get("torso_w") or 0.0) * 0.40 / total
        leg_dx = float(m.get("leg_w") or 0.0) * 0.55 / total
        arm_dy = torso_f * 0.06
        self.nodes["root"]["t"] = [0.0, hip]
        self.nodes["spine"]["t"] = [0.0, 0.0]
        self.nodes["neck"]["t"] = [0.0, -torso_f]
        self.nodes["head"]["t"] = [0.0, 0.0]
        for side, sign in (("arm_l", -1.0), ("arm_r", 1.0)):
            self.nodes[side]["t"] = [sign * arm_dx, -torso_f + arm_dy]
            self.nodes[side]["length"] = torso_f * 1.0
        for side, sign in (("leg_l", -1.0), ("leg_r", 1.0)):
            self.nodes[side]["t"] = [sign * leg_dx, 0.0]
            self.nodes[side]["length"] = leg / total
        return {"hip": hip, "torso_f": torso_f, "arm_dx": arm_dx,
                "leg_dx": leg_dx, "leg_f": leg / total}

    def solve(self, pose: Dict[str, Dict[str, float]]) -> Dict[str, Mat]:
        """按父→子顺序算世界矩阵（角色空间，bodyHeight 为单位）。

        通道语义与 glTF 一致：**绝对值替换**，不是叠加。没被通道覆盖的节点用
        骨架里的静止值。
        """
        world: Dict[str, Mat] = {}
        for name in self._order:
            node = self.nodes[name]
            p = pose.get(name) or {}
            # 平移是「相对静止值的偏移」，不是绝对值——这样同一份动作能
            # 套到任何比例的角色上（关节位置是按部件实测比例自动贴合的）。
            tx = node["t"][0] + p.get("dx", 0.0)
            ty = node["t"][1] + p.get("dy", 0.0)
            rot = p.get("rot", node["rot"])
            sx, sy = p.get("sx", 1.0), p.get("sy", 1.0)
            local = m_trs(tx, ty, rot, sx, sy)
            parent = node["parent"]
            world[name] = local if not parent else m_mul(world[str(parent)], local)
        return world


class Part:
    def __init__(self, spec: Dict[str, Any], base: Path, rig: Rig):
        self.id = str(spec["id"])
        self.category = str(spec.get("category") or "accessory")
        self.image = Image.open(base / spec["image"]).convert("RGBA")
        # 裁到内容 bbox：AI 出图常带大片透明边，裁掉后锚点归一化才有意义
        box = self.image.getchannel("A").getbbox()
        if box:
            self.image = self.image.crop(box)
        self.w, self.h = self.image.size
        bone, d_anchor, d_z = rig.rule_for(self.category)
        self.bone = str(spec.get("bone") or bone)
        a = spec.get("anchor") or {}
        joint = a.get("joint") or list(d_anchor)
        self.anchor = (float(joint[0]) * self.w, float(joint[1]) * self.h)
        self.far = a.get("far")
        self.z = int(spec.get("zPos") if spec.get("zPos") is not None else d_z)
        self.facing = str(spec.get("facing") or "down")
        self.mirror = bool(spec.get("mirror") or False)
        self.tint = spec.get("tint")
        if self.tint:
            self.image = _tint(self.image, str(self.tint))
        if self.mirror:
            self.image = self.image.transpose(Image.FLIP_LEFT_RIGHT)
            self.anchor = (self.w - self.anchor[0], self.anchor[1])


def _tint(img: Image.Image, hex_color: str) -> Image.Image:
    """整层着色（保留 alpha），用于阴影层/受击闪白。"""
    c = hex_color.lstrip("#")
    r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    flat = Image.new("RGBA", img.size, (r, g, b, 255))
    return Image.composite(flat, Image.new("RGBA", img.size, (0, 0, 0, 0)),
                           img.getchannel("A"))


# ---------------------------------------------------------------- 动作采样（glTF）
def _lerp(a: float, b: float, u: float) -> float:
    return a + (b - a) * u


def _sample(sampler: Dict[str, Any], path: str, t: float, duration: float,
            loop: bool) -> Optional[Any]:
    times: List[float] = [float(x) for x in sampler.get("input") or []]
    out: List[Any] = list(sampler.get("output") or [])
    if not times or not out:
        return None
    mode = str(sampler.get("interpolation") or "LINEAR").upper()
    if mode == "CUBICSPLINE":
        # glTF: 每帧 3 个值（入切线/值/出切线）。这里只用值，切线交给导出器。
        out = out[1::3]
    if loop and duration > 0:
        t = t % duration
    # 末端外推：保持最后一帧（glTF 默认行为是 clamp）
    if t <= times[0]:
        return out[0]
    if t >= times[-1]:
        return out[-1]
    i = 0
    while i + 1 < len(times) and times[i + 1] < t:
        i += 1
    span = times[i + 1] - times[i]
    u = 0.0 if span <= 0 else (t - times[i]) / span
    a, b = out[i], out[i + 1]
    if mode == "STEP":
        return a
    if path == "rotation":
        return _lerp(float(a), float(b), u)
    return (_lerp(float(a[0]), float(b[0]), u), _lerp(float(a[1]), float(b[1]), u))


def anim_channels(anim: Dict[str, Any], direction: str) -> List[Dict[str, Any]]:
    """默认通道 + 朝向覆盖（按 node+path 合并，后者优先）。"""
    base = list(anim.get("channels") or [])
    over = ((anim.get("perDirection") or {}).get(direction) or {}).get("channels") or []
    merged: Dict[Tuple[str, str], Dict[str, Any]] = {
        (str(c["node"]), str(c["path"])): c for c in base
    }
    for c in over:
        merged[(str(c["node"]), str(c["path"]))] = c
    return list(merged.values())


def sample_pose(anim: Dict[str, Any], direction: str, t: float,
                available: Optional[Sequence[str]] = None) -> Dict[str, Dict[str, float]]:
    """采样某一时刻的姿态。

    available：这套部件实际拥有的朝向后备。部件没有侧视/背视时，侧视只能用
    正视动作档——否则会把正视的腿在平面内转开，两条腿交叉成 X（实测）。
    """
    duration = float(anim.get("duration") or 0.0)
    if duration <= 0:
        duration = max([max((c["sampler"].get("input") or [0])) for c in anim["channels"]] or [0])
    loop = bool(anim.get("loop", True))
    use_dir = direction
    if available is not None and direction not in available:
        use_dir = "down"
    pose: Dict[str, Dict[str, float]] = {}
    for ch in anim_channels(anim, use_dir):
        node, path = str(ch["node"]), str(ch["path"])
        val = _sample(ch["sampler"], path, t, duration, loop)
        if val is None:
            continue
        slot = pose.setdefault(node, {})
        if path == "rotation":
            slot["rot"] = float(val)
        elif path == "translation":
            slot["dx"], slot["dy"] = float(val[0]), float(val[1])
        elif path == "scale":
            slot["sx"], slot["sy"] = float(val[0]), float(val[1])
    return pose


def anim_frame_count(anim: Dict[str, Any]) -> int:
    fps = int(anim.get("fps") or 12)
    duration = float(anim.get("duration") or 0.0)
    if duration <= 0:
        duration = max([max((c["sampler"].get("input") or [0])) for c in anim["channels"]] or [0])
    return max(1, int(round(duration * fps)))


# ---------------------------------------------------------------- 渲染（静态部件 → 帧）
class Scene:
    """一次渲染的上下文：部件 + 骨架 + 帧画布 + 全局缩放。"""

    def __init__(self, manifest: Dict[str, Any], base: Path, rig: Rig,
                 frame: Optional[Tuple[int, int]] = None,
                 fit: Optional[float] = None):
        self.rig = rig
        f = manifest.get("frame") or {}
        self.fw, self.fh = int(frame[0]) if frame else int(f.get("w") or 128), \
            int(frame[1]) if frame else int(f.get("h") or 128)
        self.fit = float(fit if fit is not None else manifest.get("bodyFit") or 0.9)
        self.parts = [Part(p, base, rig) for p in manifest["parts"]]
        # 全局等比缩放：所有部件共用一个 u，绝不逐件缩放。
        # 角色原始高度（部件像素）= 头 + 躯干 + 腿，这是唯一可靠的量。
        self.body_px = self._estimate_body_px()
        self.body_h = self.fh * self.fit
        # u 只做「部件像素 → bodyHeight 单位」的换算（1 单位 = 角色身高）。
        # 再乘 to_px 里的 body_h 就得到帧像素——两段各管一次，绝不重复乘。
        self.u = 1.0 / max(1.0, self.body_px)
        # 关节按实测比例贴合（拓扑来自骨架文件，位置来自部件本身）
        self.measure = self._measure_parts()
        self.fit_info = self.rig.fit_measurements(self.measure)
        # 这套部件真实拥有的朝向（用于动画档回退）
        self.facings = sorted({p.facing for p in self.parts})
        # 角色空间 -> 帧像素：x 以帧中线为 0，y 顶部留白后按 body_h 映射
        self.pad = (self.fh - self.body_h) / 2.0
        self.to_px = m_mul(m_trans(self.fw / 2.0, self.pad), m_scale(self.body_h, self.body_h))

    def _estimate_body_px(self) -> float:
        m = self._measure_parts()
        total = m["head"] + m["torso"] + m["leg"]
        if total <= 0:
            raise ValueError("无法估算角色高度：至少需要 head/torso/leg 三类部件")
        return float(total)

    def _measure_parts(self) -> Dict[str, float]:
        """量部件得到真实比例（像素）。缺哪类就用同类近似，绝不用固定常数。"""
        by: Dict[str, List[Part]] = {}
        for p in self.parts:
            by.setdefault(p.category, []).append(p)

        def h_of(*cats: str) -> float:
            vals = [p.h for c in cats for p in by.get(c, [])]
            return float(max(vals)) if vals else 0.0

        def w_of(*cats: str) -> float:
            vals = [p.w for c in cats for p in by.get(c, [])]
            return float(max(vals)) if vals else 0.0

        head = h_of("head", "hair", "face")
        torso = h_of("torso")
        leg = h_of("leg_l", "leg_r")
        return {"head": head, "torso": torso, "leg": leg,
                "torso_w": w_of("torso"), "leg_w": w_of("leg_l", "leg_r"),
                "head_w": w_of("head", "hair")}

    def render(self, pose: Dict[str, Dict[str, float]]) -> Image.Image:
        world = self.rig.solve(pose)
        canvas = Image.new("RGBA", (self.fw, self.fh), (0, 0, 0, 0))
        for part in sorted(self.parts, key=lambda p: (p.z, p.id)):
            m = m_mul(m_mul(m_mul(self.to_px, world[part.bone]), m_scale(self.u, self.u)),
                      m_trans(-part.anchor[0], -part.anchor[1]))
            canvas.alpha_composite(_warp(part.image, m, canvas.size))
        return canvas


def _warp(img: Image.Image, m: Mat, size: Tuple[int, int]) -> Image.Image:
    inv = m_inv(m)
    coeffs = (inv[0][0], inv[0][1], inv[0][2], inv[1][0], inv[1][1], inv[1][2])
    return img.transform(size, Image.AFFINE, coeffs, resample=Image.BICUBIC)


# ---------------------------------------------------------------- 帧表
def build_sheet(scene: Scene, anims: Dict[str, Dict[str, Any]], directions: Sequence[str],
                out_dir: Path, sheet_name: str = "sheet") -> Dict[str, Any]:
    """整张标准帧表：行 = 动作 × 朝向，列 = 帧。"""
    cols = max(anim_frame_count(a) for a in anims.values())
    rows: List[Tuple[str, str]] = []
    row_map: Dict[str, Dict[str, Any]] = {}
    for aid, anim in anims.items():
        start = len(rows)
        for d in directions:
            rows.append((aid, d))
        row_map[aid] = {"row": start, "frames": anim_frame_count(anim),
                        "fps": int(anim.get("fps") or 12),
                        "loop": bool(anim.get("loop", True))}
    fw, fh = scene.fw, scene.fh
    sheet = Image.new("RGBA", (cols * fw, len(rows) * fh), (0, 0, 0, 0))
    previews: Dict[str, List[Image.Image]] = {}
    for aid, anim in anims.items():
        fps = int(anim.get("fps") or 12)
        n = anim_frame_count(anim)
        for d in directions:
            r = row_map[aid]["row"] + list(directions).index(d)
            frames: List[Image.Image] = []
            for i in range(n):
                frame = scene.render(sample_pose(anim, d, i / fps, scene.facings))
                sheet.alpha_composite(frame, (i * fw, r * fh))
                frames.append(frame)
            previews[f"{aid}.{d}"] = frames
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(out_dir / f"{sheet_name}.png")
    manifest = {
        "pcs": "1.0",
        "sheet": f"{sheet_name}.png",
        "frame": {"w": fw, "h": fh},
        "cols": cols,
        "directions": list(directions),
        "anims": {k: {**v, "rowSpan": len(directions)} for k, v in row_map.items()},
        "rows": [{"anim": a, "direction": d, "row": i} for i, (a, d) in enumerate(rows)],
        "rig": scene.rig.id,
        "parts": [{"id": p.id, "category": p.category, "bone": p.bone, "zPos": p.z}
                  for p in scene.parts],
    }
    (out_dir / f"{sheet_name}.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"sheet": sheet, "manifest": manifest, "previews": previews}


def save_gifs(previews: Dict[str, List[Image.Image]], out_dir: Path, fps_map: Dict[str, int]) -> List[str]:
    """每个（动作, 朝向）一段循环 GIF，便于肉眼验收。"""
    made = []
    for key, frames in previews.items():
        if not frames:
            continue
        aid = key.split(".")[0]
        delay = int(round(1000 / max(1, fps_map.get(aid, 12))))
        path = out_dir / f"{key}.gif"
        frames[0].save(path, save_all=True, append_images=frames[1:],
                       duration=delay, loop=0, disposal=2)
        made.append(str(path))
    return made


# ---------------------------------------------------------------- compose（吃逐帧素材）
def compose_lpc(layers: List[Tuple[Path, int]], anim: str, frames: int,
                directions: Sequence[str], frame: int, out_dir: Path,
                fps: int = 8, sheet_name: str = "lpc-sheet") -> Dict[str, Any]:
    """把现成的逐帧素材（LPC 单动画 spritesheet）按契约裁切合成。

    LPC 仓库里的 `spritesheets/<部件>/<动画>.png` 布局：**行 = 4 个朝向，
    列 = 帧**（walk 是 9 列 × 4 行 = 576×256 @64px）。这条路径证明同一张
    PCS 帧表既能装 AI 变形出来的帧，也能装现成手绘素材——组件层完全一致。
    """
    imgs = sorted([(Image.open(p).convert("RGBA"), z) for p, z in layers], key=lambda t: t[1])
    scale = frame / 64.0
    sheet = Image.new("RGBA", (frames * frame, len(directions) * frame), (0, 0, 0, 0))
    previews: Dict[str, List[Image.Image]] = {}
    for ri, d in enumerate(directions):
        seq: List[Image.Image] = []
        for col in range(frames):
            cell = Image.new("RGBA", (frame, frame), (0, 0, 0, 0))
            for im, _z in imgs:
                tile = im.crop((col * 64, ri * 64, col * 64 + 64, ri * 64 + 64))
                if scale != 1.0:
                    tile = tile.resize((frame, frame), Image.LANCZOS)
                cell.alpha_composite(tile)
            sheet.alpha_composite(cell, (col * frame, ri * frame))
            seq.append(cell)
        previews[f"{anim}.{d}"] = seq
    out_dir.mkdir(parents=True, exist_ok=True)
    sheet.save(out_dir / f"{sheet_name}.png")
    manifest = {
        "pcs": "1.0", "sheet": f"{sheet_name}.png",
        "frame": {"w": frame, "h": frame}, "cols": frames,
        "directions": list(directions),
        "anims": {anim: {"row": 0, "frames": frames, "fps": fps, "loop": True,
                         "rowSpan": len(directions)}},
        "rows": [{"anim": anim, "direction": d, "row": i} for i, d in enumerate(directions)],
        "source": "lpc-compose",
    }
    (out_dir / f"{sheet_name}.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"sheet": sheet, "manifest": manifest, "previews": previews}


# ---------------------------------------------------------------- DragonBones 导出
DB_FRAME_RATE = 12


def _shelf_pack(images: List[Tuple[str, Image.Image]], pad: int = 2) -> Tuple[Image.Image, Dict[str, Any]]:
    """简单的货架式图集打包（按高度降序排，逐行铺）。够用且无第三方依赖。"""
    items = sorted(images, key=lambda t: -t[1].height)
    width = max(256, max(im.width for _, im in items) * 2)
    x = y = row_h = 0
    boxes: Dict[str, List[int]] = {}
    for name, im in items:
        if x + im.width + pad > width:
            x, y, row_h = 0, y + row_h + pad, 0
        boxes[name] = [x, y, im.width, im.height]
        x += im.width + pad
        row_h = max(row_h, im.height)
    height = y + row_h
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for name, im in items:
        bx, by = boxes[name][0], boxes[name][1]
        atlas.alpha_composite(im, (bx, by))
    return atlas, boxes


def export_dragonbones(scene: Scene, anims: Dict[str, Dict[str, Any]],
                       directions: Sequence[str], out_dir: Path,
                       name: str = "pcs_character") -> Dict[str, Any]:
    """导出 DragonBones 5.5 数据 + 图集（MIT 运行时 DragonBonesJS 可直接加载）。

    字段对齐 ObjectData（armature/bone/slot/skin/animation）：
    - bone.transform 的 x/y 是相对父节点的位移，单位 = 图集像素；我们乘 body_h;
    - slot 的数组顺序即绘制顺序（靠后在上），对应我们的 zPos 升序；
    - skin 的 display 用 pivot 表达「关节在部件里的位置」，等价于我们的 anchor；
    - animation 的时间轴是**帧**为单位，因此统一重采样到 DB_FRAME_RATE。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    unit = scene.body_h                     # 1 bodyHeight = scene.body_h 图集像素
    part_scale = scene.u * unit             # 部件像素 → 图集像素

    bones = []
    for nname in scene.rig._order:
        n = scene.rig.nodes[nname]
        b: Dict[str, Any] = {"name": nname,
                             "transform": {"x": round(n["t"][0] * unit, 3),
                                           "y": round(n["t"][1] * unit, 3),
                                           "skX": round(n["rot"], 3), "skY": 0,
                                           "scX": 1, "scY": 1}}
        if n["parent"]:
            b["parent"] = str(n["parent"])
        if n["length"]:
            b["length"] = round(n["length"] * unit, 3)
        bones.append(b)

    ordered = sorted(scene.parts, key=lambda p: (p.z, p.id))
    slots = [{"name": p.id, "parent": p.bone, "displayIndex": 0, "zIndex": i}
             for i, p in enumerate(ordered)]
    skin_slots = []
    for p in ordered:
        skin_slots.append({
            "name": p.id,
            "display": [{
                "name": p.id, "path": p.id, "type": "image",
                "pivot": {"x": round(p.anchor[0] / max(1, p.w), 4),
                          "y": round(p.anchor[1] / max(1, p.h), 4)},
                "transform": {"x": 0, "y": 0, "skX": 0, "skY": 0,
                              "scX": round(part_scale, 4), "scY": round(part_scale, 4)},
            }],
        })

    # 动画：按统一帧率重采样，逐帧写关键帧（tweenEasing 0 = 线性）
    db_anims = []
    for aid, anim in anims.items():
        for d in directions:
            rate = DB_FRAME_RATE
            n = max(1, int(round(anim_frame_count(anim) * rate / max(1, int(anim.get("fps") or rate)))))
            tracks: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
            for i in range(n):
                pose = sample_pose(anim, d, i / rate, scene.facings)
                for bone_name, slot in pose.items():
                    if bone_name not in scene.rig.nodes:
                        continue
                    t = tracks.setdefault(bone_name, {})
                    if "rot" in slot:
                        t.setdefault("rotate", []).append(
                            {"duration": 1, "tweenEasing": 0, "rotate": round(slot["rot"], 3)})
                    if "dx" in slot or "dy" in slot:
                        t.setdefault("translate", []).append(
                            {"duration": 1, "tweenEasing": 0,
                             "x": round(slot.get("dx", 0.0) * unit, 3),
                             "y": round(slot.get("dy", 0.0) * unit, 3)})
                    if "sx" in slot or "sy" in slot:
                        t.setdefault("scale", []).append(
                            {"duration": 1, "tweenEasing": 0,
                             "x": round(slot.get("sx", 1.0), 4),
                             "y": round(slot.get("sy", 1.0), 4)})
            bone_tracks = []
            for bone_name, t in tracks.items():
                entry: Dict[str, Any] = {"name": bone_name}
                entry.update(t)
                bone_tracks.append(entry)
            db_anims.append({
                "name": aid if len(directions) == 1 else f"{aid}_{d}",
                "duration": n, "playTimes": 0,
                "bone": bone_tracks, "slot": [], "ffd": [],
            })

    aabb_w = scene.fw
    aabb_h = scene.fh
    data = {
        "frameRate": DB_FRAME_RATE,
        "name": name,
        "version": "5.5",
        "compatibleVersion": "5.5",
        "armature": [{
            "type": "Armature", "name": name, "frameRate": DB_FRAME_RATE,
            "aabb": {"x": 0, "y": 0, "width": aabb_w, "height": aabb_h},
            "bone": bones, "slot": slots,
            "skin": [{"name": "", "slot": skin_slots}],
            "ik": [], "animation": db_anims,
        }],
    }
    (out_dir / "dragonbones_data.json").write_text(
        json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")

    atlas_img, boxes = _shelf_pack([(p.id, p.image) for p in scene.parts])
    atlas_img.save(out_dir / "atlas.png")
    atlas = {
        "name": name, "image": "atlas.png", "Scale": 1,
        "SubTexture": [{"name": pid, "x": boxes[pid][0], "y": boxes[pid][1],
                        "width": boxes[pid][2], "height": boxes[pid][3]}
                       for pid in boxes],
    }
    (out_dir / "texture_atlas.json").write_text(
        json.dumps(atlas, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"data": str(out_dir / "dragonbones_data.json"),
            "atlas": str(out_dir / "atlas.png"),
            "bones": len(bones), "slots": len(slots), "animations": len(db_anims)}


# ---------------------------------------------------------------- 校验
def validate(manifest: Dict[str, Any], base: Path, rig: Rig) -> List[str]:
    errs: List[str] = []
    if str(manifest.get("pcs")) != "1.0":
        errs.append(f"pcs 版本应为 1.0，实际 {manifest.get('pcs')!r}")
    f = manifest.get("frame") or {}
    if not (isinstance(f.get("w"), int) and isinstance(f.get("h"), int)):
        errs.append("frame.w / frame.h 必须是整数")
    parts = manifest.get("parts") or []
    if not parts:
        errs.append("parts 不能为空")
    seen = set()
    for p in parts:
        pid = p.get("id")
        if not pid:
            errs.append(f"部件缺 id：{p}")
            continue
        if pid in seen:
            errs.append(f"部件 id 重复：{pid}")
        seen.add(pid)
        if p.get("category") not in CATEGORY_RULES:
            errs.append(f"{pid}: 未知分类 {p.get('category')!r}")
        img = base / str(p.get("image") or "")
        if not p.get("image") or not img.is_file():
            errs.append(f"{pid}: 图片不存在 {p.get('image')!r}")
        a = (p.get("anchor") or {}).get("joint")
        if a and not all(0.0 <= float(v) <= 1.0 for v in a):
            errs.append(f"{pid}: anchor.joint 必须在 0..1（实际 {a}）")
    for cat in ("head", "torso"):
        if not any(p.get("category") == cat for p in parts):
            errs.append(f"缺少 {cat} 部件（角色高度估算与姿态都依赖它）")
    return errs


# ---------------------------------------------------------------- CLI
ANIM_DIR = HERE / "anims"
RIG_DIR = HERE / "rigs"


def _load_anims(names: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    out = {}
    for n in names:
        p = ANIM_DIR / f"{n}.json"
        if not p.is_file():
            raise SystemExit(f"找不到动作：{p}")
        out[n] = load_json(p)
    return out


def _load_rig(manifest: Dict[str, Any]) -> Rig:
    rid = str(manifest.get("rig") or "biped.default")
    p = RIG_DIR / f"{rid}.json"
    if not p.is_file():
        raise SystemExit(f"找不到骨架：{p}")
    return Rig(load_json(p))


def cmd_validate(args: argparse.Namespace) -> int:
    base = Path(args.partset)
    manifest = load_json(base / "pcs.part.json")
    rig = _load_rig(manifest)
    errs = validate(manifest, base, rig)
    if errs:
        for e in errs:
            print("ERR", e)
        print(f"VALIDATE_FAIL {len(errs)}")
        return 1
    print(f"VALIDATE_OK {len(manifest['parts'])} 部件 / {len(rig.nodes)} 骨骼")
    return 0


def cmd_sheet(args: argparse.Namespace) -> int:
    base = Path(args.partset)
    manifest = load_json(base / "pcs.part.json")
    rig = _load_rig(manifest)
    errs = validate(manifest, base, rig)
    if errs:
        for e in errs:
            print("ERR", e)
        return 1
    frame = (args.frame, args.frame) if args.frame else None
    scene = Scene(manifest, base, rig, frame=frame, fit=args.fit)
    anims = _load_anims([a for a in args.anims.split(",") if a])
    dirs = [d for d in args.dirs.split(",") if d]
    out = Path(args.out) if args.out else base / "out"
    res = build_sheet(scene, anims, dirs, out)
    fps_map = {k: int(v.get("fps") or 12) for k, v in anims.items()}
    gifs = save_gifs(res["previews"], out, fps_map)
    print(f"[i] 帧 {scene.fw}x{scene.fh} 全局缩放 u={scene.u:.4f} 角色高 {scene.body_h:.0f}px")
    print(f"[i] 帧表 {res['sheet'].size[0]}x{res['sheet'].size[1]}，"
          f"{res['manifest']['cols']} 列 × {len(res['manifest']['rows'])} 行")
    print(f"[i] 预览 GIF {len(gifs)} 段")
    print(f"DONE {out}")
    return 0


def cmd_dragonbones(args: argparse.Namespace) -> int:
    base = Path(args.partset)
    manifest = load_json(base / "pcs.part.json")
    rig = _load_rig(manifest)
    scene = Scene(manifest, base, rig, frame=(args.frame, args.frame) if args.frame else None,
                  fit=args.fit)
    anims = _load_anims([a for a in args.anims.split(",") if a])
    out = Path(args.out) if args.out else base / "out" / "dragonbones"
    info = export_dragonbones(scene, anims, [d for d in args.dirs.split(",") if d], out,
                              name=str(manifest.get("id") or "pcs_character"))
    print(f"DB_OK {info}")
    return 0


def cmd_lpc(args: argparse.Namespace) -> int:
    layers = []
    for spec in args.layers.split(","):
        path, _, z = spec.partition(":")
        layers.append((Path(path), int(z or 40)))
    res = compose_lpc(layers, args.anim, args.frames,
                      [d for d in args.dirs.split(",") if d], args.frame, Path(args.out),
                      fps=args.fps)
    gifs = save_gifs(res["previews"], Path(args.out), {args.anim: args.fps})
    print(f"[i] LPC 合成 {res['sheet'].size} / {len(gifs)} 段 GIF")
    print(f"DONE {args.out}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="PCS v1 · 部件契约帧表")
    sub = ap.add_subparsers(dest="cmd", required=True)

    v = sub.add_parser("validate", help="校验部件集是否符合契约")
    v.add_argument("partset")
    v.set_defaults(func=cmd_validate)

    s = sub.add_parser("sheet", help="渲染标准帧表（动作 × 朝向）+ 预览 GIF")
    s.add_argument("partset")
    s.add_argument("--anims", default="idle,walk,run")
    s.add_argument("--dirs", default="down,left,right,up")
    s.add_argument("--frame", type=int, default=192, help="帧边长（64=LPC 兼容）")
    s.add_argument("--fit", type=float, default=None, help="角色高占帧高比例")
    s.add_argument("--out", default=None)
    s.set_defaults(func=cmd_sheet)

    g = sub.add_parser("dragonbones", help="导出 DragonBones 5.5 数据 + 图集（MIT 运行时可直接加载）")
    g.add_argument("partset")
    g.add_argument("--anims", default="idle,walk,run")
    g.add_argument("--dirs", default="down")
    g.add_argument("--frame", type=int, default=192)
    g.add_argument("--fit", type=float, default=None)
    g.add_argument("--out", default=None)
    g.set_defaults(func=cmd_dragonbones)

    l = sub.add_parser("lpc", help="按契约合成现成逐帧素材（LPC 单动画 spritesheet）")
    l.add_argument("--layers", required=True, help="png:zPos,...（每张是同一动画的完整表）")
    l.add_argument("--anim", default="walk")
    l.add_argument("--frames", type=int, default=9, help="每行帧数（LPC walk 是 9）")
    l.add_argument("--dirs", default="down,left,right,up")
    l.add_argument("--frame", type=int, default=64)
    l.add_argument("--fps", type=int, default=8)
    l.add_argument("--out", required=True)
    l.set_defaults(func=cmd_lpc)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
