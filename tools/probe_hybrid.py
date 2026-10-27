"""两段式：A（文生图+控制网，姿势准、像素风）→ B（Edit-2511 换身份）。

A 出的像素风小人姿势对但脸不像；B 能把脸换成四视图里的人但姿势会漂。
两段式让 A 负责姿势与画风、B 只负责把脸/服装换成四视图那个人（编辑输入图就是 A 的结果，
所以"被编辑的对象"已经带着正确姿势了）。

用法：.venv/Scripts/python.exe tools/probe_hybrid.py [帧号,帧号,...]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc                      # noqa: E402
from app.pipelines.qwen20_edit import Qwen20EditControl        # noqa: E402
from tools_path import board_cells, sheet_cells                 # noqa: E402

OUT = ROOT / "out"
ROW_CELL = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]

P_HYB = ("图1 是这个角色的像素风立绘，图2 是这个角色高清设定图。"
         "请把图1 画成图2 那个人的样子：脸、发型、服装颜色、配饰、武器全部照图2，"
         "但姿势、朝向、重心、四肢落点保持图1 不变，画风保持像素风。"
         "纯白背景，不要文字，不要水印，不要多个角色。")


def main() -> None:
    want = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [0, 4, 9]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]
    eng = Qwen20EditControl(size=512, steps=30, cfg=4.0)
    eng.ensure()
    cache = eng.warm_prompts([P_HYB, "模糊, 低质量, 水印, 文字, 多个角色, 四视图"])
    pos, pos_mask = cache[P_HYB]
    neg, neg_mask = cache["模糊, 低质量, 水印, 文字, 多个角色, 四视图"]
    embeds = {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}
    print("[hyb] 提示词就绪", flush=True)
    for k in want:
        a = Image.open(OUT / f"a20_{k:02d}.png").convert("RGB")
        img, meta = eng.run_frame(control=a, ref=[a, refs[ROW_CELL[k]]],
                                  kind="src", embeds=embeds, seed=1000 + k)
        img.save(OUT / f"hyb_{k:02d}.png")
        print(f"[hyb] 帧{k} {meta}", flush=True)


if __name__ == "__main__":
    main()
