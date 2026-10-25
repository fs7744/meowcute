"""诊断「白底人物抠图后有白边」。

白边的两种成因必须分开，否则修错地方：
  A. **alpha 半透明**：边缘 alpha 在 1~254，直通 alpha 合成到深色底上会发白。
  B. **RGB 被背景污染**：alpha 是硬边（0/255），但保留下来那圈像素的 RGB 本身
     是「前景 × α + 白底 × (1-α)」的混色，看起来就是一圈白描边。

本脚本量化两者，并把抠图结果合成到黑底 / 品红底各出一张对比图。

用法：
    .venv/Scripts/python.exe tools/diag_white_fringe.py [视频路径] [--frame 5]
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "out" / "fringe"
NEIGH = [(-1, 0), (1, 0), (0, -1), (0, 1)]


def load_frame(video: Path, idx: int = 5) -> Image.Image:
    """走项目自己的解码路径（和拆帧节点完全一致：GIF 用 PIL、其余用 cv2）。"""
    from backend.app.storage import load_video_frames
    pid = video.parents[1].name
    frames, _fps, meta = load_video_frames(pid, "images", video.name,
                                           max_frames=900, max_side=1024)
    if not frames:
        raise RuntimeError(f"读不到帧：{meta.get('error') or '未知原因'}")
    print(f"  源视频 {len(frames)} 帧 @ {_fps:.2f}fps")
    return frames[min(idx, len(frames) - 1)]


def edge_ring(alpha: np.ndarray, depth: int = 2) -> np.ndarray:
    """前景中「距离透明区 depth 像素内」的像素（＝肉眼看到的那圈边）。"""
    import cv2
    kept = alpha > 127
    trans = (~kept).astype(np.uint8)
    d = cv2.dilate(trans, np.ones((3, 3), np.uint8), iterations=max(1, int(depth))) > 0
    return kept & d


def report(tag: str, rgba: Image.Image) -> dict:
    a = np.asarray(rgba)
    alpha = a[:, :, 3]
    rgb = a[:, :, :3].astype(np.int32)
    n = alpha.size
    soft = ((alpha > 0) & (alpha < 255)).sum()
    ring = edge_ring(alpha, 2)
    core = np.asarray(Image.fromarray(alpha).filter(__import__("PIL.ImageFilter",
                    fromlist=["ImageFilter"]).MinFilter(7))) > 127
    inner = core & ~ring
    out = {"tag": tag, "softPct": round(soft / n * 100, 3),
           "ringPx": int(ring.sum()), "keptPct": round((alpha > 127).sum() / n * 100, 2)}
    if ring.any() and inner.any():
        ring_l = rgb[ring].mean(axis=0)
        inner_l = rgb[inner].mean(axis=0)
        out["ringRGB"] = [int(v) for v in ring_l]
        out["innerRGB"] = [int(v) for v in inner_l]
        # 边缘比内侧「白多少」：这个值大就是肉眼看到的白边
        out["ringMinusInner"] = [int(v) for v in (ring_l - inner_l)]
        out["ringLumaMinusInner"] = int(round(ring_l.mean() - inner_l.mean()))
    return out


def compose(rgba: Image.Image, bg) -> Image.Image:
    base = Image.new("RGB", rgba.size, bg)
    base.paste(rgba, mask=rgba.getchannel("A"))
    return base


def crop_zoom(img: Image.Image, rgba: Image.Image, scale: int = 4,
              w: int = 300, h: int = 300) -> Image.Image:
    """裁一块「边缘最多」的区域放大，便于肉眼看白边。"""
    alpha = np.asarray(rgba.getchannel("A"))
    ring = edge_ring(alpha, 2)
    ys, xs = np.nonzero(ring)
    if not len(ys):
        return img.crop((0, 0, min(w // scale, img.width),
                         min(h // scale, img.height))).resize(
                             (w, h), Image.NEAREST)
    # 取边缘最密集的窗口
    cx = int(np.median(xs))
    cy = int(np.median(ys))
    bw, bh = max(24, w // scale), max(24, h // scale)
    x0 = max(0, min(img.width - bw, cx - bw // 2))
    y0 = max(0, min(img.height - bh, cy - bh // 2))
    return img.crop((x0, y0, x0 + bw, y0 + bh)).resize((w, h), Image.NEAREST)


def main() -> int:
    from backend.app.pipelines.sequence import prepare_frame
    from backend.app.storage import load_video_frames

    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frame_idx = 5
    for a in sys.argv[1:]:
        if a.startswith("--frame"):
            frame_idx = int(a.split("=")[-1]) if "=" in a else 5
    if args:
        video = Path(args[0]).resolve()
    else:
        cands = sorted(ROOT.glob("data/projects/*/images/vidsrc-*.mp4"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not cands:
            print("找不到素材")
            return 2
        video = cands[0]
    try:
        shown = video.relative_to(ROOT)
    except ValueError:
        shown = video
    print(f"素材：{shown}")
    OUT.mkdir(parents=True, exist_ok=True)

    src = load_frame(video, frame_idx)
    print(f"源帧：{src.width}×{src.height}")

    results = {}
    for tag, kw in (("now", {}),):
        cut, meta = prepare_frame(src, matte=True, tolerance=40, **kw)
        results[tag] = (cut, meta)
        print(f"\n[{tag}] 抠图方法={meta.get('method')} "
              f"去污像素={meta.get('despilledPixels')} "
              f"补洞={meta.get('holesRestored')} 边缘改色={meta.get('edgePixelsRecolored')}")
        r = report(tag, cut)
        print(f"  半透明像素占比 {r['softPct']}%（≈0 说明 alpha 是硬边 → 白边来自 RGB）")
        print(f"  边缘环 {r['ringPx']} px；环均值 RGB {r.get('ringRGB')} "
              f"vs 内部 {r.get('innerRGB')}")
        print(f"  **边缘比内部白 {r.get('ringLumaMinusInner')} 阶**"
              f"（>6 就肉眼可见）")

    cut = results["now"][0]
    for name, bg in (("black", (0, 0, 0)), ("magenta", (255, 0, 255)),
                     ("white", (255, 255, 255))):
        comp = compose(cut, bg)
        comp.save(OUT / f"compose_{name}.png")
        crop_zoom(comp, cut).save(OUT / f"edge_{name}.png")
        print(f"  → {OUT / f'edge_{name}.png'}")
    cut.save(OUT / "cut.png")
    print(f"\n产物：{OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
