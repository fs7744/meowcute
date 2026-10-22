# -*- coding: utf-8 -*-
"""make_dirs8_sheets.py — 八方向对跑结果的三联对比图（锚点 | 2.1 | edit-2511）。

用法：.venv/Scripts/python.exe tools/make_dirs8_sheets.py
产出：.verify/exp-b/sheets/<direction>.png（每张 384*3 宽）
     .verify/exp-b/sheets/_overview.png（7 张竖排总览）
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".verify" / "exp-b"
SHEETS = OUT / "sheets"
ANCHOR = OUT / "walkdown-frame0.png"
DIRS = ["down-left", "left", "up-left", "up", "up-right", "right", "down-right"]
LABELS = ["锚点(正面)", "Qwen-2.1", "Edit-2511"]
CELL = (384, 512)
PAD = 8
HDR = 22


def main():
    SHEETS.mkdir(parents=True, exist_ok=True)
    anchor = Image.open(ANCHOR).convert("RGB").resize(CELL)
    rows = []
    for d in DIRS:
        q = OUT / "dirs8-qwen21" / f"{d}.png"
        e = OUT / "dirs8-edit2511" / f"{d}.png"
        if not (q.exists() and e.exists()):
            print(f"[skip] {d} 缺图")
            continue
        imgs = [anchor,
                Image.open(q).convert("RGB").resize(CELL),
                Image.open(e).convert("RGB").resize(CELL)]
        sheet = Image.new("RGB", (CELL[0] * 3 + PAD * 4, CELL[1] + HDR + PAD * 2),
                          (24, 26, 32))
        dr = ImageDraw.Draw(sheet)
        for i, (im, lb) in enumerate(zip(imgs, LABELS)):
            x = PAD + i * (CELL[0] + PAD)
            dr.text((x, 4), lb, fill=(230, 230, 230))
            sheet.paste(im, (x, HDR))
        p = SHEETS / f"{d}.png"
        sheet.save(p)
        rows.append(sheet)
        print(f"[sheet] {p.name}")
    if rows:
        w = rows[0].width
        h = sum(r.height for r in rows) + PAD * (len(rows) - 1)
        ov = Image.new("RGB", (w, h), (24, 26, 32))
        y = 0
        for r in rows:
            ov.paste(r, (0, y))
            y += r.height + PAD
        ov.save(SHEETS / "_overview.png")
        print(f"[overview] _overview.png {ov.size}")


if __name__ == "__main__":
    main()
