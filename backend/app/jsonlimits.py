"""有界 JSON 校验：挡 NaN、Infinity 与形状炸弹。

项目文件（project.json / prompt_examples.json）都是用户可编辑的本地文件，
损坏或恶意构造的 JSON 不能让服务崩溃，也不能把内存吃穿。
"""
from __future__ import annotations

import math
from typing import Any

MAX_DEPTH = 32
MAX_NODES = 1_600_000


class BoundedJSONError(ValueError):
    pass


def _check(node: Any, depth: int, counter: List[int]) -> None:
    counter[0] += 1
    if counter[0] > MAX_NODES:
        raise BoundedJSONError(f"JSON 节点数超过上限 {MAX_NODES}")
    if depth > MAX_DEPTH:
        raise BoundedJSONError(f"JSON 嵌套深度超过上限 {MAX_DEPTH}")
    if isinstance(node, float):
        if math.isnan(node) or math.isinf(node):
            raise BoundedJSONError("JSON 含 NaN / Infinity")
    elif isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                raise BoundedJSONError("JSON 对象键必须是字符串")
            _check(v, depth + 1, counter)
    elif isinstance(node, (list, tuple)):
        for v in node:
            _check(v, depth + 1, counter)


def parse_bounded(text: str) -> Any:
    """读项目文件用这个，不用裸 json.loads。"""
    import json

    data = json.loads(text, parse_constant=lambda c: (_ for _ in ()).throw(
        BoundedJSONError(f"JSON 含非法常量 {c}")))
    _check(data, 0, [0])
    return data


def dumps_bounded(obj: Any) -> str:
    """写文件用这个：序列化前先过一遍检查。"""
    import json

    _check(obj, 0, [0])
    return json.dumps(obj, ensure_ascii=False, indent=1, allow_nan=False)
