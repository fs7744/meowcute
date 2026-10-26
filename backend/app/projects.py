"""项目：落盘到 data/projects/<pid>/project.json，图片同目录。

数据模型（前端画布直接读写这一份）：
    {
      id, name, templateId, createdAt, updatedAt,
      viewport: {x, y, z},
      nodes: [
        {id, type: "generate", x, y, prompt, styleId, refs[], params{}, pixel{}},
        {id, type: "input",    x, y, label, refs[]},
        {id, type: "refine",   x, y, pixel{}, result},
        {id, type: "matte",    x, y, matte{}, result},
        {id, type: "result",   x, y, genId, images[], prompt, negative, meta{}}
      ],
      edges: [{from, to}]
    }

为什么节点坐标存后端：项目是「无限画布」的持久化载体，前端刷新/换设备
打开必须回到原位。只存前端 localStorage 等于换浏览器就丢布局。

并发：本地单用户，用进程内 per-project 锁 + 原子写（临时文件 + replace）。
"""
from __future__ import annotations

import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import PROJECTS_DIR, ensure_dirs
# hy15 分窗阈值默认值（41）：单点定义在 providers/hy15.py，这里只取别名，
# 免得「存盘默认值」与「推理默认值」两处漂移。
from .providers.hy15 import DEFAULT_WINDOW as DEFAULT_HY15_WINDOW
from .jsonlimits import parse_bounded, dumps_bounded
from .storage import project_dir, project_size_mb, new_asset_id, write_readme

