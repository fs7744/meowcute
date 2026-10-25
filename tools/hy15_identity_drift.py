"""Review 用：量化「分窗会不会导致角色随时间漂移/身份不一致」。

为什么不用最直接的做法（把视频帧互相比较）：行走视频里角色本来就在动，
剪影 IoU 会被抬腿、摆臂混淆。所以分三类指标，各自只回答一个问题：

  - instability / motion    → 有没有闪烁、抖动（项目已有口径）
  - 接缝比                  → 段边界有没有「跳变」（ erwartet 1.0 = 与段内无异）
  - 颜色漂移（HSV 直方图）  → 有没有「换人/变色」（对姿态免疫，专测身份）

三组对照，同一参考图 / 同一提示词 / 同一种子 / 同帧数：
  A 整段          —— 基准
  B 分窗（现状）  —— 每窗跑满 + 每窗 seed 递增
  C 分窗（候选）  —— 最后一窗按剩余帧数生成 + 所有窗共用 seed
"""
import sys

sys.path.insert(0, ".")

import time
from pathlib import Path

REF = Path("data/projects/p-0d3f091b4c3c/images/vs-20261025-221119-46635a63.png")
PROMPT = "角色侧视行走：双腿交替迈步，步态平稳，手臂自然摆动。"
NEG = "模糊, 闪烁, 站桩, 形变, 多余肢体"

FRAMES = 61
STEPS = 12
W, H = 384, 512
# 注意：这里保留旧默认 21 是为了复现已存档的 A/B 结论；产品默认值自 2026-10-26
# 起是 41（见 backend/app/providers/hy15.py 的 DEFAULT_WINDOW）。要测新默认就用
# 命令行参数覆盖。
WINDOW = 21
SEED = 42
OUT = Path("out")


# ------------------------------------------------------------------ 指标
def _hsv_hist(im):
    """取非背景像素（饱和度/明度过滤）的 HSV 直方图，衡量「长什么样」。"""
    import numpy as np

    arr = np.asarray(im.convert("RGB").resize((64, 64)), dtype=np.float32) / 255.0
    mx, mn = arr.max(-1), arr.min(-1)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0)
    keep = (sat > 0.12) & (mx > 0.08)              # 过滤白/灰背景
    if keep.sum() < 32:
        keep = np.ones_like(keep)
    import colorsys

    hsv = np.zeros((*arr.shape[:2], 3), dtype=np.float32)
    for y in range(64):
        for x in range(64):
            if not keep[y, x]:
                continue
            hsv[y, x] = colorsys.rgb_to_hsv(*arr[y, x])
    hs = np.stack([hsv[..., 0][keep], hsv[..., 1][keep]])
    hh = np.histogram2d(hs[0], hs[1], bins=(24, 12), range=((0, 1), (0, 1)))[0]
    return hh / max(1e-6, hh.sum())


def color_drift(frames):
    """每帧与首帧的 HSV 直方图 L1 距离（0=完全一致，越大越「换了个人」）。"""
    import numpy as np

    ref = _hsv_hist(frames[0])
    return [float(np.abs(_hsv_hist(f) - ref).sum() * 100.0) for f in frames]


def evaluate(name, frames, seam_idx=(), note=""):
    import numpy as np

    from backend.app.pipelines.walkvideo import _silhouette, motion_stats

    ms = motion_stats(frames)
    masks = [_silhouette(f) for f in frames]
    pd_ = []
    for i in range(len(masks) - 1):
        a, b = masks[i], masks[i + 1]
        pd_.append(100.0 * (1.0 - np.logical_and(a, b).sum() / max(1, np.logical_or(a, b).sum())))
    seam = [pd_[i] for i in seam_idx if i < len(pd_)]
    non = [v for i, v in enumerate(pd_) if i not in seam_idx]
    seam_mean = float(np.mean(seam)) if seam else float("nan")
    non_mean = float(np.mean(non)) if non else float("nan")
    ratio = seam_mean / non_mean if non_mean else float("nan")

    cd = color_drift(frames)
    print(f"  {name:<14} motion={ms['motion']:5.2f} instability={ms['instability']:5.1f}%  "
          f"接缝/段内={ratio:5.2f}x  颜色漂移 首={cd[0]:4.1f} 中位={np.median(cd):5.1f} "
          f"末={cd[-1]:5.1f} 峰={max(cd):5.1f}"
          f"{'   ' + note if note else ''}")
    return {"motion": ms["motion"], "instability": ms["instability"],
            "seam_ratio": ratio, "cd_end": cd[-1], "cd_max": max(cd)}


