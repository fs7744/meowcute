"""Qwen-Image-Edit-2511 + ControlNet-Union：把「姿势」和「身份」拆成两条通道的批量引擎。

为什么要单独一个模块（而不是继续写在 tools/ 的探针里）：这套东西有 35GB 权重，
**一次加载必须复用**。探针脚本每跑一帧就重载一次（变压器 19GB + 文本编码器
15.8GB），12 帧光加载就 40 分钟，而且残留进程会把 32GB 内存吃光，随后每一步
都慢到像是卡死（实测可用内存 0.4GB 时，加载一步要 3 分钟）。

三个稳定性设计（都是被实测逼出来的，见 .workbuddy/memory/2026-10-27.md）：
1. **单例复用**：transformer / controlnet / VAE 只加载一次，帧间只换 cond 与参考图。
2. **控制注入幂等**：`attach()` 只会包一层 forward，重复调用只更新 holder。
   早期每帧重挂一次，forward 被套娃，控制网每步跑 N 次 → 第二帧起必然顶满显存。
3. **卡死看门狗**：Qwen 在显存贴着上限时会「步进不再推进、GPU 100%、不报错」。
   看门狗 N 秒收不到步进回调就置 `pipe._interrupt` 让这一帧提前收尾，
   然后**自动降级重试**（先去 CFG，再降尺寸），而不是整批挂住。
"""
from __future__ import annotations

import gc
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

from ..providers.diffusers_local import DiffusersProvider  # noqa: E402

BASE = ROOT / "models" / "qwen-image-20b"            # VAE / scheduler / 文本编码器
EDIT = ROOT / "models" / "qwen-image-edit-2511"      # transformer（fp8 分片）
CNET = ROOT / "models" / "qwen-image-controlnet-union"

FP8_INDEX = "diffusion_pytorch_model.safetensors.index.json"

DEFAULT_PROMPT = (
    "把图1 里的角色画成控制图里那个姿势：同一个角色（发型、发色、服装配色、配饰、武器"
    "完全照图1），但身体朝向、重心、四肢方位、手脚落点全部照控制图。"
    "全身角色立绘，纯白背景，不要文字、不要水印、不要多个角色。"
)
DEFAULT_NEGATIVE = (
    "模糊, 低质量, 水印, 文字, 多个角色, 四视图, 分屏, 网格线, 地面, 阴影, 灰色剪影"
)

# 看门狗默认阈值：正常帧 512/30 步约 90s、单步 1~3s，120s 没有任何一步进展
# 就一定是在共享内存里打转，而不是在算。
STALL_SECONDS = 120.0


def _pack(lat: torch.Tensor, b: int, c: int, h: int, w: int) -> torch.Tensor:
    x = lat.view(b, c, h // 2, 2, w // 2, 2)
    x = x.permute(0, 2, 4, 1, 3, 5)
    return x.reshape(b, (h // 2) * (w // 2), c * 4)


class Qwen20EditControl:
    """一次性加载、多帧复用的 Edit+ControlNet 引擎。"""

    def __init__(self, *, size: int = 512, steps: int = 30, cfg: float = 4.0,
                 ref_area: int = 512, stall_seconds: float = STALL_SECONDS,
                 verbose: bool = True):
        self.size = size
        self.steps = steps
        self.cfg = cfg
        self.ref_area = ref_area
        self.stall_seconds = stall_seconds
        self.verbose = verbose
        self.pipe = None
        self.controlnet = None
        self._te = None
        self._stalled = False
        self._last_step = 0.0
        self._lock = threading.Lock()

    # ---------- 加载 ----------
    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[edit20] {msg}", flush=True)

    def ensure(self) -> None:
        if self.pipe is not None:
            return
        from diffusers import (AutoencoderKLQwenImage, FlowMatchEulerDiscreteScheduler,
                               QwenImageControlNetModel, QwenImageEditPlusPipeline)
        from transformers import (Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer,
                                  Qwen2VLProcessor)

        from .qwen20_loader import load_fp8_transformer

        t0 = time.time()
        vae = AutoencoderKLQwenImage.from_pretrained(
            str(BASE / "vae"), torch_dtype=torch.bfloat16).to("cuda")
        vae.enable_tiling()
        transformer = load_fp8_transformer(EDIT / "transformer-fp8",
                                           verbose=self.verbose)
        controlnet = QwenImageControlNetModel.from_pretrained(
            str(CNET), torch_dtype=torch.bfloat16)
        DiffusersProvider._quantize_transformer_gpu(controlnet)
        controlnet.to("cuda")
        scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(str(BASE / "scheduler"))
        tokenizer = Qwen2Tokenizer.from_pretrained(str(BASE / "tokenizer"))
        processor = Qwen2VLProcessor.from_pretrained(str(EDIT / "processor"))
        text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
            str(BASE / "text_encoder"), torch_dtype=torch.bfloat16,
            low_cpu_mem_usage=True).eval()

        pipe = QwenImageEditPlusPipeline(
            scheduler=scheduler, vae=vae, text_encoder=text_encoder,
            tokenizer=tokenizer, processor=processor, transformer=transformer)
        pipe.set_progress_bar_config(disable=True)
        self._patch_ref_area()
        self._patch_encode_cpu(pipe)
        self.pipe, self.controlnet, self._te = pipe, controlnet, text_encoder
        self._log(f"管线就绪 {time.time() - t0:.0f}s，显存 "
                  f"{torch.cuda.memory_allocated() / 2**30:.1f}GB")

    @staticmethod
    def _patch_ref_area() -> None:
        """参考图的 VAE 编码面积：默认 1024² = 4096 token，会把序列撑到 5120。"""
        import diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus as ep
        ep.VAE_IMAGE_SIZE = int(os.environ.get("REF_AREA", "512")) ** 2

    @staticmethod
    def _patch_encode_cpu(pipe) -> None:
        """文本编码器留 CPU（bf16 15.8GB 进显存会挤掉 transformer）。"""
        _enc = pipe.encode_prompt

        def _enc_cpu(*a, **kw):
            kw["device"] = "cpu"
            e, m = _enc(*a, **kw)
            return e.to("cuda"), (m.to("cuda") if m is not None else None)

        pipe.encode_prompt = _enc_cpu

    def drop_text_encoder(self) -> None:
        """编码完就把 15.8GB 文本编码器从内存里扔掉。

        留着没用（每帧同一段提示词），但它会把可用内存压到 0，之后连模型加载
        都要 3 分钟（实测）。扔掉后第一次 encode_prompt 会失败——所以调用方要
        保证先 encode 再 drop（`warm_prompts` 就是干这个的）。
        """
        if self.pipe is not None and self._te is not None:
            self.pipe.text_encoder = None
            self._te = None
            gc.collect()
            self._log("已释放文本编码器（提示词缓存）")

    # ---------- 控制注入（幂等） ----------
    def attach(self, cond: torch.Tensor, scale: float = 1.0) -> None:
        self.ensure()
        tr = self.pipe.transformer
        if getattr(tr, "_cn_attached", False):
            tr._cn_holder["cond"] = cond
            tr._cn_holder["scale"] = scale
            return
        holder = {"cond": cond, "scale": scale}
        tr._cn_holder = holder
        tr._cn_attached = True
        orig = tr.forward
        controlnet = self.controlnet

        def patched(*args, **kw):
            hs = kw["hidden_states"]
            shapes = kw["img_shapes"]
            n_lat = shapes[0][0][1] * shapes[0][0][2]
            cn = controlnet(
                hidden_states=hs[:, :n_lat],
                controlnet_cond=holder["cond"],
                conditioning_scale=holder["scale"],
                encoder_hidden_states=kw.get("encoder_hidden_states"),
                encoder_hidden_states_mask=kw.get("encoder_hidden_states_mask"),
                timestep=kw.get("timestep"),
                img_shapes=[shapes[0][:1]],
                return_dict=False,
            )   # return_dict=False 时直接返回 block_samples 列表，不要再取 [0]
            total = hs.shape[1]
            if total > n_lat:
                def _pad(c, _t=total, _n=n_lat):
                    if c.shape[1] == _t:
                        return c
                    shape = (c.shape[0], _t - _n) + tuple(c.shape[2:])
                    return torch.cat([c, c.new_zeros(shape, dtype=c.dtype)], dim=1)
                cn = [_pad(c) for c in cn]
            kw["controlnet_block_samples"] = cn
            return orig(*args, **kw)

        tr.forward = patched

    # ---------- 编码 ----------
    def encode_control(self, img: Image.Image, size: int, kind: str = "black") -> torch.Tensor:
        """控制图 → packed latent（口径抄 pipeline_qwenimage_controlnet.py）。"""
        vae = self.pipe.vae
        im = _control_image(img, kind).convert("RGB").resize((size, size), Image.LANCZOS)
        import numpy as np
        x = torch.from_numpy(
            np.asarray(im).astype("float32") / 127.5 - 1.0
        ).permute(2, 0, 1)[None, :, None].to(device="cuda", dtype=vae.dtype)
        with torch.no_grad():        # 不包 no_grad 会留 4GB 激活（踩过）
            lat = vae.encode(x).latent_dist.mode()
        z = vae.config.z_dim
        mean = torch.tensor(vae.config.latents_mean, device="cuda").view(1, z, 1, 1, 1)
        std = 1.0 / torch.tensor(vae.config.latents_std, device="cuda").view(1, z, 1, 1, 1)
        lat = (lat - mean) * std
        lat = lat.permute(0, 2, 1, 3, 4)
        return _pack(lat, 1, z, lat.shape[3], lat.shape[4]).to(dtype=vae.dtype)

    @staticmethod
    def _ref_rgb(img) -> Image.Image:
        """参考图必须去 alpha：RGBA 直接喂 VAE 会报 4 通道错误。"""
        im = img.convert("RGBA")
        return Image.alpha_composite(
            Image.new("RGBA", im.size, (255, 255, 255, 255)), im).convert("RGB")

    @staticmethod
    def _refs(ref) -> list:
        """参考图可以是单张或多张（多张时按「图1/图2」在提示词里引用）。"""
        if isinstance(ref, (list, tuple)):
            return [Qwen20EditControl._ref_rgb(r) for r in ref]
        return [Qwen20EditControl._ref_rgb(ref)]

    # ---------- 推理 ----------
    def _watchdog(self, key: str) -> threading.Event:
        stop = threading.Event()

        def loop():
            while not stop.wait(5.0):
                if time.time() - self._last_step > self.stall_seconds:
                    self._stalled = True
                    self.pipe._interrupt = True     # 管线每步会看这个标志
                    self._log(f"看门狗：{self.stall_seconds:.0f}s 无步进，中止本帧")
                    return

        threading.Thread(target=loop, daemon=True).start()
        return stop

    def run_frame(self, *, control: Image.Image, ref: Image.Image, prompt: str = "",
                  negative: str = DEFAULT_NEGATIVE, seed: int = 1234,
                  kind: str = "black", allow_degrade: bool = True,
                  cn_scale: float = 1.0,
                  embeds: Optional[Dict[str, Any]] = None
                  ) -> Tuple[Image.Image, Dict[str, Any]]:
        """一帧：控制图定姿势，参考图定身份。失败/卡死会自动降级重试一次。"""
        self.ensure()
        size, cfg = self.size, self.cfg
        meta: Dict[str, Any] = {"size": size, "cfg": cfg, "steps": self.steps,
                                "degraded": False, "seconds": None}
        t0 = time.time()
        for attempt in (1, 2):
            cond = self.encode_control(control, size, kind)
            self.attach(cond, scale=cn_scale)
            self._stalled = False
            self._last_step = time.time()
            _t0 = time.time()
            stop = self._watchdog(f"a{attempt}")

            def _cb(pipe_, step, t, kw):
                self._last_step = time.time()
                if os.environ.get("STEP_LOG") and (step % 5 == 0 or step == self.steps - 1):
                    print(f"    [step] {step + 1}/{self.steps} "
                          f"{time.time() - _t0:.0f}s "
                          f"{torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)
                return kw

            try:
                if embeds:      # 预先编码好的提示词（文本编码器已释放）
                    tk = dict(prompt=None, negative_prompt=None,
                              prompt_embeds=embeds["pos"],
                              prompt_embeds_mask=embeds.get("pos_mask"),
                              negative_prompt_embeds=embeds["neg"] if cfg > 1 else None,
                              negative_prompt_embeds_mask=embeds.get("neg_mask") if cfg > 1 else None)
                else:
                    tk = dict(prompt=prompt,
                              negative_prompt=negative if cfg > 1 else None)
                out = self.pipe(
                    image=self._refs(ref), **tk,
                    height=size, width=size, num_inference_steps=self.steps,
                    true_cfg_scale=cfg,
                    generator=torch.Generator("cuda").manual_seed(seed + attempt),
                    callback_on_step_end=_cb,
                ).images[0].convert("RGB")
            except Exception as e:
                stop.set()
                if attempt == 2 or not allow_degrade:
                    raise
                self._log(f"异常 {type(e).__name__}: {str(e)[:80]}，降级重试")
                size, cfg = max(320, size - 64), 1.0
                meta.update(degraded=True, size=size, cfg=cfg)
                gc.collect()
                torch.cuda.empty_cache()
                continue
            stop.set()

            if not self._stalled:
                meta["seconds"] = round(time.time() - t0, 1)
                self._release()
                return out, meta

            if attempt == 2 or not allow_degrade:
                meta["seconds"] = round(time.time() - t0, 1)
                meta["stalled"] = True
                self._release()
                return out, meta
            # 卡死 → 降级（先去 CFG，再降尺寸）
            self._log("卡死降级：关 CFG、尺寸 -64 重试")
            size, cfg = max(320, size - 64), 1.0
            meta.update(degraded=True, size=size, cfg=cfg)
            gc.collect()
            torch.cuda.empty_cache()
        raise RuntimeError("重试后仍然失败")

    def _release(self) -> None:
        gc.collect()
        torch.cuda.empty_cache()

    def warm_prompts(self, prompts) -> Dict[str, Any]:
        """把提示词先编码好、然后把 15.8GB 文本编码器从内存里扔掉。

        批量跑 12 帧时同一个提示词要编 12 次，纯浪费；编码器常驻又会把内存吃光。
        """
        self.ensure()
        cache: Dict[str, Any] = {}
        for p in prompts:
            pos, pos_mask = self.pipe.encode_prompt(p, device="cpu", max_sequence_length=512)
            cache[p] = (pos, pos_mask)
        self.drop_text_encoder()
        return cache


def _control_image(src: Image.Image, kind: str) -> Image.Image:
    """动作帧 → 结构控制图。黑底白剪影实测最贴合官方 pose 示例的观感。"""
    import numpy as np
    a = np.asarray(src.convert("RGBA"))
    m = a[..., 3] > 8
    if kind == "black":
        flat = np.zeros((*m.shape, 3), np.uint8)
        flat[m] = (255, 255, 255)
    elif kind == "src":      # 直接拿源帧当控制图（保留原色，比剪影信息量大）
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[m] = a[..., :3][m]
    elif kind == "sil":
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[m] = (110, 110, 110)
    elif kind in ("canny", "depth", "softedge"):
        # **这三种才是 ControlNet-Union 真正训练过的类型**（官方 conds/ 里就是
        # canny / depth / pose / soft_edge 四种）。剪影是离分布输入，控制信号弱。
        flat = _union_style(a, m, kind)
    else:
        raise ValueError(f"未知控制图类型 {kind}")
    return Image.fromarray(flat)


def _union_style(a: np.ndarray, m: np.ndarray, kind: str) -> np.ndarray:
    """按官方 conds/ 的观感生成控制图。

    depth   ：主体亮（近）、背景由近及远变暗 —— 对齐官方 depth.png 的灰度分布
    canny   ：白底黑线的边缘图
    softedge：黑底白色柔边（主体轮廓的柔化版本）
    """
    import cv2
    import numpy as np
    if kind == "canny":
        rgb = np.where(m[..., None], a[..., :3], 255).astype(np.uint8)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 60, 160)
        out = np.full((*m.shape, 3), 255, np.uint8)
        out[edges > 0] = (0, 0, 0)
        return out
    if kind == "softedge":
        mask = (m * 255).astype(np.uint8)
        soft = cv2.GaussianBlur(mask, (0, 0), 2.0)
        return np.repeat(soft[..., None], 3, axis=2)
    # depth：主体最亮，背景按"离主体越远越暗"渐变
    dist = cv2.distanceTransform((~m).astype(np.uint8), cv2.DIST_L2, 3)
    h = max(float(dist.max()), 1e-6)
    depth = np.clip(255.0 * (1.0 - dist / h), 0, 255)
    depth[m] = 255.0
    return np.repeat(depth.astype(np.uint8)[..., None], 3, axis=2)
