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
    __slots__ = ("task_id", "total", "done", "label", "created", "cancelled",
                 "done_at", "error", "updated_at", "partial")

    def __init__(self, task_id: str, total: int = 0, label: str = ""):
        self.task_id = task_id
        self.total = int(total)
        self.done = 0
        self.label = label
        self.created = time.time()
        self.updated_at = self.created
        self.cancelled = False
        self.done_at: Optional[float] = None
        self.error: Optional[str] = None
        # 已完成的分片结果（逐帧落盘的任务会往这里追加）。
        # 为什么需要：分钟级的批量任务如果只在结束时才给结果，跑到第 10 帧崩了
        # 就等于白等 4 分钟；而且界面上「转圈 4 分钟什么都看不到」体验很差。
        self.partial: List[Dict[str, Any]] = []

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
            "partial": self.partial or None,
        }


_TASKS: Dict[str, TaskProgress] = {}
_WAITERS: Dict[str, list] = {}


def create(task_id: str, total: int = 0, label: str = "") -> TaskProgress:
    t = TaskProgress(task_id, total, label)
    _TASKS[task_id] = t
    return t


def get(task_id: str) -> Optional[TaskProgress]:
    return _TASKS.get(task_id)


def quiet_seconds(task_id: Optional[str]) -> Optional[float]:
    """距上一次上报过了多少秒（任务不存在返回 None）。

    用途：模型加载阶段没有任何 diffusers 回调，界面会静默几十秒到几分钟。
    心跳只在「静默 > 3s」时才覆盖 label，正常推理的步进不会被它打扰。
    """
    if not task_id:
        return None
    t = _TASKS.get(task_id)
    return None if t is None else time.time() - t.updated_at


def report(task_id: Optional[str], done: int, total: int, label: str = "") -> None:
    if not task_id:
        return
    t = _TASKS.get(task_id)
    if t is None:
        return
    t.done, t.total, t.label = int(done), int(total), label
    t.updated_at = time.time()
    for q in _WAITERS.get(task_id, []):
        try:
            q.put_nowait(t.snapshot())
        except Exception:
            pass


def push_partial(task_id: Optional[str], item: Dict[str, Any]) -> None:
    """追加一个「已完成的分片」，并立刻推给订阅者。

    用于分钟级的逐帧任务：每帧落盘后调用，界面能边跑边看到结果，
    中途取消/失败也不会丢已经跑出来的那部分。
    """
    if not task_id:
        return
    t = _TASKS.get(task_id)
    if t is None:
        return
    t.partial.append(item)
    t.updated_at = time.time()
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


def make_step_callback(task_id: Optional[str], total: int, label: str = "",
                       base: int = 0, grand: int = 0, hold: bool = False):
    """diffusers callback_on_step_end：每步结束报一次进度 + 检查取消。

    base/grand 用来把「一批里的第 k 张」映射到整批的进度条上：批量跑 4 张
    40 步时，第 2 张从 40 报到 80（grand=160），进度条一路向前，不会每张
    图都从 0 冲到 100% 再退回去。
    """
    def _cb(pipe, step_index, timestep, callback_kwargs):
        check_cancel(task_id)
        if hold:
            # 子步骤（一个方向的一段视频）内部不推进整体进度，只把步数写进
            # label：进度条停在「这个方向开始」的位置，但文字每步都在动。
            # 以前这里直接报 base+step/use_steps，整体 total 会被撞成 24，
            # 进度条从 50% 掉到 4%，用户以为任务重来了。
            report(task_id, base, grand or total,
                   f"{label} {step_index + 1}/{total} 步" if label
                   else f"第 {step_index + 1}/{total} 步")
        else:
            report(task_id, base + step_index + 1, grand or total,
                   label or f"第 {step_index + 1}/{total} 步")
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


def has_running() -> bool:
    """是否还有没结束的任务（空闲卸载前必须先确认，中途卸模型会打挂任务）。"""
    return any(v.done_at is None for v in _TASKS.values())


def count() -> int:
    """留着的任务条数（上层据此决定要不要跑清理）。"""
    return len(_TASKS)
