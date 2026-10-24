"""把 HunyuanVideo-1.5 原版（hyvideo 格式）权重转换成 diffusers 格式。

本机 32G 内存装不下官方脚本的全量载入（DiT 是 fp32 31.8G），所以：
  - 源权重用 safe_open 按 key 懒加载（一次只驻留一个张量）
  - 转换逻辑直接复用官方 scripts/convert_hunyuan_video1_5_to_diffusers.py 的
    映射函数（运行时抽取源码并注入流式写盘，映射规则不做手工重写）
  - 输出 bf16，约 16.6G

用法：
    python tools/hy15_convert.py --what transformer
    python tools/hy15_convert.py --what vae
    python tools/hy15_convert.py --what all --check
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "models" / "hunyuanvideo-1.5"
SRC_TRANSFORMER = CKPT / "transformer" / "480p_i2v_step_distilled"
OUT_TRANSFORMER = CKPT / "transformer-diffusers"
OUT_VAE = CKPT / "vae-diffusers"
OFFICIAL = ROOT / "tools" / "convert_hy15.py"
TYPE = "480p_i2v_step_distilled"


class LazyStateDict:
    """只支持 pop()：按需从 safetensors 读单个张量，不在内存里放整份权重。"""

    def __init__(self, path: Path):
        from safetensors import safe_open

        self._f = safe_open(str(path), framework="pt")
        self._keys = set(self._f.keys())
        self._used: set[str] = set()
        self.loaded = 0

    def pop(self, name: str):
        if name not in self._keys:
            raise KeyError(f"原权重里没有 key: {name}")
        self._used.add(name)
        self.loaded += 1
        return self._f.get_tensor(name)

    def remaining(self):
        return sorted(self._keys - self._used)


class StreamSafetensorsWriter:
    """模拟 `d[名字] = 张量`，立刻写进临时数据文件，最后拼成合法 safetensors。"""

    def __init__(self, final_path: Path, dtype):
        import torch

        self.final = Path(final_path)
        self.tmp = self.final.with_suffix(".data.tmp")
        self.final.parent.mkdir(parents=True, exist_ok=True)
        self.f = open(self.tmp, "wb")
        self.header: dict[str, dict] = {}
        self.offset = 0
        self.dtype = dtype
        self.torch = torch
        self.DTYPES = {torch.bfloat16: "BF16", torch.float32: "F32", torch.float16: "F16",
                       torch.int64: "I64", torch.int32: "I32"}

    def __setitem__(self, name: str, tensor):
        t = tensor.detach().cpu().contiguous()
        if t.is_floating_point():
            t = t.to(self.dtype)
        if t.dtype not in self.DTYPES:
            raise TypeError(f"不支持的 dtype {t.dtype}（{name}）")
        raw = t.view(self.torch.uint16).numpy().tobytes() if t.dtype == self.torch.bfloat16 else t.numpy().tobytes()
        n = len(raw)
        self.header[name] = {"dtype": self.DTYPES[t.dtype], "shape": list(t.shape),
                             "data_offsets": [self.offset, self.offset + n]}
        self.f.write(raw)
        self.offset += n

    def close(self):
        self.f.close()
        # safetensors 的 data_offsets 是相对「数据区起点」的偏移（从 0 开始），
        # 不是文件绝对偏移；header 用空格补到 8 字节对齐
        hdr = {k: {"dtype": v["dtype"], "shape": v["shape"],
                   "data_offsets": list(v["data_offsets"])} for k, v in self.header.items()}
        raw = json.dumps(hdr, separators=(",", ":")).encode("utf-8")
        raw += b" " * ((8 - len(raw) % 8) % 8)
        with open(self.final, "wb") as out:
            out.write(struct.pack("<Q", len(raw)))
            out.write(raw)
            with open(self.tmp, "rb") as src:
                shutil.copyfileobj(src, out, 16 * 1024 * 1024)
        self.tmp.unlink()
        print(f"  写出 {len(self.header)} 个张量 -> {self.final} ({self.final.stat().st_size/1024**3:.1f} GB)")


def _load_official():
    """把官方脚本当模块加载，拿到映射函数和配置表。"""
    import importlib.util

    spec = importlib.util.spec_from_file_location("official_convert_hy15", OFFICIAL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def convert_transformer():
    import torch
    from accelerate import init_empty_weights
    from diffusers import HunyuanVideo15Transformer3DModel

    off = _load_official()
    cfg = off.TRANSFORMER_CONFIGS[TYPE]
    print(f"配置: {cfg}")

    with init_empty_weights():
        model = HunyuanVideo15Transformer3DModel(**cfg)
    expected = set(model.state_dict().keys())
    print(f"目标模型期望 {len(expected)} 个张量")

    # 抽取官方映射函数源码，把「建普通 dict」换成流式写盘
    src = OFFICIAL.read_text(encoding="utf-8")
    start = src.index("def convert_hyvideo15_transformer_to_diffusers")
    end = src.index("\n    return converted_state_dict", start) + len("\n    return converted_state_dict")
    body = src[start:end]
    body = body.replace(
        "    converted_state_dict = {}",
        "    converted_state_dict = _WRITER",
    )
    body = body.replace(
        "\n    return converted_state_dict",
        "\n    converted_state_dict.close()\n    return None",
    )
    ns = {
        "torch": torch,
        "swap_scale_shift": off.swap_scale_shift,
        "_WRITER": StreamSafetensorsWriter(OUT_TRANSFORMER / "diffusion_pytorch_model.safetensors", torch.bfloat16),
    }
    exec(compile(body, str(OFFICIAL), "exec"), ns)  # noqa: S102
    convert_fn = ns["convert_hyvideo15_transformer_to_diffusers"]

    lazy = LazyStateDict(SRC_TRANSFORMER / "diffusion_pytorch_model.safetensors")
    print(f"原权重 {len(lazy._keys)} 个张量，开始流式转换 ...")
    convert_fn(lazy, config=model.config)

    got = set(ns["_WRITER"].header.keys())
    missing, extra = expected - got, got - expected
    if missing or extra:
        print(f"  [WARN] 键不匹配: 缺 {len(missing)} 多余 {len(extra)}")
        for k in sorted(missing)[:5]:
            print("   - 缺", k)
        for k in sorted(extra)[:5]:
            print("   + 多", k)
    else:
        print("  键完全匹配 ✓")
    left = lazy.remaining()
    if left:
        print(f"  [INFO] 原权重剩 {len(left)} 个未使用（如 EMA），示例: {left[:3]}")

    # diffusers 0.41 的 model.config 是 FrozenDict，没有 save_pretrained，手动落盘
    cfg_out = dict(model.config)
    cfg_out["_class_name"] = "HunyuanVideo15Transformer3DModel"
    cfg_out["_diffusers_version"] = "0.41.0"
    (OUT_TRANSFORMER / "config.json").write_text(
        json.dumps(cfg_out, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"  config -> {OUT_TRANSFORMER / 'config.json'}")


def convert_vae():
    import torch
    from accelerate import init_empty_weights
    from diffusers import AutoencoderKLHunyuanVideo15
    from safetensors.torch import load_file

    off = _load_official()
    print("读取原 VAE ...")
    sd = load_file(SRC_TRANSFORMER.parent.parent / "vae" / "diffusion_pytorch_model.safetensors")
    with init_empty_weights():
        vae = AutoencoderKLHunyuanVideo15()
    with init_empty_weights():
        meta_keys = set(vae.state_dict().keys())
    converted = off.convert_hunyuan_video_15_vae_checkpoint_to_diffusers(sd)
    missing, extra = meta_keys - set(converted), set(converted) - meta_keys
    if missing or extra:
        print(f"  [WARN] VAE 键不匹配: 缺 {len(missing)} 多余 {len(extra)}；缺失示例 {sorted(missing)[:3]}")
    else:
        print("  VAE 键完全匹配 ✓")
    del sd
    with init_empty_weights():
        vae = AutoencoderKLHunyuanVideo15()
    vae.load_state_dict({k: v.to(torch.bfloat16) for k, v in converted.items()}, strict=True, assign=True)
    vae.save_pretrained(OUT_VAE, safe_serialization=True)
    print(f"  VAE -> {OUT_VAE}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--what", choices=["transformer", "vae", "all"], default="all")
    args = ap.parse_args()
    if args.what in ("transformer", "all"):
        convert_transformer()
    if args.what in ("vae", "all"):
        convert_vae()
    return 0


if __name__ == "__main__":
    sys.exit(main())
