"""严格 A/B：I2V 同一条件下的 offload vs resident（临时脚本，可删）。

为什么要这个脚本：之前「1.88x」的结论是用 **T2V** 探针（offload 3.40s/步）对比出来的，
但在**实际场景 I2V** 里，实验 B 观察到的 offload 单步只有 ~1.84-2.05s/步——
如果基线本就那么低，1.88x 就是被 T2V/I2V 差异污染出来的错觉。

本脚本只让**一个变量**变化（是否 offload），其余全部一致：
同一参考图、同一提示词、同一 I2V 管线、同为 21 帧、同一 seed。
并且都只测**去噪**（output_type="latent"），绕开「resident 解码会 OOM」这个已知差异，
从而干净地隔离出去噪单步的真实差距。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = ROOT / "data/projects/p-0d3f091b4c3c/images/vs-20261025-221119-46635a63.png"
PROMPT = "角色侧视行走循环：双腿自然交替迈步，步态平稳连贯，取景框与背景完全静止。"
NEGATIVE = "模糊, 重影, 低清, 闪烁, 肢体变形, 站桩, 单帧定格"
FRAMES = 21
WARMUP, TIMED = 2, 4


def measure(pipe, embeds, image, frames):
    import torch

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]

    def once(steps):
        return pipe(
            prompt=None,
            prompt_embeds=pe, prompt_embeds_mask=pem,
            prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
            negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
            negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
            image=image, num_frames=frames, num_inference_steps=steps,
            generator=torch.Generator("cuda").manual_seed(42),
            output_type="latent",
        )

    try:
        _w = once(WARMUP)
        del _w
        dev = str(next(pipe.transformer.parameters()).device)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.time()
        once(TIMED)
        torch.cuda.synchronize()
        dt = time.time() - t0
        return dt / TIMED, round(torch.cuda.max_memory_allocated() / 2**30, 2), dev
    except Exception as e:
        print(f"    FAILED: {e.__class__.__name__}: {str(e)[:160]}", flush=True)
        return None, None, None


def main() -> None:
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    torch.cuda.set_per_process_memory_fraction(0.92)
    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)

    results = {}
    for mode, resident in (("offload", False), ("resident", True)):
        print(f"\n[AB] === {mode} ===", flush=True)
        hy15.release()                       # 每次换模式必须重建（体验证不允许热切）
        torch.cuda.empty_cache()
        pipe = hy15.get_pipeline(None, t2v=False, resident=resident)
        print(f"  DiT device={next(pipe.transformer.parameters()).device}", flush=True)
        per, peak, dev = measure(pipe, embeds, img, FRAMES)
        results[mode] = (per, peak)
        if per:
            print(f"  {FRAMES} 帧去噪: {per:.2f}s/步  峰值 {peak}GB  (device={dev})", flush=True)
        else:
            print(f"  {FRAMES} 帧去噪: 失败", flush=True)

    a = results.get("offload", (None, None))[0]
    b = results.get("resident", (None, None))[0]
    print("\n[AB] === 真实对照（唯一变量：是否 offload）===", flush=True)
    if a and b:
        print(f"  offload {a:.2f}s/步  →  resident {b:.2f}s/步   提速 {a/b:.2f}x", flush=True)
        if a / b < 1.2:
            print("  ⚠️ 提速很小：说明「去 offload」在真实 I2V 场景收益有限，"
                  "之前 1.88x 多半是 T2V 基线带来的错觉。", flush=True)
    else:
        print(f"  offload={a} resident={b}", flush=True)

    hy15.release()
    print("[AB] done", flush=True)


if __name__ == "__main__":
    main()