_LOCKS: Dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(pid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        if pid not in _LOCKS:
            _LOCKS[pid] = threading.Lock()
        return _LOCKS[pid]


ALLOWED_TYPES = ("generate", "result", "input", "refine", "matte", "viewgen",
                 "parts", "rigwalk", "txtvideo",
                 "threeview", "walkvideo", "sequence", "spritesheet", "play",
                 "clipsrc", "framesplit", "spritegrid", "animpreview",
                 "spritesplit", "poseclone",
                 "godot2d", "inpaint", "erase", "layer25d")

# 扣背景节点的默认参数。enabled 默认 True：模板里放进去就是为了「生成结果先扣底」，
# 默认关掉等于把开关藏起来。
DEFAULT_MATTE = {
    "enabled": True,
    "method": "auto",
    "tolerance": 80,
    "hard": True,
    "feather": 0,
    "despill": True,
    "trim": False,
    "edge": 0,
    "bottomTrim": 0.0,
}

# 视角生成节点（四视图的侧/背/右视图）：enabled=False 时透传上游正视图。
# view 固定 side | back——同一个组件、同一个接口，靠这个字段区分。
DEFAULT_VIEWGEN = {
    "enabled": True,
    "view": "side",
    "styleId": None,
    "steps": None,
    "seed": None,
    "width": 512,
    "height": 768,
    "maxSeqLen": None,
    # "" = 用程序拼的默认视角指令；非空 = 用户自己改过的正文，原样送给模型
    "instruction": "",
    # "" = 用程序常量 VIEW_NEGATIVE；非空 = 用户自定义负面词
    "negative": "",
    # 多张候选里当前预览/下游默认使用的那张（数组下标）
    "pick": 0,
}

# 四视图拼板节点：正/侧/背/右四路输入合成一张横向板（右视图可选）。
DEFAULT_THREEVIEW = {
    "enabled": True,
    "bg": "1B4332",
    # 透明底板：True 时忽略 bg，拼板输出 RGBA（各视图 alpha 原样带出）
    "transparentBg": False,
    "gap": 12,
    "baseline": 0.93,
    "cell": None,
    # 每个视角手动挑的那张（{view: assetId}）：多张候选时用户逐格选图，
    # 空 = 按 pairIndex 自动取第一组
    "picks": {},
}

# 行走视频节点（八方向第一步）：正视图 → 各方向立绘 + Wan 行走视频。
# dirMode=eight 时左侧 5 个方向真实生成、右侧 3 个方向镜像，见 walkvideo 模块。
# sources={方向: 项目内视频文件}：该方向用现成视频，跳过推导 + Wan。
# 行走视频的默认整段提示词。必须与 prompt_examples 的「原地行走（通用起点）」
# 一致：smoke 里有断言看守，改一边不改另一边会红。
DEFAULT_WALK_PROMPT = (
    "角色在原地行走：双腿以自然步幅持续交替迈步，膝盖随步伐屈伸，"
    "双脚依次离地、前摆、落地；身体随重心轻微上下颠动；手臂随步伐"
    "自然摆动，双手持枪或扶握武器时双臂与武器保持稳定；头发和衣摆随"
    "步伐轻微晃动。动作循环往复，一只脚落地时另一只脚抬起，步态平稳"
    "连贯。保持取景框与背景完全静止，角色始终位于画面中心、体型大小"
    "不变，不向画面任何方向平移、不旋转身体、镜头不动。"
)

DEFAULT_WALKVIDEO = {
    "enabled": True,
    "dirMode": "eight",                     # eight | four | single
    "action": "行走",
    "sources": {},
    "styleId": None,
    "steps": None,
    "seed": None,
    "width": 384,
    "height": 512,
    # 帧数/帧率：49 帧 @16fps ≈ 3.1s 循环，和原来的 81@24（3.4s）时长接近，
    # 但视频成本按帧数线性掉 40%。实测 81/30 单方向 75.7s，49/28 约 56s。
    # 整段提示词由用户完整输入（2026-10-23 起不再由程序拼模板）。
    # 默认值 = 示例库「原地行走」那条的全文，用户可随意改；示例库「行走视频」
    # 分类里还有举枪/奔跑/待机/潜行等完整写法可一键填入。
    "prompt": DEFAULT_WALK_PROMPT,
    # 负面词：空 = 用 walkvideo.DEFAULT_WALK_NEGATIVE。以前这段写死在管线里，
    # 卡片上看不到也改不了，和正面提示词的处理不一致。
    "negative": "",
    "numFrames": 21,                        # 默认引擎 hy15 的帧数（4k+1，上限 257；再长仅变慢、不报错）
    "fps": 12,
    "maxSide": 512,
    "maxSeqLen": None,
    # 行走动作控制（轻量闭环）：guidanceScale=None 用默认 5.0；
    # motionThreshold 是动作幅度合格线；retries 是不达标时自动加压重跑次数。
    "guidanceScale": None,
    # 49 帧实测动作分约 6.5（阈值 6.0 太贴边，容易误触发重试），留 1.0 余量。
    # 重试一次就是再跑 56s，默认最多重试 1 次（最坏 2 次，而不是 3 次）。
    "motionThreshold": 5.5,
    "retries": 1,
    # 视频引擎：hy15（HunyuanVideo-1.5，默认）/ wan（Wan2.2）。切 wan 时帧数上限
    # 回到 193、步数/CFG 语义也按 Wan 走（见 walkvideo.generate_walk）。
    "engine": "hy15",
}

# walkvideo.sources 的合法方向键（顺时针一圈）
_WALK_DIRS = ("down", "down-left", "left", "up-left", "up",
              "up-right", "right", "down-right")

# 拆件节点：角色立绘 → 6 个骨骼部件 + 武器（混合路线，见 pipelines/parts.py）。
# 这条链路替代的是「视频扩散出行走帧」：纯 CPU、确定性、天然带 alpha。
DEFAULT_PARTS = {
    "enabled": True,
    # see-through 1280 约 3 分钟；768 更快但部件细节和手臂完整度会掉
    "resolution": 1280,
    # 半写实角色的头部 see-through 会崩（back hair 变成全身鬼影），默认用爆炸图补
    "withHead": True,
    "seed": 7,
}

# 文本生视频节点：提示词 + 可选参考图 → 视频（Wan2.2 TI2V-5B，
# 纯文本与图生视频是同一份权重的两条管线类）
DEFAULT_TXTVIDEO = {
    "enabled": True,
    "prompt": "",
    "negative": "",
    "steps": None,
    "seed": None,
    # 32 的倍数：Wan/TI2V 的 patchify 要求（16 VAE × 2 patch）。848×480 不是
    # 32 的倍数，会被 diffusers 静默改成 832×480——预设必须一开始就给对。
    "width": 832,
    "height": 480,
    "numFrames": 21,
    "fps": 12,
    "maxSide": 848,
    "maxSeqLen": None,
    "pixelArt": False,
    "guidanceScale": None,
    # 画质档位与分辨率预设是 UI 便利层：前端把它们解析成具体的
    # steps/guidance/width/height 再下发（映射知识留在看得见的地方），
    # 存盘只为回显。
    "quality": "standard",
    "resolution": "480p",
    "interpolate": False,
    "upscale": False,
    # 输出格式：mp4（默认，H.264 浏览器原生播）/ gif（<img> 播，精灵管线语境）
    "format": "mp4",
    # None=自动：无参考图时后端强制 wan（纯文本只有 Wan 支持）
    "engine": None,
    # 画布跟随参考图宽高比（None=跟随）：方图立绘不该被塞进 16:9 画布
    "fitAspect": None,
}

DEFAULT_RIGWALK = {
    "enabled": True,    "frames": 8,
    # 12° 干净、18° 动作大但腿根外翻；13 是实测折中
    "swing": 13,
    "arm": 12,
    "bob": 5,
    "lift": 7,
    # 双手持枪/握把的角色必须开：两只手分属不同骨骼，各转各的会脱手
    "hold": False,
    "fps": 8,
}

# 序列帧节点（八方向第二步）：每方向视频 → 抽帧 / 去重 / 抠图 → 透明底帧。
DEFAULT_SEQUENCE = {
    "enabled": True,
    "framesPerDir": 8,
    "fps": 8,                               # 抽出来的循环按几帧/秒播放
    "matte": True,
    "tolerance": 40,
}

# 精灵整图节点（八方向第三步）：每方向透明帧 → 共享缩放规整 → 横排精灵表。
DEFAULT_SPRITESHEET = {
    "enabled": True,
    "size": 128,
    "cols": 0,                              # 每行帧数，0 = 一行排完
    "sharedScale": True,
    "anchor": "bottom",
}

# 可操控预览节点（八方向第四步）：八方向精灵表 → 方向键操控的行走播放器。
# 纯前端渲染，不调后端——它的产物就是播放器本身。
DEFAULT_PLAY = {
    "enabled": True,
    "fps": 8,
    "zoom": 2,
}

# 视频转素材链的四个节点。默认值与 templates.clip_to_sprite 里的内置图一致：
# 模板是「出厂示例」，这里是「用户在画布上新建节点时」的默认值，两处不同步
# 会表现为「用模板建的项目」和「手动加的节点」参数不一样。
DEFAULT_CLIPSRC = {
    "file": "",
    "kind": "video",
    "durationSec": 0,
    "fps": 0,
    "width": 0,
    "height": 0,
}

DEFAULT_FRAMESPLIT = {
    "enabled": True,
    "fpsCap": 12,
    "maxFrames": 120,
    "start": 0,
    "end": None,
    "matte": True,
    "tolerance": 40,
    "maxSide": 384,
    "step": 1,
    "simPct": 0,
    "picks": [],
}

# 精灵图拆解：cols/rows/cell 全 0 = 自动识别网格。不去底是默认——
# 我们自己产出的精灵图已经是透明底，逐帧抠图白等几百毫秒/帧。
DEFAULT_SPRITESPLIT = {
    "enabled": True,
    "cols": 0,
    "rows": 0,
    "cell": 0,
    "count": 0,
    "matte": False,
    "tolerance": 40,
    "picks": [],
}

# 动作复刻：instruction 留空 = 用程序里的默认指令（界面上会把它显示出来供编辑）。
# 这条规矩来自踩过的坑：提示词写死在程序里、界面上看不到，用户改不动也查不到。
DEFAULT_POSECLONE = {
    "enabled": True,
    "mode": "per-frame",
    "instruction": "",
    "negative": "",
    "steps": 0,
    "size": 0,
    "background": "transparent",
    "anchor": "bottom",
    "refMode": "auto",
    "refView": "auto",
    "seed": None,
    "maxSeqLen": None,
}

DEFAULT_SPRITEGRID = {
    "enabled": True,
    "cell": 128,
    "cols": 0,
    "padding": 0,
    "margin": 0,
    "anchor": "bottom",
    "sharedScale": True,
    "fps": 12,
}

DEFAULT_ANIMPREVIEW = {
    "enabled": True,
    "fps": 12,
    "zoom": 4,
    "loop": True,
    "bg": "checker",
}

# Godot 导出节点（素材链第 5 步）：精灵图 → SpriteFrames(.tres) + 场景(.tscn) + zip。
# 参数全部是「交付形态」的选择，没有一个是给模型看的。
DEFAULT_GODOT2D = {
    "enabled": True,
    # Godot 里的动画名（SpriteFrames 的 animation 名，可中文）
    "animName": "default",
    # 文件名主干（ASCII）：tres / tscn / png 都用它
    "name": "walk",
    "fps": 12,
    "loop": True,
    # atlas=一张图集 + AtlasTexture 切格（文件少）/ files=逐帧 PNG（切片麻烦但直观）
    "mode": "atlas",
    # res:// 下的相对目录：导出物必须整体落在这里，.tres 里的路径才成立
    "folder": "sprites",
    # 节点原点对齐：脚底（像素角色默认）/ 居中 / 顶部
    "origin": "bottom",
    # 像素风必须最近邻，否则 GPU 一采样就糊
    "textureFilter": "nearest",
}


# 局部重绘节点：上游图 + 涂抹蒙版 + 提示词 → 只重画选区。
# 参数全是「怎么重画 / 怎么贴回」的选择；提示词正文与约束条款都可覆盖，
# 程序只提供默认值（约束条款默认值见 pipelines/inpaint.DEFAULT_CONSTRAINT）。
DEFAULT_INPAINT = {
    "enabled": True,
    "instruction": "",
    # "" = 用程序默认的约束条款（「只重画标记区域」那段），非空整段替换
    "constraint": "",
    # "" = 用程序默认负面词（接缝 / 亮边 / 拼接感…）
    "negative": "",
    # 可选参考图（0-2 张，来自项目 refs/）：只是「参考造型/材质」，不参与构图
    "refs": [],
    # crop = 只送「选区 + 上下文」（细节更好）/ full = 整图重画（改全局光照）
    "mode": "crop",
    # 选区向外带的上下文比例：0.35 表示再各方向外扩 35% 的选区边长
    "context": 0.35,
    # 羽化（只向内，保证选区外像素一个都不变）
    "feather": 4,
    # 蒙版外扩：给模型留出「往哪长」的余地
    "expand": 8,
    # 硬边：像素素材用，不做羽化也不做色彩校正
    "hard": False,
    # 接缝带色彩校正（只修边界，不动区域中心）
    "matchColor": True,
    "matchStrength": 0.45,
    # none | source：把重绘区里「接近源色」的杂色吸回源图色板
    "palette": "none",
    "paletteColors": 48,
    "steps": None,
    "seed": None,
    "maxSeqLen": None,
    # 涂抹蒙版（masks/<mask-...>.png）+ 它的元信息（覆盖率 / 包围盒）
    "maskFile": "",
    "mask": None,
}


# 去除局部节点：涂掉水印/杂物，用周围内容补上。三条非 AI 路线（毫秒级）
# 加一条 AI 路线（几十秒），默认走扩散修复——这是水印/字幕最常见的场景。
DEFAULT_ERASE = {
    "enabled": True,
    # telea=扩散修复 / offset=纹理平移 / mirror=镜像 / ai=模型补全
    "method": "telea",
    # 有效蒙版的外扩：水印边缘的抗锯齿比笔画宽 1~3px，不外扩会留一圈残影
    "expand": 6,
    "feather": 2,
    "hard": False,
    "matchColor": True,
    "matchStrength": 0.6,
    "palette": "none",
    "paletteColors": 48,
    # 以下四项只对 method=ai 有意义；其余方式用不到提示词
    "instruction": "",
    "constraint": "",
    "negative": "",
    "aiMode": "crop",
    "context": 0.35,
    "steps": None,
    "seed": None,
    "maxSeqLen": None,
    "maskFile": "",
    "mask": None,
}


# 2.5D 分层模型（See-Through）：静态立绘 → 可操作的分层 PSD。
# 一次要 2~3 分钟，所以默认分辨率取 1024（官方 1280 更准但慢一倍）。
DEFAULT_LAYER25D = {
    "enabled": True,
    "resolution": 1024,
    "steps": 30,
    "seed": 42,
    "lrsplit": True,
}


def _now() -> float:
    return round(time.time(), 3)

def _project_json(pid: str) -> Path:
    return project_dir(pid) / "project.json"


def default_generate_node(x: float = 80, y: float = 120) -> Dict[str, Any]:
    """新建项目时的第一张生成卡（画布永远不为空）。"""
    return {
        "id": new_asset_id("gen"),
        "type": "generate",
        "x": x,
        "y": y,
        "prompt": "",
        "styleId": "pixel_16bit",
        "refs": [],
        "params": {"width": 512, "height": 512, "n": 1, "steps": None, "seed": None,
                   "background": "gray", "negative": ""},
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                  "palette": "auto", "gridSize": None, "targetSize": None},
        "collapsed": False,
        "createdAt": _now(),
    }


