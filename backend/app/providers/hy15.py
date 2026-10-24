"""HunyuanVideo-1.5 (480p I2V step-distilled) 本地推理封装。

为什么单独成模块而不是塞进 diffusers_local.py：
  - 它和 Wan / Qwen 的加载方式完全不同：权重是原版 hyvideo 格式，必须先用
    tools/hy15_convert.py 转成 diffusers 格式（transformer-diffusers/、vae-diffusers/）；
  - 文本编码器是 Qwen2.5-VL-7B（16G），内存装不下「它 + DiT」同时存在，必须
    两阶段：先编码存 embeds 到磁盘，再释放，最后只加载 DiT+VAE 出片；
  - byt5 分词器不需要词表文件（transformers 5.x 是纯字节映射）。

T2V / I2V 双形态（2026-10-24 补 T2V）：
  - I2V：HunyuanVideo15ImageToVideoPipeline，参考图编码进首帧 latent + SigLIP
    image_embed；
  - T2V：HunyuanVideo15Pipeline，同一份 transformer/vae，**不需要 SigLIP**
    （管线内部把 image_embeds 置零），比 I2V 还少两个组件；
  - 两条管线类不能互换（T2V 不接受 image），各加载一份、缓存键 #t2v 后缀区分，
    同进程共存必 OOM（各约 16G）。transformer in_channels=65 = 33 噪声 + 32
    图像 latent，T2V 时图像 latent 通道由管线内部置零。

显存边界（实测 4090 24G）：
  - 必须 enable_model_cpu_offload：DiT 常驻 15.5G + VAE 解码峰值 12G 会超 24G，
    Windows 上表现为卡死而不是 OOM；
  - VAE 必须开 tiling：3.6s vs 69s；
  - 帧数超线性：21 帧 2.4s/步，33 帧约 5s/步，61 帧（25.6k tokens）47s/步。
"""

from __future__ import annotations

import gc
import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import progress as progress_mod

# 与项目其它模型一致：路径相对仓库根
HY15_NAME = "hunyuanvideo-1.5"

_PIPE = None            # diffusers pipeline（常驻，跨方向复用）
_PIPE_KEY: Optional[str] = None


def root() -> Path:
    """仓库根目录（backend/app/providers -> repo）。"""
    return Path(__file__).resolve().parents[3]


def model_dir() -> Path:
    return root() / "models" / HY15_NAME


def resolve(explicit: Optional[str] = None) -> Optional[str]:
    """解析 hy15 目录；给了显式路径就用到它。目录不全返回 None（调用方回退 Wan）。"""
    cand = Path(explicit) if explicit else model_dir()
    if not cand.is_dir():
        return None
    need = [
        cand / "transformer-diffusers" / "diffusion_pytorch_model.safetensors",
        cand / "transformer-diffusers" / "config.json",
        cand / "vae-diffusers" / "diffusion_pytorch_model.safetensors",
        cand / "text_encoder" / "llm" / "config.json",
        cand / "vision_encoder" / "siglip",
    ]
    if not all(p.exists() for p in need):
        return None
    return str(cand)


def available(explicit: Optional[str] = None) -> bool:
    return resolve(explicit) is not None


# ---------------------------------------------------------------- 文本编码
def _embeds_path(base: Path, prompt: str, negative: str) -> Path:
    key = hashlib.sha1(f"{prompt}\x00{negative}".encode("utf-8")).hexdigest()[:12]
    return base / "embeds" / f"{key}.pt"


