"""视频 / GIF → 序列帧：拆帧 + 指纹 + 可选抠图。

与 `sequence.py` 的区别：那条是「八方向行走」专用的二级管线（输入已经是
内存里的方向帧，按动作周期采样、按方向分组）。这里是**通用入口**，输入是
项目里任意一段视频或 GIF，输出是「一摞帧 + 每帧的差异/锐度/指纹」，交给
前端做交互式选帧——用户要自己看、自己挑，而不是让程序替他抽 8 帧。

三件事：

1. **两级抽稀**。视频动辄 24~30fps，3 秒就 90 帧，全丢给前端既慢也没必要。
   先按「帧率上限」抽（每 round(src_fps / cap) 帧取 1），再按「最大帧数」
   等距抽稀，首尾都取。两级分开是因为含义不同：前者是「这个动画我打算按
   12fps 播」，后者是「别超过 120 帧」。抽完之后帧率已经变了，所以返回的
   `fps` 是**抽稀后**的有效帧率，前端据此给动画预览的默认速度。
2. **指纹而不是缩略图**。相似度过滤要在浏览器里实时算（拖一下滑杆就要看到
   候选帧变），所以每帧带一份 16×16 灰度的 base64 指纹（256 字节）。120 帧
   一共 30KB 左右，比让前端逐张读像素便宜得多，也比每次改参数都回后端算
   快得多。
3. **抠图默认开**。游戏素材要透明底，这一步跑不跑决定了后面精灵图能不能
   直接用。但抠图是整条链路里唯一的重活（u2net 每帧几百毫秒），所以放在
   任务制接口里跑，带进度和取消。
"""
from __future__ import annotations

import base64
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod
from .sequence import prepare_frame, sharpness

# 指纹边长。16×16 足够分辨「这一帧和上一帧是不是同一个姿势」，再大只是
# 多传字节：相似度过滤关心的是整体轮廓变化，不是细节纹理。
HASH_N = 16


# --------------------------------------------------------------------------- #
# 指纹与差异
# --------------------------------------------------------------------------- #
def frame_hash(img: Image.Image, n: int = HASH_N) -> str:
    """16×16 灰度指纹（base64）。前端用它算两帧差异，不需要回后端。

    用 LANCZOS 而不是 NEAREST：滤波后的缩略图对小位移更宽容，两帧差 1~2px
    的抖动不会算成「不同姿势」——这正是去重要效果。
    """
    g = img.convert("L").resize((n, n), Image.LANCZOS)
    return base64.b64encode(g.tobytes()).decode("ascii")


def diff_percent(a: str, b: str) -> float:
    """两个指纹的差异百分比（0~100）。供后端统计与测试用，前端有一份同构实现。"""
    try:
        ba = np.frombuffer(base64.b64decode(a), dtype=np.uint8)
        bb = np.frombuffer(base64.b64decode(b), dtype=np.uint8)
    except Exception:
        return 0.0
    if ba.size != bb.size or not ba.size:
        return 0.0
    return float(np.abs(ba.astype(np.int16) - bb.astype(np.int16)).mean() / 255.0 * 100.0)


def _to_rgba_on_black(img: Image.Image) -> Image.Image:
    """把带 alpha 的帧铺到黑底再算指纹。

    透明区的 RGB 是不可信值（抠图后我们会清零），直接取灰度会让「同样透明
    的两帧」因为残留 RGB 不同而算成不同。铺到黑底后，透明区恒为 0，差异
    只来自真正可见的前景。
    """
    rgba = img.convert("RGBA")
    if rgba.getchannel("A").getextrema() == (255, 255):
        return rgba.convert("RGB")
    bg = Image.new("RGB", rgba.size, (0, 0, 0))
    bg.paste(rgba, mask=rgba.getchannel("A"))
    return bg


