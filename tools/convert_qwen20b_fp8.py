"""把 bf16 的 Qwen-Image 20B transformer 逐分片量化成项目自己的 fp8 缓存格式。

为什么必须离线做：20B 的 bf16 权重是 39.9GB，本机内存 32GB（可用约 21GB），
`from_pretrained(bf16)` 走不通；而 24GB 显存也放不下 bf16。项目里现成的
`_quantize_transformer_gpu` 要求整个模型先在内存里，同样走不通。

所以按分片流式处理：一次只在内存里放一片（4.7GB），量化完写出、丢掉。
量化口径与 `DiffusersProvider._quantize_transformer_gpu` **逐字一致**：
    scale = amax(|W|, dim=1) / 448        （逐输出通道）
    wq    = (W / scale).clamp(-448, 448).to(float8_e4m3fn)
产物键名与 `_load_fp8_cache` 对齐：每个 Linear 用 `<name>.w_fp8` + `<name>.w_scale`
取代 `<name>.weight`。分片存（单文件 20GB 需要一次性全塞内存，装不下）。

用法：.venv/Scripts/python.exe tools/convert_qwen20b_fp8.py [--force]
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import torch
from accelerate import init_empty_weights
from diffusers import QwenImageTransformer2DModel
from safetensors.torch import load_file, save_file

ROOT = Path(__file__).resolve().parents[1]


def _arg(flag: str, default: str) -> Path:
    """--src / --dst 可覆盖默认路径（Edit-2511 用的是同一套脚本）。"""
    if flag in sys.argv:
        return Path(sys.argv[sys.argv.index(flag) + 1]).resolve()
    return ROOT / default


SRC = _arg("--src", "models/qwen-image-20b/transformer")
DST = _arg("--dst", "models/qwen-image-20b/transformer-fp8")
INDEX = "diffusion_pytorch_model.safetensors.index.json"


def linear_names() -> set[str]:
    """用 meta 骨架问出「哪些参数属于 nn.Linear」——不靠 ndim 猜。

    猜 ndim==2 会把 `pos_embed.inv_freq` 之类也算进来，加载时形状/缓冲区对不上。
    init_empty_weights 下实例化不分配任何显存/内存，20B 也是瞬间。
    """
    cfg = QwenImageTransformer2DModel.load_config(str(SRC / "config.json"))
    with init_empty_weights():
        m = QwenImageTransformer2DModel.from_config(cfg)
    return {f"{n}.weight" for n, mod in m.named_modules()
            if isinstance(mod, torch.nn.Linear)}


def main() -> None:
    force = "--force" in sys.argv
    DST.mkdir(parents=True, exist_ok=True)
    idx = json.loads((SRC / INDEX).read_text())
    shards: dict[str, list[str]] = {}
    for key, shard in idx["weight_map"].items():
        shards.setdefault(shard, []).append(key)
    lins = linear_names()
    print(f"Linear 权重 {len(lins)} 个，分片 {len(shards)} 个", flush=True)

    new_map: dict[str, str] = {}
    t_all = time.time()
    for si, (shard, keys) in enumerate(sorted(shards.items()), 1):
        dst_name = re.sub(r"-\d+-of-", f"-{si:05d}-of-", shard)
        out_path = DST / dst_name
        if out_path.is_file() and not force:
            print(f"  跳过 {shard}（已转换）", flush=True)
            for k in keys:
                new_map[k if k not in lins else k[: -len(".weight")] + ".w_fp8"] = dst_name
                if k in lins:
                    new_map[k[: -len(".weight")] + ".w_scale"] = dst_name
            continue
        t0 = time.time()
        sd = load_file(str(SRC / shard))
        out: dict[str, torch.Tensor] = {}
        nq = 0
        for k, v in sd.items():
            if k in lins:
                w = v.float()
                scale = w.abs().amax(dim=1, keepdim=True).clamp_(min=1e-12) / 448.0
                wq = (w / scale).clamp_(-448.0, 448.0).to(torch.float8_e4m3fn)
                base = k[: -len(".weight")]
                out[base + ".w_fp8"] = wq.contiguous()
                out[base + ".w_scale"] = scale.squeeze(1).contiguous()
                new_map[base + ".w_fp8"] = dst_name
                new_map[base + ".w_scale"] = dst_name
                nq += 1
            else:
                out[k] = v.contiguous()
                new_map[k] = dst_name
        save_file(out, str(out_path), metadata={"format": "pt"})
        del sd, out
        print(f"  第 {si}/{len(shards)} 片 {shard} → {dst_name}"
              f"（量化 {nq} 个 Linear，{time.time() - t0:.0f}s）", flush=True)
        # 转完立刻删源分片：39.9GB + 20GB 两份同时躺着没必要
        try:
            (SRC / shard).unlink()
        except Exception as e:
            print(f"    （没删掉源分片：{e}）", flush=True)

    (DST / INDEX).write_text(json.dumps({"metadata": {"format": "pt"},
                                         "weight_map": new_map}, indent=1))
    cfg = json.loads((SRC / "config.json").read_text())
    (DST / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2))
    (DST / "fp8_cache_meta.json").write_text(json.dumps(
        {"format": "fp8-e4m3-rowwise-buf/v2", "source": str(SRC),
         "note": "由 tools/convert_qwen20b_fp8.py 离线生成"},
        ensure_ascii=False, indent=1))
    total = sum(f.stat().st_size for f in DST.glob("*.safetensors"))
    print(f"完成，{total / 2**30:.2f}GB，总耗时 {time.time() - t_all:.0f}s", flush=True)


if __name__ == "__main__":
    main()
