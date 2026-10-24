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
                 "threeview", "walkvideo", "sequence", "spritesheet", "play")

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
    "numFrames": 21,                        # 默认引擎 hy15 的帧数（4k+1，上限 33）
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
                n["styleId"] = n.get("styleId") or base_gen["styleId"]
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
        "background": str(p.get("background") or "gray")[:16],
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
                        33 if eng == "hy15" else 193),
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
    try:
        shutil.rmtree(d)
        return True
    except Exception:
        return False


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