def encode_texts(prompt: str, negative: str = "", task_id: Optional[str] = None) -> Dict[str, Any]:
    """编码提示词并缓存；缓存命中直接返回（同一提示词跨方向/跨项目复用）。

    内存注意：Qwen2.5-VL-7B bf16 约 16G，编码完立刻删除。绝不能和 DiT 同时驻留
    （本机 32G 内存放不下 35G 组件）。
    """
    import torch

    base = Path(resolve() or model_dir())
    dst = _embeds_path(base, prompt, negative)
    if dst.exists():
        blob = torch.load(dst, map_location="cpu", weights_only=False)
        blob["cached"] = True
        return blob

    progress_mod.report(task_id, 0, 0, "加载文本编码器（Qwen2.5-VL-7B，约 16G，仅首次）…")
    from diffusers.pipelines.hunyuan_video1_5.pipeline_hunyuan_video1_5_image2video import (
        HunyuanVideo15ImageToVideoPipeline as P,
    )
    from transformers import ByT5Tokenizer, Qwen2_5_VLTextModel, Qwen2Tokenizer, T5EncoderModel

    dtype = torch.bfloat16
    device = torch.device("cpu")
    te = Qwen2_5_VLTextModel.from_pretrained(base / "text_encoder" / "llm", torch_dtype=dtype)
    tok = Qwen2Tokenizer.from_pretrained(base / "text_encoder" / "llm")
    te2 = T5EncoderModel.from_pretrained(base / "text_encoder" / "byt5-small", torch_dtype=dtype)
    # byt5 纯字节分词器：不需要 spiece.model（ModelScope 镜像缺这个文件）
    tok2 = ByT5Tokenizer()

    def _enc(text: str):
        pe, pem = P._get_mllm_prompt_embeds(
            tokenizer=tok, text_encoder=te, prompt=[text], device=device, tokenizer_max_length=1000)
        pe2, pem2 = P._get_byt5_prompt_embeds(
            tokenizer=tok2, text_encoder=te2, prompt=[text], device=device, tokenizer_max_length=256)
        return (pe.contiguous(), pem.contiguous(), pe2.contiguous(), pem2.contiguous())

    pos = _enc(prompt)
    neg = _enc(negative)
    del te, te2
    gc.collect()

    dst.parent.mkdir(parents=True, exist_ok=True)
    blob = {"prompt": prompt, "negative": negative, "pos": pos, "neg": neg}
    torch.save(blob, dst)
    blob["cached"] = False
    return blob


# ---------------------------------------------------------------- 管线
def get_pipeline(task_id: Optional[str] = None, t2v: bool = False):
    """加载（或复用）管线：DiT + VAE（I2V 另加 SigLIP）。

    t2v=False → I2V 管线（参考图进首帧 latent + SigLIP image_embed）；
    t2v=True → T2V 管线（纯文本，不需要 SigLIP，image_embeds 内部置零）。
    两条管线类不能互换，缓存键用 #t2v 后缀区分，切换时卸掉另一种
    （各约 16G，同进程共存必 OOM）。
    """
    global _PIPE, _PIPE_KEY
    base = Path(resolve() or model_dir())
    key = f"{base}#t2v" if t2v else str(base)
    if _PIPE is not None and _PIPE_KEY == key:
        return _PIPE

    import torch
    from diffusers import (
        AutoencoderKLHunyuanVideo15,
        ClassifierFreeGuidance,
        FlowMatchEulerDiscreteScheduler,
        HunyuanVideo15Transformer3DModel,
    )
    if t2v:
        from diffusers import HunyuanVideo15Pipeline as PipeCls
    else:
        from diffusers import HunyuanVideo15ImageToVideoPipeline as PipeCls
    from transformers import SiglipImageProcessor, SiglipVisionModel

    progress_mod.report(task_id, 0, 0,
                        f"加载视频底座 HunyuanVideo-1.5（约 16.6G，{'纯文本' if t2v else '图生视频'}）…")
    dtype = torch.bfloat16
    tf = HunyuanVideo15Transformer3DModel.from_pretrained(base / "transformer-diffusers", torch_dtype=dtype)
    vae = AutoencoderKLHunyuanVideo15.from_pretrained(base / "vae-diffusers", torch_dtype=dtype)

    kwargs: Dict[str, Any] = dict(
        text_encoder=None, tokenizer=None,
        transformer=tf, vae=vae,
        # shift=7.0 来自原仓库 scheduler_config（480p_i2v_step_distilled 官方值）
        scheduler=FlowMatchEulerDiscreteScheduler(
            num_train_timesteps=1000, shift=7.0, use_dynamic_shifting=False),
        text_encoder_2=None, tokenizer_2=None,
        # guidance_scale 只能设在 guider 上（__call__ 没有这个参数）。
        # step-distilled 官方值是 1.0，此时关掉 CFG 省一半计算。
        guider=ClassifierFreeGuidance(guidance_scale=1.0, enabled=False),
    )
    if not t2v:
        # I2V 才需要 SigLIP（把参考图编码成 image_embed 送进 transformer）；
        # T2V 管线内部把 image_embeds 置零，构造参数里根本没有这两个组件。
        sd = base / "vision_encoder" / "siglip"
        enc = sd / "image_encoder" if (sd / "image_encoder" / "config.json").exists() else sd
        pp = sd / "feature_extractor" if (sd / "feature_extractor" / "preprocessor_config.json").exists() else sd
        kwargs["image_encoder"] = SiglipVisionModel.from_pretrained(enc, torch_dtype=dtype)
        kwargs["feature_extractor"] = SiglipImageProcessor.from_pretrained(pp)

    pipe = PipeCls(**kwargs)
    pipe.enable_model_cpu_offload()
    pipe.vae.enable_tiling()
    _PIPE, _PIPE_KEY = pipe, key
    return pipe


