"""hy15 单步成本探针 v2（实验 A，临时脚本，可删）。

修正 v1 的两个问题：
  1) `if getattr(out,"frames",None)` 对多元素 tensor 求真值 → Boolean ambiguous；
  2) 没给 height/width，实际跑在**管线默认分辨率**（非 480x832），且只 2 步冷启动。

本版：预热一次后，每个帧数配置计时多次取稳态单步；打印真实 latent 形状，
从而得到「真实工作分辨率下 tokens/帧 → 单步耗时 / 峰值显存」的权威曲线。

关键事实（已核实）：VAE 空间压缩 16x、时间 4x，transformer patch=(1,1,1)，
故 tokens/帧 = (H/16)*(W/16)，latent 帧 = (F-1)/4+1；transformer 54 层/16 头。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROMPT = "a pixel-art style little cat character walking cycle, side view, clean silhouette"
WARMUP_STEPS = 2
TIMED_STEPS = 4


def latent_frames(frames: int) -> int:
    return (frames - 1) // 4 + 1


def run_steps(pipe, embeds, frames, steps):
    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]
    gen = None
    try:
        import torch
        gen = torch.Generator("cuda").manual_seed(0)
    except Exception:
        gen = None
    out = pipe(
        prompt=None,
        prompt_embeds=pe, prompt_embeds_mask=pem,
        prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
        num_frames=frames, num_inference_steps=steps,
        generator=gen, output_type="latent",
    )
    return out


def main() -> None:
    import torch

    from backend.app.providers import hy15

    print(f"[probe] device={torch.cuda.get_device_properties(0).name}", flush=True)

    print("[probe] 编码文本（磁盘缓存命中则秒回）…", flush=True)
    embeds = hy15.encode_texts(PROMPT, "")
    print(f"[probe] embeds cached={embeds.get('cached')}", flush=True)

    pipe = hy15.get_pipeline(None, t2v=True)
    print(f"[probe] target_size={getattr(pipe,'target_size','?')} "
          f"default_aspect={getattr(pipe,'default_aspect_ratio','?')} "
          f"vae_spatial={getattr(pipe,'vae_scale_factor_spatial','?')} "
          f"vae_temporal={getattr(pipe,'vae_scale_factor_temporal','?')}", flush=True)

    rows = []
    for frames in (21, 33, 49, 61):
        try:
            # 预热（触发 CUDA/cudnn/offload 对该形状的一次性初始化），不计时
            warm = run_steps(pipe, embeds, frames, WARMUP_STEPS)
            lat = warm.frames
            lshape = tuple(lat.shape) if hasattr(lat, "shape") else None
            del warm
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
                torch.cuda.synchronize()
            t0 = time.time()
            out = run_steps(pipe, embeds, frames, TIMED_STEPS)
            if torch.cuda.is_available():
                torch.cuda.synchronize()
            dt = time.time() - t0
            per_step = dt / TIMED_STEPS
            peak = round(torch.cuda.max_memory_allocated() / 2**30, 2)
            resv = round(torch.cuda.max_memory_reserved() / 2**30, 2)
            # tokens = latent 帧 × latent 空间(H×W)
            _, _, lf, lh, lw = lshape
            tokens = lf * lh * lw
            rows.append((frames, lf, tokens, per_step, peak, resv, lshape))
            print(f"[probe] frames={frames:3d} lat=({lf},{lh},{lw}) tokens={tokens:6d} "
                  f"per_step={per_step:6.2f}s peak={peak}GB reserved={resv}GB", flush=True)
            del out
        except Exception as e:
            import traceback
            print(f"[probe] frames={frames} FAILED: {e.__class__.__name__}: {e}", flush=True)
            traceback.print_exc()

    print("\n[probe] === 汇总（稳态单步，CFG 关，真实默认分辨率）===", flush=True)
    base = None
    for frames, lf, tokens, per_step, peak, resv, lshape in rows:
        if per_step is None:
            continue
        if base is None:
            base = per_step
        print(f"  frames={frames:3d} tokens={tokens:6d} per_step={per_step:6.2f}s "
              f"({per_step/base:.1f}x@21) peak={peak}GB reserved={resv}GB latent={lshape}", flush=True)

    try:
        hy15.release()
    except Exception:
        pass
    print("[probe] done", flush=True)


if __name__ == "__main__":
    main()
