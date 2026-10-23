"""内置模板的用户覆盖（override）。

为什么要这一层：内置模板是 `templates.py` 里的 Python 常量，用户想改默认提示词、
默认参数、节点链，只能改代码——改完下次拉代码又冲突。这里把「用户的编辑」存成
一份补丁（`data/template_overrides.json`），读取时合并到出厂定义之上：

- 代码里的 TEMPLATES 永远是不可变的「出厂默认」，升级不会覆盖用户编辑；
- 用户点「恢复默认」= 删掉这一条补丁，立即回到出厂状态；
- 创建项目走**合并后的视图**（`apply`），否则用户改了参数却不生效——这正是
  这个项目以前踩过的坑（设置页默认值没人读）。

只接受白名单字段：`id` / `status` / `custom` 由服务端控制，用户改不了，避免
「把规划中的模板改成 available」这种绕过。
"""
from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional

from .config import DATA_DIR, ensure_dirs
from .jsonlimits import dumps_bounded, parse_bounded
from .projects import ALLOWED_TYPES
from .templates import get_template as get_builtin

_PATH = DATA_DIR / "template_overrides.json"

# 用户可编辑的顶层字段。刻意不含 id / status / custom / graph 之外的运行时字段：
# - id 是主键，改了等于换一个模板；
# - status 决定「能不能新建项目」，不能让用户把规划中的模板点亮；
# - custom 由服务端按来源置位，前端据此显示「自定义 / 内置」。
_FIELDS = ("name", "tagline", "description", "highlights", "notes",
           "steps", "inputs", "outputs", "defaults", "graph")

_MAX_TEXT = {"name": 60, "tagline": 120, "description": 1200}


# --------------------------------------------------------------------------- #
# 读写
# --------------------------------------------------------------------------- #

def _load() -> Dict[str, Any]:
    ensure_dirs()
    if _PATH.is_file():
        try:
            data = parse_bounded(_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("items"), dict):
                return data
        except Exception:
            # 文件损坏时按「没有任何覆盖」处理：宁可回到出厂默认，
            # 也不能让整个模板接口 500（模板页会因此白屏）
            pass
    return {"version": 1, "items": {}}


def _save(data: Dict[str, Any]) -> None:
    ensure_dirs()
    _PATH.write_text(dumps_bounded(data), encoding="utf-8")


# --------------------------------------------------------------------------- #
# 校验
# --------------------------------------------------------------------------- #

def _text(v: Any, key: str, required: bool = False) -> Optional[str]:
    s = str(v if v is not None else "").strip()
    limit = _MAX_TEXT.get(key, 300)
    if len(s) > limit:
        s = s[:limit]
    if required and not s:
        raise ValueError(f"{key} 不能为空")
    return s


def _str_list(v: Any, item_limit: int = 200, max_items: int = 12) -> List[str]:
    if not isinstance(v, list):
        return []
    out: List[str] = []
    for x in v[:max_items]:
        s = str(x or "").strip()
        if s:
            out.append(s[:item_limit])
    return out