def chained_identical_seed(pipe, emb, image, frames, window, seed, width, height):
    """候选改进：最后一窗只生成剩余帧数 + 所有窗共用同一个 seed。"""
    import torch

    from backend.app.providers import hy15

    gen_seed = int(seed)
    out, cur = [], image
    i = 0
    sizes = []
    while len(out) < frames:
        need = frames - len(out) + 1                       # 本窗要覆盖到末尾需几帧
        n = min(window, max(13, (need - 1) // 4 * 4 + 1))  # 4k+1 且不小于模型下限 13
        sizes.append(n)
        gen = torch.Generator("cuda" if torch.cuda.is_available() else "cpu").manual_seed(gen_seed)
        clip = hy15._denoise(pipe, emb, image=cur, n_frames=n, steps=STEPS,
                             guidance=1.0, gen=gen, t2v=False, width=width, height=height)
        out.extend(clip if i == 0 else clip[1:])
        cur = clip[-1]
        i += 1
    return out[:frames], sizes


def main():
    import torch

    from backend.app.providers import hy15

    OUT.mkdir(exist_ok=True)
    from PIL import Image

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    emb = hy15.encode_texts(PROMPT, NEG)

    seam_idx = [(WINDOW - 1) * (j + 1) for j in range(6)]
    seam_idx = [i for i in seam_idx if i < FRAMES - 1]
    print(f"=== 身份漂移对照：{FRAMES}帧 / {STEPS}步 / {W}x{H} / seed={SEED} / 接缝pair={seam_idx} ===\n")

    res = {}
    # 超过 ~81 帧整段必 OOM（正是分窗存在的理由），此时没有对照组可跑
    skip_whole = FRAMES > 81

    if skip_whole:
        print(f"  （{FRAMES} 帧整段会 OOM，跳过对照组 A；这正是分窗存在的理由）")
    else:
        # A 整段
        torch.cuda.empty_cache()
        t0 = time.time()
        fa, ma = hy15.run_i2v(img, emb, frames=FRAMES, steps=STEPS, guidance=1.0,
                              seed=SEED, window=0, width=W, height=H)
        ta = time.time() - t0
        print(f"[A] 整段      {len(fa)}帧 {ma.get('size')} {ta:5.1f}s 峰值{(ma.get('vram') or {}).get('peakGB')}GB")
        res["A 整段"] = evaluate("A 整段", fa)

    # B 分窗（现状）
    torch.cuda.empty_cache()
    t0 = time.time()
    fb, mb = hy15.run_i2v(img, emb, frames=FRAMES, steps=STEPS, guidance=1.0,
                          seed=SEED, window=WINDOW, width=W, height=H)
    tb = time.time() - t0
    print(f"[B] 分窗现状  {len(fb)}帧 {mb.get('size')} {tb:5.1f}s 峰值{(mb.get('vram') or {}).get('peakGB')}GB")
    res["B 分窗现状"] = evaluate("B 分窗现状", fb, seam_idx=seam_idx)

    # C 分窗（候选：按需最后窗 + 同 seed）
    torch.cuda.empty_cache()
    pipe = hy15.get_pipeline()
    t0 = time.time()
    fc, sizes = chained_identical_seed(pipe, emb, img, FRAMES, WINDOW, SEED, W, H)
    tc = time.time() - t0
    print(f"[C] 分窗候选  {len(fc)}帧 每窗帧数={sizes} {tc:5.1f}s")
    res["C 分窗候选"] = evaluate("C 分窗候选", fc, seam_idx=seam_idx)

    tstr = "" if skip_whole else f"A={ta:.1f}s  "
    print(f"\n  耗时: {tstr}B={tb:.1f}s  C={tc:.1f}s")
    print(f"  生成帧数: B 现状={len(fb)}(每窗都跑满{WINDOW})  C 候选={len(fc)}(最后一窗按需)")

    # 末帧对照图
    def strip(name, frames, picks, dst):
        tw, th = 130, 180
        ims = [frames[i].convert("RGB").resize((tw, th)) for i in picks if i < len(frames)]
        canvas = Image.new("RGB", (tw * len(ims), th), (18, 18, 22))
        for i, im in enumerate(ims):
            canvas.paste(im, (i * tw, 0))
        canvas.save(dst)

    picks = [int(FRAMES * r) for r in (0.0, 0.25, 0.5, 0.75, 1.0)]
    picks = sorted({min(len(fb) - 1, p) for p in picks})
    if not skip_whole:
        strip("A", fa, picks, OUT / "hy15_drift_A.png")
    strip("B", fb, picks, OUT / "hy15_drift_B.png")
    strip("C", fc, picks, OUT / "hy15_drift_C.png")
    print(f"  对照图已存 out/hy15_drift_A|B|C.png（横向为第 {picks} 帧；"
          f"{'无 A（整段会 OOM）' if skip_whole else '含 A'}）")

    hy15.release()


if __name__ == "__main__":
    # 用法： python tools/hy15_identity_drift.py [frames] [seed]
    # 默认 61 帧其实是「本就不需要分窗」的场景；真要评估分窗，应跑到 >81 帧
    # （整段已 OOM）的长度，例如 121。
    if len(sys.argv) > 1:
        FRAMES = int(sys.argv[1])
    if len(sys.argv) > 2:
        SEED = int(sys.argv[2])
    if len(sys.argv) > 3:
        WINDOW = int(sys.argv[3])
    main()
