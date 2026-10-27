"""剪影锁定实验：把「动作源帧」换成不含角色的姿势占位图，看身份/姿势能不能同时拿到。

背景（2026-10-27 实测）：同一批 12 帧，喂 [动作帧, 角色立绘] 时
    低强度 → 姿势 0.91 / 身份 0.13（还在画光头小人）
    高强度 → 身份 0.74 / 姿势 0.60（站桩）
中间不存在同时好的点。怀疑原因是**图1 里站着一个完整的旧角色**：模型把它当
「要编辑的对象」，改色指令一强就退化成灰剪影（实测 12 帧里多次出现灰色人形）。

所以本实验换掉图1 的语义：不再给「别人的角色」，而是给「这一帧的姿势占位」——
一个纯色/描边的剪影形体，没有五官没有布料，只保留姿势的形状。形体和角色之间
没有竞争关系，模型只需要回答「谁站在这个轮廓里」。

用法：
    .venv/Scripts/python.exe tools/exp_lockpose.py            # 默认 3 帧 × 3 配置
    .venv/Scripts/python.exe tools/exp_lockpose.py 0,4,9      # 指定帧号（0 基）
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc          # noqa: E402
from app.providers.diffusers_local import DiffusersProvider  # noqa: E402

SHEET = ROOT / "data/projects/p-dbdb6b3dbc19/refs/ref-20261026-224308-1796cfc8.png"
BOARD = ROOT / "data/projects/p-dbdb6b3dbc19/refs/ref-20261026-224318-a94709d4.png"
OUT = ROOT / "out"
COLS, ROWS = 3, 4
STEPS = 40
SIZE = 256


def split_sheet(img: Image.Image) -> list[Image.Image]:
    cw, ch = img.width // COLS, img.height // ROWS
    return [img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch))
            for r in range(ROWS) for c in range(COLS)]


def dominant_color(img: Image.Image) -> tuple[int, int, int]:
    """前景里最常见的颜色（32 级量化后的众数）。用来给占位形体上色。"""
    a = np.asarray(img.convert("RGBA"))
    px = a[a[..., 3] > 128][:, :3]
    if len(px) == 0:
        return (190, 190, 190)
    q = (px // 32 * 32 + 16).astype(np.int32)
    keys, counts = np.unique(q[:, 0] * 65536 + q[:, 1] * 256 + q[:, 2],
                             return_counts=True)
    k = int(keys[counts.argmax()])
    return (k // 65536, (k // 256) % 256, k % 256)


def build_guide(src: Image.Image, *, fill: bool) -> Image.Image:
    """姿势占位图：只保留剪影形状。

    fill=True  → 用角色主色填满（等于「给这个形状上色的底稿」）
    fill=False → 只画 3px 描边（最接近 ControlNet softedge 的语义，
                 完全不泄露任何颜色信息）
    """
    a = np.asarray(src.convert("RGBA"))
    m = a[..., 3] > 8
    out = np.zeros((*m.shape, 4), np.uint8)
    if fill:
        out[m] = (60, 60, 60, 255)      # 深灰，不给模型「这是谁」的线索
    edge = m & ~ndimage.binary_erosion(m, iterations=3)
    out[edge] = (20, 20, 20, 255)
    return Image.fromarray(out, "RGBA")


GUIDE_RULES = (
    "图1 是这个角色在**具体某一种姿势下的剪影**（一个纯色形体，没有五官、没有布料、"
    "不是角色本身）。图2 是目标角色的设定板。\n"
    "任务：把图1 的剪影**画成一个完整上色的角色**，角色是谁由图2 决定。\n"
    "· 姿势 100% 照图1：身体朝向、重心、四肢各自的方位与弯曲、手脚落在画面的哪个位置，"
    "全部和图1 的轮廓重合；图1 抬起来的手臂，画出来也必须抬在同一个方向；\n"
    "· 角色 100% 照图2：脸型、发型、发色、瞳色、服装款式与配色、配饰、武器都用图2 的；\n"
    "· 体型可以按角色本身调整（图1 只是占位，不是要你画一个胖矮的剪影），"
    "但**每条肢体对应的方位不能变**：图1 里朝画面左侧伸出的腿，画出来也要朝左侧；\n"
    "· 必须是**完整上色的彩色角色**：头发、皮肤、衣服都要画出颜色和明暗，"
    "不要交灰色剪影、不要只描边、不要画成单色块；\n"
    "画面里只有这一个角色，背景完全透明（只要角色，不要任何底色），"
    "没有地面、没有投影、没有边框、没有文字、没有其他人物。"
)


def build_baseline(src: Image.Image) -> str:
    return pc.build_instruction(pc.DEFAULT_INSTRUCTION, index=1, total=12,
                                background="transparent")


async def one(provider: DiffusersProvider, cfg: str, src: Image.Image,
              ref: Image.Image, k: int) -> dict:
    w, h = pc._fit_size(src.width, src.height, SIZE)
    if cfg == "A":
        refs, text = [src, ref], build_baseline(src)
    elif cfg == "B":
        refs, text = [build_guide(src, fill=True), ref], GUIDE_RULES
    else:
        refs, text = [build_guide(src, fill=False), ref], GUIDE_RULES
    res = await provider.generate(
        prompt=text, width=w, height=h, steps=STEPS, seed=1234, n=1,
        refs=refs, negative=pc.DEFAULT_NEGATIVE,
        ref_bg=(255, 255, 255), cond_res=max(w, h),
        progress={"label": f"{cfg}-{k}", "base": 0, "grand": STEPS},
        max_seq_len=pc._fit_seq_len(text, None),
    )
    img = res.images[0].convert("RGBA")
    if not pc._has_transparency(img):
        from app.pipelines import matte as mp
        img, _ = mp.remove_background(img, tolerance=40, feather=0, hard=True,
                                      method="auto", despill=True)
    img, _info = pc._align_to(img, src, anchor="bottom")
    iou = pc.silhouette_iou(img, src)
    gray = pc._gray_ratio(img)
    ident = 1.0 - pc._hist_dist(pc._color_hist(img), pc._color_hist(ref))
    img.save(OUT / f"lockpose_{cfg}_{k}.png")
    return {"cfg": cfg, "k": k, "iou": round(iou, 3), "gray": round(gray, 3),
            "id": round(ident, 3), "sec": round((res.meta or {}).get("seconds", 0), 1)}


async def main() -> None:
    want = [int(x) for x in (sys.argv[1].split(",") if len(sys.argv) > 1
                             else ["1", "4", "9"])]
    cells = split_sheet(Image.open(SHEET))
    board = pc.repack_board(Image.open(BOARD))[0]
    views = pc.split_board_cells(board)
    assert len(views) == 4, f"四视图切出 {len(views)} 格"
    provider = DiffusersProvider({"model": "models/qwen-image-2.1"})

    rows = []
    for k in want:
        src, _ = pc._ensure_fg(cells[k])
        vi = pc.view_index_for(src, 4)
        print(f"--- 帧 {k} 视角 {vi + 1}  主色 {dominant_color(src)}", flush=True)
        for cfg in ("A", "B", "C"):
            r = await one(provider, cfg, src, views[vi], k)
            print(f"   {cfg}: iou={r['iou']} gray={r['gray']} id={r['id']}", flush=True)
            rows.append(r)
            # 现场拼对照条：源帧 / 占位图 / 出图
            strip = Image.new("RGBA", (src.width * 3, src.height), (255, 255, 255, 255))
            strip.alpha_composite(src, (0, 0))
            g = build_guide(src, fill=(cfg == "B"))
            strip.alpha_composite(g, (src.width, 0))
            out = Image.open(OUT / f"lockpose_{cfg}_{k}.png")
            strip.alpha_composite(out.resize(src.size, Image.NEAREST), (src.width * 2, 0))
            strip.convert("RGB").resize((src.width * 6, src.height * 2), Image.NEAREST)\
                 .save(OUT / f"lockpose_strip_{cfg}_{k}.png")

    print("\n汇总")
    for r in rows:
        print(r)
    grid_w = 120 * 3
    grid = Image.new("RGB", (grid_w * 3, 120 * len(want) * 2), (255, 255, 255))
    print("对照条已写入 out/lockpose_strip_*.png")


if __name__ == "__main__":
    asyncio.run(main())
