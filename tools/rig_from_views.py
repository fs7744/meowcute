# -*- coding: utf-8 -*-
"""四视图拼板 → 4 方向骨骼行走（正/左/背/右）。

为什么是 4 方向而不是 8：骨骼绑定必须有**每个视角自己的部件**。实测两条拆件路线
对非正面视角的表现：
  - see-through：侧视图只给单数 `handwear`、没有 `legwear`，头部是白块 → 不可用
  - 爆炸图：加了"保持侧面/背面"约束后躯干和头是侧/背视角，四肢角度偏正面，
    但骨骼渲染里远侧肢体本来就该被躯干挡住，**够用**
所以正/侧/背三个真实方向靠爆炸图各拆一次，右侧由侧面镜像；斜向按项目现有约定
（dirMode=four）不生成。

用法：
  python tools/rig_from_views.py --board .verify/hy15/ref.png --out .verify/panda/4dir
    --front-parts .verify/panda/pipeline     # 可选：正视图用已拆好的混合部件（更好看）
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

VIEWS = [("front", "正", "front"), ("side", "左", "side"), ("back", "背", "back")]
DIR_OF = {"front": "down", "side": "left", "back": "up"}


def run(cmd, timeout=900):
    print("[run]", " ".join(str(c) for c in cmd[2:4]), "...", flush=True)
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)


def split_board(board: Path, out: Path) -> list[Path]:
    """四视图拼板 → 4 个格（复用项目的 split_view_board）。"""
    from backend.app.pipelines.threeview import split_view_board

    cells = split_view_board(Image.open(board).convert("RGBA"))
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for i, c in enumerate(cells):
        p = out / f"view{i}.png"
        c.save(p)
        paths.append(p)
    print(f"[board] 拆出 {len(paths)} 格", flush=True)
    return paths


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--board", required=True, help="四视图拼板（透明底）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--front-parts", default=None, help="正视图已有的部件目录（跳过重拆）")
    ap.add_argument("--swing", type=float, default=13.0)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--hold", action="store_true")
    args = ap.parse_args()

    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    views = split_board(Path(args.board).resolve(), out / "cells")
    if len(views) < 3:
        raise SystemExit("拼板至少要有正/侧/背三格")

    from app.pipelines import rigwalk  # noqa: PLC0415

    results = {}
    for idx, (key, cn, direction) in enumerate(VIEWS):
        pdir = None
        if key == "front" and args.front_parts:
            pdir = Path(args.front_parts).resolve()
            if not (pdir / "parts.json").exists():
                pdir = None
        if pdir is None:
            ex = out / f"parts-{key}"
            if not (ex / "parts.json").exists():
                t0 = time.time()
                r = run([sys.executable, ROOT / "tools" / "exploded_parts21.py",
                         "--ref", str(views[idx]), "--out", str(ex),
                         "--view", key, "--seed", str(args.seed)])
                if not (ex / "parts.json").exists():
                    print(f"[warn] {cn}视图拆件失败：{((r.stdout or '')[-300:])}", flush=True)
                    continue
                print(f"[parts] {cn}视图 {time.time()-t0:.0f}s", flush=True)
            pdir = ex

        frames, meta = rigwalk.render(pdir, frames=args.frames, swing=args.swing,
                                      direction=direction, hold=args.hold)
        d = out / DIR_OF[key]
        d.mkdir(exist_ok=True)
        for i, f in enumerate(frames):
            f.save(d / f"{i:03d}.png")
        frames[0].save(d / "walk.gif", save_all=True, append_images=frames[1:],
                       duration=110, loop=0, disposal=2)
        W = sum(f.width for f in frames) + 4 * (len(frames) - 1)
        strip = Image.new("RGB", (W, frames[0].height), (40, 44, 52))
        x = 0
        for f in frames:
            strip.paste(f.convert("RGB"), (x, 0), f)
            x += f.width + 4
        strip.save(d / "strip.png")
        results[key] = {**meta, "dir": DIR_OF[key], "parts": str(pdir)}
        print(f"[rig] {cn}→{DIR_OF[key]} 面积±{meta['areaStdPct']}% 步幅 {meta['stridePx']}px", flush=True)

    # 右侧 = 左侧镜像（项目的既有约定：右侧方向由左侧翻转得到）
    if "side" in results:
        src = out / DIR_OF["side"]
        dst = out / "right"
        dst.mkdir(exist_ok=True)
        for fp in sorted(src.glob("*.png")):
            Image.open(fp).transpose(Image.FLIP_LEFT_RIGHT).save(dst / fp.name)
        frames = [Image.open(p).convert("RGBA") for p in sorted(src.glob("[0-9]*.png"))]
        frames = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in frames]
        if frames:
            frames[0].save(dst / "walk.gif", save_all=True, append_images=frames[1:],
                           duration=110, loop=0, disposal=2)
            W = sum(f.width for f in frames) + 4 * (len(frames) - 1)
            strip = Image.new("RGB", (W, frames[0].height), (40, 44, 52))
            x = 0
            for f in frames:
                strip.paste(f.convert("RGB"), (x, 0), f)
                x += f.width + 4
            strip.save(dst / "strip.png")
        results["right"] = {"dir": "right", "mirroredFrom": "left"}

    (out / "dirs.json").write_text(json.dumps(results, indent=2, ensure_ascii=False),
                                   encoding="utf-8")

    # 四方向对照图
    order = ["down", "left", "up", "right"]
    rows = [(d, Image.open(out / d / "strip.png")) for d in order if (out / d / "strip.png").exists()]
    if rows:
        TW = 900
        imgs = [(d, im.resize((TW, int(im.height * TW / im.width)), Image.LANCZOS)) for d, im in rows]
        H = sum(i.height + 18 for _, i in imgs) + 8
        sheet = Image.new("RGB", (TW + 20, H), (248, 248, 248))
        dr = ImageDraw.Draw(sheet)
        y = 4
        for d, im in imgs:
            dr.text((8, y), d, fill=(20, 20, 20))
            y += 14
            sheet.paste(im, (10, y))
            y += im.height + 4
        sheet.save(out / "dirs.png")
    print(f"DONE {out}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