# --------------------------------------------------------------------------- #
# 循环闭合（取整周期）
# --------------------------------------------------------------------------- #
# 为什么需要：AI 视频里「一个动作周期」很少正好等于用户裁出来的帧数，于是
# 「末帧 → 首帧」是一大步跳。实测（2026-10-26，832²/20fps 素材）首尾指纹差
# 12.6%，而段内相邻只有 1.0%——循环播放每转一圈抽一下，比任何帧内抖动都显眼。
#
# 为什么不是「把末帧对齐到首帧」：实测三种全局锚点对齐后，相邻帧掩膜 IoU
# 全都下降 0.8~3.9pt（tools/sprite_jitter_probe.py），因为帧间差异来自衣摆
# 形变而不是平移。硬对齐等于给动画凭空加位移。真正的问题是**相位不连续**，
# 所以只做一件事：按整周期裁——只保留「整周期」帧数，末帧回绕到首帧就正好
# 是一步（与段内相邻同样的相位差）。
def loop_report(hashes: Sequence[str]) -> Dict[str, float]:
    """首尾接缝 vs 段内相邻差异。口径与 diff_percent 一致，和界面上看到的数字可比。"""
    n = len(hashes)
    if n < 3:
        return {"adjacent": 0.0, "seam": 0.0, "ratio": 1.0}
    adj = float(np.median([diff_percent(hashes[i], hashes[i + 1]) for i in range(n - 1)]))
    seam = diff_percent(hashes[-1], hashes[0])
    return {"adjacent": round(adj, 2), "seam": round(seam, 2),
            "ratio": round(seam / max(adj, 0.05), 2)}


