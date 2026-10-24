"""拆件管线：角色立绘 → 6 个骨骼部件 + 武器（混合路线）。

入口 `extract()` 把重活交给 `tools/extract_parts.py` 子进程跑：
  - see-through 需要它自己的 venv（torch 2.11 cu128），跟主项目的 torch 2.6 冲突，
    所以不能在同进程 import；只能用子进程。
  - 爆炸图（Qwen-Image-2.1）走主项目 venv，也在同一个子进程里编排。

这样拆的好处：后端只依赖「一个能跑的 CLI + 约定的目录结构」，模型换代/换路径
都不用动后端代码；代价是拿不到逐帧进度，只能阶段上报 + 心跳。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .. import progress as progress_mod

ROOT = Path(__file__).resolve().parents[3]
ROLES = ("head", "torso", "arm_l", "arm_r", "leg_l", "leg_r")
ROLE_CN = {"head": "头", "torso": "躯干", "arm_l": "左臂",
           "arm_r": "右臂", "leg_l": "左腿", "leg_r": "右腿"}


def _work_dir(project_id: str) -> Path:
    from .. import storage

    d = storage.project_dir(project_id) / "parts-work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_tool(ref: Path, out: Path, *, resolution: int, with_head: bool,
              seed: int, force: bool) -> subprocess.CompletedProcess:
    cmd = [sys.executable, str(ROOT / "tools" / "extract_parts.py"),
           "--ref", str(ref), "--out", str(out),
           "--resolution", str(resolution), "--seed", str(seed)]
    if not with_head:
        cmd.append("--no-head")
    if force:
        cmd.append("--force")
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2400, env=env)


async def extract(
    project_id: str,
    image,
    *,
    resolution: int = 1280,
    with_head: bool = True,
    seed: int = 7,
    task_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """拆件并落盘到项目资产，返回 {parts: {role: assetInfo}, weapon, meta}。"""
    from .. import storage

    work = _work_dir(project_id)
    ref = work / "ref.png"
    image.convert("RGBA").save(ref)

    progress_mod.report(task_id, 0, 0,
                        "拆件中：see-through 分层（约 3 分钟）…" if with_head
                        else "拆件中：see-through 分层（约 3 分钟）…")
    t0 = time.time()
    r = await asyncio.to_thread(_run_tool, ref, work, resolution=resolution,
                                with_head=with_head, seed=seed, force=force)
    dt = time.time() - t0
    if r.returncode != 0 or not (work / "parts.json").exists():
        tail = ((r.stdout or "")[-700:] + (r.stderr or "")[-700:]).strip()
        raise RuntimeError(f"拆件失败（{dt:.0f}s）：{tail or '无输出'}")

    meta_src = json.loads((work / "parts.json").read_text(encoding="utf-8"))
    roles = meta_src.get("roles") or {}
    missing = [ROLE_CN[x] for x in ROLES if x not in roles]
    if missing:
        raise RuntimeError(f"拆件结果缺件：{'、'.join(missing)}")

    from PIL import Image as _I

    parts: Dict[str, Dict[str, Any]] = {}
    for role in ROLES:
        im = _I.open(work / roles[role]["file"]).convert("RGBA")
        info = storage.save_image(project_id, im, kind="images", prefix=f"part-{role}")
        parts[role] = {"assetId": info["id"], "url": info["url"],
                       "width": info["width"], "height": info["height"], "label": ROLE_CN[role]}

    weapon = None
    wpath = work / "weapon.png"
    if wpath.exists():
        im = _I.open(wpath).convert("RGBA")
        info = storage.save_image(project_id, im, kind="images", prefix="part-weapon")
        weapon = {"assetId": info["id"], "url": info["url"],
                  "width": info["width"], "height": info["height"], "label": "武器/道具"}

    progress_mod.report(task_id, 1, 1, "拆件完成")
    return {
        "parts": parts,
        "weapon": weapon,
        "source": meta_src.get("source"),
        "workDir": str(work),
        "meta": {"seconds": round(dt, 1), "resolution": resolution,
                 "withHead": with_head, "roles": list(parts.keys()),
                 "source": meta_src.get("source")},
    }


def parts_dir_of(project_id: str) -> Optional[Path]:
    """返回已完成拆件的工作目录（下游渲染直接读它）。"""
    d = _work_dir(project_id)
    return d if (d / "parts.json").exists() else None
