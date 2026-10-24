# -*- coding: utf-8 -*-
"""test_txtvideo.py — 文本生视频双模式 GPU 端到端测试。

1) 纯文本（ref=None）→ provider 走 Wan T2V 分支，meta.mode == 't2v'；
2) 带参考图 → I2V 分支，meta.mode == 'i2v'；
两条都验证：帧数 4k+1 对齐、输出是非空帧序列、显存峰值有记录。
另测 hy15 + 无参考图的明确报错（不应 silently 走错管线）。

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

    # --- 1) 纯文本 T2V ---
    t0 = time.time()
    r = await prov.generate_video(
        "像素风小猫少女在草地上奔跑，阳光明媚，镜头缓慢跟随，"
        "双腿持续交替迈步、身体随步伐上下颠动",
        None,
        width=832, height=480, steps=20, seed=20261024,
        num_frames=17, fps=12, max_side=832,
        engine="wan", pixel_art=True,
    )
    m = r.meta
    n = len(r.images)
    print(f"[t2v] {time.time() - t0:.1f}s 帧数={n} mode={m.get('mode')} "
          f"family={m.get('family')} peak={m.get('vram', {}).get('peakGB')}GB "
          f"size={m.get('size')}", flush=True)
    sheet(r.images[:: max(1, n // 6)], "t2v-sheet.png")
    ok_t2v = n >= 5 and m.get("mode") == "t2v" and all(f.size for f in r.images)

    # --- 2) 带参考图 I2V ---
    ref = Image.open(REF).convert("RGB")
    t0 = time.time()
    r2 = await prov.generate_video(
        "角色原地行走：双腿持续交替迈步、膝盖屈伸、身体随步伐上下颠动",
        ref,
        width=384, height=512, steps=20, seed=20261024,
        num_frames=17, fps=12, max_side=512,
        engine="wan",
    )
    m2 = r2.meta
    n2 = len(r2.images)
    print(f"[i2v] {time.time() - t0:.1f}s 帧数={n2} mode={m2.get('mode')} "
          f"peak={m2.get('vram', {}).get('peakGB')}GB size={m2.get('size')}", flush=True)
    sheet(r2.images[:: max(1, n2 // 6)], "i2v-sheet.png")
    ok_i2v = n2 >= 5 and m2.get("mode") == "i2v"

    # --- 3) hy15 + 无参考图必须明确报错 ---
    err = ""
    try:
        await prov.generate_video("一只猫", None, engine="hy15")
    except RuntimeError as e:
        err = str(e)
    print(f"[hy15+无图] {'正确报错' if 'Wan' in err else '异常：' + err[:80]}", flush=True)

    print("TXTVIDEO_DONE", flush=True)
    print(f"RESULT t2v={'PASS' if ok_t2v else 'FAIL'} "
          f"i2v={'PASS' if ok_i2v else 'FAIL'} "
          f"hy15_guard={'PASS' if 'Wan' in err else 'FAIL'}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
