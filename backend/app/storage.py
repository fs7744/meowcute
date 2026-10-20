"""项目内资产落盘：图片按 项目/类型/<前缀-时间-序号>.png 存，文件名即资产 id。

为什么不用哈希文件名：同一张图可能被精修多次、被不同节点引用，哈希会把
「内容相同」和「同一个资产」混为一谈；时间戳文件名让「一次产出 = 一个资产」
这件事在文件系统层面就成立，而且用户翻项目文件夹时能直接看出先后顺序。
sha256 仍写进 meta 便于溯源。

整个项目的输入（refs/）与产出（images/）都在同一个文件夹里，
project.json 记录画布上每个节点的输入参数与产出引用 —— 关掉浏览器再打开，
原位继续改。
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from PIL import Image

from .config import PROJECTS_DIR, ensure_dirs

ALLOWED_KINDS = {"images", "refs", "thumbs"}

# 文件名前缀：out=生成原图，px=像素精修，ref=参考图输入
SAFE_PREFIX = re.compile(r"[^a-z0-9_-]")



def project_dir(pid: str) -> Path:
    # 防目录穿越：只允许字母数字与 - _
    safe = "".join(c for c in pid if c.isalnum() or c in "-_")
    if safe != pid or not safe:
        raise ValueError("非法项目 id")
    return PROJECTS_DIR / safe


def assets_dir(pid: str, kind: str = "images") -> Path:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"非法资产类型：{kind}")
    d = project_dir(pid) / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_asset_id(prefix: str = "a") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def asset_stem(prefix: str = "out", seq: Optional[int] = None) -> str:
    """可读的文件名主干：<前缀>-<年月日>-<时分秒>[-<序号>]-<4位随机>。

    例：out-20261020-210533-1-a1b2.png
    序号让同一次生成的多张图在文件夹里天然按 1/2/3/4 排好。
    """
    p = SAFE_PREFIX.sub("", (prefix or "out").lower())[:12] or "out"
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    tail = uuid.uuid4().hex[:4]
    mid = f"-{int(seq)}" if seq is not None else ""
    return f"{p}-{ts}{mid}-{tail}"


def save_image(pid: str, img: Image.Image, kind: str = "images",
               asset_id: Optional[str] = None, prefix: str = "out",
               seq: Optional[int] = None) -> Dict[str, Any]:
    """保存一张图，返回 {id, file, url, width, height, sha256, bytes}。

    PNG 无损：像素图的色块边界经不起 JPEG 压缩的振铃。
    """
    ensure_dirs()
    aid = asset_id or asset_stem(prefix, seq)
    d = assets_dir(pid, kind)
    path = d / f"{aid}.png"
    img.save(path, "PNG", optimize=True)
    raw = path.read_bytes()
    return {
        "id": aid,
        "file": path.name,
        "url": f"/api/projects/{pid}/{kind}/{path.name}",
        "width": img.width,
        "height": img.height,
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
        "bytes": len(raw),
    }


def asset_path(pid: str, kind: str, filename: str) -> Optional[Path]:
    """解析资产文件路径（挡路径穿越）。"""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    if not filename.lower().endswith(".png"):
        return None
    p = assets_dir(pid, kind) / filename
    return p if p.is_file() else None


def load_image(pid: str, kind: str, filename: str) -> Optional[Image.Image]:
    p = asset_path(pid, kind, filename)
    if p is None:
        return None
    try:
        return Image.open(p).convert("RGBA")
    except Exception:
        return None


def delete_asset(pid: str, kind: str, filename: str) -> bool:
    p = asset_path(pid, kind, filename)
    if p is None:
        return False
    try:
        os.remove(p)
        return True
    except Exception:
        return False


def project_size_mb(pid: str) -> float:
    total = 0
    root = project_dir(pid)
    if not root.is_dir():
        return 0.0
    for f in root.rglob("*"):
        if f.is_file():
            try:
                total += f.stat().st_size
            except Exception:
                pass
    return round(total / 1024**2, 2)


# --------------------------------------------------------------------------- #
# 项目文件夹：说明文件 + 清单 + 在系统里打开
# --------------------------------------------------------------------------- #

README_NAME = "README.txt"

_README = """\
项目：{name}
模板：{template}
创建：{created}

这个文件夹就是项目的全部内容，关掉程序再打开也不会丢：

  project.json   画布状态。每个生成卡的文字描述、参考图引用、尺寸/张数/
                 背景/画风/像素参数，以及每个结果卡的图片引用和元数据都在这里；
                 还有画布缩放平移位置，所以重新打开会回到你离开的地方。

  refs/          输入。你上传的参考图，原样保留。
  images/        产出。模型生成的原图（out-*.png）、像素精修后的成品
                 （px-*.png）、扣掉背景的素材（bg-*.png）。
                 同名不同序号对应同一次生成的多张。

文件名里的时间戳就是生成时刻，例如 out-20261020-210533-2-a1b2.png
表示 2026-10-20 21:05:33 那次生成的第二张原图。

想换机器或备份：整个文件夹拷走即可，路径无关。
"""


def write_readme(pid: str, name: str, template_id: str = "", created: float = 0.0) -> None:
    """在项目根目录放一份人话版说明书，用户翻文件夹时不用猜。"""
    d = project_dir(pid)
    d.mkdir(parents=True, exist_ok=True)
    when = datetime.fromtimestamp(created).strftime("%Y-%m-%d %H:%M") if created else "-"
    text = _README.format(name=name or "未命名项目", template=template_id or "-", created=when)
    try:
        (d / README_NAME).write_text(text, encoding="utf-8")
    except Exception:
        pass  # 说明书是附赠品，写不进去不该让建项目失败


def project_files(pid: str) -> Dict[str, Any]:
    """项目文件夹清单：给前端的「项目文件夹」面板用。"""
    root = project_dir(pid)
    if not root.is_dir():
        return {"root": str(root), "files": [], "counts": {}, "sizeMB": 0.0}

    files = []
    counts = {"images": 0, "refs": 0, "other": 0}
    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(root).as_posix()
        top = rel.split("/")[0] if "/" in rel else "other"
        key = top if top in ("images", "refs") else "other"
        counts[key] += 1
        try:
            st = f.stat()
            files.append({
                "path": rel,
                "kind": key,
                "bytes": st.st_size,
                "mtime": round(st.st_mtime, 1),
            })
        except Exception:
            continue

    return {
        "root": str(root),
        "files": files,
        "counts": counts,
        "sizeMB": project_size_mb(pid),
    }


def reveal_in_os(pid: str) -> bool:
    """在系统文件管理器里打开项目文件夹（本地工具，走本机命令）。"""
    root = project_dir(pid)
    if not root.is_dir():
        return False
    try:
        if os.name == "nt":
            os.startfile(str(root))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(root)])
        else:
            subprocess.Popen(["xdg-open", str(root)])
        return True
    except Exception:
        return False

