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

显存边界（实测 4090 24G，默认分辨率 target_size=640 → 480x848，tokens/帧≈1590）：
  - 必须 enable_model_cpu_offload：DiT 常驻 15.5G + VAE 解码峰值会超 24G，
    Windows 上表现为卡死而不是 OOM；
  - VAE 必须开 tiling：3.6s vs 69s；
  - 单步耗时≈随帧数线性（非 O(N²) 灾难）：21/33/49/61 帧分别 3.4/4.5/6.7/8.8s/步，
    峰值显存 16.6/17.4/18.9/20.4GB。真正的墙是**显存**：外推 ~81 帧顶破 24G，
    121+ 帧整段必 OOM。故 window 分窗的价值是「把峰值显存按在 ~16.6GB →
    任意长度可跑」，不是提速（见 _chunked_i2v）。
    （旧注释「61 帧 47s/步」实测约高估 5 倍，已按 warmup+多次计时的稳态值更正。）
"""

from __future__ import annotations

import gc
import hashlib
import json
import math
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import progress as progress_mod

# 与项目其它模型一致：路径相对仓库根
HY15_NAME = "hunyuanvideo-1.5"

# 分窗阈值默认值（2026-10-26 定）：单位帧，仅 I2V 生效，frames>window 时才分窗。
# 依据 tools/hy15_identity_drift.py 的 121 帧对照（同图/同提示词/同ksam/64→12 步）：
#   win=21 → 6 段：instability 30.8%、颜色漂移 76.8、耗时 122.7s、峰值 18.31GB
#   win=41 → 3 段：instability 23.4%（回到整段水平）、颜色漂移 57.3、
#                  耗时 104.3s（更快）、峰值 18.76GB（24G 内安全）
# 机理：漂移随**段数**累积——每段独立采样 + 重新 conditioning，段越多越容易「换人」；
# 而瓶颈是显存（~81 帧顶破 24G），所以段数能省就省：41 帧单段实测 18.76GB 仍在安全区，
# 再往上调只会把 6→3 段的收益换成 OOM 风险。0 = 显式关闭（整段生成）。
# 另注：别改成「所有窗共用同一个 seed」——61 帧看着更好，121 帧直接崩坏
# （instability 202.6%、漂移 153.4，第 90/120 帧背景出现大面积彩色杂块）。
DEFAULT_WINDOW = 41

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


# 常驻模式的帧数安全线。实测（4090 24G）：21 帧峰值 16.6G 稳住，49 帧干净 OOM
# （DiT 常驻 15.5G + 大激活超出余量）。超过这个帧数就退回 cpu_offload。
RESIDENT_MAX_FRAMES = 25

# 单进程显存上限比例。为什么要有：24G 卡上一旦真的超了，Windows 不会干净抛错，
# 而是**静默卡死**（实测卡了 12 分钟才发现，只能 kill）。设了上限之后超行程会
# 抛 OutOfMemoryError，能立刻被 caught 并降级，而不是让用户干等。
# 取 0.97 而不是更保守（曾用 0.92，结果把 49 帧 / 512x704 这种本来放得下的
# 配置误判成 OOM——上限只该拦「跑飞到系统内存」，不该误伤正常的接近满载）。
GPU_FRACTION = 0.97


def _cap_gpu_fraction() -> None:
    """给单进程设显存上限：宁可干净抛 OOM，也不要静默卡死。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.set_per_process_memory_fraction(GPU_FRACTION)
    except Exception:
        pass


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
def get_pipeline(task_id: Optional[str] = None, t2v: bool = False, resident: bool = False):
    """加载（或复用）管线：DiT + VAE（I2V 另加 SigLIP）。

    t2v=False → I2V 管线（参考图进首帧 latent + SigLIP image_embed）；
    t2v=True → T2V 管线（纯文本，不需要 SigLIP，image_embeds 内部置零）。
    两条管线类不能互换，缓存键用 #t2v 后缀区分，切换时卸掉另一种
    （各约 16G，同进程共存必 OOM）。

    resident=True → **常驻模式（实验性，实测对提速无效，别为加速开它）**。
    初衷是怀疑「每步搬运 15.5G DiT」是瓶颈，早期测出 3.40s→1.81s/步（1.88x），
    但那是**测量偏差**：每次 pipe() 都含固定开销（VAE 编码参考图 + SigLIP + latent
    准备），摊进 4 步把基线虚高了。用 4 步/12 步解方程消掉开销后真实值为
    **offload 1.69s/步 vs resident 1.81s/步 = 0.94x（略慢）**——
    enable_model_cpu_offload 的层预取已经做得很好，去掉它并无收益，
    且常驻会把显存顶到 18.7G，VAE 解码阶段直接 OOM（差约 250MB）。
    见 tools/hy15_ab_solve_perstep.py。**结论：默认路径不要改。**
    常驻必须**从构造起就不开 offload**：已加载的管线剥钩子会失败
    （accelerate remove_hook ImportError），钩子会在每次前向后再把 DiT 搬回 CPU，
    导致「看着常驻其实是假的」（这个坑已经踩过一次）。
    """
    global _PIPE, _PIPE_KEY
    base = Path(resolve() or model_dir())
    key = f"{base}#{'t2v' if t2v else 'i2v'}{'#res' if resident else ''}"
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
    pipe.vae.enable_tiling()
    # 无论走哪条路都设显存上限：超限→干净抛 OOM，而不是 Windows 上静默卡死。
    # （实测踩过：超限后不报错也不返回，干等 12 分钟只能 kill。）
    _cap_gpu_fraction()
    if resident:
        # 常驻模式：**不要**再 enable_model_cpu_offload（否则钩子会把 DiT 搬回 CPU）。
        # 没有 offload 钩子就没人再管设备搬运了，所以「谁该在 GPU 上」必须自己说清：
        #   - transformer（15.5G）：常驻，去噪主力
        #   - image_encoder（SigLIP，~0.4G）：I2V 每次前向都要用，必须一起上
        #     （留 CPU 会直接设备不匹配报错）
        #   - vae：I2V 还要用它**编码参考图**，所以也必须上 GPU
        pipe.transformer.to("cuda")
        if getattr(pipe, "image_encoder", None) is not None:
            pipe.image_encoder.to("cuda")
        if getattr(pipe, "vae", None) is not None:
            pipe.vae.to("cuda")
    else:
        pipe.enable_model_cpu_offload()
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
# 分辨率对齐：VAE 空间压缩 16x、transformer patch=(1,1,1)，所以宽高必须是 16 的倍数，
# 否则管线内部 int(h)//16 会**静默向下取整**（尺寸变了却不报错，最难查的那种）。
_RES_MULT = 16
_RES_MIN, _RES_MAX = 128, 1280


