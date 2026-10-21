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
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .config import PROJECTS_DIR, ensure_dirs

ALLOWED_KINDS = {"images", "refs", "thumbs"}

# 文件名前缀：out=生成原图，px=像素精修，ref=参考图输入
SAFE_PREFIX = re.compile(r"[^a-z0-9_-]")

# 视频资产扩展名：GIF 走 PIL，其余走 OpenCV（见 load_video_frames）
VIDEO_EXTS = (".gif", ".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v")
VIDEO_MIME = {
    ".gif": "image/gif", ".mp4": "video/mp4", ".mov": "video/quicktime",
    ".avi": "video/x-msvideo", ".webm": "video/webm",
    ".mkv": "video/x-matroska", ".m4v": "video/mp4",
}
# 上传视频大小上限：行走循环不需要长视频，100MB 足够挡误操作
MAX_VIDEO_BYTES = 100 * 1024 * 1024



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


def save_gif(pid: str, frames: List[Image.Image], kind: str = "images",
             asset_id: Optional[str] = None, prefix: str = "walk",
             fps: int = 24) -> Dict[str, Any]:
    """把一段帧序列存成动画 GIF，返回同 save_image 的信息 + frames/fps。

    行走视频的中间产物用 GIF 而不是逐帧 PNG：81 帧 × 8 方向存成 PNG 是
    648 个文件，翻项目文件夹时全是噪音；GIF 一个方向一个文件，浏览器
    <img> 原生就能播。RGB 调色板量化在这一步是可以接受的——视频帧还要
    经过「序列帧」节点的抠图与重采样，最终精度由精灵整图节点决定。

    注意 PIL 的 GIF 写入会**合并连续完全相同的帧**（只写变化区域）：
    Wan 输出开头 / 结尾常有几帧不变，合并后文件更小、循环里也不会出现
    同姿势的重复帧——正是序列帧节点想去重做的事。读回来的帧数可能少于
    写入的帧数，下游按实际帧数处理即可。
    """
    ensure_dirs()
    if not frames:
        raise ValueError("没有可保存的帧")
    aid = asset_id or asset_stem(prefix)
    d = assets_dir(pid, kind)
    path = d / f"{aid}.gif"
    duration = max(20, int(round(1000.0 / max(1, int(fps)))))
    rgb = [f.convert("RGB") for f in frames]
    rgb[0].save(
        path, "GIF", save_all=True, append_images=rgb[1:],
        duration=duration, loop=0, disposal=2, optimize=True,
    )
    raw = path.read_bytes()
    return {
        "id": aid,
        "file": path.name,
        "url": f"/api/projects/{pid}/{kind}/{path.name}",
        "width": rgb[0].width,
        "height": rgb[0].height,
        "sha256": hashlib.sha256(raw).hexdigest()[:16],
        "bytes": len(raw),
        "frames": len(rgb),
        "fps": int(fps),
    }


def load_gif(pid: str, kind: str, filename: str
             ) -> Tuple[List[Image.Image], List[int], Dict[str, Any]]:
    """读一个动画 GIF 的全部帧（RGBA）+ 每帧时长(ms)。

    与 load_image 分开：那个会把动画压成第一帧（convert("RGBA") 只取当前
    帧）。序列帧节点重跑时要的是整段视频。

    帧和时长在**同一次遍历**里取：81 帧 384×512 的 GIF 解码一次约 0.3s，
    原来分开开两次文件是把这段开销翻倍，而序列帧节点每次重跑都要读一遍。
    """
    p = asset_path(pid, kind, filename)
    if p is None or not filename.lower().endswith(".gif"):
        return [], [], {}
    try:
        from PIL import ImageSequence

        frames: List[Image.Image] = []
        durations: List[int] = []
        with Image.open(p) as img:
            for f in ImageSequence.Iterator(img):
                frames.append(f.convert("RGBA"))
                durations.append(max(1, int(f.info.get("duration", 100))))
            meta = {"frames": len(frames), "size": [img.width, img.height],
                    "loop": img.info.get("loop", 0)}
        return frames, durations, meta
    except Exception:
        return [], [], {}


