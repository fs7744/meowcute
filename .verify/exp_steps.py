# -*- coding: utf-8 -*-
"""步数-质量曲线：不挂 LoRA，正常 true_cfg=4.0，只改步数。

用途：找出「画质还能接受」的最少步数。立绘推导是八方向链最慢的一段，
若 20~24 步画质与 40 步无明显差别，把默认步数降下来就是零依赖的提速。

用法：python .verify/exp_steps.py 20 24
"""
import asyncio
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

from backend.app import storage  # noqa: E402
from backend.app.pipelines.walkvideo import direction_instruction  # noqa: E402
from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402

PID = "p-8595c050a48d"
BASE = "out-20261021-085505-1-8dac.png"
OUT = ROOT / ".verify" / "exp-a"
OUT.mkdir(parents=True, exist_ok=True)


async def main(steps_list) -> int:
    import torch
    base = storage.load_image(PID, "images", BASE)
    p = DiffusersProvider({"model": "models/qwen-image-edit-2511"})
    p._load(p._resolve_model())
    p._lightning_steps = None           # 不挂 LoRA，走标准 true_cfg=4.0

    for n in steps_list:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
        t0 = time.time()
        res = await p.generate(
            prompt=direction_instruction("left"),
            width=384, height=512, steps=int(n), seed=20261021, n=1,
            refs=[base], task_id=None,
        )
        dt = time.time() - t0
        peak = (torch.cuda.max_memory_allocated() / 2**30
                if torch.cuda.is_available() else -1)
        out = OUT / f"left-steps{n}.png"
        res.images[0].save(out)
        meta = res.meta or {}
        print(f"[{n:>2} 步] {dt:5.1f}s  峰值 {peak:5.2f}GB  "
              f"true_cfg {meta.get('trueCfg')} → {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    args = [int(x) for x in sys.argv[1:]] or [20]
    sys.exit(asyncio.run(main(args)))
