"""关键判定：常驻 DiT 能否与 VAE 解码共存？（临时脚本，可删）

背景：原本计划「各段只出 latent + 结尾统一解码一次」，但那与链式分工冲突——
clip k 需要 clip k-1 的**末帧 PIL 图**做下一窗的 I2V 首帧条件，推迟解码就
拿不到它了（会让实验 B 刚验证过的接缝质量崩掉）。

于是问题变成：21 帧的 VAE 解码能不能直接和常驻的 15.5G DiT 塞在同一张卡上？
  - 能 → 常驻去噪无需 DiT↔VAE 倒腾，直接省下 ~11s/次搬运，且不用改 chaining。
  - 不能 → 必须每窗解码前把 DiT 搬回 CPU（每次来回 ~8-11s），收益被吃掉大半。

本脚本用 output_type="pil"（**完整生成，含 VAE 解码**）跑 I2V 21 帧来判定。
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
STEPS = 12


def build_resident():
    """直接复用 hy15.get_pipeline(resident=True)：不开 offload、transformer+SigLIP 上 GPU。
    （上一版手写构造漏了 SigLIP 的子目录探测与设备搬运，会报
      OSError: no file named model.safetensors / 设备不匹配——别手写，复用。）"""
    import torch

    from backend.app.providers import hy15

    torch.cuda.set_per_process_memory_fraction(0.92)   # 超限→干净 OOM 而非卡死
    return hy15.get_pipeline(None, t2v=False, resident=True)


def main() -> None:
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    print(f"[coexist] 总显存 {torch.cuda.get_device_properties(0).total_memory/2**30:.1f}GB", flush=True)
    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)

    print("[coexist] 构造常驻 I2V 管线（DiT→cuda，VAE 留 CPU）…", flush=True)
    pipe = build_resident()
    # 第一轮踩坑：不开 offload 就没人管设备搬运，VAE 权重还在 CPU，
    # 而 latent 输入在 cuda → Input/weight device mismatch。
    # 所以正式实现里 _decode_latents 必须先 pipe.vae.to("cuda")。这里先搬上来再测共存。
    pipe.vae.to("cuda")
    torch.cuda.empty_cache()
    print(f"[coexist] DiT device={next(pipe.transformer.parameters()).device} "
          f"VAE device={next(pipe.vae.parameters()).device} "
          f"显存已占 {torch.cuda.memory_allocated()/2**30:.2f}GB", flush=True)

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]
    torch.cuda.reset_peak_memory_stats()
    try:
        t0 = time.time()
        out = pipe(
            prompt=None,
            prompt_embeds=pe, prompt_embeds_mask=pem,
            prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
            negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
            negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
            image=img, num_frames=FRAMES, num_inference_steps=STEPS,
            generator=torch.Generator("cuda").manual_seed(42),
            output_type="pil",                      # ← 关键：完整解码出图
        )
        dt = time.time() - t0
        frames = list(out.frames[0])
        print(f"[coexist] ✅ 共存成功！{len(frames)} 帧 {frames[0].size}  总耗时 {dt:.1f}s  "
              f"峰值 {torch.cuda.max_memory_allocated()/2**30:.2f}GB", flush=True)
        print(f"[coexist] → 结论：常驻 DiT + VAE 解码可以直接共存，"
              f"无需 DiT↔VAE 搬运，chaining 逻辑不必改动。", flush=True)
    except Exception as e:
        print(f"[coexist] ❌ 放不下：{e.__class__.__name__}: {str(e)[:200]}", flush=True)
        print(f"[coexist] → 结论：解码前必须把 DiT 搬回 CPU（每次来回 ~8-11s），"
              f"需权衡是否划算。", flush=True)

    try:
        hy15.release()
    except Exception:
        pass
    print("[coexist] done", flush=True)


if __name__ == "__main__":
    main()