def create_project(name: str, template_id: str, template_defaults: Dict[str, Any],
                   template_graph: Optional[Dict[str, Any]] = None,
                   draft_of: Optional[str] = None,
                   keep_ids: bool = False) -> Dict[str, Any]:
    ensure_dirs()
    pid = new_asset_id("p")
    d = project_dir(pid)
    (d / "images").mkdir(parents=True, exist_ok=True)
    (d / "refs").mkdir(parents=True, exist_ok=True)

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, str]] = []
    base_gen = default_generate_node()

    # 自定义模板：按模板里的节点图初始化画布。运行时的图片/参考图/结果
    # 跨项目没有意义，一律清空，只保留结构、参数与连线。
    if isinstance(template_graph, dict) and isinstance(template_graph.get("nodes"), list):
        idmap: Dict[str, str] = {}
        for raw in template_graph["nodes"][:200]:
            if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_TYPES:
                continue
            # 模板编辑草稿保留模板里的节点 id：commit 时把图写回去，
            # id 不变才能让「只改了提示词」在图结构上零 diff（否则每次保存
            # 都会因为 id 全变而落一份看起来「改了结构」的覆盖）。
            _raw_id = str(raw.get("id") or "")
            if keep_ids and _raw_id and _raw_id not in idmap.values():
                nid = _raw_id
            else:
                nid = new_asset_id(raw["type"][:3])
            idmap[_raw_id] = nid
            n = dict(raw)
            n["id"] = nid
            if n["type"] == "generate":
                n["refs"] = []
                n["prompt"] = str(n.get("prompt") or "")
                # 模板里显式写 "styleId": null 表示「这节点不要画风，提示词自己带」，
                # 不能被默认值填回去；只有模板压根没写这个字段才用项目默认画风。
                if "styleId" not in n:
                    n["styleId"] = base_gen["styleId"]
                n["params"] = {**base_gen["params"],
                               **(n.get("params") if isinstance(n.get("params"), dict) else {})}
                n["pixel"] = {**base_gen["pixel"],
                              **(n.get("pixel") if isinstance(n.get("pixel"), dict) else {})}
                n.pop("lastImages", None)
            elif n["type"] == "input":
                n["refs"] = []
                n["label"] = str(n.get("label") or "")[:80]
            elif n["type"] == "refine":
                n["result"] = None
                n["pixel"] = {**base_gen["pixel"],
                              **(n.get("pixel") if isinstance(n.get("pixel"), dict) else {})}
            elif n["type"] == "matte":
                n["result"] = None
                n["matte"] = {**DEFAULT_MATTE,
                              **(n.get("matte") if isinstance(n.get("matte"), dict) else {})}
            elif n["type"] == "viewgen":
                n["result"] = None
                n.pop("images", None)
                n["viewgen"] = {**DEFAULT_VIEWGEN,
                                **(n.get("viewgen") if isinstance(n.get("viewgen"), dict) else {})}
                n["viewgen"]["pick"] = 0
            elif n["type"] == "threeview":
                n["result"] = None
                n["threeview"] = {**DEFAULT_THREEVIEW,
                                  **(n.get("threeview") if isinstance(n.get("threeview"), dict) else {})}
                # 模板里没有产出图，选图记录一并清空（否则指向不存在的 assetId）
                n["threeview"]["picks"] = {}
            elif n["type"] == "parts":
                n["parts"] = {**DEFAULT_PARTS,
                              **(n.get("parts") if isinstance(n.get("parts"), dict) else {})}
            elif n["type"] == "rigwalk":
                n["rigwalk"] = {**DEFAULT_RIGWALK,
                                **(n.get("rigwalk") if isinstance(n.get("rigwalk"), dict) else {})}
            elif n["type"] == "txtvideo":
                n["result"] = None
                n["txtvideo"] = {**DEFAULT_TXTVIDEO,
                                 **(n.get("txtvideo") if isinstance(n.get("txtvideo"), dict) else {})}
            elif n["type"] == "walkvideo":
                n["result"] = None
                n["walkvideo"] = {**DEFAULT_WALKVIDEO,
                                  **(n.get("walkvideo") if isinstance(n.get("walkvideo"), dict) else {})}
            elif n["type"] == "sequence":
                n["result"] = None
                n["sequence"] = {**DEFAULT_SEQUENCE,
                                 **(n.get("sequence") if isinstance(n.get("sequence"), dict) else {})}
            elif n["type"] == "spritesheet":
                n["result"] = None
                n["spritesheet"] = {**DEFAULT_SPRITESHEET,
                                    **(n.get("spritesheet") if isinstance(n.get("spritesheet"), dict) else {})}
            elif n["type"] == "play":
                n["result"] = None
                n["play"] = {**DEFAULT_PLAY,
                             **(n.get("play") if isinstance(n.get("play"), dict) else {})}
            elif n["type"] == "clipsrc":
                # 素材入口：跨项目的文件没有意义，只留参数与默认值
                n["clipsrc"] = {**DEFAULT_CLIPSRC,
                                **(n.get("clipsrc") if isinstance(n.get("clipsrc"), dict) else {})}
                n["clipsrc"]["file"] = ""
            elif n["type"] == "framesplit":
                n["result"] = None
                n["frames"] = []
                n["source"] = None
                n["framesplit"] = {**DEFAULT_FRAMESPLIT,
                                   **(n.get("framesplit") if isinstance(n.get("framesplit"), dict) else {})}
                n["framesplit"]["picks"] = []
            elif n["type"] == "spritesplit":
                n["result"] = None
                n["frames"] = []
                n["source"] = None
                n["spritesplit"] = {**DEFAULT_SPRITESPLIT,
                                    **(n.get("spritesplit") if isinstance(n.get("spritesplit"), dict) else {})}
                n["spritesplit"]["picks"] = []
            elif n["type"] == "poseclone":
                n["result"] = None
                n["frames"] = []
                n["poseclone"] = {**DEFAULT_POSECLONE,
                                  **(n.get("poseclone") if isinstance(n.get("poseclone"), dict) else {})}
            elif n["type"] == "spritegrid":
                n["result"] = None
                n["images"] = []
                n["spritegrid"] = {**DEFAULT_SPRITEGRID,
                                   **(n.get("spritegrid") if isinstance(n.get("spritegrid"), dict) else {})}
            elif n["type"] == "animpreview":
                n["meta"] = None
                n["animpreview"] = {**DEFAULT_ANIMPREVIEW,
                                    **(n.get("animpreview") if isinstance(n.get("animpreview"), dict) else {})}
            elif n["type"] == "godot2d":
                n["meta"] = None
                n["godot2d"] = {**DEFAULT_GODOT2D,
                                **(n.get("godot2d") if isinstance(n.get("godot2d"), dict) else {})}
            elif n["type"] == "layer25d":
                n["result"] = None
                n["images"] = []
                n["meta"] = None
                n["layer25d"] = {**DEFAULT_LAYER25D,
                                 **(n.get("layer25d") if isinstance(n.get("layer25d"), dict) else {})}
            elif n["type"] == "erase":
                n["result"] = None
                n["images"] = []
                n["meta"] = None
                eopts = {**DEFAULT_ERASE,
                         **(n.get("erase") if isinstance(n.get("erase"), dict) else {})}
                eopts["maskFile"] = ""
                eopts["mask"] = None
                n["erase"] = eopts
            elif n["type"] == "inpaint":
                # 蒙版与产出图跨项目无效：结构留下，运行时数据清空
                n["result"] = None
                n["images"] = []
                n["meta"] = None
                opts = {**DEFAULT_INPAINT,
                        **(n.get("inpaint") if isinstance(n.get("inpaint"), dict) else {})}
                opts["maskFile"] = ""
                opts["mask"] = None
                opts["refs"] = []
                n["inpaint"] = opts
            else:
                n.update({"images": [], "prompt": "", "negative": "", "meta": {}, "genId": None})
            n["createdAt"] = _now()
            nodes.append(n)
        for e in template_graph.get("edges") or []:
            if not isinstance(e, dict):
                continue
            a = idmap.get(str(e.get("from") or ""))
            b = idmap.get(str(e.get("to") or ""))
            if a and b and a != b:
                edges.append({"from": a, "to": b})

    if not nodes:
        node = default_generate_node()
        # 模板默认值落到第一张生成卡上（画风 / 参数 / 像素选项）
        node["styleId"] = (template_defaults.get("styleId") or node["styleId"])
        for k in ("width", "height", "n"):
            if k in template_defaults:
                node["params"][k] = template_defaults[k]
        px = template_defaults.get("pixel")
        if isinstance(px, dict):
            node["pixel"] = {**node["pixel"], **px}
        nodes = [node]

    project = {
        "id": pid,
        "name": (name or "未命名项目").strip()[:60],
        "templateId": template_id,
        # 非空 = 这个项目是「模板编辑草稿」：画布上多一条「保存为模板 / 放弃」，
        # 项目列表里也标出来，避免被当成普通项目误删。
        "templateDraftOf": draft_of,
        "createdAt": _now(),
        "updatedAt": _now(),
        "viewport": {"x": 0, "y": 0, "z": 1},
        "nodes": nodes,
        "edges": edges,
    }
    with _lock(pid):
        _write(pid, project)
    # 文件夹自带说明书：用户直接翻目录时能看懂哪是输入、哪是产出
    write_readme(pid, project["name"], template_id, project["createdAt"])
    return project


