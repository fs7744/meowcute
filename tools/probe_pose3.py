"""身份参考该给「多少」：整身立绘 → 紧裁 → 只有头肩。

现象已经很硬（512，同一帧，四组配置）：只要把**整身立绘**喂进去，输出一定是
「这个角色的标准站姿」，源帧的侧身迈步完全丢失；把立绘换成源帧自己（小人），
姿势则一模一样地复刻。所以问题不是「模型读不懂姿势」，而是**立绘本身带着一个
强姿态先验**（正面立正），模型把它整张复制了。

那就有个便宜的绕法：别给它一张「全身立绘」——头肩裁切只交代发型/肤色/领口，
没有身体姿态可抄，身体只能照着源帧搭。

用法：.venv/Scripts/python.exe tools/probe_pose3.py [帧号...]
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

TEXT = ("图2 是这个角色的形象参考。请让她做出图1 里那个小人的动作："
        "朝向、重心、四肢方位、手脚位置全部照图1，不要画成立正站好的标准立绘。"
        "只画一个角色，背景完全透明，不要地面、不要投影。")


def tight(img: Image.Image) -> Image.Image:
    """按 alpha 紧裁，去掉四周白边——留白会让模型把「方框里的立绘」当构图模板。"""
    b = img.convert("RGBA").getbbox()
    return img.crop(b) if b else img


def head_only(img: Image.Image) -> Image.Image:
    """头肩：取紧裁后上半部分 42%。"""
    t = tight(img)
    return t.crop((0, 0, t.width, max(8, int(t.height * 0.42))))


async def run(provider, tag, refs, src):
    w, h = pc._fit_size(src.width, src.height, SIZE)
    res = await provider.generate(
        prompt=TEXT, width=w, height=h, steps=STEPS, seed=1234, n=1,
        refs=refs, negative=pc.DEFAULT_NEGATIVE, ref_bg=(255, 255, 255),
        cond_res=max(w, h),
        progress={"label": tag, "base": 0, "grand": STEPS},
        max_seq_len=pc._fit_seq_len(TEXT, None))
    img = res.images[0].convert("RGBA")
    if not pc._has_transparency(img):
        from app.pipelines import matte as mp
        img, _ = mp.remove_background(img, tolerance=40, feather=0, hard=True,
                                      method="auto", despill=True)
    img.save(OUT / f"pp3_{tag}.png")
    small = img.resize(src.size, Image.LANCZOS)
    print(f"   {tag}: iou={pc.silhouette_iou(small, src):.3f}", flush=True)


async def main() -> None:
    want = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["4"])]
    cells, views = sheet_cells(), board_cells()
    provider = DiffusersProvider({"model": "models/qwen-image-2.1"})
    for k in want:
        src, _ = pc._ensure_fg(cells[k])
        vi = pc.view_index_for(src, 4)
        ref = views[vi]
        print(f"--- 帧 {k}（视角 {vi + 1}）", flush=True)
        await run(provider, f"k{k}_t1_head", [src, head_only(ref)], src)
        await run(provider, f"k{k}_t2_tight", [src, tight(ref)], src)
        # 三图：姿势 + 头肩（脸/发型）+ 紧裁全身（服装/配色）。多图会掉一致性，
        # 这里只是对照「头肩单独够不够」。
        await run(provider, f"k{k}_t3_head_and_body",
                  [src, head_only(ref), tight(ref)], src)


if __name__ == "__main__":
    asyncio.run(main())
