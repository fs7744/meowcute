"""hy15 去 offload 测速（实验 C，临时脚本，可删）。

假设：单步耗时被「每步把 15.5G DiT 权重经 PCIe/CPU↔GPU 搬运」主导，而非算力。
  21 帧一步纯算力约 20 TFLOP，4090 bf16 实测 ~80-100 TFLOPS → 理论 ~0.2s，
  但实测 3.39s/步（≈15x 计算下限）→ 差值应主要来自 cpu_offload 的权重搬运。

做法：同一进程内对照两组（排除 session 波动）
  A) offload  ：get_pipeline 默认 enable_model_cpu_offload（现状）
  B) resident ：剥掉 offload 钩子、DiT 常驻 GPU（只看去噪，output_type="latent" 跳过 VAE）
各在 21 / 49 帧测「预热后多次计时的稳态单步」。
并且**验证** resident 组前向之后 transformer 仍留在 cuda，防止测了个假数据。
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


def measure(pipe, embeds, frames):
    """返回 (稳态单步秒数, 峰值显存GB)。失败返回 (None, None)。"""
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
        _warm = once(WARMUP)               # 预热，不计时
        del _warm
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.time()
        once(TIMED)
        torch.cuda.synchronize()
        dt = time.time() - t0
        return dt / TIMED, round(torch.cuda.max_memory_allocated() / 2**30, 2)
    except Exception as e:
        print(f"    [measure frames={frames}] FAILED: {e.__class__.__name__}: {e}", flush=True)
        return None, None


def disable_offload(pipe):
    """尽量干净地去掉 offload 钩子。"""
    notes = []
    if hasattr(pipe, "disable_model_cpu_offload"):
        try:
            pipe.disable_model_cpu_offload()
            notes.append("disable_model_cpu_offload")
            return notes
        except Exception as e:
            notes.append(f"disable_model_cpu_offload 失败({e.__class__.__name__})")
    try:
        from accelerate.utils.modeling import remove_hook_from_module

        for name in ("transformer", "vae"):
            m = getattr(pipe, name, None)
            if m is not None:
                remove_hook_from_module(m, recurse=True)
        notes.append("remove_hook_from_module")
    except Exception as e:
        notes.append(f"remove_hook 失败({e.__class__.__name__})")
    return notes


def main() -> None:
    import torch

    from backend.app.providers import hy15

    print(f"[offload] device={torch.cuda.get_device_properties(0).name}", flush=True)
    embeds = hy15.encode_texts(PROMPT, "")
    print(f"[offload] embeds cached={embeds.get('cached')}", flush=True)

    pipe = hy15.get_pipeline(None, t2v=True)
    print(f"[offload] 管线就绪 size={pipe.transformer.__class__.__name__}", flush=True)

    rows = {}
    print("\n[offload] === A组：现状 enable_model_cpu_offload ===", flush=True)
    for f in (21, 49):
        per, peak = measure(pipe, embeds, f)
        rows[("offload", f)] = (per, peak)
        if per:
            print(f"  {f:3d} 帧: {per:6.2f}s/步  峰值 {peak}GB", flush=True)

    print("\n[offload] === 切 resident：去钩子 + DiT 常驻 GPU ===", flush=True)
    print(f"  剥离方式: {disable_offload(pipe)}", flush=True)
    pipe.to("cuda")
    d0 = str(next(pipe.transformer.parameters()).device)
    print(f"  前向前后 device 检查（应恒 cuda）: 前={d0}", flush=True)

    print("\n[offload] === B组：resident（DiT 常驻）===", flush=True)
    for f in (21, 49):
        per, peak = measure(pipe, embeds, f)
        rows[("resident", f)] = (per, peak)
        d1 = str(next(pipe.transformer.parameters()).device)
        print(f"  前向前后 device 检查: 后={d1}  {'OK 真的常驻' if d1.startswith('cuda') else '!! 又被搬走了，数据不可信'}", flush=True)
        if per:
            print(f"  {f:3d} 帧: {per:6.2f}s/步  峰值 {peak}GB", flush=True)

    print("\n[offload] === 汇总对比 ===", flush=True)
    for f in (21, 49):
        a = rows.get(("offload", f), (None, None))[0]
        b = rows.get(("resident", f), (None, None))[0]
        if a and b:
            print(f"  {f:3d} 帧: offload {a:6.2f}s → resident {b:6.2f}s   "
                  f"提速 {a/b:.2f}x", flush=True)
        else:
            print(f"  {f:3d} 帧: offload={a} resident={b}", flush=True)

    try:
        hy15.release()
    except Exception:
        pass
    print("[offload] done", flush=True)


if __name__ == "__main__":
    main()
