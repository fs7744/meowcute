"""hy15 DiT 常驻 GPU 测速（实验 C 第二版修正，临时脚本，可删）。

两处踩坑后的修正：
  v1）想剥已加载管线的 offload 钩子 → accelerate remove_hook ImportError，钩子还在，
      测出的「resident」其实是假的（device 又回 cpu，提速 1.13x=噪声）。自检抓到了。
  v2）改「另起进程、从构造就不开 offload」，但用了 pipe.to("cuda") 把**整条管线**
      含 VAE 一起搬上 GPU，24G 直接占满（23963/24564 MiB，余 ~600MB）→ 12 分钟无输出、
      内存颠簸卡死，被我 kill。

本版修正：
  - 只 pipe.transformer.to("cuda")（VAE 留 CPU）：本实验 output_type=latent 不调 VAE，
    这正是「去噪期 DiT 常驻」的真实场景，省下 VAE 那 ~1.5-2GB。
  - torch.cuda.set_per_process_memory_fraction(0.82)：超限时让 PyTorch **干净抛 OOM**，
    而不是在 Windows 上静默卡死（模块注释早就警告过这个行为）。
  - python -u + flush，输出实时可见，卡在哪一步能立刻知道。

基线（同环境 enable_model_cpu_offload，两次独立测量稳定）：
  21 帧 3.39/3.40s/步；49 帧 6.69/6.74s/步。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PROMPT = "a pixel-art style little cat character walking cycle, side view, clean silhouette"
WARMUP = 2
TIMED = 4
BASELINE = {21: 3.40, 49: 6.74}
FRAMES_TO_TEST = (21, 49)


def build_pipe_no_offload():
    """按 hy15.get_pipeline 的组件组装 T2V 管线，但不开 enable_model_cpu_offload。"""
    import torch
    from diffusers import (
        AutoencoderKLHunyuanVideo15,
        ClassifierFreeGuidance,
        FlowMatchEulerDiscreteScheduler,
        HunyuanVideo15Pipeline,
        HunyuanVideo15Transformer3DModel,
    )

    base = ROOT / "models" / "hunyuanvideo-1.5"
    dtype = torch.bfloat16
    print("[resident] 加载 transformer …", flush=True)
    tf = HunyuanVideo15Transformer3DModel.from_pretrained(base / "transformer-diffusers", torch_dtype=dtype)
    print("[resident] 加载 vae（留 CPU）…", flush=True)
    vae = AutoencoderKLHunyuanVideo15.from_pretrained(base / "vae-diffusers", torch_dtype=dtype)
    pipe = HunyuanVideo15Pipeline(
        text_encoder=None, tokenizer=None, transformer=tf, vae=vae,
        scheduler=FlowMatchEulerDiscreteScheduler(
            num_train_timesteps=1000, shift=7.0, use_dynamic_shifting=False),
        text_encoder_2=None, tokenizer_2=None,
        guider=ClassifierFreeGuidance(guidance_scale=1.0, enabled=False),
    )
    pipe.vae.enable_tiling()
    return pipe


def measure(pipe, embeds, frames):
    import torch

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]

    def once(steps):
        gen = torch.Generator("cuda").manual_seed(0)
        return pipe(
            prompt=None,
            prompt_embeds=pe, prompt_embeds_mask=pem,
            prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
            negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
            negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
            num_frames=frames, num_inference_steps=steps,
            generator=gen, output_type="latent",
        )

    try:
        torch.cuda.empty_cache()
        _w = once(WARMUP)
        del _w
        dev_mid = str(next(pipe.transformer.parameters()).device)
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.time()
        once(TIMED)
        torch.cuda.synchronize()
        dt = time.time() - t0
        return dt / TIMED, round(torch.cuda.max_memory_allocated() / 2**30, 2), dev_mid
    except Exception as e:
        print(f"    [frames={frames}] FAILED: {e.__class__.__name__}: {str(e)[:180]}", flush=True)
        return None, None, None


def main() -> None:
    import torch

    from backend.app.providers import hy15

    props = torch.cuda.get_device_properties(0)
    print(f"[resident] device={props.name} 总显存 {props.total_memory/2**30:.1f}GB", flush=True)

    # 关键：限不要超过 POR，超限让 PyTorch 干净抛 OOM，而不是 Windows 上静默卡死
    torch.cuda.set_per_process_memory_fraction(0.82)
    print("[resident] 已设进程显存上限 82%（超限→干净 OOM 而非卡死）", flush=True)

    embeds = hy15.encode_texts(PROMPT, "")
    pipe = build_pipe_no_offload()

    print("[resident] 只把 transformer 搬到 cuda（VAE 留 CPU）…", flush=True)
    t0 = time.time()
    pipe.transformer.to("cuda")
    print(f"[resident] 搬运完成 {time.time()-t0:.1f}s；"
          f"transformer device={next(pipe.transformer.parameters()).device}, "
          f"vae device={next(pipe.vae.parameters()).device}", flush=True)
    print(f"[resident] 当前显存 {torch.cuda.memory_allocated()/2**30:.2f}GB", flush=True)

    for f in FRAMES_TO_TEST:
        print(f"[resident] --- 测 {f} 帧 ---", flush=True)
        per, peak, dev_mid = measure(pipe, embeds, f)
        dev_post = str(next(pipe.transformer.parameters()).device)
        ok = dev_post.startswith("cuda")
        if per:
            base = BASELINE.get(f)
            sp = f"{base/per:.2f}x" if base else "-"
            print(f"  RESULT {f:3d} 帧: resident {per:6.2f}s/步  峰值 {peak}GB  "
                  f"(offload 基线 {base}s → 提速 {sp})  device={dev_post} "
                  f"{'OK 真常驻' if ok else '!! 非 cuda，不可信'}", flush=True)
        else:
            print(f"  RESULT {f:3d} 帧: 失败/超限（device={dev_post}）", flush=True)

    print("[resident] done", flush=True)


if __name__ == "__main__":
    main()
