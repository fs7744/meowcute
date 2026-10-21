# -*- coding: utf-8 -*-
"""实验 A：Qwen-Image-Edit-2511 的 Lightning 步数蒸馏 LoRA 对跑。

同一张基准图、同一句真实立绘指令（八方向推导用的中文指令）、同一个 seed，
跑三种配置：40 步基线 / Lightning 8 步 / Lightning 4 步，记录耗时与峰值显存，
把产出图写到 .verify/exp-a/ 供人工比对。

用法：backend/../.venv/Scripts/python.exe .verify/exp_lightning.py
"""
import asyncio
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

from PIL import Image  # noqa: E402

from backend.app import storage  # noqa: E402
from backend.app.pipelines.walkvideo import direction_instruction  # noqa: E402
from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402

PID = "p-8595c050a48d"
BASE_ASSET = "out-20261021-085505-1-8dac.png"
OUT_DIR = ROOT / ".verify" / "exp-a"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def vram_used_gb() -> float:
    """进程外视角的显存占用（nvidia-smi，单位 GB）。"""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True).stdout.strip()
        return int(out.split("\n")[0]) / 1024
    except Exception:
        return -1.0


async def run_one(provider: DiffusersProvider, base: Image.Image, tag: str,
                  lightning: object) -> dict:
    import torch

    provider.detach_lightning()
    pipe = provider._load(provider._resolve_model())
    if lightning:
        print(f"  挂载 Lightning {lightning} 步…", flush=True)
        path = provider._attach_lightning(pipe, int(lightning))
        print(f"    {Path(path).name}", flush=True)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()

    t0 = time.time()
    res = await provider.generate(
        prompt=direction_instruction("left"),
        width=384, height=512, steps=40, seed=20261021, n=1,
        refs=[base], task_id=None,
    )
    dt = time.time() - t0

    peak_gb = -1.0
    if torch.cuda.is_available():
        peak_gb = torch.cuda.max_memory_allocated() / 2**30
    sys_vram = vram_used_gb()

    img = res.images[0]
    out_path = OUT_DIR / f"left-{tag}.png"
    img.save(out_path)
    meta = dict(res.meta or {})
    print(f"  [{tag:>10}] {dt:5.1f}s  峰值 {peak_gb:5.2f}GB  系统显存 {sys_vram:4.1f}GB"
          f"  步数 {meta.get('steps')}  true_cfg {meta.get('trueCfg')}"
          f"  → {out_path.name}", flush=True)
    return {"tag": tag, "seconds": round(dt, 1), "peakGB": round(peak_gb, 2),
            "sysVRAM": sys_vram, "steps": meta.get("steps"),
            "trueCfg": meta.get("trueCfg"), "path": str(out_path)}


async def main() -> int:
    base = storage.load_image(PID, "images", BASE_ASSET)
    if base is None:
        print("基准图不存在")
        return 1
    print(f"基准图 {BASE_ASSET} {base.size}", flush=True)
    print(f"指令：{direction_instruction('left')[:60]}…", flush=True)

    cfg = {"model": "models/qwen-image-edit-2511"}
    provider = DiffusersProvider(cfg)

    print("加载底座（fp8 + offload）…", flush=True)
    t_load = time.time()
    provider._load(provider._resolve_model())
    print(f"  加载完成 {time.time() - t_load:.1f}s", flush=True)

    rows = []
    rows.append(await run_one(provider, base, "base40", None))
    rows.append(await run_one(provider, base, "light8", 8))
    rows.append(await run_one(provider, base, "light4", 4))

    print("\n汇总：")
    print(f"{'配置':>10} {'步数':>4} {'耗时':>7} {'峰值GB':>7}")
    for r in rows:
        print(f"{r['tag']:>10} {str(r['steps']):>4} {r['seconds']:>6.1f}s {r['peakGB']:>7.2f}")

    import json
    (OUT_DIR / "result.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
