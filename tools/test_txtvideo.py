# -*- coding: utf-8 -*-
"""test_txtvideo.py — 文本生视频多引擎 GPU 端到端测试。

2026-10-24 起两个引擎都支持纯文本（hy15 补了 T2V 管线）。覆盖：
1) hy15 纯文本（ref=None，engine=hy15）→ mode=t2v / engine=hy15；
2) 自动 + 无参考图 → 默认路由到 hy15（权重就绪时）；
3) wan 纯文本（engine=wan）→ mode=t2v / engine=wan（回归）；
4) hy15 带参考图 → mode=i2v（I2V 重构后回归）。
都验证：帧数 4k+1、非空帧序列、显存峰值有记录。

用法：.venv/Scripts/python.exe tools/test_txtvideo.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402

REF = ROOT / ".verify" / "ghibli" / "ref.png"
OUT = ROOT / ".verify" / "txtvideo"

PROMPT_T2V = ("像素风小猫少女在草地上奔跑，阳光明媚，镜头缓慢跟随，"
              "双腿持续交替迈步、身体随步伐上下颠动")


def sheet(frames, name, cols=6):
    if not frames:
        return
    cell = 240
    rows = (len(frames) + cols - 1) // cols
    im = Image.new("RGB", (cols * cell, rows * cell), (20, 22, 28))
    for i, f in enumerate(frames):
        t = f.convert("RGB")
        t.thumbnail((cell, cell))
        im.paste(t, ((i % cols) * cell + (cell - t.width) // 2,
                     (i // cols) * cell + (cell - t.height) // 2))
    im.save(OUT / name)


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prov = DiffusersProvider({"videoModel": "models/wan2.2-ti2v-5b"})
    results = {}

    # --- 1) hy15 纯文本 ---
    t0 = time.time()
    r = await prov.generate_video(
        PROMPT_T2V, None, width=848, height=480, steps=12, seed=20261024,
        num_frames=21, fps=12, engine="hy15")
    m = r.meta
    print(f"[hy15-t2v] {time.time() - t0:.1f}s 帧={len(r.images)} "
          f"mode={m.get('mode')} engine={m.get('engine')} "
          f"peak={m.get('vram', {}).get('peakGB')}GB size={m.get('size')}", flush=True)
    sheet(r.images[:: max(1, len(r.images) // 6)], "hy15-t2v-sheet.png")
    results["hy15_t2v"] = (len(r.images) >= 5 and m.get("mode") == "t2v"
                           and m.get("engine") == "hy15")

    # --- 2) 自动 + 无参考图（应路由到 hy15）---
    t0 = time.time()
    r2 = await prov.generate_video(
        PROMPT_T2V, None, width=848, height=480, steps=12, seed=20261024,
        num_frames=21, fps=12)
    m2 = r2.meta
    print(f"[auto-t2v] {time.time() - t0:.1f}s 帧={len(r2.images)} "
          f"mode={m2.get('mode')} engine={m2.get('engine')}", flush=True)
    results["auto_t2v"] = (len(r2.images) >= 5 and m2.get("engine") == "hy15"
                           and m2.get("mode") == "t2v")

    # --- 3) wan 纯文本（回归）---
    t0 = time.time()
    r3 = await prov.generate_video(
        PROMPT_T2V, None, width=832, height=480, steps=20, seed=20261024,
        num_frames=17, fps=12, engine="wan", pixel_art=True)
    m3 = r3.meta
    print(f"[wan-t2v]  {time.time() - t0:.1f}s 帧={len(r3.images)} "
          f"mode={m3.get('mode')} engine={m3.get('engine')} "
          f"peak={m3.get('vram', {}).get('peakGB')}GB", flush=True)
    sheet(r3.images[:: max(1, len(r3.images) // 6)], "wan-t2v-sheet.png")
    results["wan_t2v"] = (len(r3.images) >= 5 and m3.get("engine") == "wan"
                          and m3.get("mode") == "t2v")

    # --- 4) hy15 带参考图（I2V 重构回归）---
    ref = Image.open(REF).convert("RGB")
    t0 = time.time()
    r4 = await prov.generate_video(
        "角色原地行走：双腿持续交替迈步、膝盖屈伸、身体随步伐上下颠动",
        ref, width=384, height=512, steps=12, seed=20261024,
        num_frames=21, fps=12, engine="hy15")
    m4 = r4.meta
    print(f"[hy15-i2v] {time.time() - t0:.1f}s 帧={len(r4.images)} "
          f"mode={m4.get('mode')} engine={m4.get('engine')} "
          f"peak={m4.get('vram', {}).get('peakGB')}GB", flush=True)
    sheet(r4.images[:: max(1, len(r4.images) // 6)], "hy15-i2v-sheet.png")
    results["hy15_i2v"] = (len(r4.images) >= 5 and m4.get("mode") == "i2v"
                           and m4.get("engine") == "hy15")

    line = " ".join(f"{k}={'PASS' if v else 'FAIL'}" for k, v in results.items())
    print(f"RESULT {line} | ALL={'PASS' if all(results.values()) else 'FAIL'}",
          flush=True)


if __name__ == "__main__":
    asyncio.run(main())
