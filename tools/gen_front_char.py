# -*- coding: utf-8 -*-
"""生成一张正面 A-pose 角色（干净白底），给纸娃娃骨骼方案当演示素材。

底座：Qwen-Image-2.1（2026-10-23 起唯一图片底座，文生图/改图同一个模型）。
"""
import sys
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

import asyncio

from backend.app.config import load_settings

cfg = (load_settings().get("providers") or {}).get("diffusers") or {}


async def _run():
    prov = DiffusersProvider(cfg)
    return await prov.generate(prompt=PROMPT, width=768, height=768,
                               steps=40, seed=42, n=1)


res = asyncio.run(_run())
p = OUT / "front-char.png"
res.images[0].save(p)
print(f"SAVED {p} size={res.images[0].size} mode={res.images[0].mode} "
      f"meta={res.meta}")