def _norm_res(width: int, height: int) -> Tuple[int, int]:
    """把用户宽高规整到 16 的倍数并夹到安全区间；无效返回 (0, 0) 表示「用默认」。"""
    try:
        w, h = int(width), int(height)
    except Exception:
        return 0, 0
    if w <= 0 or h <= 0:
        return 0, 0
    w = max(_RES_MIN, min(_RES_MAX, round(w / _RES_MULT) * _RES_MULT))
    h = max(_RES_MIN, min(_RES_MAX, round(h / _RES_MULT) * _RES_MULT))
    return w, h


def _fit_image(image, width: int, height: int):
    """把参考图居中裁到目标长宽比并缩放到 (width, height)。

    I2V 管线自己会用「参考图长宽比 × target_size」反推输出尺寸（__call__ 没有
    width/height 参数），所以想精确落到用户选的 W×H，必须先把图裁成 W:H，
    再把 target_size 设成 sqrt(W*H)（候选尺寸面积都≈target_size²）。
    """
    from PIL import Image

    resample = getattr(getattr(Image, "Resampling", Image), "LANCZOS", None)
    try:
        img = image if hasattr(image, "crop") else Image.open(image)
    except Exception:
        return image
    sw, sh = img.size
    tgt = width / float(height)
    src = sw / float(sh)
    if src > tgt:                      # 太宽 → 裁掉左右
        nw = max(1, int(round(sh * tgt)))
        x0 = (sw - nw) // 2
        img = img.crop((x0, 0, x0 + nw, sh))
    elif src < tgt:                    # 太高 → 裁掉上下
        nh = max(1, int(round(sw / tgt)))
        y0 = (sh - nh) // 2
        img = img.crop((0, y0, sw, y0 + nh))
    try:
        return img.resize((width, height), resample) if resample else img.resize((width, height))
    except Exception:
        return img.resize((width, height))


def _set_target_size(pipe, width: int, height: int) -> None:
    """按用户宽高设置管线的 target_size（决定输出像素面积）；无效则回默认。

    管线的候选尺寸表是「面积≈target_size² × 多种长宽比」的离散集合，直接取
    sqrt(W*H) 只能近似（例如 384x512 会落到 384x496）。这里在附近微调
    target_size，挑「实际产出最接近用户所选」的那个，让分辨率真正可控。
    """
    if width > 0 and height > 0:
        area = math.sqrt(float(width) * float(height))
        try:
            vp = pipe.video_processor
            best_d, best_ts = None, int(round(area))
            lo = max(64, int(area) - 40)
            for ts in range(lo, int(area) + 41):
                h, w = vp.calculate_default_height_width(
                    height=height, width=width, target_size=ts)
                d = abs(w - width) + abs(h - height)
                if best_d is None or d < best_d:
                    best_d, best_ts = d, ts
                if d == 0:
                    break
            pipe.target_size = best_ts
        except Exception:
            pipe.target_size = int(round(area))
    else:
        try:
            pipe.target_size = pipe.transformer.config.target_size
        except Exception:
            pass


