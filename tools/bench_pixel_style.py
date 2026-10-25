"""实测 Qwen-Image-2.1 的像素风产出质量：直出 vs 精修。

要回答的不是「能不能出像素风」（能），而是「要几步才变成可对齐的素材」。
同一张图出两版，量化三件事：

1. **色数**：AI 的「像素风」是上千 px 的抗锯齿插画，色数上万；真像素画
   在 16~64 色量级。
2. **块内均匀度**：按检测出的网格切块，块内 RGB 标准差的中位数。真像素画
   每块一色 → ≈0；伪像素有渐变 → 明显大于 0。这比「回放差」更不含糊：
   回放差小只说明网格对齐得好，块内标准差小才说明它真的是色块。
3. **过渡带比例**：相邻像素差落在 5~40 之间的比例。真像素画是 0 或大跳变，
   中间出现大量小差就是抗锯齿/渐变。

用法：
    .venv/Scripts/python.exe tools/bench_pixel_style.py          # 两个提示词各 1 张
    .venv/Scripts/python.exe tools/bench_pixel_style.py --n=2    # 各 2 张
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "out" / "pixel_bench"

PROMPTS = [
    ("角色", "一只穿红色披风的灰猫剑士，正面站立，双手自然下垂，尾巴垂在身后"),
    ("场景", "一间石头砌成的小屋，木门，屋顶有烟囱，旁边一棵树"),
]


# --------------------------------------------------------------------------- #
# 指标
# --------------------------------------------------------------------------- #
def color_count(img: Image.Image) -> int:
    a = np.asarray(img.convert("RGB")).reshape(-1, 3)
    return int(len(np.unique(a, axis=0)))


def block_uniformity(img: Image.Image, grid: tuple[int, int] | None) -> float | None:
    """按网格切块，块内标准差的**中位数**（0~255 尺度）。越小越像素。"""
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    if grid:
        bw = max(2, w // max(1, grid[0]))
        bh = max(2, h // max(1, grid[1]))
    else:
        bw = bh = 8          # 没检测到网格时按 8px 估（AI 像素风常落 6~10px）
    stds = []
    for y in range(0, h - bh + 1, bh):
        for x in range(0, w - bw + 1, bw):
            blk = a[y:y + bh, x:x + bw].reshape(-1, 3)
            stds.append(float(blk.std(axis=0).mean()))
    return round(float(np.median(stds)), 2) if stds else None


def transition_ratio(img: Image.Image) -> float:
    """相邻像素差在 5~40 的比例 —— 抗锯齿/渐变的指纹。"""
    a = np.asarray(img.convert("L")).astype(np.int16)
    d = np.concatenate([np.abs(np.diff(a, axis=1)).ravel(),
                        np.abs(np.diff(a, axis=0)).ravel()])
    return round(float(((d >= 5) & (d <= 40)).mean() * 100), 2)


def analyze(tag: str, img: Image.Image, grid=None) -> dict:
    return {
        "tag": tag,
        "size": list(img.size),
        "colors": color_count(img),
        "transitionPct": transition_ratio(img),
        "blockStd": block_uniformity(img, grid),
        "grid": list(grid) if grid else None,
    }


def side_by_side(raw: Image.Image, ref: Image.Image, path: Path) -> None:
    """左边原图裁块放大，右边精修图放大 —— 同一视觉尺度，肉眼看色块差别。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    box = 220
    w, h = raw.size
    bw = bh = min(box, w, h)
    x0 = max(0, (w - bw) // 2)
    y0 = max(0, (h - bh) // 3)
    a = raw.crop((x0, y0, x0 + bw, y0 + bh)).resize((880, 880), Image.NEAREST)

    scale = max(1, round(880 / max(1, ref.width)))
    b = ref.resize((ref.width * scale, ref.height * scale), Image.NEAREST)
    if b.height > 880:
        top = max(0, (b.height - 880) // 3)
        b = b.crop((0, top, b.width, top + 880))
    canvas = Image.new("RGB", (a.width + b.width + 14, max(a.height, b.height)),
                       (24, 26, 32))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 14, 0))
    canvas.save(path)


# --------------------------------------------------------------------------- #
async def main() -> int:
    n_run = 1
    for a in sys.argv[1:]:
        if a.startswith("--n"):
            n_run = int(a.split("=")[-1]) if "=" in a else int(a[3:] or 1)

    from backend.app.config import load_settings
    from backend.app.providers.diffusers_local import DiffusersProvider
    from backend.app.pipelines.refine import refine_one
    from backend.app.pipelines.palette import extract_palette
    from backend.app.styles import build_prompt, resolve_pixel_negative

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    provider = DiffusersProvider(cfg)
    OUT.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for name, body in PROMPTS:
        built = build_prompt(body, "pixel_16bit", pixel=True, grid=[64, 64],
                             colors=16, background="gray")
        neg = resolve_pixel_negative("", True, "solid")
        print(f"\n=== {name} ===  提示词 {len(built['prompt'])} 字，负面词 {len(neg)} 字")
        for k in range(n_run):
            seed = 1234 + k
            t0 = time.time()
            res = await provider.generate(
                prompt=built["prompt"], width=1024, height=1024, steps=40,
                seed=seed, n=1, max_seq_len=1024, negative=neg or None)
            raw = res.images[0].convert("RGB")
            print(f"  出图（seed {seed}）{time.time() - t0:.1f}s  {raw.size}")
            raw.save(OUT / f"{name}_{seed}_raw.png")

            # 网格对齐（先不量化，拿到网格尺寸用于切块统计）
            snap = refine_one(raw, grid_snap=True, palette=None)
            grid = tuple(snap["meta"]["grid"]) if snap["meta"].get("grid") else None

            # 16 色量化（调色板从这张图自身提取，与生成卡默认一致）
            pal = extract_palette([raw], 16)
            ref = refine_one(raw, grid_snap=True, palette=pal)["image"].convert("RGB")
            ref.save(OUT / f"{name}_{seed}_refined16.png")

            m_raw = analyze(f"{name}_{seed}_raw", raw, grid)
            m_ref = analyze(f"{name}_{seed}_refined16", ref, grid)
            rows += [m_raw, m_ref]
            print(f"    原图   色数 {m_raw['colors']:>7}  过渡带 {m_raw['transitionPct']:>5}%  "
                  f"块内标准差 {m_raw['blockStd']}")
            print(f"    精修16 色数 {m_ref['colors']:>7}  过渡带 {m_ref['transitionPct']:>5}%  "
                  f"块内标准差 {m_ref['blockStd']}   网格 {grid} → {ref.size}")

            side_by_side(raw, ref, OUT / f"{name}_{seed}_compare.png")

    (OUT / "metrics.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n产物目录：{OUT}")
    for label, suffix in (("原图", "raw"), ("精修16", "refined16")):
        g = [r for r in rows if r["tag"].endswith(suffix)]
        if not g:
            continue
        print(f"  {label}: 色数 {np.mean([x['colors'] for x in g]):.0f}  "
              f"过渡带 {np.mean([x['transitionPct'] for x in g]):.1f}%  "
              f"块内标准差 {np.mean([x['blockStd'] for x in g]):.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
