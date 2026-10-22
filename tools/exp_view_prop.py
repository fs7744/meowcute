"""A/B：视角指令要不要附「角色部件清单」——测左视图里腰挎的刀会不会消失。

两个变体、各 3 个种子，同一张正视图（中灰底、左侧腰挎刀、无阴影）：
  A  只有通用几何守恒条款（_view_instruction(view)）
  B  再加一条角色设定清单（_view_instruction(view, character=正视图原句)）

判读方式：出图里腰带位置是否还有刀（人眼看），并把「刀的像素占比」算出来
做粗筛（金色刀镡 + 深色刀鞘的颜色聚类占比）。

用法：.venv/Scripts/python.exe tools/exp_view_prop.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

OUT = Path(".verify/view-prop")
BASE = Path(".verify/view-consistency/front-gray-0.png")
CHARACTER = ("灰白英短猫武士，红色短披风，左腰侧挎武士刀，皮质护腕，圆耳竖立，"
             "琥珀色眼睛，三头身比例，正面站立，表情专注")


def prop_metrics(im: Image.Image) -> dict:
    """腰侧刀具的粗判：金色（刀镡/刀柄缠绳）+ 近黑深色（刀鞘）像素占比。

    只做参考，最终以人眼为准——红色披风、褐色皮带也会落进色域，所以同时
    输出这两个色团的像素数，看变体之间是否差一个量级。
    """
    a = np.asarray(im.convert("RGB")).astype(np.int16)
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    gold = (r > 150) & (g > 100) & (b < 110) & (r - b > 60)
    # 刀鞘：近黑但带一点蓝（避开纯黑描边：描边是 0,0,0，鞘是 20-40 的深蓝黑）
    sheath = (r + g + b < 150) & (b >= r) & (b + g + r > 30)
    total = a.shape[0] * a.shape[1]
    return {"gold%": round(float(gold.sum()) / total * 100, 3),
            "sheath%": round(float(sheath.sum()) / total * 100, 3)}


async def main() -> int:
    from backend.app.config import load_settings
    from backend.app.pipelines import threeview as tv
    from backend.app.providers.diffusers_local import DiffusersProvider

    OUT.mkdir(parents=True, exist_ok=True)
    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    provider = DiffusersProvider(cfg)
    base = Image.open(BASE).convert("RGBA")
    print(f"base {BASE.name} {base.size}")

    variants = {
        "A-nolist": tv._apply_background(tv._view_instruction("side"), "gray"),
        "B-list": tv._apply_background(
            tv._view_instruction("side", character=CHARACTER), "gray"),
    }
    for name, prompt in variants.items():
        print(f"[{name}] {len(prompt)} 字")
        for seed in (7, 21, 42):
            t = time.time()
            res = await provider.generate(
                prompt=prompt, width=base.width, height=base.height,
                steps=40, seed=seed, n=1, refs=[base],
                negative=tv.VIEW_NEGATIVE, max_seq_len=tv._fit_seq_len(prompt, None))
            im = res.images[0].convert("RGBA")
            p = OUT / f"{name}-seed{seed}.png"
            im.save(p)
            print(f"  seed={seed} {time.time() - t:.0f}s -> {p.name} {prop_metrics(im)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
