# -*- coding: utf-8 -*-
"""实验 A 的对照：不挂 LoRA，只把步数降到 8 并关掉 true_cfg。

用来区分两种可能：
  - 若「无 LoRA 的 8 步」就很快 → 慢的是 LoRA 本身（fp8 + LoRA 叠加把 offload 打乱了）
  - 若「无 LoRA 的 8 步」也接近 100s → 慢的是固定开销（提示词/参考图编码、VAE），
    那 Lightning 对本链路没意义，步数根本不是瓶颈
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


async def main() -> int:
    import torch
    base = storage.load_image(PID, "images", BASE)
    p = DiffusersProvider({"model": "models/qwen-image-edit-2511"})
    p._load(p._resolve_model())

    # 关键：只设置步数开关，不调 load_lora_weights —— 管线上没有任何 LoRA 权重
    p._lightning_steps = 8

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    res = await p.generate(
        prompt=direction_instruction("left"),
        width=384, height=512, steps=40, seed=20261021, n=1,
        refs=[base], task_id=None,
    )
    dt = time.time() - t0
    peak = torch.cuda.max_memory_allocated() / 2**30 if torch.cuda.is_available() else -1
    out = OUT / "left-ctrl8-nolora.png"
    res.images[0].save(out)
    print(f"[ctrl8 无LoRA] {dt:5.1f}s  峰值 {peak:5.2f}GB  "
          f"步数 {(res.meta or {}).get('steps')}  true_cfg {(res.meta or {}).get('trueCfg')}"
          f"  → {out.name}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
