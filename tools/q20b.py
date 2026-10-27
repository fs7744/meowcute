"""Qwen-Image 20B（fp8 分片）的最小加载器 —— 只为验证 ControlNet 能不能定姿势。

为什么不能用 diffusers 的 from_pretrained：
  · bf16 40GB，内存 32GB 装不下（本机可用约 21GB）；
  · 项目里的 `_load_fp8_cache` 会把整个 20GB 一次性 load 进内存再 assign；
  · 这里改成**逐分片 mmap → 直接丢到显存**，内存峰值只有一片（2.3GB）。

产物口径与 `DiffusersProvider._quantize_transformer_gpu` 一致：
每层 Linear 用 `w_fp8` + `w_scale` 两个 buffer 代替 bf16 的 `weight`，
类换成 `_FP8Linear`（torch._scaled_mm 前向）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import torch
from accelerate import init_empty_weights
from safetensors.torch import load_file

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

FP8_INDEX = "diffusion_pytorch_model.safetensors.index.json"


def load_fp8_transformer(dst: Path, device: str = "cuda",
                         verbose: bool = True) -> Any:
    """从 tools/convert_qwen20b_fp8.py 产出的分片目录搭出 fp8 transformer。"""
    from diffusers import QwenImageTransformer2DModel

    from app.providers.diffusers_local import _get_fp8_linear_cls

    fp8_cls = _get_fp8_linear_cls()
    cfg = QwenImageTransformer2DModel.load_config(str(dst / "config.json"))
    with init_empty_weights():
        model = QwenImageTransformer2DModel.from_config(cfg)

    idx = json.loads((dst / FP8_INDEX).read_text())["weight_map"]
    targets = sorted({k[: -len(".w_fp8")] for k in idx if k.endswith(".w_fp8")})
    for t in targets:
        mod = model.get_submodule(t)
        mod.weight = torch.nn.Parameter(
            torch.empty(0, dtype=torch.bfloat16, device=device), requires_grad=False)

    shards: dict[str, list[str]] = {}
    for k, shard in idx.items():
        shards.setdefault(shard, []).append(k)
    for i, (shard, keys) in enumerate(sorted(shards.items()), 1):
        sd = load_file(str(dst / shard), device=device)
        # 不用 load_state_dict：它对 assign=True 也照样做形状校验，而骨架里
        # 的 w_fp8/w_scale 是 0 长度占位（2026-10-27 报了一屏 size mismatch）。
        # 每个 key 只出现在一个分片里，逐键直接落到模块上最省事。
        for k, v in sd.items():
            if k.endswith(".w_fp8") or k.endswith(".w_scale"):
                name = k.rsplit(".", 1)[1]
                mod = model.get_submodule(k.rsplit(".", 1)[0])
                mod.register_buffer(name, v, persistent=False)
                continue
            src, pname = k.rsplit(".", 1)
            mod = model.get_submodule(src)
            if pname in mod._parameters and mod._parameters[pname] is not None:
                mod._parameters[pname] = torch.nn.Parameter(v, requires_grad=False)
            else:
                mod._buffers[pname] = v
        del sd
        if verbose:
            print(f"    [fp8] {i}/{len(shards)} {shard}", flush=True)

    for t in targets:
        model.get_submodule(t).__class__ = fp8_cls
    n = sum(1 for m in model.modules() if isinstance(m, fp8_cls))
    if n != len(targets):
        raise RuntimeError(f"fp8 层数不符：{n} != {len(targets)}")

    stuck = [k for k, v in list(model.named_parameters()) + list(model.named_buffers())
             if getattr(v, "is_meta", False)]
    if stuck:
        raise RuntimeError(f"{len(stuck)} 个参数停在 meta（未加载）：{stuck[:5]}")
    model.eval()
    if verbose:
        print(f"    [fp8] 就绪：{len(targets)} 个 Linear，"
              f"{torch.cuda.memory_allocated() / 2**30:.1f}GB 显存", flush=True)
    return model
