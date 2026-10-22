# -*- coding: utf-8 -*-
"""build_rig.py — 从爆炸图部件生成 Godot 4 可用的 2D 骨骼绑定。

设计（对齐 Godot 4 开源 cutout 工作流：Skeleton2D + Bone2D + Sprite2D 挂点）：
  - 骨骼层级：Root → Pelvis → Spine → Chest → {Neck → Head, ArmL, ArmR}
                                            Pelvis → {LegL, LegR}
  - 每个部件一张 Sprite2D（centered=false）挂在对应 Bone2D 下，节点位置 =
    -pivot，使关节锚点精确落在骨骼原点（Godot 语义：缩放/旋转绕锚点）。
  - 行走循环 = AnimationPlayer 8 关键帧（原地踏步，适合正面角色）：
      身体 bob（ Spine/Chest 上移）｜双腿交替抬踏（Lift y + 缩短 scale + 外摆）
      手臂反向泵动（对侧腿抬时手臂上摆+外展）｜头部反向滞后
  - 关键教训：正面角色不能像侧视那样左右摆腿（=劈叉），必须原地抬踏。

产出：
  godot_project/project.godot   最小 Godot 4 工程（gl_compatibility）
  godot_project/rig.tscn        Skeleton2D/Bone2D/Sprite2D + 内嵌 walk 动画
  godot_project/assets/*.png    裁掉透明边的部件贴图
  rig_data.json                 同一 rig 的结构化数据（render_preview.py 消费）
"""
from __future__ import annotations

import json
import math
import shutil
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent  # D:/code/pixelforge
SRC = ROOT / ".verify" / "ghibli" / "exploded-v3fix"
OUT = HERE / "godot_project"
ASSETS = OUT / "assets"

PARTS = {
    "head": "head.png", "torso": "torso.png",
    "arm_l": "arm_l.png", "arm_r": "arm_r.png",
    "leg_l": "leg_l.png", "leg_r": "leg_r.png",
}

S = 0.85                 # 部件整体缩放
CANVAS = (700, 920)      # 预览画布（Godot 端由 Skeleton 位置决定，不受此限）
N_KEYS = 8               # 动画关键帧数
KEY_DT = 0.11            # 每关键帧时长（秒）→ 循环 0.88s

LIFT = 16.0              # 抬腿幅度（px）
ARM_PUMP = 7.0           # 手臂泵动幅度（px）
BOB = 4.0                # 身体 bob 幅度（px）
LEG_OUT = 0.09           # 抬腿外摆角（弧度）
ARM_OUT = 0.16           # 手臂外展角（弧度）
LEG_SHORT = 0.07         # 抬腿时腿部缩短比例


def trim(im: Image.Image):
    """裁掉透明边，返回 (trimmed, pivot)。pivot 为关节锚点（trimmed 坐标）。"""
    a = im.getchannel("A")
    bbox = a.getbbox()
    if bbox is None:
        raise SystemExit("部件全透明？")
    t = im.crop(bbox)
    return t


