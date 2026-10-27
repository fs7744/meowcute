"""姿势坍缩的真正原因：参考图顺序 / 提示词强度。

上一轮 probe_pose 结果很干脆（512）：
    只喂源帧 + 「姿势照图1」           → 完美复刻小人的侧身迈步
    源帧 + 角色立绘 + 现在的长指令      → 青蓝长袍女性，**正面站立**
也就是说「读姿势」这件事本身没问题；一旦把角色立绘也喂进去，模型就把结果
坍缩成「这个角色的标准站姿」。这一轮只变两件事：图序、提示词。

用法：.venv/Scripts/python.exe tools/probe_pose2.py [帧号...]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc          # noqa: E402
from app.providers.diffusers_local import DiffusersProvider  # noqa: E402
from tools_path import board_cells, sheet_cells    # noqa: E402

OUT = ROOT / "out"
STEPS = 40
SIZE = 512

V1 = ("图2 是这个角色的设定。请把图1 里的小人**换成**图2 的角色，"
      "姿势、朝向、四肢位置照图1 不动。只画一个角色，背景透明。")

V2 = ("图1 是这个角色的设定。请画图1 的角色，做出图2 里的那个动作"
      "（姿势、朝向、四肢位置照图2）。只画一个角色，背景透明。")

V3 = ("画出图2 里的角色（黑发、青蓝色宽袖长袍、腰佩长剑的东方女剑客），"
      "让她做出图1 里那个小人的动作：朝向、重心、四肢方位、手脚位置都照图1，"
      "不要把她画成立正站好的标准立绘。只画一个角色，背景透明，不要地面投影。")


def as_src_size(img: Image.Image, src: Image.Image) -> Image.Image:
    """比 IoU 前必须先统尺寸：silhouette_iou 形状不同直接返回 0（踩过）。"""
    return img if img.size == src.size else img.resize(src.size, Image.LANCZOS)


async def run(provider, tag, refs, text, src):
    w, h = pc._fit_size(src.width, src.height, SIZE)
    res = await provider.generate(
        prompt=text, width=w, height=h, steps=STEPS, seed=1234, n=1,
        refs=refs, negative=pc.DEFAULT_NEGATIVE, ref_bg=(255, 255, 255),
        cond_res=max(w, h),
        progress={"label": tag, "base": 0, "grand": STEPS},
        max_seq_len=pc._fit_seq_len(text, None))
    img = res.images[0].convert("RGBA")
    if not pc._has_transparency(img):
        from app.pipelines import matte as mp
        img, _ = mp.remove_background(img, tolerance=40, feather=0, hard=True,
                                      method="auto", despill=True)
    img.save(OUT / f"pp2_{tag}.png")
    small = as_src_size(img, src)
    print(f"   {tag}: iou={pc.silhouette_iou(small, src):.3f} "
          f"gray={pc._gray_ratio(img):.3f}", flush=True)


async def main() -> None:
    want = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["4"])]
    cells, views = sheet_cells(), board_cells()
    provider = DiffusersProvider({"model": "models/qwen-image-2.1"})
    for k in want:
        src, _ = pc._ensure_fg(cells[k])
        vi = pc.view_index_for(src, 4)
        ref = views[vi]
        print(f"--- 帧 {k}（视角 {vi + 1}）", flush=True)
        await run(provider, f"k{k}_v1_srcfirst", [src, ref], V1, src)
        await run(provider, f"k{k}_v2_reffirst", [ref, src], V2, src)
        await run(provider, f"k{k}_v3_textid", [src, ref], V3, src)


if __name__ == "__main__":
    asyncio.run(main())