def _denoise(pipe, embeds: Dict[str, Any], *, image, n_frames: int, steps: int,
             guidance: float, gen, t2v: bool, width: int = 0, height: int = 0) -> List[Any]:
    """跑一次完整去噪，返回 PIL 帧列表（T2V/I2V 共用，t2v 由 image is None 决定）。

    width/height：用户选的分辨率（0=用管线默认）。T2V 直接传 height/width；
    I2V 因为 __call__ 不接这两个参数，改为「预裁参考图到 W:H + 设 target_size」。
    """
    # 只有 >1 才真的开 CFG：官方 step-distilled 推荐 1.0（无 CFG，单次前向）
    pipe.guider.guidance_scale = guidance
    pipe.guider._enabled = guidance > 1.0 and pipe.guider.num_conditions > 1

    (pe, pem, pe2, pem2) = embeds["pos"]
    (ne, nem, ne2, nem2) = embeds["neg"]
    call_kw: Dict[str, Any] = dict(
        prompt=None,
        prompt_embeds=pe, prompt_embeds_mask=pem,
        prompt_embeds_2=pe2, prompt_embeds_mask_2=pem2,
        negative_prompt_embeds=ne, negative_prompt_embeds_mask=nem,
        negative_prompt_embeds_2=ne2, negative_prompt_embeds_mask_2=nem2,
        num_frames=n_frames, num_inference_steps=steps,
        generator=gen, output_type="pil",
    )
    if t2v:
        _set_target_size(pipe, width, height)
        if width > 0 and height > 0:
            call_kw["height"] = height
            call_kw["width"] = width
    else:
        if width > 0 and height > 0:
            image = _fit_image(image, width, height)
        _set_target_size(pipe, width, height)
        call_kw["image"] = image
    out = pipe(**call_kw)
    return list(out.frames[0])


