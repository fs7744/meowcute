"""2.5D 分层模型（See-Through）：静态动漫图 → 可操作的分层 PSD。

See-Through（shitagaki-lab，arXiv 2602.03749）把一张静态动漫立绘拆成
**Live2D 语义层**（face / topwear / handwear-l / hair…），每层都补全了被
遮挡的区域（这是它最值钱的地方：层一旦分开摆动，不会露出白洞），并用
单目深度排出绘制顺序。专业工作流里这一步靠人工分割 + 补画，一个角色
要几小时。

本管线不重造轮子：see-through 本体已装在机器上（独立 venv，torch 2.8+cu128，
与主项目 torch 2.6 不兼容），这里只做三件事：

  1. 用它的 venv 起子进程跑 tools/see_through_export.py（拿分层 PSD + 深度）
  2. 把产物收进项目目录，并把每层的画布坐标 / 深度 / 绘制顺序整理成 manifest
  3. 派生一份「摆动建议」（哪层该晃、晃多少），让前端预览器能直接动起来

代价：一次要 2~3 分钟（1280px / 30 步），所以走任务制 + 阶段进度。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import progress as progress_mod

ROOT = Path(__file__).resolve().parents[3]

# see-through 仓库根目录：机器上那份；可用环境变量改（与 tools/extract_parts.py 同款）
ST_DIR = Path(os.environ.get(
    "MEOWCUTE_SEETHROUGH_DIR", r"D:/code/gen/others/see-through-main"))

# 阶段 → 进度百分比（子进程只报阶段名，百分比由这里映射）
_STAGE_PCT = {
    "加载 LayerDiff": 5,
    "图层分解中": 12,
    "深度估计中": 60,
    "后处理": 72,
    "左右拆分": 80,
    "整理图层清单": 90,
    "完成": 100,
}

# 层语义 → 摆动幅度建议（0=不动，1=晃得最厉害）
# 依据是 Live2D 的常规做法：头发/五官/配件是「活」的，身体大块面几乎不动。
_SWING = [
    (("hair", "hairf", "hairb"), 0.9),
    (("eyewhite", "irides", "eyelash", "eyebrow", "eyes", "eyer", "eyel",
      "browr", "browl"), 0.7),
    (("mouth", "nose"), 0.6),
    (("ears", "earl", "earr"), 0.6),
    (("headwear", "helmet", "eyewear", "earwear", "neckwear", "tail", "wings"), 0.55),
    (("objects", "weapon", "accessory", "accessories"), 0.45),
    (("neck",), 0.3),
    (("topwear", "outerwear", "innerwear"), 0.22),
    (("bottomwear",), 0.18),
    (("legwear", "footwear", "shoes", "socks"), 0.12),
]


def _swing_for(name: str) -> float:
    n = (name or "").lower()
    for keys, v in _SWING:
        if any(k in n for k in keys):
            return v
    return 0.3


def st_dir() -> Path:
    return ST_DIR


def st_python() -> Optional[Path]:
    """see-through 的独立 venv 解释器。找不到就返回 None（后端给明确提示）。"""
    for rel in (".venv/Scripts/python.exe", ".venv/bin/python",
                "venv/Scripts/python.exe"):
        p = ST_DIR / rel
        if p.is_file():
            return p
    return None


def available() -> Dict[str, Any]:
    """运行环境体检：前端在按钮上直接显示「能不能用 / 缺什么」。"""
    py = st_python()
    models = ST_DIR / "models"
    missing: List[str] = []
    if not ST_DIR.is_dir():
        missing.append(f"see-through 仓库不在 {ST_DIR}")
    else:
        if py is None:
            missing.append("see-through 的独立 venv 不存在（.venv/Scripts/python.exe）")
        for name in ("layerdiff3d", "marigold"):
            if not (models / name).is_dir():
                missing.append(f"缺少模型权重 models/{name}")
    return {
        "ok": not missing,
        "dir": str(ST_DIR),
        "python": str(py) if py else "",
        "missing": missing,
    }


def _work_dir(project_id: str) -> Path:
    from .. import storage

    d = storage.project_dir(project_id) / "layer25d-work"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _out_dir(project_id: str) -> Path:
    from .. import storage

    d = storage.exports_dir(project_id) / "layer25d"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def run_layer25d(
    project_id: str,
    src: Path,
    *,
    resolution: int = 1024,
    steps: int = 30,
    seed: int = 42,
    lrsplit: bool = True,
    task_id: str = "",
) -> Dict[str, Any]:
    """跑一次分层，产物落在 exports/layer25d/，返回 manifest。"""
    env_info = available()
    if not env_info["ok"]:
        raise RuntimeError("see-through 不可用：" + "；".join(env_info["missing"]))
    py = st_python()
    assert py is not None

    work = _work_dir(project_id)
    out = _out_dir(project_id)
    # 清掉上一次的产物，避免 manifest 里混进旧层
    for p in out.iterdir():
        try:
            shutil.rmtree(p) if p.is_dir() else p.unlink()
        except OSError:
            pass

    cmd = [str(py), str(ROOT / "tools" / "see_through_export.py"),
           "--ref", str(src), "--out", str(work),
           "--resolution", str(resolution), "--steps", str(steps),
           "--seed", str(seed)]
    if lrsplit:
        cmd.append("--lrsplit")

    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("HF_HUB_DISABLE_XET", "1")

    progress_mod.report(task_id, 2, 100, "启动 see-through…")
    proc = await asyncio.create_subprocess_exec(
        *cmd, cwd=str(ST_DIR), env=env,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)

    tail: List[str] = []
    async def _pump() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            tail.append(line)
            if len(tail) > 200:
                del tail[:-200]
            if line.startswith("[stage] "):
                msg = line[len("[stage] "):]
                pct = 5
                for k, v in _STAGE_PCT.items():
                    if msg.startswith(k):
                        pct = v
                        break
                progress_mod.report(task_id, pct, 100, msg)
    pump = asyncio.create_task(_pump())
    rc = await proc.wait()
    await pump

    if rc != 0:
        err = "\n".join(tail[-12:])
        raise RuntimeError(f"see-through 失败（退出码 {rc}）：\n{err}")

    manifest_path = work / "manifest.json"
    if not manifest_path.is_file():
        raise RuntimeError("see-through 没有产出 manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # 收产物：PSD / JSON / 每层 PNG（中间文件不要——几十 MB 的层图 + 深度图）
    for name in ("model.psd", "model.psd.json", "model_depth.psd",
                 "model_lrsplit.psd", "manifest.json", "model_preview.png"):
        p = work / name
        if p.is_file():
            shutil.copy2(p, out / name)
    src_layers = work / "layers"
    dst_layers = out / "layers"
    if src_layers.is_dir():
        if dst_layers.exists():
            shutil.rmtree(dst_layers, ignore_errors=True)
        shutil.copytree(src_layers, dst_layers)
    # 清中间文件（LayerDiff 每层 PNG + 深度图 + optimized/）
    shutil.rmtree(work, ignore_errors=True)

    layers = []
    for l in manifest.get("layers") or []:
        layers.append({
            "name": l.get("name") or "",
            "file": l.get("file") or "",
            "url": f"/api/projects/{project_id}/export/layer25d/layers/{l.get('file')}",
            "width": int(l.get("width") or 0),
            "height": int(l.get("height") or 0),
            "x": int(l.get("x") or 0),
            "y": int(l.get("y") or 0),
            "box": l.get("box") or [0, 0, 0, 0],
            # 深度：0=最前（最上层），1=最后（最底层）
            "depth": l.get("depth"),
            "order": int(l.get("order") or 0),
            # 摆动建议：给前端预览器直接用
            "swing": _swing_for(l.get("name") or ""),
        })

    result = {
        **manifest,
        "layers": layers,
        "psdUrl": f"/api/projects/{project_id}/export/layer25d/model.psd",
        "psdJsonUrl": f"/api/projects/{project_id}/export/layer25d/model.psd.json",
        "depthPsdUrl": (f"/api/projects/{project_id}/export/layer25d/model_depth.psd"
                        if manifest.get("depthPsd") else None),
        "lrsplitPsdUrl": (f"/api/projects/{project_id}/export/layer25d/model_lrsplit.psd"
                          if manifest.get("lrsplitPsd") else None),
        "manifestUrl": f"/api/projects/{project_id}/export/layer25d/manifest.json",
        # 合成预览（所有层叠起来的那张）：下游「图片结果」卡显示它
        "previewUrl": (f"/api/projects/{project_id}/export/layer25d/model_preview.png"
                       if (out / "model_preview.png").is_file() else None),
        "bytes": sum(p.stat().st_size for p in out.rglob("*") if p.is_file()),
    }
    progress_mod.report(task_id, 100, 100, "完成")
    return result
