"""Review 用：量化 hy15 三项优化对「输出质量」的影响。

Part 1  VAE round-trip 累积损失
        链式分窗里，clip k 的首帧条件是 clip k-1 的**末帧**（已经过一次 VAE 解码），
        下一段又要重新 VAE 编码 → 每段一次有损往返。量化 N 次往返后的像素损失，
        N=3/6 正好对应分窗的段数。

Part 2  整段 vs 分窗 A/B（同一参考图、同一提示词、同一种子、同帧数）
        - motion / instability：项目已有指标（剪影，对明暗免疫）
        - 接缝比：段边界处的相邻帧差 ÷ 段内相邻帧差均值（>1 = 接缝处有跳变）
        - 长程漂移：每帧与首帧的差异随帧索引的变化

两个 Part 都严格建立在「可量化指标」上，而不是目视判断。
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
# 旧默认 21（产品默认自 2026-10-26 起是 41），这里保留以便复现当时的结论。
WINDOW = 21
SEED = 42
OUT = Path("out")


# --------------------------------------------------------------- 指标
def _masks_from_frames(frames):
    """复用项目自带的剪影算法，保证指标口径与 walkvideo 一致。"""
    from backend.app.pipelines.walkvideo import _silhouette

    return [_silhouette(f) for f in frames]


def pair_diffs(masks):
    """相邻帧剪影 1-IoU 序列（越小平滑，越大表示跳变/闪烁）。"""
    import numpy as np

    out = []
    for i in range(len(masks) - 1):
        a, b = masks[i], masks[i + 1]
        union = bool_ = np.logical_or(a, b).sum()
        inter = np.logical_and(a, b).sum()
        out.append(100.0 * (1.0 - inter / max(1, union)))
    return out


def first_frame_iou(masks):
    """每帧与首帧的剪影相似度，用来看「长相是否一路渐变」。"""
    import numpy as np

    m0 = masks[0]
    res = []
    for m in masks:
        union = np.logical_or(m, m0).sum()
        inter = np.logical_and(m, m0).sum()
        res.append(100.0 * inter / max(1, union))
    return res


def report(name, frames, seam_indices=()):
    """输出一组完整指标。seam_indices 是段边界的 pair 索引（0-based，pair(i,i+1)）。"""
    from backend.app.pipelines.walkvideo import motion_stats

    ms = motion_stats(frames)
    masks = _masks_from_frames(frames)
    pd_ = pair_diffs(masks)
    if pd_:
        import numpy as np

        all_mean = float(np.mean(pd_))
        seam = [pd_[i] for i in seam_indices if i < len(pd_)]
        non = [v for i, v in enumerate(pd_) if i not in seam_indices]
        seam_mean = float(np.mean(seam)) if seam else float("nan")
        non_mean = float(np.mean(non)) if non else float("nan")
        ratio = seam_mean / non_mean if non_mean else float("nan")
        print(f"  [{name}] motion={ms['motion']:5.2f}  instability={ms['instability']:5.1f}%")
        print(f"      相邻帧差 段内均值={non_mean:5.2f}  段间(接缝)={seam_mean:5.2f}  "
              f"接缝/段内={ratio:4.2f}x  (1.0=完美连续)")
        fft = first_frame_iou(masks)
        print(f"      与首帧剪影相似度: 首={fft[0]:5.1f} 中={fft[len(fft)//2]:5.1f} "
              f"末={fft[-1]:5.1f}  最大衰减={max(fft)-min(fft):5.1f}")
        return pd_, fft
    return pd_, []


def save_strip(name, frames, picks, dst):
    """抽若干帧拼成一横条，供目视复核（ES/SES 均有客观指标兜底）。"""
    from PIL import Image

    ims = [frames[i].convert("RGB") for i in picks if i < len(frames)]
    if not ims:
        return
    tw, th = 120, 170
    canvas = Image.new("RGB", (tw * len(ims), th + 16), (20, 20, 24))
    for i, im in enumerate(ims):
        canvas.paste(im.resize((tw, th)), (i * tw, 16))
    canvas.save(dst)
    print(f"      已保存图示 {dst}")


PART1_QUALITY = True


def part1(pipe):
    """VAE round-trip：把参考图反复 encode→decode，量化每段的累积损失。"""
    import numpy as np
    import torch
    from PIL import Image

    print("\n=== Part 1  VAE round-trip 累积损失（链式每段一次的损耗）===")
    img = Image.open(REF).convert("RGB")
    img = img.resize((W, H), Image.Resampling.LANCZOS)
    base = (torch.from_numpy(np.asarray(img, dtype=np.float32))
            .permute(2, 0, 1)[None].float())
    # 视频 VAE 要 5D 输入 (B,C,F,H,W)：单帧时 F=1
    # 注意：必须始终在 [-1,1] 域循环。第一版把 [0,255] 的像素结果直接喂回编码器，
    # 得到「第 2 次就崩到 5.8dB」的假结论——那是脚本 bug，不是模型问题。
    x01 = base / 255.0                                   # [0,1]
    x = (x01 * 2.0 - 1.0).to("cuda", torch.bfloat16).unsqueeze(2)  # [-1,1]，并补时间维 F=1
    ref_img = x01[0].permute(1, 2, 0).numpy() * 255.0
    sf = pipe.vae.config.scaling_factor
    pipe.vae.to("cuda")
    cur = None
    try:
        for i in range(1, 7):
            with torch.no_grad():
                src = x if cur is None else cur
                lat = pipe.vae.encode(src).latent_dist.mode() * sf
                y = pipe.vae.decode(lat / sf, return_dict=False)[0]
                cur = y.float().clamp(-1, 1).to(torch.bfloat16)   # 仍在 [-1,1]
            arr = ((cur[0, :, 0].float().cpu().permute(1, 2, 0).numpy() + 1.0) / 2.0) * 255.0
            if i == 1:
                print(f"  （参考：一次 encode→decode 本身就有 L1={float(np.abs(arr-ref_img).mean()):.2f}，"
                      f"这是 VAE 的固有损失）")
            l1 = float(np.abs(arr - ref_img).mean())
            rmse = float(np.sqrt(((arr - ref_img) ** 2).mean()))
            psnr = float(20 * np.log10(255.0 / max(1e-6, rmse)))
            tag = "  ← 对应分窗 3 段" if i == 3 else ("  ← 对应分窗 6 段" if i == 6 else "")
            print(f"  第 {i} 次往返: 与原图 L1={l1:6.2f}/255  近似PSNR={psnr:5.1f}dB{tag}")
    finally:
        pipe.vae.to("cpu")
        torch.cuda.empty_cache()


def main():
    import torch

    from backend.app.pipelines.walkvideo import motion_stats  # noqa: F401  口径复用
    from backend.app.providers import hy15

    OUT.mkdir(exist_ok=True)
    img = None
    if REF.exists():
        from PIL import Image

        img = Image.open(REF).convert("RGBA")
    else:
        from PIL import Image

        img = Image.new("RGB", (512, 512), (200, 200, 200))
        print("!! 参考图不存在，用灰色占位，指标仅供参考")

    emb = hy15.encode_texts(PROMPT, NEG)

    # Part 1 用真实管线，顺带完成加载（后面 A/B 不再付加载成本）
    pipe = hy15.get_pipeline()
    part1(pipe)

    print(f"\n=== Part 2  整段 vs 分窗  ({FRAMES} 帧 / {STEPS} 步 / {W}x{H} / seed={SEED}) ===")

    # ---- A：整段 ----
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    fa, ma = hy15.run_i2v(img, emb, frames=FRAMES, steps=STEPS, guidance=1.0,
                          seed=SEED, window=0, width=W, height=H)
    ta = time.time() - t0
    print(f"  A 整段  → {len(fa)}帧 {ma.get('size')} {ta:6.1f}s "
          f"峰值{(ma.get('vram') or {}).get('peakGB')}GB")
    pa, _ia = report("A 整段", fa)

    # ---- B：分窗 ----
    torch.cuda.empty_cache()
    torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    fb, mb = hy15.run_i2v(img, emb, frames=FRAMES, steps=STEPS, guidance=1.0,
                          seed=SEED, window=WINDOW, width=W, height=H)
    tb = time.time() - t0
    # 拼接方式：seg0 = 0..WINDOW-1；seg k 追加 window-1 帧 → 边界 pair = WINDOW-1, 2*(WINDOW-1)...
    # 拼接方式：seg0=0..WINDOW-1；seg k 再追加 window-1 帧
    # → 边界 pair 索引 = (WINDOW-1) 开始每 (WINDOW-1) 一个
    seam_idx = [(WINDOW - 1) * (j + 1) for j in range(6)]
    seam_idx = [i for i in seam_idx if i < len(fb) - 1]
    print(f"  B 分窗(win={WINDOW}) → {len(fb)}帧 {mb.get('size')} {tb:6.1f}s "
          f"峰值{(mb.get('vram') or {}).get('peakGB')}GB  接缝pair索引={seam_idx}")
    pb, _ib = report(f"B 分窗win={WINDOW}", fb, seam_indices=seam_idx)

    print(f"\n  耗时对比: 整段 {ta:6.1f}s  分窗 {tb:6.1f}s  (分窗/整段 = {tb/ta:4.2f}x)")

    # ---- 图示：抽帧对比（含接缝两侧）----
    picks = [0, 10, 20, 21, 30, 40, 41, 50, 60]
    save_strip("A", fa, picks, OUT / "hy15_qa_A_whole.png")
    save_strip("B", fb, picks, OUT / "hy15_qa_B_chunked.png")

    hy15.release()
    print("\n[done] 详细指标见上，图示见 out/hy15_qa_*.png")


if __name__ == "__main__":
    from backend.app.providers import hy15 as _h

    if len(sys.argv) > 1 and sys.argv[1] == "part1":
        part1(_h.get_pipeline())
        _h.release()
    else:
        main()
