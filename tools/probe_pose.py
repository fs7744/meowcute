"""姿势能不能被读出来：尺寸 / 参考图组合 / 提示词 三者的最小探针。

上一轮 exp_lockpose 的结论：身份迁移稳定在 0.9，但**姿势完全没动**——
源帧是「侧面迈步」，三种配置画出来都是「正面站立持剑」。所以先别谈锁定，
先确认一件事：Qwen-Image-2.1 到底有没有在读图1 的姿势。

怀疑点：输出尺寸。Qwen-Image 官方推荐 928~1472，我们一直在 256×256 上跑，
这个分辨率下模型只够画一个"立正站好"的粗人形，四肢角度这种细节在 token
序列里几乎不占位置。

用法：.venv/Scripts/python.exe tools/probe_pose.py [帧号...]
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc          # noqa: E402
from app.providers.diffusers_local import DiffusersProvider  # noqa: E402
from tools_path import sheet_cells, board_cells    # noqa: E402

OUT = ROOT / "out"
STEPS = 40

POSE_ONLY = (
    "画图1 里的这个角色，姿势、朝向、四肢位置、身体重心**和图1 完全一致**。"
    "只输出这一个角色，背景完全透明，不要地面、不要投影、不要文字。"
)


async def run(provider, tag, refs, text, src, size):
    w, h = pc._fit_size(src.width, src.height, size)
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
    img.save(OUT / f"pose_{tag}.png")
    info = {"tag": tag, "iou": round(pc.silhouette_iou(img, src), 3),
            "gray": round(pc._gray_ratio(img), 3)}
    print("  ", info, flush=True)
    return info, img


async def main() -> None:
    want = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1 else ["4"])]
    cells = sheet_cells()
    views = board_cells()
    provider = DiffusersProvider({"model": "models/qwen-image-2.1"})
    base = pc.build_instruction(pc.DEFAULT_INSTRUCTION, index=1, total=12,
                                background="transparent")
    for k in want:
        src, _ = pc._ensure_fg(cells[k])
        vi = pc.view_index_for(src, 4)
        ref = views[vi]
        print(f"--- 帧 {k}（视角 {vi + 1}）", flush=True)
        for tag, refs, text, size in [
            (f"k{k}_s256_src", [src], POSE_ONLY, 256),
            (f"k{k}_s512_src", [src], POSE_ONLY, 512),
            (f"k{k}_s512_both", [src, ref], base, 512),
            (f"k{k}_s768_both", [src, ref], base, 768),
        ]:
            await run(provider, tag, refs, text, src, size)


if __name__ == "__main__":
    asyncio.run(main())