def _write(pid: str, project: Dict[str, Any]) -> None:
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "project.json.tmp"
    tmp.write_text(dumps_bounded(project), encoding="utf-8")
    tmp.replace(_project_json(pid))


def load_project(pid: str) -> Optional[Dict[str, Any]]:
    p = _project_json(pid)
    if not p.is_file():
        return None
    try:
        project = parse_bounded(p.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(project, dict) or not isinstance(project.get("nodes"), list):
        return None
    return project


def save_project(pid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """保存画布状态：只接受白名单字段，防止前端把任意垃圾写进 project.json。"""
    cur = load_project(pid)
    if cur is None:
        raise FileNotFoundError("项目不存在")
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("nodes 必须是数组")
    if len(nodes) > 2000:
        raise ValueError("节点数超过上限 2000")
    clean_nodes: List[Dict[str, Any]] = []
    for n in nodes:
        if not isinstance(n, dict) or n.get("type") not in ALLOWED_TYPES:
            continue
        item: Dict[str, Any] = {
            "id": str(n.get("id") or new_asset_id("n")),
            "type": n["type"],
            "x": _num(n.get("x"), 0),
            "y": _num(n.get("y"), 0),
        }
        if n["type"] == "generate":
            item.update({
                "prompt": str(n.get("prompt") or "")[:6000],
                "styleId": str(n.get("styleId") or "") or None,
                "refs": [r for r in (n.get("refs") or []) if isinstance(r, dict)][:8],
                "params": _clean_params(n.get("params")),
                "pixel": _clean_pixel(n.get("pixel")),
                "lastImages": [i for i in (n.get("lastImages") or []) if isinstance(i, dict)][:12],
                "collapsed": bool(n.get("collapsed")),
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "input":
            item.update({
                "label": str(n.get("label") or "")[:80],
                "refs": [r for r in (n.get("refs") or []) if isinstance(r, dict)][:8],
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "refine":
            item.update({
                "pixel": _clean_pixel(n.get("pixel")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "matte":
            item.update({
                "matte": _clean_matte(n.get("matte")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "viewgen":
            item.update({
                "viewgen": _clean_viewgen(n.get("viewgen")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                # 多张候选（每张都和正视图的一组对应）：不落盘的话刷新页面
                # 就只剩一张，拼板里的选图全丢
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "threeview":
            item.update({
                "threeview": _clean_threeview(n.get("threeview")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "walkvideo":
            item.update({
                "walkvideo": _clean_walkvideo(n.get("walkvideo")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "parts":
            item.update({
                "parts": _clean_parts(n.get("parts")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "rigwalk":
            item.update({
                "rigwalk": _clean_rigwalk(n.get("rigwalk")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "txtvideo":
            item.update({
                "txtvideo": _clean_txtvideo(n.get("txtvideo")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "sequence":
            item.update({
                "sequence": _clean_sequence(n.get("sequence")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "spritesheet":
            item.update({
                "spritesheet": _clean_spritesheet(n.get("spritesheet")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "play":
            item.update({
                "play": _clean_play(n.get("play")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "clipsrc":
            item.update({
                "clipsrc": _clean_clipsrc(n.get("clipsrc")),
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "framesplit":
            item.update({
                "framesplit": _clean_framesplit(n.get("framesplit")),
                "frames": [c for c in (_clean_split_frame(x)
                                       for x in (n.get("frames") or [])[:600]) if c],
                "source": (n.get("source") if isinstance(n.get("source"), dict) else None),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "spritesplit":
            item.update({
                "spritesplit": _clean_spritesplit(n.get("spritesplit")),
                "frames": [c for c in (_clean_split_frame(x)
                                       for x in (n.get("frames") or [])[:256]) if c],
                "source": (n.get("source") if isinstance(n.get("source"), dict) else None),
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "poseclone":
            item.update({
                "poseclone": _clean_poseclone(n.get("poseclone")),
                "frames": [c for c in (_clean_split_frame(x)
                                       for x in (n.get("frames") or [])[:256]) if c],
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "spritegrid":
            item.update({
                "spritegrid": _clean_spritegrid(n.get("spritegrid")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:16],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                # 索引 JSON 的地址：白名单里漏了它 → 刷新页面后「下载索引 JSON」
                # 按钮凭空消失（文件其实还在项目目录里），用户只能重跑一次拼板。
                "indexUrl": str(n.get("indexUrl") or "")[:400] or None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "animpreview":
            item.update({
                "animpreview": _clean_animpreview(n.get("animpreview")),
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "godot2d":
            item.update({
                "godot2d": _clean_godot2d(n.get("godot2d")),
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "layer25d":
            item.update({
                "layer25d": _clean_layer25d(n.get("layer25d")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:4],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "erase":
            item.update({
                "erase": _clean_erase(n.get("erase")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:8],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        elif n["type"] == "inpaint":
            item.update({
                "inpaint": _clean_inpaint(n.get("inpaint")),
                "result": n.get("result") if isinstance(n.get("result"), dict) else None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:8],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else None,
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        else:
            item.update({
                "genId": str(n.get("genId") or "") or None,
                "images": [i for i in (n.get("images") or []) if isinstance(i, dict)][:12],
                "prompt": str(n.get("prompt") or "")[:12000],
                "negative": str(n.get("negative") or "")[:2000],
                "meta": n.get("meta") if isinstance(n.get("meta"), dict) else {},
                "createdAt": _num(n.get("createdAt"), _now()),
            })
        clean_nodes.append(item)

    edges = []
    for e in payload.get("edges") or []:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            edges.append({"from": str(e["from"]), "to": str(e["to"])})

    vp = payload.get("viewport") if isinstance(payload.get("viewport"), dict) else {}
    project = {
        **cur,
        "name": (str(payload.get("name") or cur.get("name") or "未命名项目").strip()[:60]),
        "nodes": clean_nodes,
        "edges": edges[:4000],
        "viewport": {
            "x": _num(vp.get("x"), 0),
            "y": _num(vp.get("y"), 0),
            "z": min(3.0, max(0.1, _num(vp.get("z"), 1))),
        },
        "updatedAt": _now(),
    }
    with _lock(pid):
        _write(pid, project)
    return project


def _num(v: Any, default: float) -> float:
    try:
        f = float(v)
        if f != f or abs(f) > 1e7:   # NaN / 离谱坐标
            return default
        return round(f, 2)
    except Exception:
        return default


def _clean_params(p: Any) -> Dict[str, Any]:
    p = p if isinstance(p, dict) else {}
    def _i(v, d, lo, hi):
        try:
            return max(lo, min(int(v), hi))
        except Exception:
            return d
    return {
        "width": _i(p.get("width"), 512, 128, 1536),
        "height": _i(p.get("height"), 512, 128, 1536),
        "n": _i(p.get("n"), 1, 1, 4),
        "steps": (_i(p.get("steps"), 0, 4, 50) or None) if p.get("steps") else None,
        "seed": (_i(p.get("seed"), 0, 0, 2**31 - 1)) if p.get("seed") not in (None, "") else None,
        # 背景：**显式空串 = 不指定**（提示词里已经写了背景，不要再追加一句）。
        # 只有字段缺失（从没设置过 / 老项目）才回落中灰底。
        # 以前这里是 `str(... or "gray")`，空串会被静默改回 gray —— 于是
        # 「提示词自带背景」的人怎么都关不掉程序追加的那句纯色底约束。
        "background": ("gray" if p.get("background") is None
                       else str(p.get("background"))[:16]),
        # 提示词质量档位（文本序列长度）。白名单漏了它 → 用户在生成卡上选了
        # 「高质量」，保存刷新后静默回到「自动」，且没有任何提示。
        "maxSeqLen": (_i(p.get("maxSeqLen"), 0, 256, 1536) or None) if p.get("maxSeqLen") else None,
        # 负面词覆盖（空 = 全局默认 + 像素契约）。同 maxSeqLen，不进白名单就
        # 是「改了存不下、刷新静默回默认」。
        "negative": str(p.get("negative") or "")[:600],
    }


def _clean_matte(m: Any) -> Dict[str, Any]:
    """扣背景参数：白名单 + 范围夹紧，方法名只认已知的四个。"""
    m = m if isinstance(m, dict) else {}

    def _i(v, d, lo, hi):
        try:
            return max(lo, min(int(v), hi))
        except Exception:
            return d

    method = str(m.get("method") or "auto")[:16]
    if method not in ("auto", "u2net", "grabcut", "key"):
        method = "auto"
    try:
        bottom_trim = float(m.get("bottomTrim") or 0)
    except Exception:
        bottom_trim = 0.0
    return {
        "enabled": bool(m.get("enabled", True)),
        "method": method,
        "tolerance": _i(m.get("tolerance"), 80, 10, 200),
        "hard": bool(m.get("hard", True)),
        "feather": _i(m.get("feather"), 0, 0, 24),
        "despill": bool(m.get("despill", True)),
        "trim": bool(m.get("trim")),
        "edge": _i(m.get("edge"), 0, -3, 3),
        "bottomTrim": round(max(0.0, min(bottom_trim, 0.3)), 3),
    }


def _clean_viewgen(v: Any) -> Dict[str, Any]:
    """视角生成参数：view 只认 side/back，尺寸走 8 的倍数约束。"""
    v = v if isinstance(v, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    view = str(v.get("view") or "side")[:8]
    # side_r 必须放行：之前只认 side/back，右视图节点存盘后再读回来会被
    # 改成侧视图（画布上凭空多一个「侧视图」节点，右视图永远拼不上板）
    if view not in ("side", "back", "side_r"):
        view = "side"
    style_id = str(v.get("styleId") or "")[:40] or None
    return {
        "enabled": bool(v.get("enabled", True)),
        "view": view,
        "styleId": style_id,
        "steps": (_i(v.get("steps"), 0, 4, 60) or None) if v.get("steps") else None,
        "seed": (_i(v.get("seed"), 0, 0, 2**31 - 1)) if v.get("seed") not in (None, "") else None,
        "width": _i(v.get("width"), 512, 128, 1536),
        "height": _i(v.get("height"), 768, 128, 1536),
        "maxSeqLen": (_i(v.get("maxSeqLen"), 0, 256, 1536) or None) if v.get("maxSeqLen") else None,
        "pick": _i(v.get("pick"), 0, 0, 8),
        # 自定义视角指令正文：白名单里漏掉这一项，用户改完存盘再读回来就没了
        # （历史坑：字段没进规整函数 = 「编辑了不生效」）。字符上限与
        # 提示词预算同一口径（1920 token ≈ 1400 字），超出也没用，编码器读不到。
        "instruction": str(v.get("instruction") or "")[:4000],
        # 自定义负面词：同上，不进白名单就存不下来
        "negative": str(v.get("negative") or "")[:400],
    }


def _clean_threeview(t: Any) -> Dict[str, Any]:
    """四视图拼板参数：底板色只认 6 位十六进制，基线夹在 0.5-0.99。"""
    t = t if isinstance(t, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    bg = str(t.get("bg") or "1B4332").lstrip("#")[:6]
    if len(bg) != 6 or any(c not in "0123456789abcdefABCDEF" for c in bg):
        bg = "1B4332"
    try:
        baseline = float(t.get("baseline") or 0.93)
    except Exception:
        baseline = 0.93
    cell = t.get("cell")
    if not (isinstance(cell, (list, tuple)) and len(cell) == 2):
        cell = None
    else:
        cell = [_i(cell[0], 0, 64, 2048), _i(cell[1], 0, 64, 2048)]
    return {
        "enabled": bool(t.get("enabled", True)),
        "bg": bg,
        "transparentBg": bool(t.get("transparentBg", False)),
        "gap": _i(t.get("gap"), 12, 0, 64),
        "baseline": round(min(0.99, max(0.5, baseline)), 3),
        "cell": cell,
        "picks": {k: str(v)[:40] for k, v in (t.get("picks") or {}).items()
                  if k in ("front", "side", "back", "side_r") and v},
    }


def _clean_walkvideo(w: Any) -> Dict[str, Any]:
    """行走视频参数：方向模式白名单，帧数夹在 Wan 的 4k+1 可行区间。"""
    w = w if isinstance(w, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    mode = str(w.get("dirMode") or "eight")[:12]
    if mode not in ("eight", "four", "single"):
        mode = "eight"

    # 现成视频来源 / 方向立绘：{方向: 文件名}，白名单方向 + 文件名形状（防路径穿越）
    srcs: Dict[str, str] = {}
    raw_srcs = w.get("sources") if isinstance(w.get("sources"), dict) else {}
    for d, f in (raw_srcs or {}).items():
        d = str(d)[:12]
        f = str(f or "")[:120]
        if d in _WALK_DIRS and f and "/" not in f and "\\" not in f and ".." not in f:
            srcs[d] = f
    dir_imgs: Dict[str, str] = {}
    raw_imgs = w.get("dirImages") if isinstance(w.get("dirImages"), dict) else {}
    for d, f in (raw_imgs or {}).items():
        d = str(d)[:12]
        f = str(f or "")[:120]
        if d in _WALK_DIRS and f and "/" not in f and "\\" not in f and ".." not in f:
            dir_imgs[d] = f
    # 整段提示词（用户完整输入）。老项目只有 action：用动作词拼一句完整的
    # 起始提示词做一次性迁移，之后用户直接在节点上改全文——程序不再拼接。
    prompt = str(w.get("prompt") or "").strip()
    if not prompt:
        act = str(w.get("action") or "").strip()[:20]
        if act and act != "行走":
            prompt = (f"角色在原地{act}：双腿以自然步幅持续交替迈步，膝盖随步伐屈伸，"
                      "双脚依次离地、前摆、落地，步态平稳连贯；身体随重心轻微上下颠动；"
                      "手臂随步伐自然摆动，双手持枪或扶握武器时双臂与武器保持稳定。"
                      "保持取景框与背景完全静止，角色始终位于画面中心、体型大小不变，"
                      "不向画面任何方向平移、不旋转身体、镜头不动。")
        else:
            prompt = DEFAULT_WALK_PROMPT
    # 上限 400：比 UI 建议的 300 字宽松（UI 只警告不拦截），但防止有人粘一篇
    # 作文进来把 project.json 撑爆
    prompt = prompt[:400]
    eng = str(w.get("engine") or "hy15").strip().lower()[:12]
    if eng not in ("hy15", "wan"):
        eng = "hy15"
    return {
        "enabled": bool(w.get("enabled", True)),
        "dirMode": mode,
        # 视频引擎：白名单之外一律回默认（hy15）。不进白名单 = 改了存不下、刷新就丢
        "engine": eng,
        "prompt": prompt,
        # 自定义负面词（空 = 用管线默认常量）。同 prompt：不进白名单就存不下来
        "negative": str(w.get("negative") or "")[:400],
        "action": str(w.get("action") or "行走")[:20] or "行走",
        "sources": srcs,
        "styleId": str(w.get("styleId") or "")[:40] or None,
        "steps": (_i(w.get("steps"), 0, 4, 60) or None) if w.get("steps") else None,
        "seed": (_i(w.get("seed"), 0, 0, 2**31 - 1)) if w.get("seed") not in (None, "") else None,
        "width": _i(w.get("width"), 384, 128, 1024),
        "height": _i(w.get("height"), 512, 128, 1024),
        "numFrames": _i(w.get("numFrames"), 21 if eng == "hy15" else 81, 5,
                        257 if eng == "hy15" else 193),
        "fps": _i(w.get("fps"), 24, 8, 30),
        "maxSide": _i(w.get("maxSide"), 512, 256, 1024),
        "maxSeqLen": (_i(w.get("maxSeqLen"), 0, 256, 1536) or None) if w.get("maxSeqLen") else None,
        # 下面五个字段以前不在白名单里：UI 上能改、推理时也认，但一保存刷新就
        # 静默重置回默认值——用户以为「设置丢了」，其实是根本没存。
        "dirImages": dir_imgs,
        "pixelArt": bool(w.get("pixelArt", False)),
        "guidanceScale": (round(float(w["guidanceScale"]), 2)
                          if w.get("guidanceScale") not in (None, "") else None),
        "motionThreshold": round(max(0.0, min(float(w.get("motionThreshold") or 6.0), 60.0)), 2),
        "retries": _i(w.get("retries"), 2, 0, 5),
        # hy15 分窗阈值：0=关闭（整段生成，长片会 OOM）；>0 且 numFrames 更大时链式
        # 分窗，默认 41（段数越少漂移越小，见 hy15.DEFAULT_WINDOW 的实测依据）。
        # 仅 hy15 生效；Wan 忽略。不进白名单 = 前端设了也存不下。
        "window": _i(w.get("window"), DEFAULT_HY15_WINDOW, 0, 257),
    }


def _clean_parts(p: Any) -> Dict[str, Any]:
    """拆件参数：分辨率夹 768-1600（低于 768 手臂会断）。"""
    p = p if isinstance(p, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    return {
        "enabled": bool(p.get("enabled", True)),
        "resolution": _i(p.get("resolution"), 1280, 768, 1600),
        "withHead": bool(p.get("withHead", True)),
        "seed": _i(p.get("seed"), 7, 0, 2**31 - 1),
    }


def _clean_rigwalk(r: Any) -> Dict[str, Any]:
    """骨骼行走参数：帧数 4-24，摆角 6-24（超过 24 腿根必外翻）。"""
    r = r if isinstance(r, dict) else {}

    def _f(val, d, lo, hi):
        try:
            return max(lo, min(float(val), hi))
        except Exception:
            return d

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    return {
        "enabled": bool(r.get("enabled", True)),
        "frames": _i(r.get("frames"), 8, 4, 24),
        "swing": _f(r.get("swing"), 13, 6, 24),
        "arm": _f(r.get("arm"), 12, 0, 30),
        "bob": _f(r.get("bob"), 5, 0, 16),
        "lift": _f(r.get("lift"), 7, 0, 20),
        "hold": bool(r.get("hold", False)),
        "fps": _i(r.get("fps"), 8, 4, 24),
    }


def _clean_txtvideo(t: Any) -> Dict[str, Any]:
    """文本生视频参数：尺寸/帧数/fps 夹在与 Wan 解码器一致的范围里。"""
    t = t if isinstance(t, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    def _opt_i(val, lo, hi):
        if val is None or val == "":
            return None
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return None

    def _opt_f(val, lo, hi):
        if val is None or val == "":
            return None
        try:
            return max(lo, min(float(val), hi))
        except Exception:
            return None

    engine = t.get("engine")
    quality = t.get("quality")
    resolution = t.get("resolution")
    return {
        "enabled": bool(t.get("enabled", True)),
        "prompt": str(t.get("prompt") or "")[:4000],
        "negative": str(t.get("negative") or "")[:1000],
        "steps": _opt_i(t.get("steps"), 4, 60),
        "seed": _opt_i(t.get("seed"), 0, 2 ** 31),
        "width": _i(t.get("width"), 832, 128, 1280),
        "height": _i(t.get("height"), 480, 128, 1280),
        "numFrames": _i(t.get("numFrames"), 21, 5, 257),
        "fps": _i(t.get("fps"), 12, 8, 30),
        "maxSide": _i(t.get("maxSide"), 832, 256, 1280),
        "maxSeqLen": _opt_i(t.get("maxSeqLen"), 256, 1536),
        "pixelArt": bool(t.get("pixelArt", False)),
        "guidanceScale": _opt_f(t.get("guidanceScale"), 1.0, 9.0),
        "interpolate": bool(t.get("interpolate", False)),
        "upscale": bool(t.get("upscale", False)),
        "quality": quality if quality in ("draft", "standard", "fine") else "standard",
        "resolution": (resolution if resolution in ("480p", "720p", "custom")
                       else "480p"),
        "interpolate": bool(t.get("interpolate", False)),
        "upscale": bool(t.get("upscale", False)),
        "format": fmt if (fmt := t.get("format")) in ("mp4", "gif") else "mp4",
        "engine": engine if engine in ("auto", "wan", "hy15") else None,
        # 三态：None=跟随参考图比例 / True=强制跟随 / False=按用户画幅不动
        "fitAspect": (None if t.get("fitAspect") is None
                      else bool(t.get("fitAspect"))),
    }


def _clean_sequence(s: Any) -> Dict[str, Any]:
    """序列帧参数：每方向帧数 2-24，容差夹在 10-120。"""
    s = s if isinstance(s, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    return {
        "enabled": bool(s.get("enabled", True)),
        "framesPerDir": _i(s.get("framesPerDir"), 8, 2, 24),
        "fps": _i(s.get("fps"), 8, 2, 30),
        "matte": bool(s.get("matte", True)),
        "tolerance": _i(s.get("tolerance"), 40, 10, 120),
    }


def _clean_spritesheet(s: Any) -> Dict[str, Any]:
    """精灵整图参数：单元格 32-512，锚点只认 bottom/center/top。"""
    s = s if isinstance(s, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    anchor = str(s.get("anchor") or "bottom")[:8]
    if anchor not in ("bottom", "center", "top"):
        anchor = "bottom"
    return {
        "enabled": bool(s.get("enabled", True)),
        "size": _i(s.get("size"), 128, 32, 512),
        "cols": _i(s.get("cols"), 0, 0, 32),
        "sharedScale": bool(s.get("sharedScale", True)),
        "anchor": anchor,
    }


def _clean_play(p: Any) -> Dict[str, Any]:
    """可操控预览参数：播放帧率与显示倍率。"""
    p = p if isinstance(p, dict) else {}

    def _i(val, d, lo, hi):
        try:
            return max(lo, min(int(val), hi))
        except Exception:
            return d

    return {
        "enabled": bool(p.get("enabled", True)),
        "fps": _i(p.get("fps"), 8, 2, 30),
        "zoom": _i(p.get("zoom"), 3, 1, 8),
    }


def _cl(v: Any, d: float, lo: float, hi: float) -> float:
    try:
        f = float(v)
        if f != f:          # NaN 是 truthy，min/max 拦不住
            return d
        return round(max(lo, min(f, hi)), 3)
    except Exception:
        return d


def _ci(v: Any, d: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(int(v), hi))
    except Exception:
        return d


def _clean_clipsrc(c: Any) -> Dict[str, Any]:
    """视频 / GIF 源：只存文件名，其余是展示信息（不参与逻辑）。"""
    c = c if isinstance(c, dict) else {}
    return {
        "file": str(c.get("file") or "")[:200],
        "kind": "gif" if str(c.get("kind") or "") == "gif" else "video",
        "durationSec": _cl(c.get("durationSec"), 0.0, 0.0, 3600.0),
        "fps": _cl(c.get("fps"), 0.0, 0.0, 240.0),
        "width": _ci(c.get("width"), 0, 0, 8192),
        "height": _ci(c.get("height"), 0, 0, 8192),
    }


def _clean_split_frame(f: Any) -> Optional[Dict[str, Any]]:
    """拆帧产物的一帧。hash 是前端实时过滤的唯一依据，不能丢。"""
    if not isinstance(f, dict):
        return None
    fn = str(f.get("file") or "")[:200]
    if not fn:
        return None
    return {
        "i": _ci(f.get("i"), 0, 0, 10000),
        "file": fn,
        "url": str(f.get("url") or "")[:400],
        "thumbFile": str(f.get("thumbFile") or "")[:200],
        "thumbUrl": str(f.get("thumbUrl") or "")[:400],
        "t": _cl(f.get("t"), 0.0, 0.0, 3600.0),
        "w": _ci(f.get("w"), 0, 0, 8192),
        "h": _ci(f.get("h"), 0, 0, 8192),
        "diff": _cl(f.get("diff"), 0.0, 0.0, 100.0),
        "sharp": _cl(f.get("sharp"), 0.0, 0.0, 1000.0),
        "hash": str(f.get("hash") or "")[:400],
        # 动作复刻的每帧指标。不进白名单 → 刷新页面后「动作重合 62%」那排
        # 告警凭空消失，用户以为复刻结果变好了（实际只是指标没了）。
        "iou": (None if f.get("iou") is None
                else _cl(f.get("iou"), 0.0, 0.0, 1.0)),
        "dx": (None if f.get("dx") is None else _ci(f.get("dx"), 0, -8192, 8192)),
        "dy": (None if f.get("dy") is None else _ci(f.get("dy"), 0, -8192, 8192)),
        "seconds": (None if f.get("seconds") is None
                    else _cl(f.get("seconds"), 0.0, 0.0, 3600.0)),
    }


def _clean_spritesplit(f: Any) -> Dict[str, Any]:
    """精灵图拆解参数。picks 与 framesplit 同语义（勾选了哪些帧）。"""
    f = f if isinstance(f, dict) else {}
    picks = [str(x)[:200] for x in (f.get("picks") or []) if isinstance(x, str)][:256]
    return {
        "enabled": bool(f.get("enabled", True)),
        "cols": _ci(f.get("cols"), 0, 0, 64),
        "rows": _ci(f.get("rows"), 0, 0, 64),
        "cell": _ci(f.get("cell"), 0, 0, 1024),
        "count": _ci(f.get("count"), 0, 0, 256),
        "matte": bool(f.get("matte", False)),
        "tolerance": _ci(f.get("tolerance"), 40, 10, 120),
        "picks": picks,
    }


def _clean_poseclone(f: Any) -> Dict[str, Any]:
    """动作复刻参数。instruction 允许 4000 字：它是一整段多图编辑指令，
    默认值本身就有 300 多字，按 200 字截断会把「不要画出四个视角」这类
    关键约束拦腰砍掉（而那正是最容易出错的一条）。"""
    f = f if isinstance(f, dict) else {}
    mode = str(f.get("mode") or "per-frame")
    bg = str(f.get("background") or "transparent")
    anchor = str(f.get("anchor") or "bottom")
    ref_mode = str(f.get("refMode") or "auto")
    return {
        "enabled": bool(f.get("enabled", True)),
        "mode": mode if mode in ("per-frame", "whole-sheet") else "per-frame",
        "instruction": str(f.get("instruction") or "")[:4000],
        "negative": str(f.get("negative") or "")[:1200],
        "steps": _ci(f.get("steps"), 0, 0, 50),
        "size": _ci(f.get("size"), 0, 0, 1536),
        "background": bg if bg in ("transparent", "", "none", "white",
                                   "gray", "black") else "transparent",
        "anchor": anchor if anchor in ("bottom", "center") else "bottom",
        "refMode": ref_mode if ref_mode in ("auto", "as-is") else "auto",
        # 角色参考视角：auto=用第一帧试 4 格选最像的；board=整板一起喂（老行为）
        "refView": (str(f.get("refView") or "auto")
                    if str(f.get("refView") or "auto") in (
                        "auto", "front", "side", "back", "side2", "board")
                    else "auto"),
        "seed": (None if f.get("seed") in (None, "")
                 else _ci(f.get("seed"), 0, 0, 2**31 - 1)),
        "maxSeqLen": (None if f.get("maxSeqLen") in (None, "", 0)
                      else _ci(f.get("maxSeqLen"), 0, 256, 1920)),
    }


def _clean_framesplit(f: Any) -> Dict[str, Any]:
    """拆帧 + 选帧参数。step/simPct 是「用户挑帧」的口径，必须原样存。

    这两个值决定「哪些帧进精灵图」，丢了的话刷新页面选帧全丢——用户看到的
    是「我刚挑好的帧变成默认全选了」。
    """
    f = f if isinstance(f, dict) else {}
    picks = [str(x)[:200] for x in (f.get("picks") or []) if isinstance(x, str)][:600]
    return {
        "enabled": bool(f.get("enabled", True)),
        "fpsCap": _cl(f.get("fpsCap"), 12.0, 0.5, 60.0),
        "maxFrames": _ci(f.get("maxFrames"), 120, 2, 600),
        "start": _cl(f.get("start"), 0.0, 0.0, 3600.0),
        "end": (None if f.get("end") in (None, "", 0, "0")
                else _cl(f.get("end"), 0.0, 0.0, 3600.0)),
        "matte": bool(f.get("matte", True)),
        # 抠图底座：auto / u2net / birefnet-general-lite ...（不进白名单＝存不下）
        "matteModel": (str(f.get("matteModel") or "auto")
                       if str(f.get("matteModel") or "auto") in (
                           "auto", "u2net", "birefnet-general-lite",
                           "birefnet-general", "isnet-anime") else "auto"),
        # 循环闭合：按整周期裁帧（默认开）。不进白名单 = 前端关了也存不下
        "loopClose": bool(f.get("loopClose", True)),
        "tolerance": _ci(f.get("tolerance"), 40, 10, 120),
        "maxSide": _ci(f.get("maxSide"), 384, 64, 1024),
        # 跳帧步长：每 N 帧取 1 帧
        "step": _ci(f.get("step"), 1, 1, 30),
        # 近似度过滤阈值（%）：与上一张保留帧差异低于它就算重复
        "simPct": _cl(f.get("simPct"), 0.0, 0.0, 40.0),
        "picks": picks,
    }


def _clean_spritegrid(s: Any) -> Dict[str, Any]:
    """精灵图参数：单元格 / 列数 / 帧间距 / 外边距 / 锚点 / 共享缩放。"""
    s = s if isinstance(s, dict) else {}
    anchor = str(s.get("anchor") or "bottom")[:8]
    if anchor not in ("bottom", "center", "top"):
        anchor = "bottom"
    return {
        "enabled": bool(s.get("enabled", True)),
        "cell": _ci(s.get("cell"), 128, 8, 512),
        "cols": _ci(s.get("cols"), 0, 0, 64),
        "padding": _ci(s.get("padding"), 0, 0, 32),
        "margin": _ci(s.get("margin"), 0, 0, 64),
        "anchor": anchor,
        "sharedScale": bool(s.get("sharedScale", True)),
        # 落格方式：shared=统一裁剪窗口（默认，不会把衣摆摆动放大成左右位移）
        "pivot": ("per-frame" if str(s.get("pivot") or "") in ("per-frame", "perframe")
                  else "shared"),
        "fps": _cl(s.get("fps"), 12.0, 0.5, 60.0),
    }


def _clean_animpreview(a: Any) -> Dict[str, Any]:
    """动画预览：帧率、倍率、循环。bg 只影响预览底色（透明素材要能看出边界）。"""
    a = a if isinstance(a, dict) else {}
    bg = str(a.get("bg") or "checker")[:12]
    if bg not in ("checker", "dark", "light", "green"):
        bg = "checker"
    return {
        "enabled": bool(a.get("enabled", True)),
        "fps": _cl(a.get("fps"), 12.0, 0.5, 60.0),
        "zoom": _ci(a.get("zoom"), 3, 1, 12),
        "loop": bool(a.get("loop", True)),
        "bg": bg,
    }


def _clean_godot2d(g: Any) -> Dict[str, Any]:
    """Godot 导出参数：交付形态（模式 / 目录 / 原点 / 过滤）+ 动画名与文件名。

    文件名主干只留 ASCII：Godot 的 res:// 路径在 Windows/macOS/Linux 之间要
    通用，中文文件名在某些导出/打包链路上会翻车；动画名仍然可以中文（那是
    资源里的字符串，不是路径）。
    """
    g = g if isinstance(g, dict) else {}
    mode = "files" if str(g.get("mode") or "") == "files" else "atlas"
    origin = str(g.get("origin") or "bottom")[:8]
    if origin not in ("bottom", "center", "top"):
        origin = "bottom"
    filt = "linear" if str(g.get("textureFilter") or "") == "linear" else "nearest"
    stem = "".join(c for c in str(g.get("name") or "walk").lower()
                   if c.isalnum() or c in "-_")[:40].strip("-_") or "walk"
    # 目录：允许 a/b 分层，逐段只留安全字符（与 pipelines/godot2d.safe_folder 同口径）
    segs = []
    for seg in str(g.get("folder") or "sprites").replace("\\", "/").split("/"):
        clean = "".join(c for c in seg.lower() if c.isalnum() or c in "-_").strip("-_")
        if clean and clean not in (".", ".."):
            segs.append(clean[:40])
    return {
        "enabled": bool(g.get("enabled", True)),
        "animName": str(g.get("animName") or "default")[:64],
        "name": stem,
        "fps": _cl(g.get("fps"), 12.0, 0.5, 60.0),
        "loop": bool(g.get("loop", True)),
        "mode": mode,
        "folder": "/".join(segs[:4]) or "sprites",
        "origin": origin,
        "textureFilter": filt,
    }


def _clean_inpaint(g: Any) -> Dict[str, Any]:
    """局部重绘参数：白名单 + 夹紧。

    这里最大的坑是「前端能改、存不下」：`constraint` / `refs` / `maskFile`
    任何一项漏进白名单，表现都是「改了刷新就回默认」或者「涂了蒙版重开就没了」。
    """
    g = g if isinstance(g, dict) else {}
    mode = "full" if str(g.get("mode") or "") == "full" else "crop"
    pal = "source" if str(g.get("palette") or "") == "source" else "none"
    refs = [r for r in (g.get("refs") or []) if isinstance(r, dict)][:2]
    mask = g.get("mask") if isinstance(g.get("mask"), dict) else None
    mask_info = None
    if mask:
        box = mask.get("box") if isinstance(mask.get("box"), list) else []
        mask_info = {
            "file": str(mask.get("file") or "")[:200],
            "url": str(mask.get("url") or "")[:400],
            "width": _ci(mask.get("width"), 0, 0, 8192),
            "height": _ci(mask.get("height"), 0, 0, 8192),
            "coverage": _cl(mask.get("coverage"), 0.0, 0.0, 1.0),
            "box": [_ci(v, 0, 0, 8192) for v in box][:4],
        }
    return {
        "enabled": bool(g.get("enabled", True)),
        "instruction": str(g.get("instruction") or "")[:4000],
        "constraint": str(g.get("constraint") or "")[:4000],
        "negative": str(g.get("negative") or "")[:600],
        "refs": refs,
        "mode": mode,
        "context": _cl(g.get("context"), 0.35, 0.0, 2.0),
        "feather": _ci(g.get("feather"), 4, 0, 64),
        "expand": _ci(g.get("expand"), 8, 0, 64),
        "hard": bool(g.get("hard")),
        "matchColor": bool(g.get("matchColor", True)),
        "matchStrength": _cl(g.get("matchStrength"), 0.45, 0.0, 1.0),
        "palette": pal,
        "paletteColors": _ci(g.get("paletteColors"), 48, 8, 256),
        "steps": (_ci(g.get("steps"), 0, 4, 60) or None) if g.get("steps") else None,
        "seed": (_ci(g.get("seed"), 0, 0, 2**31 - 1))
        if g.get("seed") not in (None, "") else None,
        "maxSeqLen": (_ci(g.get("maxSeqLen"), 0, 256, 1536) or None)
        if g.get("maxSeqLen") else None,
        "maskFile": str(g.get("maskFile") or "")[:200],
        "mask": mask_info,
    }


def _clean_layer25d(g: Any) -> Dict[str, Any]:
    """2.5D 分层参数：白名单 + 夹紧（漏一项就是「改了存不下」）。"""
    g = g if isinstance(g, dict) else {}
    return {
        "enabled": bool(g.get("enabled", True)),
        "resolution": _ci(g.get("resolution"), 1024, 512, 2048),
        "steps": _ci(g.get("steps"), 30, 4, 60),
        "seed": _ci(g.get("seed"), 42, 0, 2 ** 31 - 1),
        "lrsplit": bool(g.get("lrsplit", True)),
    }


def _clean_erase(g: Any) -> Dict[str, Any]:
    """去除局部参数：白名单 + 夹紧（漏一项就是「改了存不下」）。"""
    g = g if isinstance(g, dict) else {}
    method = str(g.get("method") or "telea")[:8]
    if method not in ("telea", "offset", "mirror", "ai"):
        method = "telea"
    pal = "source" if str(g.get("palette") or "") == "source" else "none"
    mask = g.get("mask") if isinstance(g.get("mask"), dict) else None
    mask_info = None
    if mask:
        box = mask.get("box") if isinstance(mask.get("box"), list) else []
        mask_info = {
            "file": str(mask.get("file") or "")[:200],
            "url": str(mask.get("url") or "")[:400],
            "width": _ci(mask.get("width"), 0, 0, 8192),
            "height": _ci(mask.get("height"), 0, 0, 8192),
            "coverage": _cl(mask.get("coverage"), 0.0, 0.0, 1.0),
            "box": [_ci(v, 0, 0, 8192) for v in box][:4],
        }
    return {
        "enabled": bool(g.get("enabled", True)),
        "method": method,
        "expand": _ci(g.get("expand"), 6, 0, 64),
        "feather": _ci(g.get("feather"), 2, 0, 32),
        "hard": bool(g.get("hard")),
        "matchColor": bool(g.get("matchColor", True)),
        "matchStrength": _cl(g.get("matchStrength"), 0.6, 0.0, 1.0),
        "palette": pal,
        "paletteColors": _ci(g.get("paletteColors"), 48, 8, 256),
        "instruction": str(g.get("instruction") or "")[:2000],
        "constraint": str(g.get("constraint") or "")[:4000],
        "negative": str(g.get("negative") or "")[:600],
        "aiMode": "full" if str(g.get("aiMode") or "") == "full" else "crop",
        "context": _cl(g.get("context"), 0.35, 0.0, 2.0),
        "steps": (_ci(g.get("steps"), 0, 4, 60) or None) if g.get("steps") else None,
        "seed": (_ci(g.get("seed"), 0, 0, 2**31 - 1))
        if g.get("seed") not in (None, "") else None,
        "maxSeqLen": (_ci(g.get("maxSeqLen"), 0, 256, 1536) or None)
        if g.get("maxSeqLen") else None,
        "maskFile": str(g.get("maskFile") or "")[:200],
        "mask": mask_info,
    }


def _clean_pixel(p: Any) -> Dict[str, Any]:
    p = p if isinstance(p, dict) else {}

    def _size(v):
        if isinstance(v, (list, tuple)) and len(v) == 2:
            try:
                return [max(2, min(int(v[0]), 1024)), max(2, min(int(v[1]), 1024))]
            except Exception:
                return None
        return None
    return {
        "enabled": bool(p.get("enabled", True)),
        "gridSnap": bool(p.get("gridSnap", True)),
        "colors": max(2, min(int(p.get("colors") or 16), 256)),
        "palette": str(p.get("palette") or "auto")[:24],
        "gridSize": _size(p.get("gridSize")),
        "targetSize": _size(p.get("targetSize")),
        "transparentBg": bool(p.get("transparentBg")),
    }


def list_projects() -> List[Dict[str, Any]]:
    ensure_dirs()
    out: List[Dict[str, Any]] = []
    for d in PROJECTS_DIR.iterdir() if PROJECTS_DIR.is_dir() else []:
        if not d.is_dir():
            continue
        project = load_project(d.name)
        if project is None:
            continue
        results = [n for n in project.get("nodes", []) if n.get("type") == "result"]
        images = [img for n in results for img in (n.get("images") or [])]
        thumb = images[-1].get("url") if images else None
        out.append({
            "id": project["id"],
            "name": project.get("name") or "未命名项目",
            "templateId": project.get("templateId"),
            "templateDraftOf": project.get("templateDraftOf"),
            "createdAt": project.get("createdAt"),
            "updatedAt": project.get("updatedAt"),
            "nodeCount": len(project.get("nodes", [])),
            "resultCount": len(results),
            "imageCount": len(images),
            "thumbnail": thumb,
            "sizeMB": project_size_mb(project["id"]),
        })
    out.sort(key=lambda x: x.get("updatedAt") or 0, reverse=True)
    return out


def find_draft(tid: str) -> Optional[Dict[str, Any]]:
    """找一个模板已有的编辑草稿（同模板只留一份）。"""
    ensure_dirs()
    for d in PROJECTS_DIR.iterdir() if PROJECTS_DIR.is_dir() else []:
        if not d.is_dir():
            continue
        project = load_project(d.name)
        if project is not None and project.get("templateDraftOf") == tid:
            return project
    return None


def delete_project(pid: str) -> bool:
    d = project_dir(pid)
    if not d.is_dir():
        return False
    # Windows 上刚被读过的文件（浏览器在下载导出 zip、杀毒在扫新写的 png）
    # 句柄还没释放，rmtree 会抛 PermissionError。重试几次比直接失败好：
    # 失败会往上冒成 404「项目不存在」，而项目明明还在磁盘上。
    for attempt in range(3):
        try:
            shutil.rmtree(d)
            return True
        except Exception:
            time.sleep(0.15 * (attempt + 1))
    return not d.exists()


def rename_project(pid: str, name: str) -> Optional[Dict[str, Any]]:
    project = load_project(pid)
    if project is None:
        return None
    project["name"] = (name or "").strip()[:60] or project.get("name")
    project["updatedAt"] = _now()
    with _lock(pid):
        _write(pid, project)
    write_readme(pid, project["name"], project.get("templateId", ""),
                 project.get("createdAt", 0))
    return project