def release() -> None:
    """卸载管线并归还显存（八方向跑完就调，下游是纯 CPU 计算）。"""
    global _PIPE, _PIPE_KEY
    _PIPE = None
    _PIPE_KEY = None
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


# ---------------------------------------------------------------- 生成
def _generate(
    embeds: Dict[str, Any],
    *,
    image=None,
    frames: int = 21,
    steps: int = 12,
    guidance: float = 1.0,
    seed: Optional[int] = None,
    task_id: Optional[str] = None,
    progress: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], Dict[str, Any]]:
    """预计算 embeds → 帧序列（T2V/I2V 共用，image=None 即纯文本）。

    帧数：内部对齐到 4k+1，并限制在 [13, 33]。61 帧会因序列长度超线性退化
    （25.6k tokens ≈ 47s/步）而失去可用性。
    """
    import torch

    progress_mod.check_cancel(task_id)
    t2v = image is None
    pipe = get_pipeline(task_id, t2v=t2v)

    n = max(13, min(33, (int(frames) - 1) // 4 * 4 + 1))
    st = max(6, min(20, int(steps or 12)))
    use_guid = 1.0 if guidance is None else float(guidance)
    # 只有 >1 才真的开 CFG：官方 step-distilled 推荐 1.0（无 CFG，单次前向）
    pipe.guider.guidance_scale = use_guid
    pipe.guider._enabled = use_guid > 1.0 and pipe.guider.num_conditions > 1

    gen = None
    if seed is not None:
        gen = torch.Generator("cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]

    try:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass

    progress_mod.report(
        task_id, int((progress or {}).get("base") or 0),
        int((progress or {}).get("grand") or st),
        f"{str((progress or {}).get('label') or '')} 去噪 {st} 步（{n} 帧）…".strip())
    t0 = time.time()
    call_kw: Dict[str, Any] = dict(
        prompt=None,
        prompt_embeds=pe, prompt_embeds_mask=pem,
        prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
        num_frames=n, num_inference_steps=st,
        generator=gen, output_type="pil",
    )
    if not t2v:
        call_kw["image"] = image
    out = pipe(**call_kw)
    dt = time.time() - t0
    imgs = list(out.frames[0])

    vram: Dict[str, Any] = {}
    try:
        if torch.cuda.is_available():
            vram = {
                "peakGB": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                "reservedGB": round(torch.cuda.memory_reserved() / 2**30, 2),
                "totalGB": round(
                    torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
            }
    except Exception:
        pass

    return imgs, {
        "engine": "hy15", "family": "hy15", "mode": "t2v" if t2v else "i2v",
        "model": "hunyuanvideo-1.5/480p_i2v_step_distilled",
        "steps": st, "cfg": use_guid, "frames": len(imgs),
        "seconds": round(dt, 1),
        "size": [imgs[0].width, imgs[0].height] if imgs else None,
        **({"vram": vram} if vram else {}),
    }


def run_i2v(
    image,
    embeds: Dict[str, Any],
    *,
    frames: int = 21,
    steps: int = 12,
    guidance: float = 1.0,
    seed: Optional[int] = None,
    task_id: Optional[str] = None,
    progress: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], Dict[str, Any]]:
    """首帧 + 预计算 embeds → 帧序列（图生视频）。"""
    return _generate(embeds, image=image, frames=frames, steps=steps,
                     guidance=guidance, seed=seed, task_id=task_id, progress=progress)


def run_t2v(
    embeds: Dict[str, Any],
    *,
    frames: int = 21,
    steps: int = 12,
    guidance: float = 1.0,
    seed: Optional[int] = None,
    task_id: Optional[str] = None,
    progress: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Any], Dict[str, Any]]:
    """纯文本 embeds → 帧序列（文生视频，无参考图）。"""
    return _generate(embeds, image=None, frames=frames, steps=steps,
                     guidance=guidance, seed=seed, task_id=task_id, progress=progress)
