"""内置 diffusers 推理服务：本地真模型（无需第三方 UI）。

从归档 PixelForge 的 diffusers_local.py 移植（同批实测参数），保留：
- 懒加载：不装 torch/diffusers 也能启动服务，只在首次生成时报错；
- 单例缓存 + 家族互斥显存换出（单卡 24GB 现实）；
- 组件级 fp8 权重量化 + 落盘缓存（Qwen 系 39GB → 约 20GB / 2.1 全套 → 14.8GB）；
- 步进回调：SSE 进度 + 取消。

2026-10-23 底座统一：图片侧**只有 Qwen-Image-2.1**（qwen21 族，文生图 /
指令改图 / 原生 RGBA 三合一）。Z-Image-Turbo 与 Qwen-Image-Edit-2511 两条
管线连同各自 loader / 参数已删除——三套底座并存时，「负面词只在一个族生效」
「透明声明只在一个族被识别」这类静默失效会反复出现，权重也都在归档目录。
支持的族：
- qwen21：Qwen-Image-2.1（7B 单流 DiT + Qwen3-VL 编码器）
      文生图与指令改图合体；组件分批 fp8 + cpu offload，峰值约 14.8GB
      40 步 · true_cfg_scale=4.0（必须配 negative_prompt，否则 CFG 静默失效）
- wan：Wan2.2 TI2V-5B（图生视频，行走视频节点用）
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import time
from typing import Any, Dict, List, Optional

from .base import BaseProvider, ProviderResult
from ..config import resolve_path
from .. import progress as progress_mod

_CACHE: Dict[str, Any] = {}
_CAPS: Dict[str, Dict[str, Any]] = {}
_FAMILY: Dict[str, str] = {}
_LOCK = asyncio.Lock()

# 最近一次用到某个底座的时间戳：显存是独占资源，跑完就一直抱着不放会让
# 其他程序（浏览器 / 游戏 / 另一个 AI 工具）长期分不到显存。
_LAST_USE = time.time()

_FP8_LINEAR_CLS: Any = None

# fp8 缓存的布局版本号。改了 state_dict 的键布局（fp8 Parameter → fp8 buffer
# w_fp8 + 空 weight）就必须换号，否则旧缓存会被误判成命中并在加载时 KeyError。
_FP8_CACHE_FORMAT = "fp8-e4m3-rowwise-buf/v2"


def _get_fp8_linear_cls() -> Any:
    """懒加载 _FP8Linear 类（本模块遵循「无 torch 也能 import」的设计）。

    fp8(E4M3) 权重量化 Linear：权重存 fp8 + 逐输出通道 scale，前向时反量化
    成 bf16 再算。权重是普通 fp8 张量（safetensors 0.4.2+ 原生支持、
    bit 级往返一致），因此可落盘缓存。

    为什么 fp8 权重不能直接放 weight Parameter：diffusers 到处用「取第一个
    Parameter 的 dtype」推断计算精度，时间步张量（0-1000）会被转成 fp8 而
    e4m3 上限只有 448 —— 一转就溢出成 inf/NaN。所以布局是：真数据放 buffer
    ``w_fp8``，weight 置成一个 0 字节的 bf16 Parameter 专供 dtype 探针。
    """
    global _FP8_LINEAR_CLS
    if _FP8_LINEAR_CLS is None:
        import torch
        import torch.nn.functional as F

        class _FP8Linear(torch.nn.Linear):
            """权重存 fp8 buffer（w_fp8）+ 一个 bf16 空 Parameter 当 dtype 探针。"""

            def forward(self, x):  # type: ignore[override]
                w_fp8 = getattr(self, "w_fp8", None)
                if w_fp8 is None:                    # 非量化路径（理论上不会到）
                    w = self.weight.to(x.dtype)
                    return F.linear(x, w, self.bias)
                dt = x.dtype if x.dtype in (torch.bfloat16, torch.float16) else torch.bfloat16
                if x.dtype != dt:
                    x = x.to(dt)
                w = w_fp8.to(device=x.device, dtype=dt)
                scale = getattr(self, "w_scale", None)
                if scale is not None:
                    w = w * scale.to(device=w.device, dtype=dt)[:, None]
                b = self.bias
                if b is not None:
                    b = b.to(device=w.device, dtype=dt)
                return F.linear(x, w, b)

        _FP8_LINEAR_CLS = _FP8Linear
    return _FP8_LINEAR_CLS


def _wrap_fp8_weight(linear: "torch.nn.Linear") -> None:
    """把 Linear 的裸 fp8 weight 转成 fp8 buffer + bf16 空 Parameter。"""
    import torch

    w = linear.weight
    if isinstance(w, torch.nn.Parameter):
        w = w.data
    if not isinstance(w, torch.Tensor) or w.dtype != torch.float8_e4m3fn:
        return
    linear.register_buffer("w_fp8", w)
    linear.weight = torch.nn.Parameter(
        torch.empty(0, dtype=torch.bfloat16, device=w.device), requires_grad=False)


def _read_json(path: str) -> Dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def detect_family(model_id: str) -> str:
    """模型族识别：qwen21（文生图 / 参考图编辑 / RGBA 三合一）/ wan / unknown。

    先读 model_index.json 的 _class_name（最可靠），再看目录名兜底；
    认不出来返回 "unknown"（由调用方报错，不静默按别的族加载）。
    2026-10-23 起图片侧只剩 Qwen-Image-2.1：Z-Image-Turbo 与
    Qwen-Image-Edit-2511 两条管线已删（权重在归档目录，避免三套 loader
    三套参数各自漂移——迁移前就有过「负面词只在一族生效」的静默失效）。
    """
    if model_id in _FAMILY:
        return _FAMILY[model_id]
    family = "unknown"
    if os.path.isdir(model_id):
        idx = _read_json(os.path.join(model_id, "model_index.json"))
        cls = str(idx.get("_class_name") or "")
        if "QwenImage21" in cls:
            family = "qwen21"
        elif "Wan" in cls:
            family = "wan"
    if family == "unknown":
        name = os.path.basename(model_id.rstrip("/\\")).lower()
        if "qwen-image-2" in name or "qwen_image_2" in name or "qwenimage21" in name:
            family = "qwen21"
        elif "wan" in name:
            family = "wan"
    _FAMILY[model_id] = family
    return family


# 单卡 24GB 的现实：Qwen-Image-2.1 fp8 峰值约 14.8GB、Wan2.2 TI2V-5B 约 10GB，
# 两个模型族在同进程共存可能 OOM 或被 WDDM 挤到共享内存，必须互斥驻留。
_BIG_FAMILIES = {"qwen21", "wan"}


def _evict_other_families(incoming: str) -> None:
    """加载大族模型前，把缓存里另一大族的驻留模型卸掉，给显存让位。"""
    if incoming not in _BIG_FAMILIES:
        return
    victims = [m for m in list(_CACHE)
               if (_FAMILY.get(m) or detect_family(m)) in _BIG_FAMILIES
               and (_FAMILY.get(m) or detect_family(m)) != incoming]
    if not victims:
        return
    for mid in victims:
        _CACHE.pop(mid, None)
        _CAPS.pop(mid, None)
    gc.collect()
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
    print(f"[meowcute] 显存换出：卸载 {'/'.join(victims)}，为 {incoming} 让位",
          flush=True)


MAX_REFS = 4


def _normalize_refs(ref=None, refs=None) -> List[Any]:
    """把 ref（单张）与 refs（多张）合并成统一的参考图列表。

    上限 4 张：Qwen-Image-2.1 支持多图输入，但再多既拉长编码时间，也很难
    让模型分清主次。顺序有意义——第一张决定输出宽高。
    """
    out: List[Any] = []
    if ref is not None:
        out.append(ref)
    if refs:
        out.extend([r for r in refs if r is not None])
    return out[:MAX_REFS]


def _step_label(progress: Optional[Dict[str, Any]], i: int) -> str:
    """步进回调里的标签：批量时带上「第 k/n 张」的前缀，单张不带。"""
    label = str((progress or {}).get("label") or "")
    return f"{label} 第 {i + 1} 张" if label else f"第 {i + 1} 张"


def _ref_fill_color(rgba: Any) -> tuple:
    """参考图透明区要铺什么色：先看边框实心像素的众数，全透明就退中灰。

    中灰（#808080）不是随便挑的：实测这是模型最愿意当成「什么都没有」的
    底色，边带不会被画成场景元素；白底则经常被理解成「白色地面/纸」。
    """
    import numpy as np

    a = np.asarray(rgba)[:, :, 3]
    solid = np.asarray(rgba)[:, :, :3][a > 32]
    if solid.size < 96:
        return (128, 128, 128)
    q = (solid // 16 * 16)
    colors, counts = np.unique(q, axis=0, return_counts=True)
    return tuple(int(v) for v in colors[counts.argmax()] + 8)


def _prep_ref_images(refs: List[Any], w: int, h: int,
                     bg: Optional[Any] = None) -> List[Any]:
    """参考图进编辑管线前的归一化：**等比缩放居中 + 透明区铺底色**。

    这里修的是两个静默的图像条件 bug（比提示词更能决定角色像不像）：
    1. `convert("RGB")` 会把 alpha=0 的区域压成黑色——透明底正视图喂进去，
       模型看到的是「黑底上的角色」，它就会顺手把黑当背景画进结果（暗部、
       底色、地面），和「透明背景」的指令直接打架。现在按参考图自己的
       实心边框色（或调用方给的底色）铺平，模型看到的是一张完整的图。
    2. `resize((w, h))` 是**拉伸**：512×768 的正视图转 768×768 时角色被横向
       拉胖 1.5 倍，模型转完视角再按这个比例画，直接换了个体型。改成等比
       缩放 + 居中留边（letterbox），参考图里的比例和位置才是真的。
    """
    from PIL import Image
    out: List[Any] = []
    for r in refs:
        if r is None:
            continue
        im = r.convert("RGBA") if hasattr(r, "convert") else None
        if im is None:
            continue
        fill = tuple(int(v) for v in bg[:3]) if bg is not None else _ref_fill_color(im)
        scale = min(w / im.width, h / im.height)
        nw = max(1, min(w, int(round(im.width * scale))))
        nh = max(1, min(h, int(round(im.height * scale))))
        small = im.resize((nw, nh), Image.LANCZOS)
        canvas = Image.new("RGB", (w, h), fill)
        # 带 alpha 贴入：半透明边缘与底色混合，和原图叠在底色上的观感一致
        canvas.paste(small, ((w - nw) // 2, (h - nh) // 2), small)
        out.append(canvas)
    return out


def _frames_to_pil(frames: Any) -> List[Any]:
    """把视频管线的输出统一成一维 PIL 列表（RGBA）。

    Wan I2V 的输出结构随 output_type 变：output_type="pil" 是 [batch][frame] 的
    PIL 列表，="np" 是 numpy 数组 [batch][frame][h][w][c]。这里两种都收，
    免得为了换输出类型去改调用点——类型差异不是业务逻辑该操心的事。

    顺带解决了一个真实踩坑：把 [0]（取 batch 0）后的结果当成 PIL 用，
    在默认 np 模式下会报 'numpy.ndarray' object has no attribute 'convert'。
    """
    import numpy as np
    from PIL import Image

    seq = frames[0] if (hasattr(frames, "__len__") and len(frames)
                        and isinstance(frames[0], (list, tuple))) else frames
    # numpy 输出带 batch 维（[b][f][h][w][c]）。这里把所有前置 batch 维压掉：
    # 判据是「单个元素的维度 ≥ 4」——只有帧数据才有 5 维，灰度图只有 2 维、
    # 单帧 HWC 只有 3 维，不会被误当 batch。逐元素 isinstance 判断既啰嗦又易错。
    if not hasattr(seq, "ndim") and len(seq) and hasattr(seq[0], "ndim"):
        if seq[0].ndim >= 4:          # [b][f][h][w][c]
            seq = [seq[b] for b in range(len(seq))]
    out: List[Any] = []
    for f in seq:
        if isinstance(f, Image.Image):
            out.append(f.convert("RGBA"))
        else:
            arr = np.asarray(f)
            if arr.ndim >= 4:          # 残留 batch 维（单个视频对象）
                arr = arr[0]
            # 只在「最后一维明显不是通道」时才转置。必须同时满足首末维都是
            # 小通道数（3/4）才算 CHW——只用 shape[0] 判会把 HWC 的小尺寸图
            # （比如 8×10×3）误转置成 (3,8,10) 再喂给 fromarray，直接抛 TypeError。
            if (arr.ndim == 3 and arr.shape[-1] not in (3, 4)
                    and arr.shape[0] in (3, 4)):
                arr = np.transpose(arr, (1, 2, 0))   # CHW → HWC
            if arr.ndim == 2:
                # 灰度图：必须真的复制成 3 通道。用 arr[:, :, None] 补一维会得到
                # (h, w, 1)，PIL 的 fromarray 不认（KeyError (1,1,1)）。
                arr = np.repeat(arr[:, :, None], 3, axis=2)
            out.append(Image.fromarray(arr.astype("uint8")).convert("RGBA"))
    return out


def _resolve_seq_len(prompt: str, requested: Optional[int]) -> int:
    """文本编码的最大序列长度：界面指定优先，否则按提示词长度估算。

    不能写死：太短会把尾部的像素契约截断（中文提示词常 400 字），太长则
    白白增加文本编码开销。估算按中文约 1.3 token/字 + 64 余量，向上取 128
    的倍数，并夹在 [512, 1536]——512 是两条管线的默认值，低于它没有收益。
    """
    if requested:
        return max(128, min(2048, int(requested)))
    est = int(len(prompt or "") * 1.3) + 64
    est = max(512, min(1536, est))
    return (est + 127) // 128 * 128


def resolve_video_model(explicit: Optional[str] = None) -> str:
    """解析 Wan2.2 TI2V-5B 视频底座路径，解析不到就抛带指引的错误。

    顺序：显式参数 → 设置里的 videoModel → 设置里任何 wan 族 provider →
    models/wan2.2-ti2v-5b。
    """
    if explicit:
        hit = resolve_path(explicit)
        if os.path.isdir(hit):
            return hit
    try:
        from ..config import load_settings

        s = load_settings()
        direct = resolve_path(str((s.get("providers", {}).get("diffusers", {}) or {}).get("videoModel") or ""))
        if direct and os.path.isdir(direct) and detect_family(direct) == "wan":
            return direct
        for cfg in (s.get("providers") or {}).values():
            if not isinstance(cfg, dict):
                continue
            mid = resolve_path(str(cfg.get("model") or ""))
            if mid and os.path.isdir(mid) and detect_family(mid) == "wan":
                return mid
    except Exception:
        pass
    default = resolve_path("models/wan2.2-ti2v-5b")
    if os.path.isdir(default):
        return default
    raise RuntimeError(
        "未找到 Wan2.2 TI2V-5B 视频底座。请将 models/wan2.2-ti2v-5b 放到项目 "
        "models/ 目录下，或在设置里配置视频底座路径。")


# torch 不在 requirements.txt 里：它是 2.9GB 的推理依赖，只有真要出图才需要，
# 塞进基础依赖会让「只想维护提示词 / 看项目」的人白等十几分钟。
# 代价是没装 torch 时点生成会抛 ModuleNotFoundError——这里统一换成可执行的指引。
_TORCH_HINT = (
    "未安装 PyTorch，AI 生成不可用（项目 / 提示词 / 画风等管理功能不受影响）。\n"
    "安装推理依赖（约 3GB）：\n"
    "  Windows:            start.bat --gpu\n"
    "  macOS / Linux:      ./start.sh --gpu\n"
    "或手动安装（NVIDIA 显卡，CUDA 12.4）：\n"
    "  pip install torch torchvision==0.21.0 --index-url https://download.pytorch.org/whl/cu124\n"
    "  pip install diffusers transformers accelerate safetensors huggingface_hub peft torchao==0.16.0"
)


def _require_torch():
    try:
        import torch
    except Exception as e:  # pragma: no cover
        raise RuntimeError(_TORCH_HINT) from e
    return torch


def _torch_status() -> Dict[str, Any]:
    try:
        import torch  # noqa: F401
    except Exception as e:
        return {"installed": False, "error": str(e)}

    import torch

    free_gb = 0.0
    if torch.cuda.is_available():
        try:
            free, _ = torch.cuda.mem_get_info()
            free_gb = round(free / 1024**3, 1)
        except Exception:
            pass
    return {
        "installed": True,
        "version": torch.__version__,
        "cuda": torch.cuda.is_available(),
        "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
        "vramGB": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
        if torch.cuda.is_available() else 0,
        "freeGB": free_gb,
    }


# 两个大族在 24GB 卡上的实测占用（fp8 + cpu offload）：
# Qwen-Image-2.1 约 14.8GB、Wan2.2 TI2V-5B 约 19GB。留 1GB 余量给 CUDA context。
_NEED_GB = {"qwen21": 14.8, "wan": 19.0}


def _vram_note(family: str) -> Optional[str]:
    """可用显存不够时返回一句人话告警，够了返回 None。

    为什么值得单独查一次：Windows(WDDM) 显存打满不会 OOM，而是把超出部分
    放到系统内存（共享显存），DiT 每一步都从 PCIe 拉权重。实测同一张 512²：
    桌面占 1GB 时 2.1s，桌面占 6GB 时 203s——差 100 倍，而报错一个字都没有，
    用户只会以为「模型很慢」。这里不拒绝请求（慢总比不能跑好），只把原因
    写进日志与 meta。
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        free_gb = free / 1024**3
        need = _NEED_GB.get(family, 20.5)
        if free_gb >= need:
            return None
        return (
            f"可用显存只剩 {free_gb:.1f}GB（{family} 需要约 {need:.0f}GB，"
            f"总共 {total / 1024**3:.0f}GB）。超出部分会被 Windows 放进共享内存，"
            "推理会慢 50-100 倍（实测 2s → 200s+）。"
            "请关闭占显卡的程序（浏览器硬件加速、游戏、视频播放、其他 AI 工具）后重试。"
        )
    except Exception:  # pragma: no cover
        return None


