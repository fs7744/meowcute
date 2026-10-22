# -*- coding: utf-8 -*-
"""生成一张正面 A-pose 角色（干净白底），给纸娃娃骨骼方案当演示素材。"""
import os
import sys
import torch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.app.providers.diffusers_local import DiffusersProvider

OUT = ROOT / ".verify" / "rigwalk2"
OUT.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "像素风游戏角色，正面站立，A-pose，双臂自然下垂贴身体两侧，"
    "三头身比例，短红披风，圆头大眼，纯品红色背景，全身可见，游戏素材风格"
)

prov = DiffusersProvider({"model": "models/z-image-turbo"})
gen = torch.Generator("cpu").manual_seed(42)
out = prov._run_zimage(
    "models/z-image-turbo", PROMPT, 768, 768, 9, 42, gen, 1)
imgs, meta = out
p = OUT / "front-char.png"
imgs[0].save(p)
print(f"SAVED {p} size={imgs[0].size} mode={imgs[0].mode} meta={meta}")
