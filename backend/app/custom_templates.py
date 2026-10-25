"""自定义工作流模板：用户把画布上的节点图存成模板，新建项目时复用。

与内置模板（templates.py）的区别：
- 内置模板是代码里写死的「说明书 + 默认参数」；
- 自定义模板是用户画布结构的快照：节点、连线、每个节点的参数。

保存时只留「结构 + 参数」，去掉运行时数据（图片、参考图、生成结果）——
跨项目引用旧项目的文件没有意义，反而会指向不存在的 asset。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, ensure_dirs
from .jsonlimits import dumps_bounded, parse_bounded

_PATH = DATA_DIR / "custom_templates.json"
_ALLOWED = ("generate", "result", "input", "refine", "matte", "viewgen",
            "threeview", "walkvideo", "sequence", "spritesheet", "play",
            "txtvideo", "parts", "rigwalk",
            "clipsrc", "framesplit", "spritegrid", "animpreview", "godot2d",
            "inpaint", "erase", "layer25d")
_ICON_COLORS = ["#FFB020", "#40E0D0", "#FF6B9D", "#8B7BFF", "#5B9DFF"]


def _load() -> Dict[str, Any]:
    ensure_dirs()
    if _PATH.is_file():
        try:
            data = parse_bounded(_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("templates"), list):
                return data
        except Exception:
            pass
    return {"version": 1, "templates": []}


def _save(data: Dict[str, Any]) -> None:
    ensure_dirs()
    _PATH.write_text(dumps_bounded(data), encoding="utf-8")


def _num(v: Any, d: float = 0.0) -> float:
    try:
        f = float(v)
        return round(f, 2) if f == f else d
    except Exception:
        return d


def _clean_node(n: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(n, dict) or n.get("type") not in _ALLOWED:
        return None
    item: Dict[str, Any] = {
        "id": str(n.get("id") or "")[:80],
        "type": n["type"],
        "x": _num(n.get("x")),
        "y": _num(n.get("y")),
    }
    if n["type"] == "generate":
        item.update({
            "prompt": str(n.get("prompt") or "")[:6000],
            "styleId": str(n.get("styleId") or "") or None,
            "refs": [],   # 跨项目无效
            "params": n.get("params") if isinstance(n.get("params"), dict) else {},
            "pixel": n.get("pixel") if isinstance(n.get("pixel"), dict) else {},
        })
    elif n["type"] == "input":
        item.update({"label": str(n.get("label") or "")[:80], "refs": []})
    elif n["type"] == "refine":
        item.update({
            "pixel": n.get("pixel") if isinstance(n.get("pixel"), dict) else {},
            "result": None,
        })
    elif n["type"] == "matte":
        item.update({
            "matte": n.get("matte") if isinstance(n.get("matte"), dict) else {},
            "result": None,
        })
    elif n["type"] == "viewgen":
        item.update({
            "viewgen": n.get("viewgen") if isinstance(n.get("viewgen"), dict) else {},
            "result": None,
        })
    elif n["type"] == "threeview":
        item.update({
            "threeview": n.get("threeview") if isinstance(n.get("threeview"), dict) else {},
            "result": None,
        })
    elif n["type"] == "walkvideo":
        item.update({
            "walkvideo": n.get("walkvideo") if isinstance(n.get("walkvideo"), dict) else {},
            "result": None,
        })
    elif n["type"] == "sequence":
        item.update({
            "sequence": n.get("sequence") if isinstance(n.get("sequence"), dict) else {},
            "result": None,
        })
    elif n["type"] == "spritesheet":
        item.update({
            "spritesheet": n.get("spritesheet") if isinstance(n.get("spritesheet"), dict) else {},
            "result": None,
        })
    elif n["type"] == "play":
        item.update({
            "play": n.get("play") if isinstance(n.get("play"), dict) else {},
            "result": None,
        })
    elif n["type"] == "txtvideo":
        item.update({
            "txtvideo": n.get("txtvideo") if isinstance(n.get("txtvideo"), dict) else {},
            "result": None,
        })
    elif n["type"] == "parts":
        item.update({
            "parts": n.get("parts") if isinstance(n.get("parts"), dict) else {},
            "result": None,
        })
    elif n["type"] == "rigwalk":
        item.update({
            "rigwalk": n.get("rigwalk") if isinstance(n.get("rigwalk"), dict) else {},
            "result": None,
        })
    elif n["type"] in ("clipsrc", "framesplit", "spritegrid", "animpreview",
                       "godot2d", "inpaint", "erase", "layer25d"):
        # 这几个节点自己带一个同名字段存参数；拆帧产物（frames）与导出包
        # 跨项目无效，模板里只保留参数与结构。
        item.update({
            n["type"]: n.get(n["type"]) if isinstance(n.get(n["type"]), dict) else {},
            "result": None,
            "meta": None,
        })
    else:
        item.update({"images": [], "prompt": "", "negative": "", "meta": {}, "genId": None})
    return item


def _clean_graph(graph: Any, normalize: bool = True) -> Dict[str, Any]:
    g = graph if isinstance(graph, dict) else {}
    nodes: List[Dict[str, Any]] = []
    seen = set()
    for raw in (g.get("nodes") or [])[:120]:
        n = _clean_node(raw)
        if n is None:
            continue
        nid = n["id"] or f"n-{uuid.uuid4().hex[:8]}"
        if nid in seen:
            continue
        n["id"] = nid
        seen.add(nid)
        nodes.append(n)
    edges = []
    for e in (g.get("edges") or [])[:400]:
        if isinstance(e, dict) and e.get("from") and e.get("to"):
            a, b = str(e["from"]), str(e["to"])
            if a in seen and b in seen and a != b:
                edges.append({"from": a, "to": b})
    # 坐标归一化：模板可能来自画布任意位置，新建项目时从左上角开始摆放，
    # 否则视口 {0,0,1} 里第一个节点可能在屏幕外。
    # 模板编辑草稿 commit 时传 normalize=False：坐标本来就在模板坐标系里，
    # 整体平移会让「只改了提示词」也产生坐标 diff。
    if normalize and nodes:
        minx = min(n["x"] for n in nodes)
        miny = min(n["y"] for n in nodes)
        for n in nodes:
            n["x"] = round(n["x"] - minx + 80, 2)
            n["y"] = round(n["y"] - miny + 120, 2)
    return {"nodes": nodes, "edges": edges}


def list_templates() -> List[Dict[str, Any]]:
    return _load().get("templates", [])


def get_template(tid: str) -> Optional[Dict[str, Any]]:
    for t in _load().get("templates", []):
        if t.get("id") == tid:
            return t
    return None


def _public(t: Dict[str, Any]) -> Dict[str, Any]:
    """补齐与内置模板一致的字段，前端可以同一套渲染。"""
    graph = t.get("graph") or {"nodes": [], "edges": []}
    nodes = graph.get("nodes") or []
    steps = []
    labels = {"input": "选择图片", "generate": "生成图", "refine": "像素精修",
              "matte": "扣除背景", "result": "图片结果",
              "viewgen": "生成视角", "threeview": "四视图拼板",
              "walkvideo": "行走视频", "sequence": "序列帧",
              "spritesheet": "精灵整图", "play": "可操控预览",
              "clipsrc": "选择视频", "framesplit": "拆解帧",
              "spritegrid": "精灵图", "animpreview": "动画预览",
              "godot2d": "Godot 导出", "inpaint": "局部重绘",
              "erase": "去除局部", "layer25d": "2.5D 分层模型"}
    for i, n in enumerate(nodes):
        steps.append({"n": i + 1, "title": labels.get(n.get("type"), n.get("type")),
                      "detail": "画布节点，占位坐标见 graph"})
    return {
        "id": t.get("id"),
        "name": t.get("name") or "未命名模板",
        "tagline": t.get("tagline") or "自定义工作流",
        "status": "available",
        "accent": t.get("accent") or _ICON_COLORS[0],
        "icon": t.get("icon") or "sprite",
        "description": t.get("description") or "用户自定义的节点图模板。",
        "highlights": t.get("highlights") or [f"{len(nodes)} 个节点", f"{len(graph.get('edges') or [])} 条连线"],
        "inputs": [],
        "steps": steps,
        "outputs": [],
        "defaults": t.get("defaults") if isinstance(t.get("defaults"), dict) else {},
        "notes": ["自定义模板：由画布「存为模板」生成，运行时图片会清空。"],
        "custom": True,
        "graph": graph,
        "createdAt": t.get("createdAt"),
        "updatedAt": t.get("updatedAt"),
    }


def create(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _load()
    now = time.time()
    graph = _clean_graph(payload.get("graph") or {
        "nodes": payload.get("nodes"), "edges": payload.get("edges")})
    if not graph["nodes"]:
        raise ValueError("模板至少要有一个节点")
    idx = len(data.get("templates", []))
    t = {
        "id": f"ct-{uuid.uuid4().hex[:10]}",
        "name": str(payload.get("name") or "未命名模板").strip()[:60],
        "tagline": str(payload.get("tagline") or "自定义工作流")[:120],
        "description": str(payload.get("description") or "")[:600],
        "accent": _ICON_COLORS[idx % len(_ICON_COLORS)],
        "icon": "sprite",
        "graph": graph,
        "defaults": payload.get("defaults") if isinstance(payload.get("defaults"), dict) else {},
        "createdAt": now,
        "updatedAt": now,
    }
    data.setdefault("templates", []).append(t)
    _save(data)
    return _public(t)


def update(tid: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = _load()
    for t in data.get("templates", []):
        if t.get("id") != tid:
            continue
        if "name" in payload:
            t["name"] = str(payload.get("name") or t["name"]).strip()[:60]
        if "tagline" in payload:
            t["tagline"] = str(payload.get("tagline") or "")[:120]
        if "description" in payload:
            t["description"] = str(payload.get("description") or "")[:600]
        if payload.get("graph") or payload.get("nodes") is not None:
            g = _clean_graph(payload.get("graph") or {
                "nodes": payload.get("nodes"), "edges": payload.get("edges")})
            if g["nodes"]:
                t["graph"] = g
        if isinstance(payload.get("defaults"), dict):
            t["defaults"] = payload["defaults"]
        t["updatedAt"] = time.time()
        _save(data)
        return _public(t)
    return None


def delete(tid: str) -> bool:
    data = _load()
    before = len(data.get("templates", []))
    data["templates"] = [t for t in data.get("templates", []) if t.get("id") != tid]
    if len(data["templates"]) == before:
        return False
    _save(data)
    return True
