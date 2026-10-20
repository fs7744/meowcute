"""内置 diffusers 推理服务：本地真模型（无需第三方 UI）。

从归档 PixelForge 的 diffusers_local.py 移植（同批实测参数），保留：
- 懒加载：不装 torch/diffusers 也能启动服务，只在首次生成时报错；
- 单例缓存 + 家族互斥显存换出（单卡 24GB 现实）；
- Qwen-Image-Edit transformer fp8 权重量化 + 落盘缓存（39GB → 约 20GB）；
- 步进回调：SSE 进度 + 取消。

本版只支持两个族（第一个工作流模板用不到视频/分层，按需再接）：
- zimage：Z-Image-Turbo（S3-DiT 单流 + Qwen3-4B 中文编码器）
      中文提示词直喂，9 步蒸馏 · guidance_scale 固定 0.0 · 1024² 原生分辨率
- qwen_edit：Qwen-Image-Edit-2511（20B MMDiT + Qwen2.5-VL 编码器）
      指令式编辑（必须传参考图），中文指令直喂，最多 4 张参考图
      40 步 · true_cfg_scale=4.0 · guidance_scale=1.0 · FP8/bf16
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
from typing import Any, Dict, List, Optional

from .base import BaseProvider, ProviderResult
from ..config import resolve_path
from .. import progress as progress_mod

_CACHE: Dict[str, Any] = {}
_CAPS: Dict[str, Dict[str, Any]] = {}
_FAMILY: Dict[str, str] = {}
_LOCK = asyncio.Lock()

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
    """模型族识别：zimage / qwen_edit / qwen_layered / wan / unknown。

    先读 model_index.json 的 _class_name（最可靠），再看目录名兜底；
    认不出来返回 "unknown"（由调用方报错，不静默按别的族加载）。
    """
    if model_id in _FAMILY:
        return _FAMILY[model_id]
    family = "unknown"
    if os.path.isdir(model_id):
        idx = _read_json(os.path.join(model_id, "model_index.json"))
        cls = str(idx.get("_class_name") or "")
        if "ZImage" in cls:
            family = "zimage"
        elif "QwenImageLayered" in cls:
            family = "qwen_layered"
        elif "QwenImageEdit" in cls:
            family = "qwen_edit"
        elif "Wan" in cls:
            family = "wan"
    if family == "unknown":
        name = os.path.basename(model_id.rstrip("/\\")).lower()
        if "z-image" in name or "zimage" in name:
            family = "zimage"
        elif "qwen-image-layered" in name or "qwen_image_layered" in name:
            family = "qwen_layered"
        elif "qwen-image-edit" in name or "qwen_image_edit" in name:
            family = "qwen_edit"
        elif "wan" in name:
            family = "wan"
    _FAMILY[model_id] = family
    return family


# 单卡 24GB 的现实：Z-Image bf16 约 21GB、Qwen-Image-Edit fp8 前向峰值 20.3GB，
# 任两个模型族在同进程共存都可能 OOM 或被 WDDM 挤到共享内存，必须互斥驻留。
_BIG_FAMILIES = {"zimage", "qwen_edit", "qwen_layered", "wan"}


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

    上限 4 张：Qwen-Image-Edit-2511 官方支持多图输入，但再多既拉长编码时间，
    也很难让模型分清主次。顺序有意义——第一张决定输出宽高。
    """
    out: List[Any] = []
    if ref is not None:
        out.append(ref)
    if refs:
        out.extend([r for r in refs if r is not None])
    return out[:MAX_REFS]


def resolve_edit_model(explicit: Optional[str] = None) -> str:
    """解析 Qwen-Image-Edit 底座路径，解析不到就抛带指引的错误。

    顺序：显式参数 → 设置里的 editModel → 设置里任何 qwen_edit 族 provider →
    models/qwen-image-edit-2511。
    """
    if explicit:
        hit = resolve_path(explicit)
        if os.path.isdir(hit):
            return hit
    try:
        from ..config import load_settings

        s = load_settings()
        direct = resolve_path(str((s.get("providers", {}).get("diffusers", {}) or {}).get("editModel") or ""))
        if direct and os.path.isdir(direct) and detect_family(direct) == "qwen_edit":
            return direct
        for cfg in (s.get("providers") or {}).values():
            if not isinstance(cfg, dict):
                continue
            mid = resolve_path(str(cfg.get("model") or ""))
            if mid and os.path.isdir(mid) and detect_family(mid) == "qwen_edit":
                return mid
    except Exception:
        pass
    default = resolve_path("models/qwen-image-edit-2511")
    if os.path.isdir(default):
        return default
    raise RuntimeError(
        "未找到 Qwen-Image-Edit 底座。请将 models/qwen-image-edit-2511 放到项目 "
        "models/ 目录下，或在设置里配置编辑底座路径。")


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