def build():
    if ASSETS.exists():
        shutil.rmtree(ASSETS)
    ASSETS.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    # 1) 读取 + 裁剪部件，记录内容尺寸与锚点
    info = {}
    for role, fn in PARTS.items():
        im = Image.open(SRC / fn).convert("RGBA")
        t = trim(im)
        t.save(ASSETS / fn)
        w, h = t.size
        if role == "head":
            pivot = (w / 2, h)      # 颈底 = 头部下缘中点
        else:
            pivot = (w / 2, 0)      # 肩/髋 = 部件上缘中点
        info[role] = {"w": w * S, "h": h * S, "pivot": pivot, "file": fn}
        print(f"[part] {role:6s} crop={w}x{h} scaled={info[role]['w']:.0f}x{info[role]['h']:.0f} pivot={pivot}")

    legH = info["leg_l"]["h"]
    torsoH = info["torso"]["h"]
    headH = info["head"]["h"]
    torsoW = info["torso"]["w"]
    total = legH + torsoH + headH
    ground_y = (CANVAS[1] + total) / 2.0     # 角色垂直居中
    hip_y = ground_y - legH
    shoulder_y = hip_y - torsoH
    shoulder_dx = torsoW * 0.52
    hip_dx = torsoW * 0.22
    print(f"[layout] total={total:.0f} ground_y={ground_y:.0f} hip_y={hip_y:.0f} "
          f"shoulder_y={shoulder_y:.0f} shoulder_dx={shoulder_dx:.0f} hip_dx={hip_dx:.0f}")

    # 2) 骨骼层级（局部坐标，y 向下为负=向上；锚点见各 Sprite）
    #    rest 位置即场景初始姿态
    bones = [
        {"name": "Root",   "parent": "",        "pos": (0.0, 0.0)},
        {"name": "Pelvis", "parent": "Root",    "pos": (0.0, hip_y - ground_y)},
        {"name": "Spine",  "parent": "Pelvis",  "pos": (0.0, -torsoH * 0.45)},
        {"name": "Chest",  "parent": "Spine",   "pos": (0.0, -torsoH * 0.55)},
        {"name": "Neck",   "parent": "Chest",   "pos": (0.0, 2.0)},
        {"name": "Head",   "parent": "Neck",    "pos": (0.0, 2.0)},  # 颈底没入领口 4px
        {"name": "ArmL",   "parent": "Chest",   "pos": (-shoulder_dx, 0.0)},
        {"name": "ArmR",   "parent": "Chest",   "pos": (shoulder_dx, 0.0)},
        {"name": "LegL",   "parent": "Pelvis",  "pos": (-hip_dx, 0.0)},
        {"name": "LegR",   "parent": "Pelvis",  "pos": (hip_dx, 0.0)},
    ]
    # 3) 挂点绑定（绘制顺序即数组序：腿→躯干→臂→头）
    sprites = [
        {"name": "LegLSprite", "bone": "LegL", "role": "leg_l"},
        {"name": "LegRSprite", "bone": "LegR", "role": "leg_r"},
        {"name": "TorsoSprite", "bone": "Chest", "role": "torso"},
        {"name": "ArmLSprite", "bone": "ArmL", "role": "arm_l"},
        {"name": "ArmRSprite", "bone": "ArmR", "role": "arm_r"},
        {"name": "HeadSprite", "bone": "Head", "role": "head"},
    ]

    # 4) 行走循环关键帧（原地踏步）
    lift_l = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    lift_r = [0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0]
    frames = []
    for f in range(N_KEYS):
        ll, rr = lift_l[f], lift_r[f]
        bob = -BOB * (ll + rr)
        sway = 0.02 * math.sin(2 * math.pi * f / N_KEYS)
        frames.append({
            "Spine":  {"pos": (0.0, 0.55 * bob)},
            "Chest":  {"pos": (0.0, 0.80 * bob), "rot": sway},
            "Head":   {"pos": (0.0, 0.35 * bob)},
            "ArmL":   {"pos": (0.0, -ARM_PUMP * rr), "rot": ARM_OUT * rr},
            "ArmR":   {"pos": (0.0, -ARM_PUMP * ll), "rot": -ARM_OUT * ll},
            "LegL":   {"pos": (0.0, -LIFT * ll), "rot": LEG_OUT * ll,
                       "scale": (1.0 - LEG_SHORT * ll, 1.0 - LEG_SHORT * ll)},
            "LegR":   {"pos": (0.0, -LIFT * rr), "rot": -LEG_OUT * rr,
                       "scale": (1.0 - LEG_SHORT * rr, 1.0 - LEG_SHORT * rr)},
        })

    # 5) 汇总动画轨道（pos/rot/scale 三类）
    tracks = []
    for b in bones:
        name = b["name"]
        for prop in ("pos", "rot", "scale"):
            vals = []
            for fr in frames:
                d = fr.get(name, {})
                if prop in d:
                    vals.append(d[prop])
                elif prop == "rot":
                    vals.append(0.0)
                elif prop == "scale":
                    vals.append((1.0, 1.0))
                else:
                    vals.append((0.0, 0.0))
            # 全零轨道不写（省体积，Godot 端即保持 rest）
            zero = (all(v == 0.0 for v in vals) if prop == "rot"
                    else all(v == (0.0, 0.0) for v in vals))
            if prop == "scale":
                zero = all(v == (1.0, 1.0) for v in vals)
            if not zero:
                tracks.append({"bone": name, "prop": prop, "values": vals})

    rig = {
        "canvas": CANVAS, "bg": (244, 241, 234, 255),
        "parts": {r: {"file": info[r]["file"], "pivot": info[r]["pivot"],
                      "w": info[r]["w"], "h": info[r]["h"]} for r in PARTS},
        "bones": bones, "sprites": sprites,
        "anim": {"length": N_KEYS * KEY_DT, "n_keys": N_KEYS, "dt": KEY_DT,
                 "tracks": tracks},
    }
    (HERE / "rig_data.json").write_text(
        json.dumps(rig, ensure_ascii=False, indent=1), encoding="utf-8")

    # 6) 写 Godot 工程
    write_godot(rig, info)
    print(f"[godot] 工程已生成：{OUT}")
    print(f"[godot] rig.tscn 骨骼 {len(bones)} 根、挂点 {len(sprites)} 个、"
          f"动画轨道 {len(tracks)} 条、{N_KEYS} 关键帧")