def estimate_period(hashes: Sequence[str]) -> Dict[str, Any]:
    """自相关式找周期：对每个滞后 L，算「隔 L 帧的两帧」平均差，找**真正的谷**。

    为什么不能取「全局最小滞后」：慢动作素材里 d(L) 随 L 单调上升，最小值永远
    落在最小的 L（实测会报出周期 3，等于瞎猜）。真正的周期应表现为**局部极小**：
    同相位那两帧比它左右两个滞后都明显更小。所以只认
    d(L) < 0.75 × min(d(L-1), d(L+1)) 且 d(L) < 0.85 × 中位 的谷。
    两段实测素材（站立衣摆飘 / 背身走远）曲线全单调、无谷 → 正确返回「无周期」。
    """
    n = len(hashes)
    if n < 10:
        return {"period": 0, "confidence": 0.0}
    curve: Dict[int, float] = {}
    for lag in range(1, n // 2 + 1):
        vals = [diff_percent(hashes[i], hashes[i + lag]) for i in range(n - lag)]
        curve[lag] = float(np.mean(vals))
    if len(curve) < 5:
        return {"period": 0, "confidence": 0.0}
    med = float(np.median(list(curve.values())))
    if med <= 0.05:
        return {"period": 0, "confidence": 0.0}
    valleys = [
        lag for lag in range(4, max(5, n // 2))
        if curve.get(lag - 1) is not None and curve.get(lag + 1) is not None
        and curve[lag] < 0.75 * min(curve[lag - 1], curve[lag + 1])
        and curve[lag] < 0.85 * med
    ]
    if not valleys:
        return {"period": 0, "confidence": 0.0,
                "medianDiff": round(med, 2),
                "bestLag": min(curve, key=curve.get),
                "bestDiff": round(min(curve.values()), 2)}
    period = int(min(valleys))            # 取最小谷，避免落到 2 倍/3 倍周期
    return {"period": period,
            "confidence": round(1.0 - curve[period] / med, 3),
            "bestDiff": round(curve[period], 2), "medianDiff": round(med, 2)}


def plan_loop(hashes: Sequence[str], *, min_keep: int = 6) -> Dict[str, Any]:
    """决定要不要按整周期裁、裁到多少帧。**只在真有接缝问题时才动手**。

    返回的 action 是给用户看的结论：
      off          未启用
      tooShort     帧太少，不判断
      alreadyClosed 首尾差已经不比相邻差大（≤1.5 倍），不用动
      noPeriod     找不到明显周期（不是循环素材）
      cannotTrim   找到周期但裁完不足 min_keep 帧，或裁完等于没裁
      noGain       裁完接缝并没变好（宁可不裁，别白丢帧）
      trimmed      已裁到整周期
    """
    n = len(hashes)
    rep = loop_report(hashes)
    out: Dict[str, Any] = {"action": "off", **rep,
                           "countBefore": n, "countAfter": n,
                           "period": 0, "cycles": 0, "dropped": 0}
    if n < min_keep + 2:
        out["action"] = "tooShort"
        return out
    if rep["ratio"] <= 1.5:
        out["action"] = "alreadyClosed"
        return out
    est = estimate_period(hashes)
    period = int(est.get("period") or 0)
    out.update({k: est[k] for k in ("bestLag", "bestDiff", "medianDiff", "confidence")
                if k in est})
    out["period"] = period
    if period < 3:
        out["action"] = "noPeriod"
        return out
    keep = (n // period) * period
    if keep < min_keep or keep >= n:
        out["action"] = "cannotTrim"
        return out
    after = loop_report(hashes[:keep])
    if after["ratio"] >= rep["ratio"] * 0.8:
        out["action"] = "noGain"
        out["ratioAfter"] = after["ratio"]
        return out
    out.update({"action": "trimmed", "cycles": keep // period,
                "countAfter": keep, "dropped": n - keep,
                "seamAfter": after["seam"], "ratioAfter": after["ratio"]})
    return out


# --------------------------------------------------------------------------- #
# 选帧计划
# --------------------------------------------------------------------------- #
def plan_indices(
    total: int,
    *,
    src_fps: float = 24.0,
    fps_cap: float = 12.0,
    max_frames: int = 120,
    start: float = 0.0,
    end: Optional[float] = None,
) -> Tuple[List[int], Dict[str, Any]]:
    """源帧序号 → 要真正取出来的帧序号列表。

    顺序是「先按时间裁、再按帧率抽、最后按帧数截」，因为每步的语义不同：
    时间裁是用户明确要的片段，帧率是播放节奏，帧数是成本上限。反过来做会让
    上限把用户选的区间截断。
    """
    total = max(0, int(total))
    if not total:
        return [], {"reason": "源里没有帧"}

    # 1) 时间区间
    fps_src = float(src_fps) if src_fps and src_fps == src_fps and src_fps > 0 else 24.0
    i0 = max(0, min(int(round(max(0.0, start) * fps_src)), total - 1))
    i1 = total - 1
    if end is not None:
        i1 = min(total - 1, max(i0, int(round(float(end) * fps_src)) - 1))
    span = i1 - i0 + 1

    # 2) 帧率上限 → 步长
    cap = float(fps_cap) if fps_cap and fps_cap > 0 else fps_src
    step = max(1, int(round(fps_src / cap))) if cap < fps_src else 1
    picked = list(range(i0, i1 + 1, step))

    # 3) 最大帧数：等距抽稀（首尾都留，循环时末帧接首帧）
    thinned = False
    if len(picked) > max_frames >= 2:
        n = max_frames
        idx = [round(i * (len(picked) - 1) / (n - 1)) for i in range(n)]
        seen: set = set()
        out: List[int] = []
        for k in idx:
            if k in seen:
                continue
            seen.add(k)
            out.append(picked[k])
        picked = out
        thinned = True

    eff_fps = round(fps_src / step, 3)
    # 二次抽稀会再降有效帧率（等距丢帧），按实际保留比例折算
    if thinned and len(picked) > 1:
        ratio = len(picked) / max(1, len(range(i0, i1 + 1, step)))
        eff_fps = round(eff_fps * ratio, 3)

    meta = {
        "sourceFrames": total,
        "rangeFrames": span,
        "step": step,
        "picked": len(picked),
        "thinned": thinned,
        "srcFps": round(fps_src, 3),
        "effFps": eff_fps,
        "durationSec": round(span / fps_src, 3),
        "start": round(i0 / fps_src, 3),
        "end": round((i1 + 1) / fps_src, 3),
    }
    return picked, meta


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_split(
    frames: Sequence[Image.Image],
    *,
    fps: float = 24.0,
    fps_cap: float = 12.0,
    max_frames: int = 120,
    start: float = 0.0,
    end: Optional[float] = None,
    matte: bool = True,
    tolerance: int = 40,
    matte_model: str = "auto",
    loop_close: bool = True,
    task_id: Optional[str] = None,
    on_frame: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """源帧 → 选中的帧（可选抠图），每帧带指纹/差异/锐度。

    loop_close：按整周期裁到「整周期」帧数，让末帧回绕到首帧正好是一步。
    只在**真有接缝问题**时动手（见 plan_loop），代价是可能丢几帧尾巴。

    **on_frame**：每帧处理完就回调一次。调用方应在这里落盘并把 `item["image"]`
    置空——否则拆 600 帧 × 384² 就是 338MB 像素一直堆在内存里（且中途失败
    一帧都拿不到）。meta 只用到标量字段（diff/hash），释放 image 不影响统计。

    返回 (frames, meta)。frames 元素：
        {i, srcIndex, t, image, hash, diff, sharp, matte}
    i 是**输出序号**（0 起），srcIndex 是源视频里的帧号，t 是源时间戳。
    """
    total = len(frames)
    picks, plan = plan_indices(total, src_fps=fps, fps_cap=fps_cap,
                               max_frames=max_frames, start=start, end=end)
    if not picks:
        raise RuntimeError("这一段里没有可用的帧：检查起始 / 结束时间是否超出视频长度")

    # 循环闭合：在**抠图之前**用原帧指纹判周期（16×16 灰度，几千次比较，可忽略）。
    # 放在抠图前是为了不白跑——裁掉的帧本来就不该花几百毫秒去抠。
    loop_meta: Dict[str, Any] = {"action": "off"}
    if loop_close and len(picks) >= 8:
        raw_hashes = [frame_hash(frames[si]) for si in picks]
        loop_meta = plan_loop(raw_hashes)
        if loop_meta.get("action") == "trimmed":
            picks = picks[:int(loop_meta["countAfter"])]
            fps_src = plan["srcFps"]
            plan = {**plan,
                    "picked": len(picks),
                    "end": round((picks[-1] + 1) / fps_src, 3),
                    # 裁完的时长按**输出帧率**算：前端是按 effFps 播的
                    "durationSec": round(len(picks) / max(0.5, plan["effFps"]), 3),
                    "loopTrimmed": int(loop_meta.get("dropped") or 0)}
    elif loop_close:
        loop_meta = {"action": "tooShort", "countBefore": len(picks),
                     "countAfter": len(picks), "dropped": 0}

    out: List[Dict[str, Any]] = []
    prev_hash: Optional[str] = None
    matte_errors = 0
    n = len(picks)
    fps_src = plan["srcFps"]
    for k, si in enumerate(picks):
        progress_mod.check_cancel(task_id)
        progress_mod.report(task_id, k, n,
                            f"处理帧 {k + 1}/{n}"
                            + ("（抠图中…）" if matte else ""))
        img = frames[si]
        work: Image.Image
        mm: Dict[str, Any] = {"matte": False}
        if matte:
            try:
                work, mm = prepare_frame(img, matte=True,
                                         tolerance=int(tolerance),
                                         model=matte_model)
            except Exception:
                # 单帧抠图失败不该让整批失败：留原帧并计数，前端会标出来
                work = img.convert("RGBA")
                matte_errors += 1
        else:
            work = img.convert("RGBA")

        h = frame_hash(_to_rgba_on_black(work))
        diff = 0.0 if prev_hash is None else diff_percent(prev_hash, h)
        prev_hash = h
        out.append({
            "i": k,
            "srcIndex": int(si),
            "t": round(si / fps_src, 3),
            "image": work,
            "hash": h,
            "diff": round(diff, 2),
            "sharp": round(sharpness(work), 2),
            "matte": bool(matte),
        })
        # 边跑边落盘：调用方存盘后置空 image 释放像素（600 帧时是几百 MB）
        if on_frame is not None:
            try:
                on_frame(out[-1])
            except Exception:
                pass
    progress_mod.report(task_id, n, n, f"{n} 帧就绪")

    meta = {
        **plan,
        "fpsCap": fps_cap,
        "maxFrames": max_frames,
        "matte": bool(matte),
        "tolerance": int(tolerance),
        "count": len(out),
        "matteErrors": matte_errors,
        # 各帧与前帧的差异中位数：前端拿它当「相似度阈值」滑杆的默认值，
        # 比给个拍脑袋的常数靠谱（不同素材的运动幅度差一个数量级）
        "diffMedian": round(float(np.median([f["diff"] for f in out[1:]]))
                            if len(out) > 1 else 0.0, 2),
        "diffMax": round(float(max((f["diff"] for f in out[1:]), default=0.0)), 2),
    }
    # 循环闭合的最终账（用**处理后的**指纹重算，和界面上看到的 diff 同一口径）：
    # 前端拿它显示「首尾差 12.6% → 1.1%（相邻 1.0%）」，用户一眼知道裁得值不值。
    if loop_close:
        fin = loop_report([f["hash"] for f in out])
        before = int(loop_meta.get("countBefore") or len(out))
        meta["loop"] = {**loop_meta, **fin,
                        "countBefore": before,
                        "countAfter": len(out),
                        "dropped": max(0, before - len(out))}
    else:
        meta["loop"] = {"action": "off", "countAfter": len(out)}
    return out, meta
