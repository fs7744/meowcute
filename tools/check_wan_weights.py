"""校验 Wan2.2-TI2V-5B 权重文件是否损坏。

不看文件在不在（ls 就能看），而是把每个 safetensors 真正读一遍：
- header 合法 + 最后一个张量的字节能否完整读到（截断是下载中断最常见的隐形损伤）；
- 每张量统计：dtype / shape / abs 均值 / max / NaN / Inf / 全零；
- 汇总异常清单。单\index 张量的局部损坏不会让模型加载失败，只会让输出崩坏，
  这正是「文件看着全、效果却很差」要排查的东西。

用法：python tools/check_wan_weights.py [--model 路径] [--all]
默认只抽样每个文件 12 个张量（够抓出成片乱码），--all 全量读（慢，约几分钟）。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import torch
from safetensors.torch import safe_open

DEFAULT_MODEL = Path(__file__).resolve().parents[1] / "models" / "wan2.2-ti2v-5b"

# bf16 权重合理范围：Wan DiT 大多在 1e-3~1e-1，单张量 max 超过 60 基本是坏的
WARN_ABS_MAX = 60.0
WARN_ABS_MEAN_LO = 1e-6  # 比这还小 → 疑似全零/未初始化


def _stat_tensor(name: str, t: torch.Tensor) -> dict:
    tf = t.detach().to(torch.float32)
    finite = torch.isfinite(tf)
    n = tf.numel()
    nan = int((~finite).sum())
    # NaN 参与 max/min 会污染结果，先把非有限值替换掉再算
    safe = torch.where(finite, tf, torch.zeros((), dtype=torch.float32))
    amax = float(safe.abs().max()) if n else 0.0
    amean = float(safe.abs().mean()) if n else 0.0
    zero = int((safe == 0).sum())
    return {
        "name": name,
        "shape": list(t.shape),
        "dtype": str(t.dtype).replace("torch.", ""),
        "numel": n,
        "abs_mean": round(amean, 6),
        "abs_max": round(amax, 4),
        "nan": nan,
        "zero_ratio": round(zero / n, 4) if n else 1.0,
        "bad": bool(nan > 0 or not math.isfinite(amean) or amax > WARN_ABS_MAX),
    }


def check_file(path: Path, sample: int | None) -> tuple[list[dict], list[str]]:
    """读一个 safetensors 文件。sample=None 表示全量。"""
    stats: list[dict] = []
    errs: list[str] = []
    with safe_open(str(path), framework="pt", device="cpu") as f:
        keys = list(f.keys())
        # 有意把「最后一个张量」留在抽样里：文件被截断时它最先读不出来
        idxs = list(range(len(keys))) if sample is None else _pick_idx(len(keys), sample)
        for i in idxs:
            k = keys[i]
            try:
                t = f.get_tensor(k)
                stats.append(_stat_tensor(k, t))
            except Exception as e:  # 读不出来就是硬损伤，记录下来继续
                errs.append(f"{k}: {type(e).__name__}: {e}")
    return stats, errs


def _pick_idx(n: int, sample: int) -> list[int]:
    if n <= sample:
        return list(range(n))
    step = max(1, n // sample)
    idxs = sorted(set(list(range(0, n, step))[:sample] + [0, n - 1]))
    return idxs


def scan_dir(tag: str, d: Path, sample: int | None) -> dict:
    files = sorted(d.glob("*.safetensors"))
    print(f"\n=== {tag}（{d}）—— {len(files)} 个分片 ===", flush=True)
    out = {"dir": str(d), "files": [], "errors": [], "bad": []}
    for fp in files:
        t0 = time.time()
        try:
            stats, errs = check_file(fp, sample)
        except Exception as e:
            errs = [f"文件打不开：{type(e).__name__}: {e}"]
            stats = []
        size_gb = fp.stat().st_size / 2**30
        mean_all = (sum(s["abs_mean"] for s in stats) / len(stats)) if stats else 0.0
        max_all = max((s["abs_max"] for s in stats), default=0.0)
        nan_all = sum(s["nan"] for s in stats)
        bad = [s for s in stats if s["bad"]]
        flag = "OK " if not errs and not bad else "!! "
        print(f"{flag}{fp.name:<52} {size_gb:6.2f}GB  "
              f"抽检 {len(stats):3d} 张量  mean={mean_all:.5f} max={max_all:8.3f} "
              f"nan={nan_all}  ({time.time() - t0:.0f}s)", flush=True)
        for e in errs:
            print(f"    读取失败 {e}", flush=True)
        for s in bad:
            print(f"    异常张量 {s['name']} mean={s['abs_mean']} max={s['abs_max']} "
                  f"nan={s['nan']} zero={s['zero_ratio']}", flush=True)
        out["files"].append({
            "file": fp.name, "sizeGB": round(size_gb, 2), "sampled": len(stats),
            "abs_mean": round(mean_all, 6), "abs_max": round(max_all, 4),
            "nan": nan_all, "errors": errs,
            "bad": [s["name"] for s in bad],
        })
        out["errors"] += [f"{fp.name}: {e}" for e in errs]
        out["bad"] += [f"{fp.name}:{s['name']}" for s in bad]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=str(DEFAULT_MODEL))
    ap.add_argument("--all", action="store_true", help="全量读取（慢）")
    ap.add_argument("--json", default="", help="把结果写成 JSON 报告")
    args = ap.parse_args()

    root = Path(args.model)
    if not root.is_dir():
        print(f"模型目录不存在：{root}")
        return 2
    sample = None if args.all else 12
    report = {"model": str(root), "sample": sample, "parts": {}}
    for tag, sub in (("transformer bf16", "transformer"),
                     ("transformer fp8 缓存", "transformer-fp8"),
                     ("text_encoder umT5-xxl", "text_encoder"),
                     ("vae", "vae")):
        d = root / sub
        if d.is_dir():
            report["parts"][sub] = scan_dir(tag, d, sample)

    total_bad = sum(len(p["bad"]) + len(p["errors"]) for p in report["parts"].values())
    print("\n================ 结论 ================")
    if total_bad == 0:
        print("未发现损坏：所有分片的头尾张量都能读出，抽样无 NaN/Inf/越界值。")
        print("→ 若仍「效果很差」，问题更可能在量化(fp8)或采样参数，而不是文件本身。")
    else:
        print(f"发现 {total_bad} 处异常，详见上面 !! 行。")
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=1))
        print(f"报告已写 {args.json}")
    return 0 if total_bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