def fnum(v):
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def write_godot(rig, info):
    lines = []
    lines.append("[gd_scene load_steps=8 format=3]\n")
    ext_ids = {}
    for i, role in enumerate(["torso", "head", "arm_l", "arm_r", "leg_l", "leg_r"], 1):
        rid = f"{i}_{role}"
        ext_ids[role] = rid
        lines.append(f'[ext_resource type="Texture2D" path="res://assets/{info[role]["file"]}" id="{rid}"]\n')
    lines.append("")

    lines.append('[node name="Character" type="Node2D"]\n')
    lines.append('[node name="Skeleton" type="Skeleton2D" parent="."]\n')

    def bone_path(name):
        chain, cur = [], name
        parent_of = {b["name"]: b["parent"] for b in rig["bones"]}
        while cur:
            chain.append(cur)
            cur = parent_of[cur]
        return "/".join(reversed(chain))

    for b in rig["bones"]:
        x, y = b["pos"]
        if b["parent"] == "":
            lines.append(f'[node name="{b["name"]}" type="Bone2D" parent="Skeleton"]')
        else:
            lines.append(f'[node name="{b["name"]}" type="Bone2D" parent="Skeleton/{bone_path(b["parent"])}"]')
        if (x, y) != (0.0, 0.0):
            lines.append(f"position = Vector2({fnum(x)}, {fnum(y)})")
        lines.append(f"rest = Transform2D(1, 0, 0, 1, {fnum(x)}, {fnum(y)})\n")

    for sp in rig["sprites"]:
        role = sp["role"]
        px, py = rig["parts"][role]["pivot"]
        lines.append(f'[node name="{sp["name"]}" type="Sprite2D" parent="Skeleton/{bone_path(sp["bone"])}"]')
        lines.append(f"position = Vector2({fnum(-px)}, {fnum(-py)})")
        lines.append("centered = false")
        lines.append(f'texture = ExtResource("{ext_ids[role]}")')
        lines.append("texture_filter = 1\n")

    anim = rig["anim"]
    lines.append('[node name="AnimationPlayer" type="AnimationPlayer" parent="."]')
    lines.append('libraries = {\n"": SubResource("Animation_walk")\n}')
    lines.append('autoplay = "walk"\n')

    lines.append('[sub_resource type="Animation" id="Animation_walk"]')
    lines.append('resource_name = "walk"')
    lines.append(f"length = {fnum(anim['length'])}")
    lines.append("loop_mode = 1")
    for i, tr in enumerate(anim["tracks"]):
        times = ", ".join(f"{fnum(k * anim['dt'])}" for k in range(anim["n_keys"]))
        lines.append(f"tracks/{i}/type = \"value\"")
        lines.append(f'tracks/{i}/path = NodePath("Skeleton/{bone_path(tr["bone"])}:{tr["prop"]}")')
        lines.append(f"tracks/{i}/interp = 1")
        lines.append(f"tracks/{i}/loop_mode = 1")
        if tr["prop"] == "rot":
            vals = ", ".join(f"{v:.6f}" for v in tr["values"])
            vtype = "PackedFloat32Array"
        else:
            vals = ", ".join(f"Vector2({fnum(v[0])}, {fnum(v[1])})" for v in tr["values"])
            vtype = "PackedVector2Array"
        lines.append(f"tracks/{i}/keys = {{")
        lines.append(f'"times": PackedFloat32Array({times}),')
        lines.append(f'"transitions": PackedFloat32Array({", ".join("1" for _ in range(anim["n_keys"]))}),')
        lines.append(f'"update": PackedInt32Array({", ".join("0" for _ in range(anim["n_keys"]))}),')
        lines.append(f'"values": {vtype}({vals})')
        lines.append("}")
    (OUT / "rig.tscn").write_text("\n".join(lines) + "\n", encoding="utf-8")

    (OUT / "project.godot").write_text(
        "config_version=5\n\n"
        "[application]\n\n"
        'config/name="Ghibli Walk Rig"\n'
        'run/main_scene="res://rig.tscn"\n'
        'config/features=PackedStringArray("4.4")\n\n'
        "[rendering]\n\n"
        'renderer/rendering_method="gl_compatibility"\n',
        encoding="utf-8")


if __name__ == "__main__":
    build()
