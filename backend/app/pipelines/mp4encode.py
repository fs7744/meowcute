"""帧序列 → H.264 MP4（imageio-ffmpeg 自带静态 ffmpeg）。

为什么单独一个模块：系统 PATH 里没有 ffmpeg（这是 GIF 长期作为视频默认
格式的根因），而浏览器可播的 MP4 需要 H.264 + yuv420p——cv2 的 VideoWriter
只有 MPEG-4 Part 2（Chrome/Firefox 基本不放）。imageio-ffmpeg 是 pypi 上
约 30MB 的静态 ffmpeg 发行包，libx264 编译在内。

编码参数的选择：
- yuv420p：Chrome/Safari/Firefox 全兼容的像素格式（4:2:0 无 alpha）。
  GIF 有透明区，但视频不该有——MP4 场景是「最终成片」，alpha 交给抠图链。
- +faststart：moov 元数据前移到文件头，浏览器边下边播（不然要等整个文件）。
- crf 20：默认画质档。视频底座的输出本身带压缩伪影，再高 crf 会叠一层块噪；
  20 是肉眼无损与文件体积的平衡点（实测 25 帧 848×480 约 0.3-0.8MB）。
- 偶数尺寸：libx264 要求宽高为偶数（yuv420p 2×2 块），奇数会直接报错；
  H.264 Level 4.0 上限 1920×1080，超出先缩（项目分辨率远低于此，防呆）。
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def ffmpeg_exe() -> str:
    """静态 ffmpeg 路径；没装 imageio-ffmpeg 时抛可读错误。"""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if os.path.exists(exe):
            return exe
    except Exception as e:  # noqa: BLE001
        raise RuntimeError(
            "MP4 编码需要 imageio-ffmpeg（pypi 静态 ffmpeg 发行包）。"
            "安装：pip install imageio-ffmpeg") from e
    raise RuntimeError("imageio-ffmpeg 的 ffmpeg 二进制缺失，请重装该包")


def encode_mp4(
    frames: List[Any],
    *,
    fps: int = 24,
    crf: int = 20,
    max_side: int = 1920,
    dest: Optional[Path] = None,
) -> Tuple[Path, Dict[str, Any]]:
    """PIL 帧列表 → H.264 MP4 文件。返回 (路径, meta)。

    - 尺寸取第一帧（帧序列必须同尺寸；视频底座输出恒等，防呆只查首帧）；
    - 奇数尺寸向下取偶（libx264 硬约束）；
    - 大尺寸先缩到 max_side 内（Level 4.0 上限防呆）；
    - dest=None 时写系统临时目录（调用方负责搬走/删）。
    """
    if not frames:
        raise ValueError("encode_mp4 需要至少一帧")
    exe = ffmpeg_exe()
    w, h = frames[0].width, frames[0].height
    scale = 1.0
    if max(w, h) > max_side:
        scale = max_side / max(w, h)
        w, h = int(w * scale), int(h * scale)
    w, h = max(2, w // 2 * 2), max(2, h // 2 * 2)

    from PIL import Image

    pipe_frames = []
    for f in frames:
        im = f.convert("RGB")
        if (im.width, im.height) != (w, h):
            im = im.resize((w, h), Image.LANCZOS)
        pipe_frames.append(im)

    out = Path(dest) if dest else Path(tempfile.mkdtemp()) / "video.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        exe, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24",
        "-s", f"{w}x{h}", "-r", str(max(1, int(fps))), "-i", "-",
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        "-crf", str(max(0, min(51, int(crf)))),
        str(out),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for im in pipe_frames:
        proc.stdin.write(im.tobytes())
    proc.stdin.close()
    err = proc.stderr.read().decode("utf-8", "replace")[-400:]
    code = proc.wait()
    if code != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg 编码失败（exit {code}）：{err}")
    return out, {
        "frames": len(pipe_frames), "fps": int(fps), "size": [w, h],
        "bytes": out.stat().st_size,
    }
