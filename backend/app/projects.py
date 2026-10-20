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


ALLOWED_TYPES = ("generate", "result", "input", "refine", "matte")

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
                   "background": "gray"},
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                  "palette": "auto", "gridSize": None, "targetSize": None},
        "collapsed": False,
        "createdAt": _now(),
    }


def create_project(name: str, template_id: str, template_defaults: Dict[str, Any],
                   template_graph: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
            nid = new_asset_id(raw["type"][:3])
            idmap[str(raw.get("id") or "")] = nid
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
