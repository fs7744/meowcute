"""八方向行走 · 阶段二：序列帧（均匀抽帧 + 去重 + 抠图）。

输入是行走视频节点产出的每方向一段帧序列（Wan 81 帧 @ 24fps ≈ 3.4 秒），
输出是每方向一小摞透明底帧（默认 8 帧）——精灵整图节点的直接输入。

三件事各自解决一个真实问题：
1. **均匀抽帧**：81 帧直接进精灵表太密（同一姿势会出现两三次），按目标
   帧数等间距取样，首尾都取——末帧与首帧姿势接近，循环播放时才不会「跳」。
2. **相邻去重**：Wan 生成的视频里常有连续几帧几乎不变（起步/停步阶段），
   循环里放两张同姿势的帧，播起来明显卡一下。判据是降采样后的平均像素差，
   阈值保守——宁可留一张近重复，也不误删真的在动的帧。
3. **抠图**：视频带纯色背景，精灵素材要透明底。抠图走项目统一的
   remove_background（u2net 语义 → grabcut → 颜色键），并移植 threeview 的
   三个修正：铺边距再抠（防踩边肢体被截）、补封闭孔洞（脚掌/手心被误判成
   背景时补回）、边缘溢色清洗（描边外圈的「黑+底色」抗锯齿混色）。

镜像方向在这一阶段不需要特殊处理——帧在行走视频节点已经整段翻过来了。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from . import matte as matte_pipe
from .. import progress as progress_mod
from .walkvideo import DIR_LABELS

# 相邻去重的像素差阈值（32×32 灰度图的平均绝对差）
DEDUP_THRESHOLD = 1.5


# --------------------------------------------------------------------------- #
# 抽帧与去重
# --------------------------------------------------------------------------- #
def sample_frames(frames: List[Image.Image], count: int) -> List[Image.Image]:
    """等间距取 count 帧，首尾都取（末帧姿势接近首帧，循环才顺）。"""
    n = len(frames)
    count = max(1, min(int(count), n))
    if count >= n:
        return list(frames)
    idx = [round(i * (n - 1) / (count - 1)) for i in range(count)]
    # round 可能撞出重复下标（帧数很少时），保序去重
    seen = set()
    out: List[Image.Image] = []
    for i in idx:
        if i in seen:
            continue
        seen.add(i)
        out.append(frames[i])
    return out


def _diff_score(a: Image.Image, b: Image.Image) -> float:
    """两帧的差异分：缩到 32×32 灰度后的平均绝对差。"""
    ga = np.asarray(a.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.int16)
    gb = np.asarray(b.convert("L").resize((32, 32), Image.BILINEAR), dtype=np.int16)
    return float(np.abs(ga - gb).mean())


def sharpness(img: Image.Image) -> float:
    """锐度：灰度图的平均梯度能量（越高越清晰）。

    视频底座偶尔会吐出「糊掉/重影」的帧——整帧被涂抹、边缘发虚，连像素硬边
    都保不住。这种帧进精灵表就是一块糊斑（用户反馈的「像素糊了」「画面闪烁」
    有一半来自这里）。梯度能量对模糊极其敏感，用它把离群帧挑出来丢掉。
    """
    g = np.asarray(img.convert("L").resize((96, 96)), dtype=np.float32)
    gy = np.abs(np.diff(g, axis=0)).mean()
    gx = np.abs(np.diff(g, axis=1)).mean()
    return float(gx + gy)


def reject_blurry(frames: List[Image.Image], ratio: float = 0.55
                  ) -> Tuple[List[Image.Image], int]:
    """剔除明显糊掉/重影的帧，返回 (留下的帧, 丢掉的数量)。

    判据是「锐度低于中位数的 ratio 倍」。用中位数而不是均值：糊帧是少数，
    均值会被它们拖低，中位数稳。留 2 帧保底，避免整段都糊时把视频清空。
    """
    if len(frames) < 4:
        return list(frames), 0
    scores = [sharpness(f) for f in frames]
    med = float(np.median(scores))
    if med <= 0:
        return list(frames), 0
    kept = [f for f, s in zip(frames, scores) if s >= med * ratio]
    if len(kept) < max(2, len(frames) // 4):
        return list(frames), 0          # 丢太多说明整段都糊，别硬删
    return kept, len(frames) - len(kept)


def dedup_frames(frames: List[Image.Image],
                 threshold: float = DEDUP_THRESHOLD) -> Tuple[List[Image.Image], int]:
    """掉掉与前一帧几乎相同的帧，返回 (帧列表, 去掉的帧数)。

    只和**前一帧**比：行走循环是连续运动，和更早的帧比会把「回到相似姿势」
    误判成重复（循环第 8 帧姿势接近第 1 帧是应该的）。
    """
    if len(frames) < 2:
        return list(frames), 0
    out = [frames[0]]
    dropped = 0
    for f in frames[1:]:
        if _diff_score(out[-1], f) < threshold:
            dropped += 1
            continue
        out.append(f)
    # 全掉光了（静态视频）：退回原序列，至少还有帧可用
    if len(out) < 2:
        return list(frames), 0
    return out, dropped


# --------------------------------------------------------------------------- #
# 抠图（移植 threeview 的铺边距 + 补洞 + 去溢色）
# --------------------------------------------------------------------------- #
def _border_bg(img: Image.Image) -> Tuple[int, int, int]:
    """取画面边框上出现最多的颜色作为底色。"""
    arr = np.asarray(img.convert("RGB"))
    border = np.concatenate([
        arr[0, :, :].reshape(-1, 3), arr[-1, :, :].reshape(-1, 3),
        arr[:, 0, :].reshape(-1, 3), arr[:, -1, :].reshape(-1, 3),
    ])
    q = border // 16 * 16
    colors, counts = np.unique(q, axis=0, return_counts=True)
    base = colors[counts.argmax()]
    return tuple(int(v) for v in base + 8)


def _fill_enclosed_holes(cut: Image.Image, max_ratio: float = 0.02
                         ) -> Tuple[Image.Image, int]:
    """补回被角色完全围住的透明孔洞（只改 alpha，RGB 原样）。"""
    a = np.asarray(cut).copy()
    kept = a[:, :, 3] > 8
    holes = (~kept).astype(np.uint8)
    try:
        import cv2
        n, lab = cv2.connectedComponents(holes, connectivity=4)
    except Exception:
        try:
            from scipy import ndimage
        except Exception:
            return cut, 0
        lab, count = ndimage.label(
            holes, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
        n = int(count) + 1
    h, w = kept.shape
    limit = int(max_ratio * h * w)
    filled = 0
    for i in range(1, n):
        comp = lab == i
        ys, xs = np.nonzero(comp)
        if not len(ys):
            continue
        if ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1:
            continue  # 触边 = 外部背景
        area = int(comp.sum())
        if area > limit:
            continue
        a[comp, 3] = 255
        filled += area
    if not filled:
        return cut, 0
    return Image.fromarray(a, "RGBA"), filled


def prepare_frame(img: Image.Image, *, matte: bool = True,
                  tolerance: int = 40, pad_frac: float = 0.12
                  ) -> Tuple[Image.Image, Dict[str, Any]]:
    """一帧 → 透明底 RGBA。matte=False 时只转格式（给调试用）。"""
    work = img.convert("RGBA")
    if not matte:
        return work, {"matte": False}
    w, h = work.size
    p = max(8, int(min(w, h) * pad_frac))
    bg = _border_bg(work)
    canvas = Image.new("RGB", (w + 2 * p, h + 2 * p), bg)
    canvas.paste(work.convert("RGB"), (p, p))
    cut, meta = matte_pipe.remove_background(
        canvas, tolerance=tolerance, feather=0, hard=True,
        method="auto", despill=True)
    cut = cut.crop((p, p, p + w, p + h))
    cut = matte_pipe.drop_bg_islands(cut)
    cut, filled = _fill_enclosed_holes(cut)
    cut, recolored = matte_pipe.clean_edge_spill(cut, bg)
    return cut, dict(meta, holesRestored=filled, edgePixelsRecolored=recolored)


# --------------------------------------------------------------------------- #
# 展示用拼条
# --------------------------------------------------------------------------- #
def contact_strip(frames: List[Image.Image], *, thumb_w: int = 96,
                  gap: int = 6, bg: Tuple[int, int, int] = (24, 26, 32)
                  ) -> Image.Image:
    """把一摞帧横向排成一条缩略图（节点卡片上显示用，不影响产出文件）。"""
    if not frames:
        return Image.new("RGBA", (thumb_w, thumb_w), (0, 0, 0, 0))
    thumbs = []
    for f in frames:
        r = thumb_w / max(1, f.width)
        th = int(round(f.height * r))
        thumbs.append(f.resize((thumb_w, max(1, th)), Image.LANCZOS))
    h = max(t.height for t in thumbs)
    strip = Image.new("RGBA", (thumb_w * len(thumbs) + gap * (len(thumbs) - 1), h), bg + (255,))
    x = 0
    for t in thumbs:
        strip.alpha_composite(t, (x, (h - t.height) // 2))
        x += thumb_w + gap
    return strip


# --------------------------------------------------------------------------- #
# 阶段入口
# --------------------------------------------------------------------------- #
async def run_sequence(
    directions: List[Dict[str, Any]],
    *,
    frames_per_dir: int = 8,
    matte: bool = True,
    tolerance: int = 40,
    task_id: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """每方向：抽帧 → 去重 → 抠图。

    directions: [{dir, label, frames: [PIL], fps, ...}]（行走视频节点的输出）。
    返回同构列表，frames 换成处理后的透明底帧，并带上 count/dropped/strip 信息。
    """
    total = len(directions)
    out: List[Dict[str, Any]] = []
    done = 0
    for d in directions:
        # 纯 CPU 循环也要能取消：每抠完一帧就能被打断（8 方向 × 8 帧，
        # 中途取消不必等剩下几十帧跑完）
        progress_mod.check_cancel(task_id)
        frames = list(d.get("frames") or [])
        if not frames:
            raise RuntimeError(f"{DIR_LABELS.get(d.get('dir'), d.get('dir'))}方向没有视频帧")
        progress_mod.report(task_id, done, total,
                            f"{d.get('label') or DIR_LABELS.get(d.get('dir'), '')}：抽帧抠图中…")
        # 先剔掉糊掉的帧，再抽样：糊帧一旦被抽中，精灵表里就是一块抹布
        clean, blurry = reject_blurry(frames)
        picked = sample_frames(clean, frames_per_dir)
        picked, dropped = dedup_frames(picked)
        prepared: List[Image.Image] = []
        removed: List[float] = []
        matte_errors = 0
        for f in picked:
            progress_mod.check_cancel(task_id)
            try:
                cut, m = prepare_frame(f, matte=matte, tolerance=tolerance)
            except Exception:
                # 单帧抠图失败（罕见：极端尺寸 / 全同色画面）不该让整批失败：
                # 用未抠的帧兜底，计数交给上层提示
                cut, m = f.convert("RGBA"), {"matte": False}
                matte_errors += 1
            prepared.append(cut)
            removed.append(float(m.get("removedRatio", 0)))
        done += 1
        progress_mod.report(task_id, done, total,
                            f"{d.get('label') or DIR_LABELS.get(d.get('dir'), '')}：{len(prepared)} 帧")
        out.append({
            **d,
            "frames": prepared,
            "count": len(prepared),
            "sourceFrames": len(frames),
            "dropped": dropped,
            "blurryDropped": blurry,
            "matteErrors": matte_errors,
            "strip": contact_strip(prepared),
            "bgRemovedAvg": round(sum(removed) / max(1, len(removed)), 3),
        })
    meta = {
        "framesPerDir": frames_per_dir,
        "matte": matte,
        "tolerance": tolerance,
        "directions": len(out),
        "counts": [d["count"] for d in out],
        "dropped": sum(d["dropped"] for d in out),
        "blurryDropped": sum(d.get("blurryDropped", 0) for d in out),
        "matteErrors": sum(d.get("matteErrors", 0) for d in out),
    }
    return out, meta
