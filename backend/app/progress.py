"""任务进度：SSE 推送「第 x/y 步 + 已用时长」，可随时取消。

生成是分钟级操作，转圈只说明「没死」，步数才说明「还要多久」。
取消靠 diffusers 的 callback_on_step_end 每步抛 CancelledError 中止推理。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Optional


class CancelledError(Exception):
    """用户在等待中点了取消。"""


class TaskProgress:
    __slots__ = ("task_id", "total", "done", "label", "created", "cancelled", "done_at", "error")

    def __init__(self, task_id: str, total: int = 0, label: str = ""):
        self.task_id = task_id
        self.total = int(total)
        self.done = 0
        self.label = label
        self.created = time.time()
        self.cancelled = False
        self.done_at: Optional[float] = None
        self.error: Optional[str] = None

    def snapshot(self) -> Dict[str, Any]:
        return {
            "taskId": self.task_id,
            "done": self.done,
            "total": self.total,
            "label": self.label,
            "elapsed": round(time.time() - self.created, 1),
            "cancelled": self.cancelled,
            "doneAt": self.done_at,
            "error": self.error,
        }


_TASKS: Dict[str, TaskProgress] = {}
_WAITERS: Dict[str, list] = {}


def create(task_id: str, total: int = 0, label: str = "") -> TaskProgress:
    t = TaskProgress(task_id, total, label)
    _TASKS[task_id] = t
    return t


def get(task_id: str) -> Optional[TaskProgress]:
    return _TASKS.get(task_id)


def report(task_id: Optional[str], done: int, total: int, label: str = "") -> None:
    if not task_id:
        return
    t = _TASKS.get(task_id)
    if t is None:
        return
    t.done, t.total, t.label = int(done), int(total), label
    for q in _WAITERS.get(task_id, []):
        try:
            q.put_nowait(t.snapshot())
        except Exception:
            pass


def check_cancel(task_id: Optional[str]) -> None:
    if not task_id:
        return
    t = _TASKS.get(task_id)
    if t is not None and t.cancelled:
        raise CancelledError("任务已取消")


def cancel(task_id: str) -> bool:
    t = _TASKS.get(task_id)
    if t is None:
        return False
    t.cancelled = True
    for q in _WAITERS.get(task_id, []):
        try:
            q.put_nowait(t.snapshot())
        except Exception:
            pass
    return True


def finish(task_id: Optional[str], error: Optional[str] = None) -> None:
    if not task_id:
        return
    t = _TASKS.get(task_id)
    if t is None:
        return
    t.done_at = time.time()
    t.error = error
    for q in _WAITERS.get(task_id, []):
        try:
            q.put_nowait(t.snapshot())
        except Exception:
            pass


def subscribe(task_id: str) -> "asyncio.Queue":
    q: "asyncio.Queue" = asyncio.Queue()
    _WAITERS.setdefault(task_id, []).append(q)
    return q


def unsubscribe(task_id: str, q: "asyncio.Queue") -> None:
    lst = _WAITERS.get(task_id)
    if lst and q in lst:
        lst.remove(q)
    if lst is not None and not lst:
        _WAITERS.pop(task_id, None)


def make_step_callback(task_id: Optional[str], total: int, label: str = ""):
    """diffusers callback_on_step_end：每步结束报一次进度 + 检查取消。"""
    def _cb(pipe, step_index, timestep, callback_kwargs):
        check_cancel(task_id)
        report(task_id, step_index + 1, total, label or f"第 {step_index + 1}/{total} 步")
        return callback_kwargs
    return _cb


def cleanup(max_age: float = 3600.0) -> None:
    """清掉一小时前的已完成任务，防 _TASKS 无限增长。"""
    now = time.time()
    for tid in [t for t, v in _TASKS.items()
                if v.done_at is not None and now - v.done_at > max_age]:
        _TASKS.pop(tid, None)
        _WAITERS.pop(tid, None)


def ids() -> list:
    """当前还留着的任务 id（供上层清理自己那份结果缓存）。"""
    return list(_TASKS)