# 两个大族在 24GB 卡上的实测/估算占用（bf16 / fp8+offload）：
# Z-Image 19.3GB、Qwen-Edit fp8 前向峰值 20.3GB。留 1GB 余量给 CUDA context。
_NEED_GB = {"zimage": 20.5, "qwen_edit": 21.0}


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
    label = "本地底座（Z-Image / Qwen-Edit）"

    def _resolve_model(self) -> str:
        model = (self.cfg.get("model") or "").strip()
        if model:
            # 相对路径按项目根解析：从别的目录启动服务也能找到 models/ 下的底座
            return resolve_path(model)
        return resolve_path(os.environ.get("MEOWCUTE_MODEL", "models/z-image-turbo"))

    def _load(self, model_id: str):
        if model_id in _CACHE:
            return _CACHE[model_id]
        family = detect_family(model_id)
        if family not in ("zimage", "qwen_edit"):
            raise RuntimeError(
                f"暂不支持的模型：{model_id}。当前版本只支持 Z-Image-Turbo（文生图）"
                "与 Qwen-Image-Edit-2511（参考图编辑）。")
        # 单卡 24GB 只容一个大家族：切换前先把另一大族卸了，否则共存必 OOM
        _evict_other_families(family)
        if family == "zimage":
            return self._load_zimage(model_id)
        return self._load_qwen_edit(model_id)

    def _load_zimage(self, model_id: str):
        """Z-Image-Turbo：bf16 单流 S3-DiT + Qwen3-4B 中文编码器，8 步蒸馏。"""
        try:
            import torch
        except Exception as e:  # pragma: no cover
            raise RuntimeError(_TORCH_HINT) from e
        try:
            from diffusers import ZImagePipeline
        except Exception as e:
            raise RuntimeError(
                "Z-Image 需要较新版本的 diffusers（含 ZImagePipeline）。请执行：\n"
                "  ./start.sh --gpu      （Windows：start.bat --gpu）\n"
                "或  pip install -U diffusers\n"
                f"原始错误：{e}"
            ) from e
        # cuDNN 卷积算法启发式在显存紧张时会选中非 Tensor Core 慢速路径
        # （实测 4090 上 VAE 解码从 5s 劣化到 4 分钟以上），开 benchmark
        # 按实际 shape 自动调优，确定性走最快算法
        torch.backends.cudnn.benchmark = True
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Z-Image 是 bf16 单流 DiT，需要 CUDA GPU（如 RTX 4090）；CPU 推理不可行。"
            )
        # low_cpu_mem_usage 保持默认 True：实测 False 会让推理慢 10 倍以上
        pipe = ZImagePipeline.from_pretrained(model_id, torch_dtype=torch.bfloat16)
        pipe.to("cuda")
        pipe.set_progress_bar_config(disable=True)
        _CAPS[model_id] = {
            "family": "zimage",
            "notes": ["Z-Image：中文提示词直喂，9 步蒸馏，guidance 固定 0"],
        }
        _CACHE[model_id] = pipe
        return pipe

    def _load_qwen_edit(self, model_id: str):
        """Qwen-Image-Edit-2511：20B MMDiT + Qwen2.5-VL 编码器，指令式编辑。

        显存策略（实测踩坑）：bf16 全量约 54GB（transformer 单独 39GB），
        远超 24GB。bf16 + enable_model_cpu_offload 不可行——offload 钩子在前向时
        要把整个 transformer 搬上 GPU，39GB 必爆 OOM。解决：transformer 做 fp8
        权重量化（39GB → 约 20GB），再开 cpu offload 让 text_encoder/VAE 按阶段
        换入换出。4090 Ada 架构上 fp8 开销可忽略。量化结果落盘缓存
        （transformer-fp8/），冷启动从 ~100s 降到 ~35s。
        """
        try:
            import torch
        except Exception as e:  # pragma: no cover
            raise RuntimeError(_TORCH_HINT) from e
        try:
            from diffusers import QwenImageEditPlusPipeline
        except Exception as e:
            raise RuntimeError(
                "Qwen-Image-Edit 需要较新版本的 diffusers（含 QwenImageEditPlusPipeline）。"
                "请执行：\n"
                "  ./start.sh --gpu      （Windows：start.bat --gpu）\n"
                "或  pip install -U diffusers\n"
                f"原始错误：{e}"
            ) from e
        torch.backends.cudnn.benchmark = True
        if not torch.cuda.is_available():
            raise RuntimeError(
                "Qwen-Image-Edit 是 20B DiT，需要 CUDA GPU（如 RTX 4090）；CPU 推理不可行。"
            )
        use_fp8 = os.environ.get("MEOWCUTE_QWEN_FP8", "1") != "0"
        transformer = None
        if use_fp8:
            try:
                transformer = self._load_qwen_transformer_fp8(model_id)
            except Exception as e:
                print(f"[meowcute] fp8 transformer 快速路径失败（{e}），退回完整加载", flush=True)
                transformer = None
        if transformer is not None:
            # 注入已加载的 transformer：from_pretrained 对已实例化组件跳过磁盘读取
            pipe = QwenImageEditPlusPipeline.from_pretrained(
                model_id, torch_dtype=torch.bfloat16, transformer=transformer)
        else:
            pipe = QwenImageEditPlusPipeline.from_pretrained(
                model_id, torch_dtype=torch.bfloat16)
            if use_fp8:
                self._quantize_fp8(pipe.transformer, "qwen_edit.transformer")
        pipe.enable_model_cpu_offload()
        pipe.set_progress_bar_config(disable=True)
        _CAPS[model_id] = {
            "family": "qwen_edit",
            "notes": ["Qwen-Image-Edit：中文指令直喂；必须传参考图；fp8 + cpu offload"],
        }
        _CACHE[model_id] = pipe
        return pipe

    # ---------- Qwen-Image-Edit fp8 transformer：量化 + 落盘缓存 ----------

    def _load_qwen_transformer_fp8(self, model_id: str):
        """加载 fp8 版 transformer（约 20GB，进 24GB 卡的关键）。

        两条路径：缓存命中（<model>/transformer-fp8/）safetensors mmap 零拷贝
        assign，约 20s；未命中则 bf16 完整加载 → GPU 逐层 fp8 量化（约 10s）
        → 落盘缓存（约 20s）。任何失败都抛异常，由 _load_qwen_edit 退回完整加载。
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

    def _run_zimage(self, model_id: str, prompt: str, width: int, height: int,
                    steps: Optional[int], seed: Optional[int], generator: Any, n: int,
                    task_id: Optional[str] = None):
        """Z-Image 文生图：8 次 DiT 前向、CFG 固定 0、1024² 原生分辨率。

        注意：调度器把 step 0 当热身，num_inference_steps=9 才对应 8 次前向；
        Turbo 蒸馏已内化 CFG，guidance_scale 给正值反而掉画质。
        """
        import torch  # noqa: F401

        pipe = self._load(model_id)
        use_steps = 9 if not steps else max(4, min(int(steps), 12))
        w = int(width) // 16 * 16
        h = int(height) // 16 * 16
        images = []
        for i in range(max(1, min(n, 4))):
            out = pipe(
                prompt=prompt,
                num_inference_steps=use_steps,
                guidance_scale=0.0,
                height=h,
                width=w,
                generator=generator,
                # 默认 512 token：画风词 + 主体 + 像素契约的中文提示词很容易顶满，
                # 超出的尾部（恰恰是像素契约）会被静默截断。768 足够覆盖实测
                # 最长的提示词（约 400 字），又比 1024 少一截文本编码开销。
                max_sequence_length=768,
                # 步进回调：SSE 进度 + 取消检查（diffusers 每步结束调一次）
                callback_on_step_end=progress_mod.make_step_callback(
                    task_id, use_steps, label=f"第 {i + 1} 张"),
            )
            images.append(out.images[0].convert("RGBA"))
            if generator is not None and seed is not None:
                generator = torch.Generator(device=generator.device).manual_seed(int(seed) + i + 1)
        return images, {"steps": use_steps, "cfg": 0.0, "family": "zimage",
                        "mode": "txt2img", "turbo": True}

    def _run_qwen_edit(self, model_id: str, prompt: str, negative: str,
                       width: int, height: int, steps: Optional[int],
                       seed: Optional[int], generator: Any, n: int,
                       refs: Optional[Any] = None, task_id: Optional[str] = None):
        """Qwen-Image-Edit 指令式编辑：参考图（可多张）+ 中文指令 → 编辑图。

        - 必须传参考图（没有输入时直接报错，而不是静默出图）；
        - 多图：Plus 管线原生支持多张输入，所有参考图统一缩放到目标尺寸；
        - 输出尺寸：显式 width/height 生效（16 对齐）；缺省时跟随第一张参考图。
          注意管线自己的默认是「按最后一张参考图的长宽比算 1024² 面积」，
          所以必须显式传参，否则用户设的宽高会被忽略；
        - true_cfg_scale=4.0 是官方推荐；guidance_scale=1.0 关掉二次 CFG。
        """
        import torch  # noqa: F401

        refs = [r for r in (refs or []) if r is not None]
        if not refs:
            raise ValueError(
                "Qwen-Image-Edit 是指令式编辑器，必须传参考图。"
                "请在上传区添加至少一张参考图。")

        pipe = self._load(model_id)
        use_steps = 40 if not steps else max(20, min(int(steps), 50))
        from PIL import Image as _Image

        srcs = [r.convert("RGB") for r in refs]
        w = int(width or srcs[0].width) // 16 * 16
        h = int(height or srcs[0].height) // 16 * 16
        srcs = [s.resize((w, h), _Image.LANCZOS) for s in srcs]
        # 单图传 Image，多图传 list（Plus 管线两者都吃）
        image_arg = srcs[0] if len(srcs) == 1 else srcs
        images = []
        for i in range(max(1, min(n, 4))):
            out = pipe(
                image=image_arg,
                prompt=prompt,
                negative_prompt=negative or " ",
                height=h,
                width=w,
                num_inference_steps=use_steps,
                true_cfg_scale=4.0,
                guidance_scale=1.0,
                generator=generator,
                # 步进回调：40 步里每一步都报，长任务的进度条就靠它；
                # 取消时抛 CancelledError 中止推理
                callback_on_step_end=progress_mod.make_step_callback(
                    task_id, use_steps, label=f"第 {i + 1} 张"),
            )
            images.append(out.images[0].convert("RGBA"))
            if generator is not None and seed is not None:
                generator = torch.Generator(device=generator.device).manual_seed(int(seed) + i + 1)
        return images, {"steps": use_steps, "cfg": 1.0, "trueCfg": 4.0,
                        "family": "qwen_edit",
                        "mode": "edit" if len(srcs) == 1 else "ref2img",
                        "refCount": len(srcs), "size": [w, h]}

    def _run(self, model_id: str, prompt: str, negative: str, width: int, height: int,
             steps: int, seed: Optional[int], n: int,
             ref: Optional[Any] = None, refs: Optional[Any] = None,
             task_id: Optional[str] = None):
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
        if family == "zimage":
            if ref is not None or refs:
                raise RuntimeError(
                    "Z-Image 是纯文生图底座，不支持参考图。请改用编辑底座（有参考图时自动路由）。"
                )
            images, meta = self._run_zimage(model_id, prompt, width, height,
                                            steps, seed, generator, n, task_id=task_id)
        else:
            images, meta = self._run_qwen_edit(model_id, prompt, negative, width, height,
                                               steps, seed, generator, n,
                                               refs=_normalize_refs(ref, refs),
                                               task_id=task_id)
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
    ) -> ProviderResult:
        # 入口先卡一次：缺 torch 时直接把安装指引抛给前端，
        # 而不是等 _run 里 import 出一句光秃秃的 "No module named 'torch'"
        _require_torch()
        requested = self._resolve_model()
        family = detect_family(requested)
        all_refs = _normalize_refs(ref, refs)
        model_id = requested
        auto_routed = False

        # 带参考图 = 图生图/参考图生成；Z-Image 没有这条管线，自动改走编辑底座，
        # 而不是把参考图悄悄丢掉（历史上就是这样静默忽略的）。
        if all_refs and family == "zimage":
            try:
                model_id = resolve_edit_model()
                family = "qwen_edit"
                auto_routed = True
            except Exception as e:
                raise RuntimeError(
                    "Z-Image 是纯文生图底座，不支持参考图。要按参考图生成，"
                    "请先配置编辑底座 models/qwen-image-edit-2511。") from e

        if steps is None:
            # 蒸馏模型只需极少步数：Z-Image 9 步（8 次前向）、
            # Qwen-Image-Edit 40 步（非蒸馏）；给 28 步纯属浪费
            steps = 9 if family == "zimage" else 40
        # 取消检查点：排队等锁之前 + 拿到锁之后。模型加载（fp8 量化可达
        # 十几分钟）和文本编码阶段无法中断——它们不在 diffusers 的步进
        # 回调里，只能等进入采样后的第一步。
        progress_mod.check_cancel(task_id)
        async with _LOCK:
            progress_mod.check_cancel(task_id)
            images, meta = await asyncio.to_thread(
                self._run, model_id, prompt, self.cfg.get("negativePrompt", ""),
                width, height, steps, seed, n, ref, all_refs or None,
                task_id,
            )
        status = _torch_status()
        out_meta = {"provider": "diffusers", "model": model_id, "device": status.get("device"),
                    **meta, "caps": _CAPS.get(model_id)}
        if auto_routed:
            out_meta["autoRouted"] = True
            out_meta["requestedModel"] = requested
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
        zh_tag = "（中文直喂）" if family in ("zimage", "qwen_edit") else "（不支持）"
        return {
            "ok": True,
            "message": f"{st['device']} · {st['vramGB']}GB 显存（当前可用 "
                       f"{st.get('freeGB', 0)}GB） · 模型 {model} · 族 {family}"
                       + zh_tag
                       + ("" if model not in _CACHE else " · 已加载"),
            "caps": _CAPS.get(model),
        }

    @staticmethod
    def unload() -> None:
        _CACHE.clear()
        _CAPS.clear()
        gc.collect()
        try:
            import torch

            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass
