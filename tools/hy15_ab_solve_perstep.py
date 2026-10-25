"""用「4步/12步解方程」消除固定开销，测真实单步耗时（临时脚本，可删）。

为什么不用之前的算法：之前的 per_step = 单次 pipe() 总耗时 / 步数，但每次 pipe() 都含
**固定开销 O**（I2V 要先用 VAE 编码参考图 + SigLIP 编码 + latent 准备），
摊到 4 步上会把基线虚高一大截——这正是 3.73s/步 vs tqdm 循环内 1.84-2.05s/步 的差距来源。

解法：同一模式测两次不同步数
    T(4)  = O + 4S
    T(12) = O + 12S   →   S = (T(12) - T(4)) / 8
固定开销 O 被抵消，得到的 S 才是不带偏的真实单步耗时。

resident 侧的注意点：I2V 需要 **VAE 编码参考图**（不只是解码），所以常驻模式下
VAE 也必须在 GPU，否则会 Input/weight device mismatch（上一版就死在这）。
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
STEPS_LIST = (4, 12)


def timed_once(pipe, embeds, image, frames, steps):
    import torch

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]
    torch.cuda.synchronize()
    t0 = time.time()
    out = pipe(
        prompt=None,
        prompt_embeds=pe, prompt_embeds_mask=pem,
        prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
        image=image, num_frames=frames, num_inference_steps=steps,
        generator=torch.Generator("cuda").manual_seed(42),
        output_type="latent",                 # 只测去噪，绕开解码 OOM 差异
    )
    torch.cuda.synchronize()
    dt = time.time() - t0
    del out
    return dt


def main() -> None:
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    torch.cuda.set_per_process_memory_fraction(0.92)
    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)

    solved = {}
    for mode, resident in (("offload", False), ("resident", True)):
        print(f"\n[solve] === {mode} ===", flush=True)
        hy15.release()
        torch.cuda.empty_cache()
        pipe = hy15.get_pipeline(None, t2v=False, resident=resident)
        if resident:
            # I2V 要用 VAE 编码参考图，常驻模式下 VAE 必须在 GPU（否则设备不匹配）
            pipe.vae.to("cuda")
        print(f"  DiT={next(pipe.transformer.parameters()).device} "
              f"VAE={next(pipe.vae.parameters()).device}", flush=True)
        times = {}
        try:
            timed_once(pipe, embeds, img, FRAMES, 2)          # 预热
            for st in STEPS_LIST:
                times[st] = timed_once(pipe, embeds, img, FRAMES, st)
                print(f"  T({st:2d} 步) = {times[st]:7.2f}s", flush=True)
            s = (times[12] - times[4]) / 8
            o = times[4] - 4 * s
            solved[mode] = s
            print(f"  → 真实单步 S = {s:.2f}s/步（固定开销 O ≈ {o:.1f}s）", flush=True)
        except Exception as e:
            print(f"  FAILED: {e.__class__.__name__}: {str(e)[:160]}", flush=True)

    a, b = solved.get("offload"), solved.get("resident")
    print("\n[solve] === 结论（真实单步，已消除固定开销）===", flush=True)
    if a and b:
        print(f"  offload {a:.2f}s/步 → resident {b:.2f}s/步   真实提速 {a/b:.2f}x", flush=True)
        if a / b < 1.25:
            print("  ⚠️ 收益很小 —— 初始的 1.88x 高度疑似被「固定开销摊进少量步数」放大出来的错觉。", flush=True)
    else:
        print(f"  offload={a} resident={b}", flush=True)

    hy15.release()
    print("[solve] done", flush=True)


if __name__ == "__main__":
    main()
