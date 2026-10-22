"""用本项目自己的模型和管线生成「可爱的像素猫」图标。

流程：Qwen-Image-2.1 按像素契约出图 → FFT 网格对齐精修到 32×32 → 抠掉中灰背景 → 拼候选图。

用法：
    python tools/gen_icon.py              # 生成 4 个候选，输出 tools/icon_candidates.png
    python tools/gen_icon.py --pick 2     # 把第 2 个候选写成 favicon

产物：
    tools/_icon/cand{n}_raw.png      模型原图 512²
    tools/_icon/cand{n}_32.png       精修后的 32×32 真像素图（透明背景）
    tools/_icon/cand{n}_x8.png       放大 8 倍，人眼检查用
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from collections import deque
from pathlib import Path

os.environ.setdefault("MEOWCUTE_DATA_DIR", str(Path(__file__).resolve().parent / "_icon" / "_data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from backend.app.config import load_settings  # noqa: E402
from backend.app.pipelines import refine as refine_mod  # noqa: E402
from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402
from backend.app.styles import build_prompt  # noqa: E402

OUT = Path(__file__).resolve().parent / "_icon"
GRID = 32

# 图标要的是「头部特写」：主体单一、颜色少、居中，才能在小尺寸下立得住。
# 「四周留白」很关键：不留白的话抠完背景，脸就贴着边，缩到 16px 会糊成一团。
SUBJECT = (
    "一只超可爱的白猫头像，正面朝向观众，圆圆的脑袋，胖乎乎的脸颊，"
    "大眼睛，粉色内耳，粉色腮红，小小的粉鼻子，微笑，只有头部不要身体，"
    "头像占画面约七成，四周留出空白背景，不要裁切"
)


def cut_background(img: Image.Image, tol: int = 20) -> Image.Image:
    """把四周连成一片的背景色抠成透明。

    精修后背景通常是单一色块，从边框泛洪就能干净抠掉；留 tol 是给模型
    没画成绝对纯色时兜底（相邻格差一点点也算背景）。不碰角色内部同色区域。
    """
    img = img.convert("RGBA")
    w, h = img.size
    px = img.load()
    bg = px[0, 0][:3]

    def is_bg(c: tuple[int, int, int]) -> bool:
        return all(abs(c[i] - bg[i]) <= tol for i in range(3))

    seen = [[False] * w for _ in range(h)]
    q = deque()
    for x in range(w):
        q.append((x, 0))
        q.append((x, h - 1))
    for y in range(h):
        q.append((0, y))
        q.append((w - 1, y))

    while q:
        x, y = q.popleft()
        if x < 0 or y < 0 or x >= w or y >= h or seen[y][x]:
            continue
        seen[y][x] = True
        r, g, b, a = px[x, y]
        if a == 0 or not is_bg((r, g, b)):
            continue
        px[x, y] = (r, g, b, 0)
        q.extend(((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)))
    return img


def sheet(rows: list[tuple[str, Image.Image]]) -> Image.Image:
    """候选拼版：大图深/浅两遍 + 一行真实标签尺寸（16/32），看得清才算过。"""
    cell = 176
    pad = 12
    cols = len(rows)
    strip_h = 56
    width = cols * (cell + pad) + pad
    board = Image.new('RGBA', (width, 2 * (cell + pad) + strip_h + pad), (240, 240, 244, 255))
    for i, (_, img) in enumerate(rows):
        big = img.resize((cell, cell), Image.NEAREST)
        for j, bg in enumerate(((244, 244, 246, 255), (26, 26, 34, 255))):
            tile = Image.new('RGBA', (cell, cell), bg)
            tile.alpha_composite(big)
            board.paste(tile, (pad + i * (cell + pad), pad + j * (cell + pad)))
    # 第三行：真实尺寸，16px 能认出来才算合格图标
    y0 = 2 * (cell + pad) + pad
    for i, (_, img) in enumerate(rows):
        x = pad + i * (cell + pad) + 8
        for size in (16, 24, 32):
            tile = Image.new('RGBA', (size, size), (26, 26, 34, 255))
            tile.alpha_composite(img.resize((size, size), Image.NEAREST))
            board.paste(tile, (x, y0 + (strip_h - size) // 2))
            x += size + 10
    return board


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    s = load_settings()
    cfg = (s.get("providers") or {}).get("diffusers") or {}
    provider = DiffusersProvider(cfg)

    built = build_prompt(
        SUBJECT, "pixel_16bit", pixel=True, grid=[GRID, GRID], colors=12,
        background="中性中灰背景（#808080），背景平铺纯净、无渐变、无投影、无杂物")
    print('提示词：', built["prompt"], flush=True)

    t0 = time.time()
    res = await provider.generate(
        prompt=built["prompt"], width=512, height=512, steps=9,
        seed=int(sys.argv[sys.argv.index('--seed') + 1]) if '--seed' in sys.argv else 20261020,
        n=4, task_id=None)
    print(f'生成 4 张：{time.time() - t0:.1f}s', flush=True)

    picks: list[tuple[str, Image.Image]] = []
    for i, raw in enumerate(res.images, 1):
        raw = raw.convert('RGB')
        raw.save(OUT / f'cand{i}_raw.png')
        rb = refine_mod.refine_batch(
            [raw], grid_snap=True, grid_size=[GRID, GRID],
            colors=12, palette_name='auto')
        small = cut_background(rb['items'][0]['image'])
        small.save(OUT / f'cand{i}_32.png')
        small.resize((small.width * 8, small.height * 8), Image.NEAREST).save(
            OUT / f'cand{i}_x8.png')
        meta = rb['items'][0]['meta']
        print(f'  候选 {i}: 网格 {meta["grid"]} · 色数 {meta["colors"]} · '
              f'板外 {meta["offPalette"]}', flush=True)
        picks.append((f'cand{i}', small))

    board = sheet(picks)
    board.save(OUT.parent / 'icon_candidates.png')
    print(f'候选图：{OUT.parent / "icon_candidates.png"}', flush=True)
    return 0


def half_size(src: Image.Image) -> Image.Image:
    """32→16 的像素降采样：每 2×2 取多数不透明色。

    直接最近邻会把轮廓打断（一格黑一格白地漏掉），box 平均又会糊成灰边。
    按块多数表决，等于把「2 像素宽的描边」压成「1 像素宽」，形状还在。
    """
    src = src.convert('RGBA')
    w, h = src.size
    out = Image.new('RGBA', (w // 2, h // 2), (0, 0, 0, 0))
    sp, op = src.load(), out.load()
    for y in range(h // 2):
        for x in range(w // 2):
            block = [sp[x * 2 + dx, y * 2 + dy] for dy in (0, 1) for dx in (0, 1)]
            solid = [c for c in block if c[3] > 96]
            if not solid:
                op[x, y] = (0, 0, 0, 0)
                continue
            # 出现最多的颜色胜出（同票时取先出现的，保证稳定）
            best = max(solid, key=lambda c: (solid.count(c), -solid.index(c)))
            op[x, y] = best
    return out


def write_ico(src: Image.Image, path: Path, sizes: tuple[int, ...] = (16, 32, 64)) -> None:
    """手写 ICO（内嵌 PNG 帧）。

    不用 PIL 的 ico 保存：它会对每一档做 LANCZOS 缩放，像素图的硬边会被插值成糊边。
    这里按倍数选缩放方式：整数倍用最近邻（硬边），16px 用块多数表决降采样。
    """
    import io
    import struct

    frames: list[tuple[int, bytes]] = []
    for n in sizes:
        if n * 2 == src.width:
            frame = half_size(src)
        elif n % src.width == 0:
            frame = src.resize((n, n), Image.NEAREST)
        else:
            frame = src.resize((n, n), Image.NEAREST)
        buf = io.BytesIO()
        frame.save(buf, 'PNG', optimize=True)
        frames.append((n, buf.getvalue()))

    header = struct.pack('<HHH', 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = b''
    for n, data in frames:
        entries += struct.pack('<BBBBHHII', n % 256, n % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.write_bytes(header + entries + b''.join(d for _, d in frames))


def pick(idx: int) -> None:
    """把选中的候选写成 favicon（多尺寸 ico + png）。"""
    src = OUT / f'cand{idx}_32.png'
    if not src.is_file():
        raise SystemExit(f'没有 {src}，先跑一次生成')
    img = Image.open(src).convert('RGBA')
    pub = Path(__file__).resolve().parents[1] / 'frontend' / 'public'
    pub.mkdir(parents=True, exist_ok=True)
    img.save(pub / 'favicon-32.png')
    img.resize((192, 192), Image.NEAREST).save(pub / 'favicon-192.png')
    write_ico(img, pub / 'favicon.ico')
    print(f'已写入 {pub}/favicon.ico · favicon-32.png · favicon-192.png')


if __name__ == '__main__':
    if '--pick' in sys.argv:
        pick(int(sys.argv[sys.argv.index('--pick') + 1]))
    else:
        sys.exit(asyncio.run(main()))
