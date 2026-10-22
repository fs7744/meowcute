"""运行时配置：持久化到 data/settings.json（本地优先，不上云）。

与归档 PixelForge 的差异：
- 数据目录环境变量改为 MEOWCUTE_DATA_DIR（测试隔离用）；
- 模型路径默认自动探测 models/ 目录，不再要求用户手填绝对路径；
- 新增 pixel 段：像素精修默认参数（网格对齐 / 调色板 / 显示放大倍率）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[2]
# 数据目录可被环境变量覆盖：测试跑在临时目录里，绝不碰用户项目数据。
DATA_DIR = Path(os.environ.get("MEOWCUTE_DATA_DIR") or (ROOT / "data"))
PROJECTS_DIR = DATA_DIR / "projects"
SETTINGS_PATH = DATA_DIR / "settings.json"
PROMPT_EXAMPLES_PATH = DATA_DIR / "prompt_examples.json"
# 模型权重目录（与数据目录分离：权重不动，数据可再删）
MODELS_DIR = ROOT / "models"

# 已知底座目录名 → 族。用于设置页自动探测与下拉选择。
# 2026-10-22 起主力底座为 Qwen-Image-2.1（文生图/改图/RGBA 三合一），
# Z-Image 与 Qwen-Image-Edit-2511 退为可选旧档（权重已移归档目录）。
KNOWN_MODELS: Dict[str, str] = {
    "qwen-image-2.1": "qwen21",
    "wan2.2-ti2v-5b": "wan",
    "z-image-turbo": "zimage",
    "qwen-image-edit-2511": "qwen_edit",
    "qwen-image-edit-2511-lightning": "qwen_edit_lora",
    "qwen-image-layered": "qwen_layered",
}

DEFAULT_EDIT_MODEL = "models/qwen-image-2.1"
DEFAULT_VIDEO_MODEL = "models/wan2.2-ti2v-5b"

DEFAULT_SETTINGS: Dict[str, Any] = {
    # 本地底座：Qwen-Image-2.1（文生图 + 指令编辑/参考图，2026-10-22 迁移）
    # + Wan2.2 TI2V-5B（图生视频，行走视频节点用）
    "activeProvider": "diffusers",
    "providers": {
        "diffusers": {
            "label": "本地底座（Qwen-Image-2.1 / Wan 视频）",
            "baseUrl": "",
            "apiKey": "",
            "model": "models/qwen-image-2.1",
            "editModel": DEFAULT_EDIT_MODEL,
            "videoModel": DEFAULT_VIDEO_MODEL,
            # 负面词带像素约束：AI 出图最常见的三类瑕疵，对像素风是致命伤
            "negativePrompt": "模糊, 低质量, 水印, 文字, 渐变, 抗锯齿, 3D渲染, 照片, 噪点",
            "enabled": True,
        },
    },
    "defaults": {"steps": 9, "width": 512, "height": 512, "n": 1},
    # 像素精修默认参数：选像素类画风时自动建议（block/colors 由画风给出）
    "pixel": {
        "enabled": True,
        "gridSnap": True,
        "colors": 16,
        "palette": "auto",
        "dither": False,
        "displayScale": 8,
    },
    # 默认不烧 AI 角标：角标一旦画进像素，后续处理会把它带进素材
    "aiLabel": False,
}


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)


def resolve_path(p: str) -> str:
    """把设置里的相对路径按项目根目录解析。

    绝对路径 / 已存在的路径原样返回，否则按 ROOT 拼一次；
    拼出来存在就用，不存在也返回原值（让调用方报它自己的错）。
    """
    p = (p or "").strip()
    if not p or os.path.isabs(p) or Path(p).exists():
        return p
    cand = ROOT / p
    return str(cand) if cand.exists() else p


def scan_models() -> List[Dict[str, Any]]:
    """扫描 models/ 目录，返回 [{path, family, name, gb}]。

    供设置页展示与默认值自动探测；目录不存在时返回空列表（不报错，
    没下模型也能启动服务，只是不能生成）。
    """
    out: List[Dict[str, Any]] = []
    if not MODELS_DIR.is_dir():
        return out
    for entry in sorted(MODELS_DIR.iterdir()):
        if not entry.is_dir():
            continue
        family = KNOWN_MODELS.get(entry.name)
        if family is None:
            # 目录名不在表里时读 model_index.json 的 _class_name 兜底
            idx = entry / "model_index.json"
            if idx.is_file():
                try:
                    cls = str(json.loads(idx.read_text(encoding="utf-8")).get("_class_name") or "")
                    if "ZImage" in cls:
                        family = "zimage"
                    elif "QwenImage21" in cls:
                        family = "qwen21"
                    elif "QwenImageEdit" in cls:
                        family = "qwen_edit"
                    elif "QwenImageLayered" in cls:
                        family = "qwen_layered"
                    elif "Wan" in cls:
                        family = "wan"
                except Exception:
                    family = None
        if family is None:
            continue
        try:
            gb = round(sum(f.stat().st_size for f in entry.rglob("*") if f.is_file()) / 1024**3, 1)
        except Exception:
            gb = 0.0
        out.append({
            "path": f"models/{entry.name}",
            "family": family,
            "name": entry.name,
            "gb": gb,
        })
    return out


def find_model(family: str) -> Optional[str]:
    """按族在 models/ 下找第一个匹配的底座路径（相对形式）。"""
    for m in scan_models():
        if m["family"] == family:
            return m["path"]
    return None


def load_settings() -> Dict[str, Any]:
    ensure_dirs()
    data: Dict[str, Any] = {}
    if SETTINGS_PATH.exists():
        try:
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    merged = {**DEFAULT_SETTINGS, **data}
    merged["providers"] = {**DEFAULT_SETTINGS["providers"], **data.get("providers", {})}
    merged["defaults"] = {**DEFAULT_SETTINGS["defaults"], **data.get("defaults", {})}
    merged["pixel"] = {**DEFAULT_SETTINGS["pixel"], **data.get("pixel", {})}
    return merged


def save_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    ensure_dirs()
    current = load_settings()
    merged = {**current, **payload}
    merged["providers"] = {**current["providers"], **payload.get("providers", {})}
    merged["defaults"] = {**current["defaults"], **payload.get("defaults", {})}
    merged["pixel"] = {**current["pixel"], **payload.get("pixel", {})}
    # allow_nan=False：NaN 写坏不如写炸
    SETTINGS_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return merged
