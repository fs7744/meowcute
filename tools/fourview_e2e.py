# -*- coding: utf-8 -*-
"""fourview_e2e.py — 四视图管线端到端实测（项目真实图片）。

正面锚点 = 生产管线正面视图（walk-down GIF 首帧）；
侧/背 = 项目三视图锚点 vs-/vb-；
右视图 = 本次新增的 side_r 指令接力（编辑底座）；
最后拼四视图板，验证 derive(side_r) → compose(4 张) 全链路。
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from backend.app.pipelines import threeview as tv  # noqa: E402

P = ROOT / "data" / "projects" / "p-e1fd855eb0f5" / "images"
OUT = ROOT / ".verify"


async def main():
    front = Image.open(OUT / "exp-b" / "walkdown-frame0.png").convert("RGB")
    side = Image.open(P / "vs-20261021-213110-8cc7.png").convert("RGB")
    back = Image.open(P / "vb-20261021-213403-92a0.png").convert("RGB")
    print(f"[anchor] front={front.size} side={side.size} back={back.size}", flush=True)

    print("[derive] side_r（编辑底座指令接力）…", flush=True)
    right, meta = await tv.derive_view(
        "side_r", front, steps=40, seed=20261022,
        width=512, height=768)
    right.save(OUT / "fourview-side-r.png")
    print(f"[derive] side_r done: {meta.get('seconds')}s "
          f"model={meta.get('model')} → fourview-side-r.png", flush=True)

    board, bmeta = tv.compose_view_board([front, side, back, right])
    board.save(OUT / "fourview-board-real.png")
    print(f"[compose] 四视图板 {board.size}，格序 "
          f"{[v['view'] for v in bmeta['views']]}", flush=True)
    print("E2E_DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