def load_video_frames(pid: str, kind: str, filename: str,
                      max_frames: int = 600, max_side: int = 1024
                      ) -> Tuple[List[Image.Image], float, Dict[str, Any]]:
    """读一个视频文件的全部帧（RGBA）+ 帧率。

    GIF 用 PIL（和 load_gif 同一条路，帧率取平均帧间隔的倒数）；其余格式
    （mp4/mov/webm…）走 OpenCV 的 VideoCapture——venv 里 cv2 是随 GPU
    依赖一起装的，没装时返回空列表让调用方给清楚提示。

    行走循环一般 24~60fps、几秒长，max_frames 600 足够；超长视频截断而不是
    报错——序列帧节点后面本来就会抽稀。

    max_side：解码时就限制长边。手机拍的 1080×1920 视频按 RGBA 存 600 帧
    要 5GB 内存——节点最终只用 384×512，先缩再存，内存和后续抠图都省。
    """
    p = asset_path(pid, kind, filename)
    if p is None:
        return [], 0.0, {}
    low = filename.lower()
    try:
        if low.endswith(".gif"):
            frames, durations, meta = load_gif(pid, kind, filename)
            if not frames:
                return [], 0.0, {}
            avg = max(1.0, sum(durations) / len(durations))
            return frames, round(1000.0 / avg, 3), {"frames": len(frames),
                                                    "source": "gif"}
        import cv2
        cap = cv2.VideoCapture(str(p))
        if not cap.isOpened():
            return [], 0.0, {}
        fps = cap.get(cv2.CAP_PROP_FPS) or 24.0
        frames: List[Image.Image] = []
        scaled = False
        while len(frames) < max_frames:
            ok, frame = cap.read()
            if not ok:
                break
            # cv2 是 BGR；alpha 通道没有，直接转 RGB
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(rgb)
            if max_side and max(img.size) > max_side:
                # 解码即缩：避免整段 1080p 帧堆在内存里（见 docstring）
                img.thumbnail((max_side, max_side), Image.LANCZOS)
                scaled = True
            frames.append(img.convert("RGBA"))
        cap.release()
        if not frames:
            return [], 0.0, {}
        return frames, round(float(fps), 3), {"frames": len(frames),
                                              "source": "opencv",
                                              "preScaled": scaled}
    except ImportError:
        return [], 0.0, {"error": "缺 opencv-python：读取 mp4 等视频需要它（pip install opencv-python）"}
    except Exception as e:
        return [], 0.0, {"error": f"视频解码失败：{e}"}


def save_video_stream(pid: str, src, suffix: str, kind: str = "images",
                      prefix: str = "vidsrc") -> Dict[str, Any]:
    """把上传流直接写进项目（分块，不整包读进内存）。

    100MB 上限的视频如果先 read() 再 write_bytes()，内存和磁盘各要过两遍；
    直接按 1MB 分块落盘，峰值内存只有一块。
    """
    ensure_dirs()
    d = assets_dir(pid, kind)
    aid = asset_stem(prefix)
    path = d / f"{aid}{suffix.lower()}"
    total = 0
    try:
        with open(path, "wb") as out:
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_VIDEO_BYTES:
                    raise ValueError("视频不能超过 100MB")
                out.write(chunk)
        if total == 0:
            raise ValueError("文件是空的")
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {
        "id": aid,
        "file": path.name,
        "url": f"/api/projects/{pid}/{kind}/{path.name}",
        "bytes": total,
    }


def list_videos(pid: str, kind: str = "images") -> List[Dict[str, Any]]:
    """列出项目里的视频资产：以前生成的 walk-*.gif + 上传的 vidsrc-*。

    行走视频节点「选择现成视频」用：自己历史生成的片段和外部导入的片段
    在同一个列表里，文件名带方向信息（walk-down-…）的直接标出来。
    """
    d = assets_dir(pid, kind)
    if not d.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for f in sorted(d.iterdir(), key=lambda x: x.stat().st_mtime, reverse=True):
        if not f.is_file() or f.suffix.lower() not in VIDEO_EXTS:
            continue
        stem = f.stem
        if stem.startswith("walk-"):
            parts = stem.split("-")
            src_dir = parts[1] if len(parts) > 1 else None
            origin = "generated"
        elif stem.startswith("vidsrc-"):
            src_dir = None
            origin = "upload"
        else:
            continue
        st = f.stat()
        out.append({
            "file": f.name,
            "url": f"/api/projects/{pid}/{kind}/{f.name}",
            "origin": origin,
            "dir": src_dir,
            "bytes": st.st_size,
            "mtime": st.st_mtime,
        })
    return out


def asset_path(pid: str, kind: str, filename: str) -> Optional[Path]:
    """解析资产文件路径（挡路径穿越）。"""
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        return None
    low = filename.lower()
    if not (low.endswith(".png") or low.endswith(VIDEO_EXTS)):
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

