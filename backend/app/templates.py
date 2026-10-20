"""工作流模板定义：新建项目必须先选一个模板。

模板是「一条可执行的生产链」的说明书 + 默认参数集合，不是页面代码。
前端拿模板渲染介绍页与新建项目弹窗；画布按模板初始化节点。

当前可用：pixel_sprite_txt2img（像素精灵 · 文字生图）。
后续模板（逐帧动作、三视图、场景元素）先在介绍页亮出规划，不假装备可用——
「摆一个点了没反应的入口」比没有更伤信任。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "pixel_sprite_txt2img",
        "name": "像素精灵 · 文字生图",
        "tagline": "一句话 → 一张可达标的像素游戏素材",
        "status": "available",
        "accent": "#00FF88",
        "icon": "sprite",
        "description": (
            "用本地 Z-Image 底座把文字描述变成游戏素材，再用确定性管线把 AI 的"
            "「像素感」收成真像素：自动检测网格、每格取主色、限定调色板、"
            "alpha 二值化。支持多张参考图（自动路由到编辑底座）与 15 种游戏画风。"
        ),
        "highlights": [
            "提示词五段式：画风前置 + 主体描述 + 像素契约 + 负面清单 + 参数",
            "像素精修：FFT 网格检测 + Sobel 吸附 + 共享调色板，输出原生像素尺寸",
            "参考图 1-4 张：自动切换 Qwen-Image-Edit 指令编辑，保持角色一致",
            "结果卡可放大预览，原图 / 精修一键对比",
        ],
        "inputs": [
            {"key": "prompt", "label": "文字描述", "required": True,
             "hint": "主体、服装、道具、动作；画风用下面的选择器，不要写进正文"},
            {"key": "examples", "label": "优秀示例", "required": False,
             "hint": "角色 / 场景 / 道具 / 动作 / 画风补充，点击即插入"},
            {"key": "refs", "label": "参考图（0-4 张）", "required": False,
             "hint": "选了参考图自动走编辑底座，保持角色外观一致"},
            {"key": "format", "label": "图片格式", "required": False,
             "hint": "输出宽高、生成张数、随机种子"},
            {"key": "style", "label": "游戏画风", "required": False,
             "hint": "15 种画风，像素类会自动建议精修参数"},
        ],
        "steps": [
            {"n": 1, "title": "组装提示词", "detail": "画风词前置 → 主体 → 像素契约；纯字符串拼接，可预览"},
            {"n": 2, "title": "本地推理", "detail": "无参考图走 Z-Image（9 步）；有参考图自动走 Qwen-Image-Edit（40 步）"},
            {"n": 3, "title": "像素精修", "detail": "FFT 检测网格 → 网格线吸附 → 每格多数表决 → 共享色板量化 → alpha 二值化"},
            {"n": 4, "title": "落盘成卡", "detail": "结果以卡片落到无限画布，保留原图与精修两版，可放大对比"},
        ],
        "outputs": [
            {"label": "原生像素 PNG", "detail": "如 64×64，可无损用于引擎"},
            {"label": "AI 原图", "detail": "精修前版本，用于对比与再精修"},
            {"label": "质量指标", "detail": "网格尺寸、色数、色板外颜色数、alpha 二值率"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                      "palette": "auto", "gridSize": None, "targetSize": None},
        },
        "notes": [
            "像素风的关键不是「模型画得像」，而是「后处理收得回来」——"
            "本模板默认开启像素精修，关掉它会得到一张有像素质感的插画。",
            "参考图会走编辑底座：指令以「保持角色外观不变」为主，",
            "适合「已有角色，换姿势 / 换道具 / 出不同素材」的场景。",
        ],
    },
    {
        "id": "frame_action_sheet",
        "name": "逐帧动作精灵表",
        "tagline": "角色锚点图 → N 帧动作 → 精灵表",
        "status": "planned",
        "accent": "#FF3366",
        "icon": "frames",
        "description": (
            "以一张角色锚点图为身份母版，逐帧生成动作（行走 / 攻击 / 跳跃），"
            "再抠图规整、拼成精灵表并导出引擎包。逐帧生成而非一次出多帧——"
            "模型没有「第 N 格画什么」的定位能力。"
        ),
        "highlights": [
            "身份由同一张参考图钉住，帧间不漂移",
            "共享缩放 + 底部对齐，播放不抖动",
            "循环接缝诊断（首尾帧差异 / 卡住帧）",
        ],
        "inputs": [], "steps": [], "outputs": [],
        "defaults": {},
        "notes": ["规划中：依赖动画帧一致性验证与精灵表合成管线。"],
    },
    {
        "id": "char_three_view",
        "name": "角色三视图",
        "tagline": "正 / 侧 / 背三视图自动拼板",
        "status": "planned",
        "accent": "#FFD500",
        "icon": "views",
        "description": (
            "正视图唯一需要文生图；侧 / 背由编辑底座从正视推导，保证同一角色。"
            "分步可单独重跑，重跑上游自动作废下游。"
        ),
        "highlights": ["三视图坐标系一致", "单步重跑", "拼板导出"],
        "inputs": [], "steps": [], "outputs": [],
        "defaults": {},
        "notes": ["规划中：依赖编辑底座的多视角推导与拼板管线。"],
    },
    {
        "id": "scene_layers",
        "name": "场景语义分层",
        "tagline": "一张场景图 → 多层视差素材",
        "status": "planned",
        "accent": "#5B57EB",
        "icon": "layers",
        "description": (
            "把同一张场景图拆成背景 / 中景 / 前景图层（而非各生成一遍），"
            "天然坐标对齐，可直接做视差滚动。"
        ),
        "highlights": ["同图拆层无接缝", "视差工程导出"],
        "inputs": [], "steps": [], "outputs": [],
        "defaults": {},
        "notes": ["规划中：依赖 Qwen-Image-Layered 底座。"],
    },
]

_BY_ID: Dict[str, Dict[str, Any]] = {t["id"]: t for t in TEMPLATES}


def list_templates() -> List[Dict[str, Any]]:
    return TEMPLATES


def get_template(tid: str) -> Optional[Dict[str, Any]]:
    return _BY_ID.get(tid)


def available_templates() -> List[Dict[str, Any]]:
    return [t for t in TEMPLATES if t["status"] == "available"]