def _chunked_i2v(pipe, embeds: Dict[str, Any], *, image, frames: int, steps: int,
                 guidance: float, seed, window: int, task_id, progress,
                 width: int = 0, height: int = 0) -> List[Any]:
    """把长视频切成多个 ≤window 帧的 I2V 段链式生成，把峰值显存按在单段水平。

    根因（实验 A 实测，480x848 默认分辨率）：hy15 单步耗时随帧数≈线性
    （21/33/49/61 帧 = 3.4/4.5/6.7/8.8s/步），但峰值显存随 latent 序列线性增长
    （16.6→20.4GB），外推 ~81 帧顶破 24G、121+ 帧整段必 OOM。分窗后每段 ≤window，
    峰值显存与总长度无关（41 帧/段实测 18.76GB）——于是任意长度都能在 24G 内跑完。
    window 取多少见 DEFAULT_WINDOW：段数越少漂移越小，故默认 41 而不是更小的 21。
    代价：不是提速（单步本就线性），反而多几次 VAE 解码/SigLIP/latent 准备的固定开销；
    换来的是「整段会 OOM 的长度也能生成」。

    连续性：clip 1 用原始参考图；clip k 用 clip k-1 的末帧做 I2V 首帧条件
    （I2V 天然支持首帧条件），拼接时丢掉重复的边界帧。接缝质量取决于模型对
    「首帧→后续」的保持能力，是链式方案的主要权衡点。
    """
    import math

    # 首段 window 帧，之后每段新增 window-1 帧（丢 1 个重复边界帧）
    # total = window + (k-1)*(window-1) >= frames
    k = max(1, math.ceil((frames - window) / (window - 1)) + 1)
    prog = progress or {}
    grand = int(prog.get("grand") or (k * steps))
    out_frames: List[Any] = []
    cur = image
    for i in range(k):
        progress_mod.check_cancel(task_id)
        clip_seed = None if seed is None else int(seed) + i * 100003
        gen = None
        if clip_seed is not None:
            import torch
            gen = torch.Generator("cuda" if torch.cuda.is_available() else "cpu").manual_seed(clip_seed)
        progress_mod.report(
            task_id, int(prog.get("base") or 0) + i * steps, grand,
            f"{str(prog.get('label') or '')} 分窗 {i + 1}/{k}（每窗 {window} 帧）…".strip())
        clip = _denoise(pipe, embeds, image=cur, n_frames=window, steps=steps,
                        guidance=guidance, gen=gen, t2v=False,
                        width=width, height=height)
        # 首段全收，后续段丢掉与上一段重复的首帧
        out_frames.extend(clip if i == 0 else clip[1:])
        cur = clip[-1]
    return out_frames[:frames]


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
    window: int = DEFAULT_WINDOW,
    width: int = 0,
    height: int = 0,
) -> Tuple[List[Any], Dict[str, Any]]:
    """预计算 embeds → 帧序列（T2V/I2V 共用，image=None 即纯文本）。

    帧数：内部对齐到 4k+1，下限 13（模型要求的最小可用帧数），上限不限制。
    window：分窗阈值（0=关闭，默认 DEFAULT_WINDOW=41）。仅 I2V 生效：frames>window
    时切成多个 ≤window 的链式段。实测单步耗时本就≈线性，分窗**不提速**；它的价值
    是把峰值显存从「随总长度线性增长」变成「按单窗口封顶」——整段必 OOM 的长视频
    才能在 24G 内跑完（详见 _chunked_i2v / DEFAULT_WINDOW 的取舍依据）。
    T2V 无首帧锚点、分窗无意义，自动忽略。
    width/height：用户选的分辨率（0=沿用管线默认）。单步耗时∝tokens∝H×W，
    这是最直接、最可预期的提速杠杆（像素素材最终只缩到 64-256px 的格子）。
    """
    import torch

    progress_mod.check_cancel(task_id)
    t2v = image is None
    pipe = get_pipeline(task_id, t2v=t2v)

    # 帧数不设上限：4k+1 对齐 + 下限 13 即可，具体多长由用户决定。
    n = max(13, (int(frames) - 1) // 4 * 4 + 1)
    st = max(6, min(20, int(steps or 12)))
    use_guid = 1.0 if guidance is None else float(guidance)

    win = 0
    if window and int(window) >= 13:
        win = max(13, (int(window) - 1) // 4 * 4 + 1)
    chunked = win >= 13 and n > win and image is not None

    # 分辨率：规整到 16 的倍数；无效则 (0,0) → 各路径沿用管线默认
    rw, rh = _norm_res(width, height)

    gen = None
    if seed is not None and not chunked:
        gen = torch.Generator("cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))

    try:
        if torch.cuda.is_available():
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass

    t0 = time.time()
    if chunked:
        imgs = _chunked_i2v(pipe, embeds, image=image, frames=n, steps=st,
                            guidance=use_guid, seed=seed, window=win,
                            task_id=task_id, progress=progress,
                            width=rw, height=rh)
    else:
        progress_mod.report(
            task_id, int((progress or {}).get("base") or 0),
            int((progress or {}).get("grand") or st),
            f"{str((progress or {}).get('label') or '')} 去噪 {st} 步（{n} 帧"
            f"{f' / {rw}x{rh}' if rw else ''}）…".strip())
        imgs = _denoise(pipe, embeds, image=image, n_frames=n, steps=st,
                        guidance=use_guid, gen=gen, t2v=t2v,
                        width=rw, height=rh)
    dt = time.time() - t0

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

    meta: Dict[str, Any] = {
        "engine": "hy15", "family": "hy15", "mode": "t2v" if t2v else "i2v",
        "model": "hunyuanvideo-1.5/480p_i2v_step_distilled",
        "steps": st, "cfg": use_guid, "frames": len(imgs),
        "seconds": round(dt, 1),
        "size": [imgs[0].width, imgs[0].height] if imgs else None,
    }
    if chunked:
        meta["window"] = win
    return imgs, {**meta, **({"vram": vram} if vram else {})}


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
    window: int = DEFAULT_WINDOW,
    width: int = 0,
    height: int = 0,
) -> Tuple[List[Any], Dict[str, Any]]:
    """首帧 + 预计算 embeds → 帧序列（图生视频）。window>0 且 frames>window 时分窗。"""
    return _generate(embeds, image=image, frames=frames, steps=steps,
                     guidance=guidance, seed=seed, task_id=task_id, progress=progress,
                     window=window, width=width, height=height)


def run_t2v(
    embeds: Dict[str, Any],
    *,
    frames: int = 21,
    steps: int = 12,
    guidance: float = 1.0,
    seed: Optional[int] = None,
    task_id: Optional[str] = None,
    progress: Optional[Dict[str, Any]] = None,
    window: int = DEFAULT_WINDOW,
    width: int = 0,
    height: int = 0,
) -> Tuple[List[Any], Dict[str, Any]]:
    """纯文本 embeds → 帧序列（文生视频，无参考图）。window 对 T2V 无效（无首帧锚点）。"""
    return _generate(embeds, image=None, frames=frames, steps=steps,
                     guidance=guidance, seed=seed, task_id=task_id, progress=progress,
                     window=window, width=width, height=height)
