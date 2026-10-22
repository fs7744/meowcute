"""环境体检：每项都带「所以现在该做什么」。

不做成「一堆勾叉」——检查项的文案必须能直接翻译成下一步动作。
15 项里真正会挡住生成的只有四类：torch、GPU、diffusers、模型权重；
其它（磁盘、目录可写）是排障时的线索。
"""
from __future__ import annotations

import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from .config import DATA_DIR, MODELS_DIR, ensure_dirs, scan_models


def _item(key: str, label: str, ok: bool, detail: str = "",
          fix: str = "", level: str = "error") -> Dict[str, Any]:
    return {"key": key, "label": label, "ok": ok, "detail": detail,
            "fix": fix, "level": level if not ok else "ok"}


def run_doctor() -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []

    # ---- Python 侧 ----
    items.append(_item(
        "python", "Python 解释器", True,
        detail=f"{os.sys.version.split()[0]}（{os.sys.executable}）"))

    # ---- torch / GPU ----
    torch_ok = False
    gpu_name, vram, free_vram = "", 0.0, 0.0
    try:
        import torch
        torch_ok = True
        items.append(_item("torch", "PyTorch", True, detail=torch.__version__))
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram = round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1)
            try:
                free, _ = torch.cuda.mem_get_info()
                free_vram = round(free / 1024**3, 1)
            except Exception:
                pass
            items.append(_item(
                "cuda", "CUDA GPU", True,
                detail=f"{gpu_name} · {vram}GB（可用 {free_vram}GB）"))
            items.append(_item(
                "vram", "显存余量", free_vram >= 15.0,
                detail=f"当前可用 {free_vram}GB，Qwen-Image-2.1 fp8 峰值约 14.8GB",
                fix="关闭浏览器硬件加速 / 游戏 / 视频播放 / 其他 AI 工具后重试。"
                    "显存不足不会报错，但推理会慢 50-100 倍（走共享内存）。",
                level="warn"))
        else:
            items.append(_item(
                "cuda", "CUDA GPU", False,
                detail="torch 已安装但 CUDA 不可用",
                fix="确认装的是 CUDA 版 torch（cu124），并更新 NVIDIA 驱动。"))
    except Exception as e:
        items.append(_item(
            "torch", "PyTorch", False, detail=str(e)[:200],
            fix="运行 ./start.sh --gpu（Windows：start.bat --gpu）安装推理依赖（约 3GB）。"
                "不装也能用项目管理 / 提示词维护 / 像素精修，只是不能生成。"))

    # ---- diffusers ----
    if importlib.util.find_spec("diffusers") is not None:
        try:
            import diffusers
            items.append(_item("diffusers", "diffusers", True, detail=diffusers.__version__))
        except Exception as e:
            items.append(_item("diffusers", "diffusers", False, detail=str(e)[:200],
                               fix="pip install -U diffusers（需含 QwenImagePipeline）"))
    else:
        items.append(_item("diffusers", "diffusers", False, detail="未安装",
                           fix="pip install diffusers transformers accelerate safetensors"))

    # ---- 模型权重 ----
    models = scan_models()
    families = {m["family"] for m in models}
    items.append(_item(
        "models", "本地模型权重", "qwen21" in families,
        detail="、".join(f"{m['name']}（{m['gb']}GB）" for m in models) or f"{MODELS_DIR} 下没有识别到模型",
        fix="把 qwen-image-2.1 放到项目 models/ 目录。"))
    # 文生图与改图是同一个模型（Qwen-Image-2.1），所以这一项和上面同生共死；
    # 保留一项是为了在 UI 上把「参考图生成」单独说清楚，别让用户以为还要另配。
    items.append(_item(
        "edit_model", "参考图改图", "qwen21" in families,
        detail="文生图 / 改图同一个模型：Qwen-Image-2.1（带参考图时自动走改图模式）"
        if "qwen21" in families
        else "未找到 qwen-image-2.1：参考图生成不可用（文生图同样不可用）",
        fix="把 qwen-image-2.1 放到 models/ 目录。",
        level="warn"))

    # ---- 数据目录 ----
    try:
        ensure_dirs()
        with tempfile.NamedTemporaryFile(dir=str(DATA_DIR), suffix=".probe", delete=True) as f:
            f.write(b"ok")
        items.append(_item("data_dir", "数据目录可写", True, detail=str(DATA_DIR)))
    except Exception as e:
        items.append(_item("data_dir", "数据目录可写", False, detail=str(e)[:200],
                           fix=f"检查 {DATA_DIR} 的写权限，或用环境变量 MEOWCUTE_DATA_DIR 换目录。"))

    # ---- 磁盘 ----
    try:
        usage = os.statvfs(str(DATA_DIR)) if hasattr(os, "statvfs") else None
        if usage:
            free_gb = usage.f_bavail * usage.f_frsize / 1024**3
            items.append(_item("disk", "磁盘余量", free_gb > 5,
                               detail=f"{free_gb:.1f}GB 可用",
                               fix="清理磁盘（模型与产出都占空间）", level="warn"))
        else:
            import shutil
            total, used, free = shutil.disk_usage(str(Path(DATA_DIR)))
            free_gb = free / 1024**3
            items.append(_item("disk", "磁盘余量", free_gb > 5,
                               detail=f"{free_gb:.1f}GB 可用",
                               fix="清理磁盘（模型与产出都占空间）", level="warn"))
    except Exception:
        pass

    errors = [i for i in items if not i["ok"] and i["level"] == "error"]
    warns = [i for i in items if not i["ok"] and i["level"] == "warn"]
    can_generate = torch_ok and "qwen21" in families and importlib.util.find_spec("diffusers") is not None
    return {
        "ok": len(errors) == 0,
        "canGenerate": bool(can_generate),
        "summary": ("环境正常，可以生成" if can_generate and not errors
                    else ("可生成但有告警" if can_generate else "当前不能生成，见下方修复指引")),
        "items": items,
        "warnings": len(warns),
        "errors": len(errors),
        "gpu": gpu_name, "vramGB": vram, "freeVRAMGB": free_vram,
        "models": models,
    }