class DiffusersProvider(BaseProvider):
    id = "diffusers"
    label = "本地底座（Qwen-Image-2.1 / Wan 视频）"

    def _resolve_model(self) -> str:
        model = (self.cfg.get("model") or "").strip()
        if model:
            # 相对路径按项目根解析：从别的目录启动服务也能找到 models/ 下的底座
            return resolve_path(model)
        return resolve_path(os.environ.get("MEOWCUTE_MODEL", "models/qwen-image-2.1"))

    def _load(self, model_id: str):
        if model_id in _CACHE:
            return _CACHE[model_id]
        family = detect_family(model_id)
        if family not in ("qwen21", "wan"):
            raise RuntimeError(
                f"暂不支持的模型：{model_id}。当前版本只支持 Qwen-Image-2.1"
                "（文生图 / 参考图编辑 / 原生 RGBA 三合一）与 Wan2.2 TI2V-5B"
                "（图生视频）。")
        # 单卡 24GB 只容一个大家族：切换前先把另一大族卸了，否则共存必 OOM
        _evict_other_families(family)
        if family == "qwen21":
            return self._load_qwen21(model_id)
        return self._load_wan(model_id)

    def _load_qwen21(self, model_id: str):
        """Qwen-Image-2.1：7B 单流 DiT 统一底座（2026-10-23 起唯一的图片底座）。

        一个 checkpoint 覆盖三件事：文生图、指令改图（参考图编辑）、原生
        RGBA 透明输出。实测（同图同指令对比 Edit-2511）：更忠实、峰值显存
        14.6~14.8GB（Edit-2511 fp8 为 19.8~22.4GB）、单次 13~19s（快 6~9 倍）。

        显存策略（实测踩坑）：一次性 from_pretrained 整个仓库要 31GB，本机
        可用内存扛不住，必须组件分批加载 + fp8 量化再组装——
        transformer 13.25GB bf16 → fp8（_load_component_fp8，缓存落盘），
        text_encoder 16.33GB bf16 → torchao fp8（与实验脚本同路径，约 9s）。
        """
        import torch
        from pathlib import Path

        try:
            from diffusers import QwenImage21Pipeline, QwenImage21Transformer2DModel
        except Exception as e:
            raise RuntimeError(
                "Qwen-Image-2.1 需要较新版本的 diffusers（含 QwenImage21Pipeline）。"
                "请执行：\n"
                "  ./start.sh --gpu      （Windows：start.bat --gpu）\n"
                "或  pip install -U diffusers\n"
                f"原始错误：{e}"
            ) from e
        try:
            from transformers import Qwen3VLForConditionalGeneration
        except Exception as e:
            raise RuntimeError(
                "Qwen-Image-2.1 的文本编码器需要 transformers ≥ 5.17。请执行：\n"
                "  pip install -U transformers\n"
                f"原始错误：{e}"
            ) from e

        torch.backends.cudnn.benchmark = True
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Qwen-Image-2.1 是 7B DiT，需要 CUDA GPU（如 RTX 4090）；CPU 推理不可行。"
            )

        print("[meowcute] 加载并量化 Qwen-Image-2.1 transformer（fp8）…", flush=True)
        transformer = self._load_component_fp8(
            model_id, "transformer", QwenImage21Transformer2DModel,
            "qwen21.transformer")
        print("[meowcute] 加载并量化 Qwen-Image-2.1 text_encoder（fp8）…", flush=True)
        text_encoder = Qwen3VLForConditionalGeneration.from_pretrained(
            str(Path(model_id) / "text_encoder"), torch_dtype=torch.bfloat16)
        self._quantize_fp8(text_encoder, "qwen21.text_encoder")

        pipe = QwenImage21Pipeline.from_pretrained(
            model_id, transformer=transformer, text_encoder=text_encoder,
            torch_dtype=torch.bfloat16)
        offload = _enable_offload(pipe, "qwen21")
        pipe.set_progress_bar_config(disable=True)
        _CAPS[model_id] = {
            "family": "qwen21",
            "notes": ["Qwen-Image-2.1：文生图 / 指令改图 / 原生 RGBA 三合一；"
                      "fp8（transformer+text_encoder）+ cpu offload",
                      offload + "；峰值约 14.8GB，任务结束即释放"],
        }
        _CACHE[model_id] = pipe
        return pipe

    def _run_qwen21(self, model_id: str, prompt: str, negative: str,
                    width: int, height: int, steps: Optional[int],
                    seed: Optional[int], generator: Any, n: int,
                    refs: Optional[Any] = None, task_id: Optional[str] = None,
                    max_seq_len: Optional[int] = None,
                    ref_bg: Optional[Any] = None,
                    progress: Optional[Dict[str, Any]] = None):
        """Qwen-Image-2.1 统一推理：带参考图 = 指令改图，不带 = 文生图。

        - true_cfg_scale=4.0 必须配 negative_prompt：管线是
          `do_true_cfg = true_cfg_scale > 1 and has_neg_prompt`，不传负向
          时 CFG **静默失效**（2026-10-22 实测 cfg1/cfg4 输出逐字节相同）；
        - 输出尺寸显式传 width/height（output_resolution 只在缺省时兜底）；
        - 改图路径把参考图统一缩放到目标尺寸（管线对多图原生支持）；
        - 提示词要透明底时（含 RGBA 描述）直接出 alpha，可省掉后续抠图。
        """
        import torch
        from PIL import Image as _Image

        pipe = self._load(model_id)
        use_steps = 40 if not steps else max(20, min(int(steps), 50))
        w = int(width or 512) // 16 * 16
        h = int(height or 512) // 16 * 16

        srcs = _prep_ref_images([r for r in (refs or []) if r is not None], w, h, ref_bg)
        image_arg = srcs[0] if len(srcs) == 1 else (srcs or None)

        images = []
        try:
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        for i in range(max(1, min(n, 4))):
            kw = dict(
                prompt=prompt,
                negative_prompt=negative if negative is not None else "",
                height=h, width=w,
                output_resolution=max(w, h),
                num_inference_steps=use_steps,
                true_cfg_scale=4.0,
                generator=generator,
                callback_on_step_end=progress_mod.make_step_callback(
                    task_id, use_steps,
                    label=_step_label(progress, i),
                    base=int((progress or {}).get("base") or 0),
                    grand=int((progress or {}).get("grand") or 0)),
            )
            if image_arg is not None:
                kw["image"] = image_arg
            out = pipe(**kw)
            images.append(out.images[0].convert("RGBA"))
            if generator is not None and seed is not None:
                generator = torch.Generator(device=generator.device).manual_seed(int(seed) + i + 1)
        _release_vram()
        meta = {"steps": use_steps, "cfg": 1.0, "trueCfg": 4.0,
                "family": "qwen21",
                "mode": "edit" if srcs else "txt2img",
                "refCount": len(srcs), "size": [w, h]}
        try:
            if torch.cuda.is_available():
                meta["vram"] = {
                    "peakGB": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                    "reservedGB": round(torch.cuda.memory_reserved() / 2**30, 2),
                    "totalGB": round(
                        torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
                }
        except Exception:
            pass
        return images, meta

    # ---------- Qwen-Image-Edit fp8 transformer：量化 + 落盘缓存 ----------

    def _load_qwen_transformer_fp8(self, model_id: str):
        """加载 fp8 版 transformer（约 20GB，进 24GB 卡的关键）。

        两条路径：缓存命中（<model>/transformer-fp8/）safetensors mmap 零拷贝
        assign，约 20s；未命中则 bf16 完整加载 → GPU 逐层 fp8 量化（约 10s）
        → 落盘缓存（约 20s）。任何失败都抛异常，由上层退回完整加载。
        """
        import time
        from pathlib import Path

        import torch  # noqa: F401
        from diffusers import QwenImageTransformer2DModel

        model_dir = Path(model_id)
        cache_dir = model_dir / "transformer-fp8"
        if self._fp8_cache_valid(cache_dir, model_dir):
            t0 = time.time()
            t = self._load_fp8_cache(cache_dir)
            print(f"[meowcute] fp8 缓存命中：{cache_dir.name}/（{time.time() - t0:.0f}s，"
                  f"跳过 39GB bf16 读取与量化）", flush=True)
            return t

        t0 = time.time()
        t = QwenImageTransformer2DModel.from_pretrained(
            str(model_dir / "transformer"), torch_dtype=torch.bfloat16)
        t_load = time.time() - t0
        n = self._quantize_transformer_gpu(t)
        t_quant = time.time() - t0 - t_load
        try:
            self._save_fp8_cache(t, cache_dir, model_dir)
        except Exception as e:
            print(f"[meowcute] fp8 缓存落盘失败（{e}），不影响本次运行", flush=True)
        print(f"[meowcute] transformer fp8 量化完成（{n} 个 Linear，"
              f"bf16 加载 {t_load:.0f}s + GPU 量化 {t_quant:.0f}s，缓存已写 "
              f"{cache_dir.name}/）", flush=True)
        return t

    # ---------- Wan2.2 TI2V-5B：图生视频（参考图 + 动作提示词 → 帧序列） ----------

    def _load_wan(self, model_id: str):
        """Wan2.2 TI2V-5B：5B DiT + umT5-xxl + Wan VAE，diffusers WanImageToVideoPipeline。

        为什么用视频底座做精灵：逐帧编辑（Qwen-Edit 一帧一帧改姿势）每帧都是
        独立推理，帧间没有运动连续性，快速动作会抖；视频底座一次生成整段动作，
        肢体路径天然连贯，抽帧后就是现成的循环素材。

        管线类必须用 I2V 版：仓库的 model_index.json 写的是 ``WanPipeline``（纯文生
        视频，__call__ 不接受 image），但 TI2V-5B 的 transformer 是
        ``in_channels=48``（16 噪声 + 32 图像 latent），只有 WanImageToVideoPipeline
        会把参考图编码进第一帧的 latent 通道。照 model_index 加载会直接报
        ``unexpected keyword argument 'image'``。

        显存策略（照 Qwen-Edit 的作业）：
        - transformer bf16 18.6GB → GPU 逐层 fp8（约 9.3GB），落盘缓存
          transformer-fp8/，冷启动省掉 18GB 读取；
        - umT5-xxl bf16 10.6GB → fp8（约 5.3GB），只在条件编码时用，
          enable_model_cpu_offload 会让它按阶段换出；
        - VAE 2.6GB 保持 bf16（解码质量敏感，且只在首尾用一次）。
        合计常驻约 17GB，24GB 卡可行。
        """
        try:
            import torch
        except Exception as e:  # pragma: no cover
            raise RuntimeError(_TORCH_HINT) from e
        try:
            from diffusers import WanImageToVideoPipeline, WanTransformer3DModel
        except Exception as e:
            raise RuntimeError(
                "Wan2.2 视频生成需要 diffusers ≥ 0.36（含 "
                "WanImageToVideoPipeline / WanTransformer3DModel）。"
                "请执行 pip install -U diffusers。\n"
                f"原始错误：{e}"
            ) from e
        # ftfy 是 encode_prompt → prompt_clean 的硬依赖，但 diffusers 不默认带它。
        # 缺了会在真正开始去噪时才炸（NameError 埋在 diffusers 内部，位置很难找），
        # 所以在这里拦一次并说清楚装什么。
        try:
            import ftfy  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "缺 ftfy：Wan 的提示词编码器会调用 ftfy.fix_text 清洗文本。\n"
                "请执行：pip install ftfy"
            ) from e
        torch.backends.cudnn.benchmark = True
        if not torch.cuda.is_available():
            raise RuntimeError("Wan2.2 是视频 DiT，需要 CUDA GPU；CPU 推理不可行。")

        transformer = None
        if os.environ.get("MEOWCUTE_QWEN_FP8", "1") != "0":
            try:
                transformer = self._load_component_fp8(
                    model_id, "transformer", WanTransformer3DModel, "wan.transformer")
            except Exception as e:
                print(f"[meowcute] fp8 transformer 快速路径失败（{e}），退回完整加载",
                      flush=True)
                transformer = None

        # WanImageToVideoPipeline 与 WanPipeline 的组件字段一致，可以直接复用
        # 仓库里的 config/权重；不存在的 transformer_2/transformer_3 传 None。
        if transformer is not None:
            pipe = WanImageToVideoPipeline.from_pretrained(
                model_id, torch_dtype=torch.bfloat16, transformer=transformer)
        else:
            pipe = WanImageToVideoPipeline.from_pretrained(
                model_id, torch_dtype=torch.bfloat16)
            if os.environ.get("MEOWCUTE_QWEN_FP8", "1") != "0":
                self._quantize_fp8(pipe.transformer, "wan.transformer")
        # umT5-xxl 10.6GB：fp8 后约 5.3GB，offload 会在编码完成后把它搬出显存
        if os.environ.get("MEOWCUTE_QWEN_FP8", "1") != "0":
            self._quantize_fp8(pipe.text_encoder, "wan.text_encoder")
        pipe.enable_model_cpu_offload()
        pipe.set_progress_bar_config(disable=True)
        caps: Dict[str, Any] = {
            "family": "wan",
            "notes": ["Wan2.2 TI2V-5B：参考图 + 中文提示词 → 视频帧序列；"
                      "fp8 + cpu offload"],
        }
        _CAPS[model_id] = caps
        _CACHE[model_id] = pipe
        return pipe

    def _run_wan(self, model_id: str, prompt: str, negative: str,
                 width: int, height: int, steps: Optional[int],
                 seed: Optional[int], generator: Any,
                 ref: Optional[Any], num_frames: int, fps: int, max_side: int,
                 task_id: Optional[str] = None, pixel_art: bool = False,
                 guidance_scale: Optional[float] = None):
        """图生视频：参考图 + 动作提示词 → 帧序列（PIL 列表）。

        - num_frames 必须是 4k+1（Wan 时序 VAE 的要求），内部向下取整；
        - 输入图按 16 对齐缩放到目标尺寸，长边超过 max_side 时先降（编辑成本
          随像素数涨，视频尤其明显——帧数 × 分辨率双重放大）；
        - guidance_scale=5.0 / steps=30 是 TI2V-5B 的官方推荐量级；步数可调，
          但不低于 20（再低动作会跳变）。
        """
        import torch

        if ref is None:
            raise ValueError("Wan 图生视频需要一张参考图（ref）")
        pipe = self._load(model_id)
        use_steps = 30 if not steps else max(20, min(int(steps), 50))
        # CFG 可调控：默认 5.0（TI2V-5B 官方推荐）。行走动作偏弱时上调能显著
        # 加强运动幅度——这正是「动作幅度不够就自动重试加压」的闭环杠杆。
        # 上限 9.0 防止过头引入重影/崩坏；重试逻辑会按步递增到这里。
        gs = 5.0 if guidance_scale is None else float(
            max(1.0, min(9.0, guidance_scale)))
        from PIL import Image as _Image

        src = ref.convert("RGB")
        w = int(width or src.width) // 16 * 16
        h = int(height or src.height) // 16 * 16
        if max(w, h) > max_side:
            scale = max_side / max(w, h)
            w, h = max(16, int(w * scale) // 16 * 16), max(16, int(h * scale) // 16 * 16)
        # 像素保真只在**放大**时用最近邻（保住硬边）。缩小绝不能最近邻：
        # 512×768 → 384×512 这种抽点会产生大量锯齿，视频底座会把它当成
        # 噪声放大，实测剪影面积从稳定的 20~27% 抖到 13~69%（整帧冲淡/重影）。
        # 缩小用 LANCZOS，抗锯齿的损失会被下游精灵表的硬边裁切吃掉。
        upscaling = (w * h) > (src.width * src.height)
        resample = _Image.NEAREST if (pixel_art and upscaling) else _Image.LANCZOS
        src = src.resize((w, h), resample)
        # 峰值统计从这张视频开始算：不清零的话会带上之前方向的峰值，报出来的
        # 数字对不上「一段视频到底要多少显存」
        try:
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass
        # 4k+1：Wan 时序 VAE 把帧数压成 (n-1)/4+1 个潜帧，非 4k+1 会补齐或报错
        frames = max(5, (int(num_frames) - 1) // 4 * 4 + 1)
        out = pipe(
            image=src,
            prompt=prompt,
            negative_prompt=negative or " ",
            height=h,
            width=w,
            num_frames=frames,
            num_inference_steps=use_steps,
            guidance_scale=gs,
            generator=generator,
            # Wan 的 I2V 管线默认 output_type="np"（返回 ndarray），不是 "pil"。
            # 显式要 PIL：下游统一按 Image 处理，少一层 numpy 往返也少一处错位风险。
            output_type="pil",
            # 步进回调：30 步的视频生成是全场最长的等待，进度与取消都靠它
            callback_on_step_end=progress_mod.make_step_callback(task_id, use_steps),
        )
        imgs = _frames_to_pil(out.frames)
        # 峰值显存：用户抱怨「显存很大」时，最需要知道的就是这个数到底多少。
        # 注意报 reserved 而不是 allocated——分配器把峰值一直攥着不还，
        # nvidia-smi 的「已用」其实是 reserved。
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
        # 一段视频跑完就把缓存块还回去：八方向连跑五段，分配器会越攒越大
        _release_vram()
        return imgs, {
            "steps": use_steps, "cfg": gs, "family": "wan",
            "mode": "i2v", "frames": len(imgs), "fps": fps,
            "seconds": round(len(imgs) / max(1, int(fps)), 2),
            "size": [w, h],
            **({"vram": vram} if vram else {}),
        }

    def _load_component_fp8(self, model_id: str, subdir: str, cls, tag: str):
        """任意 diffusers 组件的 fp8 加载（量化 + 落盘缓存），Qwen/Wan 共用。

        与 _load_qwen_transformer_fp8 的区别只是组件目录和模型类可指定，
        逻辑完全一样：命中缓存 mmap 直读，未命中 GPU 逐层量化后写缓存。
        """
        import time
        import torch
        from pathlib import Path

        model_dir = Path(model_id)
        cache_dir = model_dir / f"{subdir}-fp8"
        if self._fp8_cache_valid(cache_dir, model_dir, subdir):
            t0 = time.time()
            t = self._load_fp8_cache(cache_dir, cls)
            print(f"[meowcute] fp8 缓存命中：{cache_dir.name}/（{time.time() - t0:.0f}s）",
                  flush=True)
            return t

        t0 = time.time()
        t = cls.from_pretrained(str(model_dir / subdir), torch_dtype=torch.bfloat16)
        t_load = time.time() - t0
        n = self._quantize_transformer_gpu(t)
        try:
            self._save_fp8_cache(t, cache_dir, model_dir, subdir)
        except Exception as e:
            print(f"[meowcute] {tag} fp8 缓存落盘失败（{e}），不影响本次运行", flush=True)
        print(f"[meowcute] {tag} fp8 量化完成（{n} 个 Linear，bf16 加载 {t_load:.0f}s，"
              f"缓存已写 {cache_dir.name}/）", flush=True)
        return t

    @staticmethod
    def _fp8_cache_valid(cache_dir, model_dir, subdir: str = "transformer") -> bool:
        """缓存有效性：三件套齐全 + 源权重文件指纹未变。"""
        if os.environ.get("MEOWCUTE_QWEN_CACHE", "1") == "0":
            return False
        if not ((cache_dir / "model.safetensors").is_file()
                and (cache_dir / "config.json").is_file()
                and (cache_dir / "fp8_cache_meta.json").is_file()):
            return False
        try:
            meta = json.loads((cache_dir / "fp8_cache_meta.json").read_text())
            # 布局变了就必须重建：源权重指纹没变，光看源文件会误判成命中，
            # 然后用旧布局加载直接 KeyError
            if meta.get("format") != _FP8_CACHE_FORMAT:
                return False
            for name, (size, mtime) in meta.get("files", {}).items():
                f = model_dir / subdir / name
                if not f.is_file() or f.stat().st_size != size or int(f.stat().st_mtime) != mtime:
                    return False
            return True
        except Exception:
            return False

    @staticmethod
    def _save_fp8_cache(transformer, cache_dir, model_dir, subdir: str = "transformer") -> None:
        """把量化后的组件存成 diffusers 风格缓存目录。"""
        import shutil

        from safetensors.torch import save_file

        cache_dir.mkdir(parents=True, exist_ok=True)
        sd = {k: (v.data if hasattr(v, "data") else v).detach().contiguous()
              for k, v in transformer.state_dict().items()}
        save_file(sd, str(cache_dir / "model.safetensors"))
        shutil.copy(str(model_dir / subdir / "config.json"),
                    str(cache_dir / "config.json"))
        # 源指纹：权重文件名 + 大小 + mtime（重新下载/换版本即失效，自动重建）
        files = {}
        for f in sorted((model_dir / subdir).glob("*.safetensors")):
            st = f.stat()
            files[f.name] = [st.st_size, int(st.st_mtime)]
        (cache_dir / "fp8_cache_meta.json").write_text(json.dumps(
            {"source": str(model_dir), "format": _FP8_CACHE_FORMAT,
             "files": files},
            ensure_ascii=False, indent=1))

    @staticmethod
    def _load_fp8_cache(cache_dir, cls=None):
        """从缓存快速搭出 fp8 组件：meta 骨架 + mmap assign + 换类。

        用 diffusers 同款低内存加载路径（accelerate init_empty_weights +
        load_state_dict(assign=True)），大模型不分配 bf16 空张量。
        """
        import torch  # noqa: F401
        from accelerate import init_empty_weights
        from safetensors.torch import load_file

        if cls is None:
            from diffusers import QwenImageTransformer2DModel as cls

        fp8_cls = _get_fp8_linear_cls()
        config = cls.load_config(str(cache_dir / "config.json"))
        sd = load_file(str(cache_dir / "model.safetensors"))
        # 缓存里的量化 Linear 权重存两处：w_fp8（fp8 裸张量，真数据）和
        # weight（0 字节 bf16，只为运行时 dtype 探针）。骨架是纯 nn.Linear，
        # 它要满尺寸 weight，两者形状对不上——必须先把 weight 从待加载字典里
        # 拿掉，否则 load_state_dict(assign=True) 会报 size mismatch 直接失败。
        fp8_targets = {k[: -len(".w_fp8")] for k in sd if k.endswith(".w_fp8")}
        fp8_weight_keys = {f"{k}.weight" for k in fp8_targets}
        sd = {k: v for k, v in sd.items() if k not in fp8_weight_keys}
        try:
            with init_empty_weights():
                model = cls.from_config(config)
        except Exception:
            model = cls.from_config(config)
        missing, unexpected = model.load_state_dict(sd, strict=False, assign=True)
        extras = [k for k in unexpected if k.endswith((".w_scale", ".w_fp8"))]
        stray = [k for k in missing if k not in fp8_weight_keys]
        if stray or len(extras) != len(unexpected):
            raise RuntimeError(
                f"缓存键不匹配（missing={len(missing)} stray={len(stray)} "
                f"unexpected={len(unexpected)}），删除 {cache_dir.name}/ 可重建")
        for key in unexpected:
            mod = model.get_submodule(key.rsplit(".", 1)[0])
            mod.register_buffer(key.rsplit(".", 1)[1], sd[key])
        n = 0
        for mod in model.modules():
            if isinstance(mod, torch.nn.Linear) and getattr(mod, "w_fp8", None) is not None:
                mod.__class__ = fp8_cls
                mod.weight = torch.nn.Parameter(
                    torch.empty(0, dtype=torch.bfloat16, device=mod.w_fp8.device),
                    requires_grad=False)
                n += 1
        if n == 0:
            raise RuntimeError("缓存中没有任何 fp8 Linear，格式可能损坏")
        return model

    @staticmethod
    def _quantize_transformer_gpu(transformer) -> int:
        """逐 Linear 在 GPU 上做 fp8 权重量化，原地换类为 _FP8Linear。

        E4M3 + 逐输出通道 scale（scale = amax(|W|, dim=1) / 448）。
        峰值显存仅为单层权重（最大约 75MB），20B 模型全程约 10s。
        """
        import torch

        fp8_cls = _get_fp8_linear_cls()
        n = 0
        n_linear = sum(1 for m in transformer.modules()
                       if type(m) is torch.nn.Linear)
        for mod in transformer.modules():
            if type(mod) is not torch.nn.Linear:
                continue
            w = mod.weight.detach().to("cuda", non_blocking=True)
            scale = w.abs().amax(dim=1, keepdim=True).clamp_(min=1e-12) / 448.0
            wq = (w / scale).clamp_(-448.0, 448.0).to(torch.float8_e4m3fn)
            mod.weight = torch.nn.Parameter(wq.to("cpu"), requires_grad=False)
            mod.register_buffer("w_scale", scale.squeeze(1).to("cpu").float())
            mod.__class__ = fp8_cls
            _wrap_fp8_weight(mod)
            n += 1
        if n != n_linear:
            print(f"[meowcute] 警告：{n_linear} 个 Linear 中仅量化 {n} 个", flush=True)
        return n

    @staticmethod
    def _quantize_fp8(module: Any, tag: str) -> Any:
        """torchao fp8 权重量化：bf16 → fp8（显存减半，权重误差 <4%）。

        失败时静默退回 bf16（打印警告），保证功能可用。
        """
        import time

        import torch  # noqa: F401

        try:
            from torchao.quantization import Float8WeightOnlyConfig, quantize_
        except Exception as e:
            print(f"[meowcute] torchao 不可用（{e}），{tag} 保持 bf16", flush=True)
            return module
        try:
            n_lin = sum(1 for m in module.modules() if isinstance(m, torch.nn.Linear))
            t0 = time.time()
            quantize_(module, Float8WeightOnlyConfig())
            print(f"[meowcute] {tag} fp8 权重量化完成（{n_lin} 个 Linear，"
                  f"{time.time() - t0:.0f}s，显存约减半）", flush=True)
        except Exception as e:
            print(f"[meowcute] {tag} fp8 量化失败（{e}），保持 bf16", flush=True)
        return module

    # ---------- 推理 ----------

    def _run(self, model_id: str, prompt: str, negative: str, width: int, height: int,
             steps: int, seed: Optional[int], n: int,
             ref: Optional[Any] = None, refs: Optional[Any] = None,
             task_id: Optional[str] = None,
             max_seq_len: Optional[int] = None,
             ref_bg: Optional[Any] = None,
             progress: Optional[Dict[str, Any]] = None):
        torch = _require_torch()

        # 先看显存够不够，再决定要不要让用户干等：桌面程序占着显存时，模型会被
        # Windows 挤到共享内存，同一张图从 2s 变 200s，而界面只会显示「生成中」。
        family_hint = _CAPS.get(model_id, {}).get("family") or detect_family(model_id)
        vram_note = _vram_note(family_hint)
        if vram_note:
            print(f"[meowcute] 警告：{vram_note}", flush=True)
            # 进度条上直接说明原因：否则用户只看到「生成中」卡很久，
            # 会以为程序坏了，其实是被 Windows 挤到共享内存
            progress_mod.report(
                task_id, 0, 0,
                "显存不足，推理可能慢 50-100 倍；建议关闭占显卡的程序，"
                "或把尺寸降到 512/768")

        generator = None
        if seed is not None:
            generator = torch.Generator(
                device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))

        self._load(model_id)
        family = _CAPS.get(model_id, {}).get("family") or detect_family(model_id)
        if family == "qwen21":
            images, meta = self._run_qwen21(model_id, prompt, negative, width, height,
                                            steps, seed, generator, n,
                                            refs=_normalize_refs(ref, refs),
                                            task_id=task_id,
                                            max_seq_len=max_seq_len,
                                            ref_bg=ref_bg,
                                            progress=progress)
        else:
            raise RuntimeError(f"暂不支持的模型族：{family}（{model_id}）")
        if vram_note:
            meta["vramWarning"] = vram_note
        return images, meta

    async def generate(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        n: int = 1,
        ref=None,
        refs: Optional[Any] = None,
        task_id: Optional[str] = None,
        max_seq_len: Optional[int] = None,
        negative: Optional[str] = None,
        ref_bg: Optional[Any] = None,
        progress: Optional[Dict[str, Any]] = None,
    ) -> ProviderResult:
        # 入口先卡一次：缺 torch 时直接把安装指引抛给前端，
        # 而不是等 _run 里 import 出一句光秃秃的 "No module named 'torch'"
        _require_torch()
        _touch()
        requested = self._resolve_model()
        family = detect_family(requested)
        all_refs = _normalize_refs(ref, refs)
        model_id = requested

        # 带参考图 = 指令改图：Qwen-Image-2.1 是文生图/改图合一座椅，
        # 留在同一个模型走改图模式，不存在「静默丢掉参考图」的分支。

        if steps is None:
            # Qwen-Image-2.1 是非蒸馏底座，官方推荐 40 步；调用方显式给了
            # 就尊重它（_run_qwen21 会夹在 20-50 之间）
            steps = 40
        # 取消检查点：排队等锁之前 + 拿到锁之后。模型加载（fp8 量化可达
        # 十几分钟）和文本编码阶段无法中断——它们不在 diffusers 的步进
        # 回调里，只能等进入采样后的第一步。
        progress_mod.check_cancel(task_id)
        async with _LOCK:
            progress_mod.check_cancel(task_id)
            # 负面词：调用方显式给了就用它（像素契约/视角接力各有自己的负面
            # 清单），没给就回落到设置里的默认值。历史上这里只读设置，导致
            # 后端算出来的 built["negative"] 从来没到过模型——UI 展示了、
            # 推理没用上，属于静默失效。
            neg = self.cfg.get("negativePrompt", "") if negative is None else negative
            images, meta = await asyncio.to_thread(
                self._run, model_id, prompt, neg,
                width, height, steps, seed, n, ref, all_refs or None,
                task_id, max_seq_len, ref_bg, progress,
            )
        status = _torch_status()
        out_meta = {"provider": "diffusers", "model": model_id, "device": status.get("device"),
                    **meta, "caps": _CAPS.get(model_id)}
        return ProviderResult(images=images, meta=out_meta)

    async def generate_video(
        self,
        prompt: str,
        ref,
        width: int = 0,
        height: int = 0,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        num_frames: int = 81,
        fps: int = 24,
        max_side: int = 832,
        task_id: Optional[str] = None,
        pixel_art: bool = False,
        negative: Optional[str] = None,
        guidance_scale: Optional[float] = None,
    ) -> ProviderResult:
        """图生视频：参考图 + 提示词 → 帧序列（ProviderResult.images = 帧）。

        与 generate() 分开而不是加参数：视频的输入输出语义都不同（一张图进、
        几十帧出、按时长而非张数描述），塞进同一个方法只会让调用方猜。
        """
        _require_torch()
        _touch()
        # 视频底座自己解析：以前读 self._resolve_model()（那是**图片**底座），
        # 全靠 walkvideo 事先把 cfg["model"] 改写成 wan 路径才不出错——别的
        # 调用方一调就必报「当前模型不是视频底座」。这里直接按 videoModel 解。
        model_id = resolve_video_model(self.cfg.get("videoModel"))
        if detect_family(model_id) != "wan":
            raise RuntimeError(
                f"视频底座不是 Wan 模型：{model_id}。"
                "请先下载 Wan2.2 TI2V-5B 并把它配置为视频底座。")
        import torch

        generator = None
        if seed is not None:
            generator = torch.Generator(
                device="cuda" if torch.cuda.is_available() else "cpu").manual_seed(int(seed))
        note = _vram_note("wan")
        if note:
            print(f"[meowcute] {note}", flush=True)
        progress_mod.check_cancel(task_id)
        async with _LOCK:
            progress_mod.check_cancel(task_id)
            images, meta = await asyncio.to_thread(
                self._run_wan, model_id, prompt,
                negative if negative is not None else self.cfg.get("negativePrompt", ""),
                width, height, steps, seed, generator, ref,
                int(num_frames), int(fps), int(max_side), task_id,
                bool(pixel_art), guidance_scale,
            )
        status = _torch_status()
        out_meta = {"provider": "diffusers", "model": model_id,
                    "device": status.get("device"), **meta,
                    "caps": _CAPS.get(model_id)}
        if note:
            out_meta["vramWarning"] = note
        return ProviderResult(images=images, meta=out_meta)

    async def test(self) -> Dict[str, Any]:
        st = _torch_status()
        if not st["installed"]:
            return {"ok": False,
                    "message": "未安装 PyTorch，AI 生成不可用。运行 ./start.sh --gpu"
                               "（Windows：start.bat --gpu）安装推理依赖，约 3GB"}
        import importlib.util

        if importlib.util.find_spec("diffusers") is None:
            return {"ok": False, "message": "未安装 diffusers，请运行 ./start.sh --gpu"}
        model = self._resolve_model()
        family = detect_family(model)
        zh_tag = "（中文直喂）" if family == "qwen21" else "（不支持）"
        message = (f"{st['device']} · {st['vramGB']}GB 显存（当前可用 "
                   f"{st.get('freeGB', 0)}GB） · 模型 {model} · 族 {family}"
                   + zh_tag
                   + ("" if model not in _CACHE else " · 已加载"))
        # 视频底座单独报一句：行走视频节点需要它，没配的话在这里就能看到
        try:
            vmodel = resolve_video_model()
            message += f" · 视频底座 {vmodel}（{'已就绪' if os.path.isdir(vmodel) else '缺失'}）"
        except Exception:
            message += " · 视频底座未配置（行走视频不可用）"
        return {
            "ok": True,
            "message": message,
            "caps": _CAPS.get(model),
        }

    @staticmethod
    def unload() -> None:
        """卸载全部驻留底座并归还显存。"""
        if not _CACHE:
            # 没东西也清一次：PyTorch 的缓存分配器会把跑过的峰值一直攥着，
            # nvidia-smi 看到的「已用」多半是这块缓存，不是真的在用。
            _release_vram()
            return
        _CACHE.clear()
        _CAPS.clear()
        gc.collect()
        _release_vram()

    @staticmethod
    def release(model_id: Optional[str] = None) -> None:
        """只卸一个模型（其余保持驻留）。没给 id 时等价于 unload()。"""
        if model_id is None:
            DiffusersProvider.unload()
            return
        if model_id in _CACHE:
            _CACHE.pop(model_id, None)
            _CAPS.pop(model_id, None)
            gc.collect()
            _release_vram()

    @staticmethod
    def is_loaded() -> bool:
        return bool(_CACHE)

    @staticmethod
    def idle_seconds() -> float:
        """距上次真正用到底座过了多久（用于空闲自动卸载）。"""
        return time.time() - _LAST_USE

    @staticmethod
    def vram_gb() -> Dict[str, float]:
        """当前显存账（GB）：已用 / 缓存占用 / 本次峰值 / 总量。

        报告「reserved」而不是只看 allocated，是因为用户抱怨的「显存很大」
        基本都是 reserved——分配器不主动还，只有 empty_cache 才降得下去。
        """
        try:
            import torch

            if not torch.cuda.is_available():
                return {}
            return {
                "allocated": round(torch.cuda.memory_allocated() / 2**30, 2),
                "reserved": round(torch.cuda.memory_reserved() / 2**30, 2),
                "peak": round(torch.cuda.max_memory_allocated() / 2**30, 2),
                "total": round(
                    torch.cuda.get_device_properties(0).total_memory / 2**30, 2),
            }
        except Exception:
            return {}


def _touch() -> None:
    """每次真正跑到推理都刷一下「最近使用」时间。"""
    global _LAST_USE
    _LAST_USE = time.time()


def _release_vram() -> None:
    """归还 PyTorch 缓存的显存块（gc 之后调用，确保没有引用残留）。"""
    try:
        import torch

        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


def _enable_offload(pipe: Any, tag: str) -> str:
    """开 cpu offload，失败时退回「整条管线常驻」。

    enable_model_cpu_offload 依赖 accelerate：装不上（或版本不匹配）时如果直接
    让加载失败，用户就再也出不了图——退到常驻虽然吃显存，但至少能用，并在
    caps 里如实标注当前策略。
    """
    try:
        pipe.enable_model_cpu_offload()
        return "cpu offload"
    except Exception as e:
        print(f"[meowcute] {tag} 开不了 cpu offload（{e}），退回整条管线常驻显存",
              flush=True)
        try:
            pipe.to("cuda")
        except Exception:
            pass
        return "常驻显存（缺 accelerate）"
