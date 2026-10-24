"""单独基准：HunyuanVideo-1.5 DiT 一次 forward 的真实耗时（排查"一步都不走"）。

用法：python tools/hy15_bench.py --frames 16 6
frames 是 latent 帧数：61 像素帧 -> 16 latent 帧（(61-1)/4+1）
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parent.parent
CKPT = ROOT / "models" / "hunyuanvideo-1.5"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, nargs="+", default=[16])
    ap.add_argument("--seq-text", type=int, default=1985)  # 729 image + 256 byt5 + 1000 mllm
    ap.add_argument("--height", type=int, default=39)
    ap.add_argument("--width", type=int, default=41)
    ap.add_argument("--offload", action="store_true", help="不常驻 GPU，每次 forward 前后搬运")
    args = ap.parse_args()

    from diffusers import HunyuanVideo15Transformer3DModel

    t0 = time.time()
    print("loading transformer ...", flush=True)
    model = HunyuanVideo15Transformer3DModel.from_pretrained(CKPT / "transformer-diffusers", torch_dtype=torch.bfloat16)
    model.eval()
    if not args.offload:
        model.to("cuda")
    print(f"loaded in {time.time()-t0:.1f}s, VRAM {torch.cuda.memory_allocated()/1024**3:.1f}G", flush=True)

    for f in args.frames:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        hs = torch.randn(1, 65, f, args.height, args.width, dtype=torch.bfloat16, device="cuda")
        text = torch.randn(1, args.seq_text, 3584, dtype=torch.bfloat16, device="cuda")
        text2 = torch.randn(1, args.seq_text, 1472, dtype=torch.bfloat16, device="cuda")
        img = torch.randn(1, 729, 1152, dtype=torch.bfloat16, device="cuda")
        mask = torch.ones(1, args.seq_text, dtype=torch.bool, device="cuda")
        # 管线里会把 timestep cast 成 latent dtype
        ts = torch.tensor([500.0], device="cuda", dtype=torch.bfloat16)
        tsr = torch.tensor([400.0], device="cuda", dtype=torch.bfloat16)

        toks = f * (args.height // 1) * (args.width // 1)
        print(f"--- latent frames={f}, image tokens={toks} ---", flush=True)
        for i in range(2):
            torch.cuda.synchronize()
            t1 = time.time()
            with torch.no_grad():
                model(
                    hidden_states=hs,
                    timestep=ts,
                    timestep_r=tsr,
                    encoder_hidden_states=text,
                    encoder_attention_mask=mask,
                    encoder_hidden_states_2=text2,
                    encoder_attention_mask_2=mask,
                    image_embeds=img,
                )
            torch.cuda.synchronize()
            dt = time.time() - t1
            peak = torch.cuda.max_memory_allocated() / 1024**3
            print(f"  run{i}: {dt*1000:.0f} ms, peak VRAM {peak:.1f}G", flush=True)
            if i == 0 and dt > 120:
                print("  首次就超过 120s，判定异常，停止后续", flush=True)
                return 2
        del hs, text, text2, img, mask
        torch.cuda.empty_cache()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