def _steps(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: List[Dict[str, Any]] = []
    for i, s in enumerate(v[:20]):
        if not isinstance(s, dict):
            continue
        title = str(s.get("title") or "").strip()[:60]
        if not title:
            continue
        # 序号：调用方给了合法 n 就尊重它（出厂定义原样提交时才不会因为重排
        # 产生假 diff），否则按顺序补（编辑器删掉一步后编号仍是连续的）
        raw_n = s.get("n")
        n = int(raw_n) if isinstance(raw_n, int) and 1 <= raw_n <= 99 else i + 1
        out.append({"n": n, "title": title,
                    "detail": str(s.get("detail") or "").strip()[:300]})
    return out


def _inputs(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in v[:12]:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()[:60]
        if not label:
            continue
        out.append({
            "key": str(it.get("key") or label).strip()[:40],
            "label": label,
            "required": bool(it.get("required", False)),
            "hint": str(it.get("hint") or "").strip()[:300],
        })
    return out


def _outputs(v: Any) -> List[Dict[str, Any]]:
    if not isinstance(v, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in v[:12]:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label") or "").strip()[:60]
        if label:
            out.append({"label": label,
                        "detail": str(it.get("detail") or "").strip()[:300]})
    return out


def _num(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
    except Exception:
        return default
    if f != f or f in (float("inf"), float("-inf")):   # NaN / inf
        return default
    return round(max(-1e5, min(f, 1e5)), 2)


def _graph(v: Any) -> Optional[Dict[str, Any]]:
    """校验节点图结构，但**不重建节点**（保留用户能编辑到的所有字段）。

    与 `custom_templates._clean_node` 的区别：那个是「从项目快照提取已知字段」，
    适合自定义模板；这里编辑的是内置模板，节点字段本来就干净，重建反而会把
    未来新增的节点字段悄悄丢掉。所以只做三件事：
    1. 类型 / id / 坐标校验；
    2. 清掉跨项目无意义的运行时数据（refs、result、images）；
    3. 丢悬空连线，并把坐标平移到左上角（与自定义模板一致，否则视口里看不到）。
    """
    if not isinstance(v, dict):
        return None
    raw_nodes = v.get("nodes")
    if not isinstance(raw_nodes, list):
        return None
    nodes: List[Dict[str, Any]] = []
    seen: set = set()
    for i, raw in enumerate(raw_nodes[:120]):
        if not isinstance(raw, dict) or raw.get("type") not in ALLOWED_TYPES:
            continue
        nid = str(raw.get("id") or "").strip()[:80] or f"n{i + 1}"
        if nid in seen:
            nid = f"{nid}-{i}"
        seen.add(nid)
        n = {k: copy.deepcopy(val) for k, val in raw.items()}
        n["id"] = nid
        n["x"] = _num(raw.get("x"), 80 + i * 40)
        n["y"] = _num(raw.get("y"), 140)
        if n["type"] == "generate":
            n["refs"] = []                 # 跨项目引用无意义
            n["prompt"] = str(n.get("prompt") or "")[:6000]
        elif n["type"] == "input":
            n["refs"] = []
        elif n["type"] == "result":
            n["images"] = []
            n["meta"] = {}
        else:
            n["result"] = None
        n.pop("lastImages", None)
        nodes.append(n)
    if not nodes:
        raise ValueError("模板至少要有一个合法节点")

    edges: List[Dict[str, str]] = []
    for e in (v.get("edges") or [])[:400]:
        if not isinstance(e, dict):
            continue
        a = str(e.get("from") or "").strip()
        b = str(e.get("to") or "").strip()
        # 悬空连线直接丢：删节点时前端会自动清边，但历史数据/手改 JSON
        # 可能留下指向不存在节点的边，留着会让画布运行期来找不存在的上游
        if a and b and a in seen and b in seen and a != b:
            edges.append({"from": a, "to": b})

    # 只修负坐标（视口原点在左上，负坐标的节点开局看不见）。
    # 不做「整体平移到 80/120」：内置模板出厂坐标本来就在可视区，平移会让
    # 「用户没改图」也产生 diff，进而存下一条无意义的全量覆盖。
    minx = min(n["x"] for n in nodes)
    miny = min(n["y"] for n in nodes)
    dx = round(80 - minx, 2) if minx < 0 else 0
    dy = round(120 - miny, 2) if miny < 0 else 0
    if dx or dy:
        for n in nodes:
            n["x"] = round(n["x"] + dx, 2)
            n["y"] = round(n["y"] + dy, 2)
    return {"nodes": nodes, "edges": edges}


def _clean_patch(payload: Dict[str, Any]) -> Dict[str, Any]:
    """把前端提交的补丁收敛到白名单字段。未提交的字段不写（保持出厂值）。"""
    patch: Dict[str, Any] = {}
    if "name" in payload:
        patch["name"] = _text(payload.get("name"), "name", required=True)
    if "tagline" in payload:
        patch["tagline"] = _text(payload.get("tagline"), "tagline")
    if "description" in payload:
        patch["description"] = _text(payload.get("description"), "description")
    if "highlights" in payload:
        patch["highlights"] = _str_list(payload.get("highlights"), 160, 12)
    if "notes" in payload:
        patch["notes"] = _str_list(payload.get("notes"), 300, 12)
    if "steps" in payload:
        patch["steps"] = _steps(payload.get("steps"))
    if "inputs" in payload:
        patch["inputs"] = _inputs(payload.get("inputs"))
    if "outputs" in payload:
        patch["outputs"] = _outputs(payload.get("outputs"))
    if "defaults" in payload:
        d = payload.get("defaults")
        if not isinstance(d, dict):
            raise ValueError("defaults 必须是对象")
        patch["defaults"] = copy.deepcopy(d)
    if "graph" in payload:
        g = _graph(payload.get("graph"))
        if g is None:
            raise ValueError("graph 必须是 {nodes: [], edges: []}")
        patch["graph"] = g
    if not patch:
        raise ValueError("没有可保存的字段")
    return patch


# --------------------------------------------------------------------------- #
# 公共 API
# --------------------------------------------------------------------------- #

def apply(t: Dict[str, Any]) -> Dict[str, Any]:
    """把覆盖合并到模板定义上，返回**新对象**（绝不改模块级常量 TEMPLATES）。"""
    out = copy.deepcopy(t)
    patch = _load().get("items", {}).get(str(t.get("id")))
    if isinstance(patch, dict):
        for k, v in patch.items():
            if k in _FIELDS:
                out[k] = copy.deepcopy(v)
        out["overridden"] = True
        out["updatedAt"] = patch.get("_updatedAt")
    else:
        out["overridden"] = False
    out["editable"] = True
    return out


def set_override(tid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    base = get_builtin(tid)
    if base is None:
        raise ValueError("内置模板不存在")
    patch = _clean_patch(payload)
    # **合并**已有覆盖，而不是整体替换。模板有两类编辑入口：
    # 画布（提交 graph / defaults）和文案弹窗（提交 name / description），
    # 若后者把覆盖整体替换掉，用户改个简介就会把之前用画布改的节点图冲回出厂。
    data = _load()
    items = data.setdefault("items", {})
    existing = items.get(tid) or {}
    merged = {k: v for k, v in existing.items() if k in _FIELDS}
    merged.update(patch)
    # 大小护栏：defaults / graph 都是用户可填的 JSON，进来前先量体积，
    # 免得一个几十 MB 的图把 project.json 的读取路径拖垮
    payload_text = dumps_bounded(merged)
    if len(payload_text) > 400_000:
        raise ValueError(f"模板内容过大（{len(payload_text) // 1024}KB），上限 400KB")
    # 只存**与出厂不同的字段**（最小补丁）。前端提交的是完整表单，
    # 若把完整表单原样落盘，用户点一次「保存」就等于接管了这个模板的
    # 所有字段，以后代码里更新出厂模板的其它字段都会被这份旧快照盖住。
    diff = {k: v for k, v in merged.items() if base.get(k) != v}
    if not diff:
        # 一个字段都没改 = 等同恢复默认（不留下空覆盖，否则列表永远显示「已修改」）
        items.pop(tid, None)
        data["items"] = items
        _save(data)
        return apply(base)
    diff["_updatedAt"] = time.time()
    items[tid] = diff
    _save(data)
    return apply(base)


def reset_override(tid: str) -> Dict[str, Any]:
    base = get_builtin(tid)
    if base is None:
        raise ValueError("内置模板不存在")
    data = _load()
    items = data.get("items") or {}
    items.pop(tid, None)
    data["items"] = items
    _save(data)
    return apply(base)


def overridden_ids() -> List[str]:
    return list((_load().get("items") or {}).keys())
