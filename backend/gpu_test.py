"""真实 GPU 生成验证：Qwen-Image-2.1 文生图 → 像素精修 → 落盘。

用法：python backend/gpu_test.py [--refs]
产物：backend/_gpu_out/*.png（原图 + 精修图，供人眼检查）
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("MEOWCUTE_DATA_DIR", tempfile.mkdtemp(prefix="meowcute-gpu-"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.config import load_settings  # noqa: E402
from backend.app.pipelines import refine as refine_mod  # noqa: E402
from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402
from backend.app.styles import build_prompt  # noqa: E402

OUT = Path(__file__).resolve().parent / "_gpu_out"


async def main() -> int:
    OUT.mkdir(exist_ok=True)
    s = load_settings()
    cfg = (s.get("providers") or {}).get("diffusers") or {}
    provider = DiffusersProvider(cfg)

    t = await provider.test()
    print("模型状态：", t.get("message"), flush=True)

    built = build_prompt(
        "灰白英短猫武士，红色短披风，腰挎武士刀，三头身比例，正面站立",
        "pixel_16bit", pixel=True, grid=[64, 64], colors=16)
    print("提示词：", built["prompt"], flush=True)

    t0 = time.time()
    res = await provider.generate(
        prompt=built["prompt"], width=512, height=512, steps=9,
        seed=12345, n=1, task_id=None)
    gen_sec = time.time() - t0
    img = res.images[0]
    img.save(OUT / "raw.png")
    print(f"生成完成：{gen_sec:.1f}s · {img.size} · meta={ {k: v for k, v in res.meta.items() if k in ('model','family','steps','mode')} }", flush=True)

    t1 = time.time()
    rb = refine_mod.refine_batch([img], grid_snap=True, colors=16, palette_name="auto")
    ref_sec = time.time() - t1
    out = rb["items"][0]
    out["image"].save(OUT / "refined.png")
    # 放大 8 倍方便人眼检查（最近邻，保硬边）
    disp = out["image"].resize(
        (out["image"].width * 8, out["image"].height * 8), 0)
    disp.save(OUT / "refined_x8.png")
    print(f"精修完成：{ref_sec:.2f}s · meta={out['meta']}", flush=True)
    print(f"产物目录：{OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
