"""诊断：AI 视频转序列帧时「角色在格子里来回漂」到底来自哪一类，能不能靠对齐修掉。

为什么先量再改：抖动至少四类，治法完全不同——
  ① 整体平移漂移   ② 慢速来回摆   ③ 尺度呼吸（忽大忽小）   ④ 像素沸腾（轮廓每帧随机 1~2px）
而且**最反直觉的是**：先要确认「到底有没有整体位移」。实测两段素材（2026-10-26）
里，头部质心只晃 3px、脚底行 0px，大幅晃的是衣摆/头发/腰胯——这种情况任何全局
对齐都是给画面**凭空加位移**，相邻帧 IoU 实测会下降 1~4pt（见 --anchors）。

所以本工具不做「修」，只做「量」，回答三件事：
  1. 各部位（头肩 / 腰胯 / 脚 / 全剪影）各自的位移量程 → 判断是整体平移还是形变摆动
  2. 尺度（bbox 宽高）与脚底行变化 → 判断有没有「走远/呼吸」
  3. 三种锚点对齐后的相邻帧 IoU，与「不对齐」比 → 直接回答「该不该上对齐、用哪个锚」
另外给循环闭合：首尾帧 IoU / 质心差 vs 段内相邻中位——循环处「跳一下」往往是
用户真正看到的不顺滑。

用法：
  python tools/sprite_jitter_probe.py <video.mp4> [--max 60] [--fast] [--anchors]
  --fast     用白光键代替生产抠图（快 10 倍，够用；纯色底时推荐）
  --anchors  额外跑「锚点对齐前后 IoU」对比（慢一点，但决定要不要上对齐）
输出：stdout 报告 + out/jitter_<name>.png（分部位轨迹图）
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")

import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path("out")


# ------------------------------------------------------------------ 解帧
def read_frames(path: Path, max_frames: int = 120, max_side: int = 1024):
    import cv2

    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"打不开视频：{path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not (fps and fps == fps and 0 < fps < 1000):
        fps = 24.0
    frames = []
    while len(frames) < max_frames:
        ok, bgr = cap.read()
        if not ok:
            break
        img = Image.fromarray(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
        if max(img.size) > max_side:
            img.thumbnail((max_side, max_side), Image.LANCZOS)
        frames.append(img.convert("RGBA"))
    cap.release()
    return frames, float(fps)


# ------------------------------------------------------------------ 掩膜
def alpha_of(img: Image.Image, fast: bool = False) -> np.ndarray:
    """前景掩膜（bool）。fast=False 时走生产同一条抠图链路。"""
    if not fast:
        try:
            from backend.app.pipelines.sequence import prepare_frame
            cut, _ = prepare_frame(img, matte=True)
            a = np.asarray(cut.split()[-1], dtype=np.uint8)
            if (a > 128).sum() > 64:
                return a > 128
        except Exception as exc:
            print(f"  [warn] 抠图失败，改用白光键：{exc}")
    rgb = np.asarray(img.convert("RGB"), dtype=np.int16)
    h, w, _ = rgb.shape
    corners = np.concatenate([rgb[0, 0], rgb[0, w - 1], rgb[h - 1, 0], rgb[h - 1, w - 1]])
    bg = np.median(corners.reshape(4, 3), axis=0)
    return np.abs(rgb - bg).sum(-1) > 36


# ------------------------------------------------------------------ 指标
def frame_stats(mask: np.ndarray) -> dict:
    ys, xs = np.nonzero(mask)
    if xs.size < 16:
        return {}
    x0, x1 = int(xs.min()), int(xs.max())
    y0, y1 = int(ys.min()), int(ys.max())
    foot = None
    for y in range(y1, y0 - 1, -1):          # 从下往上第一个有 ≥3 前景像素的行
        if int(mask[y].sum()) >= 3:
            foot = y
            break
    return {"cx": float(xs.mean()), "cy": float(ys.mean()),
            "x0": x0, "y0": y0, "x1": x1, "y1": y1,
            "w": x1 - x0 + 1, "h": y1 - y0 + 1,
            "foot": float(foot if foot is not None else y1),
            "area": int(xs.size)}


def part_cx(mask: np.ndarray, y0: int, y1: int) -> float:
    ys, xs = np.nonzero(mask[y0:y1])
    return float(xs.mean()) if xs.size else np.nan


def anchor_points(masks, stats):
    """三种常用锚点：整幅质心 / 脚底行+头肩中心 / bbox 底边中心。"""
    out = {"质心": [], "脚底+头肩": [], "bbox底边中心": []}
    for m, s in zip(masks, stats):
        H = s["h"]
        out["质心"].append((s["cx"], s["cy"]))
        out["脚底+头肩"].append((part_cx(m, s["y0"], s["y0"] + int(H * 0.18)), s["foot"]))
        out["bbox底边中心"].append(((s["x0"] + s["x1"]) / 2, float(s["y1"])))
    return out


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


def _shift(m: np.ndarray, dx: float, dy: float) -> np.ndarray:
    return np.roll(np.roll(m, int(round(dy)), axis=0), int(round(dx)), axis=1)


def anchor_compare(masks, stats) -> dict:
    """每种锚点对齐后的相邻帧 IoU 中位；比「不对齐」高才说明对齐有用。"""
    n = len(masks)
    base = float(np.median([_iou(masks[i], masks[i + 1]) for i in range(n - 1)]))
    res = {"不对齐": round(base, 4)}
    for name, pts in anchor_points(masks, stats).items():
        vals = []
        for i in range(n - 1):
            dx = pts[i][0] - pts[i + 1][0]
            dy = pts[i][1] - pts[i + 1][1]
            vals.append(_iou(_shift(masks[i], dx, dy), masks[i + 1]))
        res[name] = round(float(np.median(vals)), 4)
    return res


def range_of(v) -> float:
    a = np.asarray(v, dtype=np.float64)
    a = a[~np.isnan(a)]
    return float(a.max() - a.min()) if a.size else 0.0


# ------------------------------------------------------------------ 画图
def _font(size: int):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def draw_chart(path: Path, series, fps: float, title: str):
    W = 1000
    rowh = 130
    H = 60 + rowh * len(series) + 40
    img = Image.new("RGB", (W, H), (250, 250, 248))
    d = ImageDraw.Draw(img)
    f, fs = _font(15), _font(12)
    d.text((24, 16), title, font=f, fill=(30, 30, 30))
    for si, (label, v, color) in enumerate(series):
        y0 = 50 + si * rowh
        d.rectangle([80, y0, W - 30, y0 + rowh - 26], outline=(212, 212, 206))
        v = np.asarray(v, dtype=np.float64)
        lo, hi = float(np.nanmin(v)), float(np.nanmax(v))
        if hi - lo < 1e-6:
            hi, lo = lo + 1, lo - 1
        n = len(v)
        pts = [(80 + (W - 120) * (i / max(1, n - 1)),
                y0 + (rowh - 30) * (1 - (val - lo) / (hi - lo)) + 2)
               for i, val in enumerate(v)]
        if len(pts) > 1:
            d.line(pts, fill=color, width=2)
        d.text((26, y0 + 6), label, font=fs, fill=(70, 70, 70))
        d.text((26, y0 + 26), f"{lo:.0f}", font=fs, fill=(145, 145, 145))
        d.text((26, y0 + rowh - 44), f"{hi:.0f}", font=fs, fill=(145, 145, 145))
        d.text((W - 190, y0 + 6), f"量程 {hi - lo:.1f}px", font=fs, fill=(145, 145, 145))
    d.text((80, H - 26), f"帧序号（源 {fps:.1f}fps）", font=fs, fill=(125, 125, 125))
    img.save(path)


# ------------------------------------------------------------------ 主流程
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    path = Path(sys.argv[1])
    max_frames = 60
    if "--max" in sys.argv:
        max_frames = int(sys.argv[sys.argv.index("--max") + 1])
    fast = "--fast" in sys.argv
    do_anchor = "--anchors" in sys.argv

    print(f"[1] 解帧 {path.name} …", flush=True)
    frames, fps = read_frames(path, max_frames=max_frames)
    if not frames:
        print("没有解出帧")
        return 1
    print(f"    {len(frames)} 帧 @ {fps:.2f}fps  {frames[0].size}")

    print(f"[2] 掩膜（{'白光键' if fast else '生产抠图链路'}）…", flush=True)
    masks, stats = [], []
    for i, fr in enumerate(frames):
        m = alpha_of(fr, fast=fast)
        st = frame_stats(m)
        if st:
            masks.append(m)
            stats.append(st)
    if len(stats) < 6:
        print("有效帧太少，无法诊断")
        return 1
    n = len(stats)
    print(f"    有效 {n} 帧，剪影面积中位 {int(np.median([s['area'] for s in stats]))}px")

    head = [part_cx(m, s["y0"], s["y0"] + int(s["h"] * 0.18)) for m, s in zip(masks, stats)]
    hip = [part_cx(m, s["y0"] + int(s["h"] * 0.45), s["y0"] + int(s["h"] * 0.62))
           for m, s in zip(masks, stats)]
    feet = [part_cx(m, s["y0"] + int(s["h"] * 0.90), s["y1"] + 1) for m, s in zip(masks, stats)]
    full = [s["cx"] for s in stats]
    footrow = [s["foot"] for s in stats]
    bh = [s["h"] for s in stats]
    bw = [s["w"] for s in stats]

    print("[3] 分部位位移量程（这是判「整体平移」还是「形变摆动」的关键）")
    for nm, arr in (("头肩", head), ("腰胯", hip), ("脚部", feet),
                    ("全剪影", full), ("脚底行", footrow)):
        print(f"    {nm:5s} 量程 {range_of(arr):6.1f}px   std {float(np.nanstd(arr)):5.2f}")
    print(f"    bbox 高 量程 {range_of(bh):6.1f}px（占中位 "
          f"{100 * range_of(bh) / max(1, np.median(bh)):.1f}%）"
          f"  宽 量程 {range_of(bw):6.1f}px（{100 * range_of(bw) / max(1, np.median(bw)):.1f}%）")

    loop_iou = _iou(masks[0], masks[-1])
    adj = [_iou(masks[i], masks[i + 1]) for i in range(n - 1)]
    adj_med = float(np.median(adj))
    loop_d = float(np.hypot(full[-1] - full[0], stats[-1]["cy"] - stats[0]["cy"]))
    adj_d = float(np.median([np.hypot(full[i + 1] - full[i], stats[i + 1]["cy"] - stats[i]["cy"])
                             for i in range(n - 1)]))
    print(f"[4] 循环闭合：首尾 IoU {loop_iou:.3f}（段内相邻中位 {adj_med:.3f}，"
          f"低 {(adj_med - loop_iou) * 100:+.1f}pt）；首尾质心差 {loop_d:.1f}px "
          f"（段内相邻中位 {adj_d:.1f}px）")

    res = {}
    if do_anchor:
        print("[5] 锚点对比（相邻帧 IoU 中位，越高越一致）")
        res = anchor_compare(masks, stats)
        base = res["不对齐"]
        for k, v in res.items():
            tag = "" if k == "不对齐" else (
                "  ↑ 有用" if v > base + 1e-4 else ("  ↓ 更差" if v < base - 1e-4 else "  ≈ 无变化"))
            print(f"    {k:12s} {v:.4f}{tag}（{(v - base) * 100:+.2f}pt）")

    OUT.mkdir(exist_ok=True)
    tag = path.stem
    png = OUT / f"jitter_{tag}.png"
    draw_chart(png, [("头肩 cx", head, (216, 90, 48)),
                     ("腰胯 cx", hip, (29, 158, 117)),
                     ("脚部 cx", feet, (24, 120, 190)),
                     ("脚底行", footrow, (120, 90, 190))],
               fps, f"{path.name}  {n} 帧 @ {fps:.1f}fps")
    (OUT / f"jitter_{tag}.json").write_text(json.dumps({
        "video": str(path), "frames": n, "fps": fps,
        "rangePx": {"head": round(range_of(head), 2), "hip": round(range_of(hip), 2),
                    "feet": round(range_of(feet), 2), "full": round(range_of(full), 2),
                    "footRow": round(range_of(footrow), 2), "bboxH": round(range_of(bh), 2),
                    "bboxW": round(range_of(bw), 2)},
        "loop": {"iou": round(loop_iou, 4), "adjIouMedian": round(adj_med, 4),
                 "centroidGapPx": round(loop_d, 2), "adjGapPxMedian": round(adj_d, 2)},
        "anchors": res,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"    图：{png}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
