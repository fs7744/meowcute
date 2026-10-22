# -*- coding: utf-8 -*-
"""生成一张宫崎骏动画风格的正面角色参考图（干净浅底），给爆炸图路线当硬核测试。

复杂点（对比之前三头身像素小人）：飘逸长发盖肩、连衣裙复杂衣褶、非对称姿态，
用来检验视角接力对复杂角色的泛化能力。底座：Qwen-Image-2.1（2026-10-23 起唯一图片底座）。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / ".verify" / "ghibli"
OUT.mkdir(parents=True, exist_ok=True)

PROMPT = (
    "宫崎骏动画电影风格角色设计稿，少女，正面站立，双手自然垂在身侧，"
    "一头飘逸的齐腰长发，穿着带荷叶边的连衣长裙，眼神温和，线条柔和，"
    "水彩手绘质感，浅米色纯色背景，角色完整居中，全身可见，"
    "姿态端正对称，游戏立绘风格"
)


def main():
    import asyncio

    from backend.app.config import load_settings
    from backend.app.providers.diffusers_local import DiffusersProvider

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}

    async def _run():
        prov = DiffusersProvider(cfg)
        res = await prov.generate(prompt=PROMPT, width=768, height=768,
                                  steps=40, seed=7, n=1)
        return res

    res = asyncio.run(_run())
    p = OUT / "ref.png"
    res.images[0].save(p)
    print(f"SAVED {p} size={res.images[0].size} mode={res.images[0].mode} "
          f"meta={res.meta}")


if __name__ == "__main__":
    main()
