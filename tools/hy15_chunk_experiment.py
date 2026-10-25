"""hy15 分窗（链式）端到端验证（实验 B，临时脚本，可删）。

目的：验证新加的分窗 I2V 在「整段必然 OOM 的长度」上能跑通，并核对：
  1) 输出帧数正确（= 目标帧数）；
  2) 峰值显存被压平在每个 ~21 帧段的水平（vs 整段同长度会顶破 24G OOM）；
  3) 段间接缝：把每个 clip 边界的前后几帧存成条带图，肉眼/后续检查连续性。

实测基准（实验 A，480x848 默认分辨率）：整段单步 21→3.39s / 33→4.54s /
49→6.69s / 61→8.83s，峰值显存 16.6→20.4GB——单步近似线性，真正的墙是显存
（外推 ~81 帧顶破 24G）。故分窗的价值是「显存压平 → 任意长度可跑」而非提速。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF_IMAGE = ROOT / "data/projects/p-0d3f091b4c3c/images/vs-20261025-221119-46635a63.png"
PROMPT = ("角色侧视行走循环：双腿以自然步幅持续交替迈步，膝盖随步伐屈伸，"
          "双脚依次离地、前摆、落地，步态平稳连贯；身体随重心轻微上下颠动；"
          "保持取景框与背景完全静止，角色始终位于画面中心、体型大小不变，"
          "不向画面任何方向平移、不旋转身体、镜头不动。")
NEGATIVE = "模糊, 重影, 低清, 闪烁, 肢体变形, 站桩, 单帧定格, 姿势不变, 动作僵硬"

FRAMES = 121      # 整段这个长度必 OOM；分窗成 6×21
# 旧默认 21（产品默认自 2026-10-26 起是 41），这里保留以便复现当时的结论。
WINDOW = 21
STEPS = 12
SEED = 42


def main() -> None:
    import math

    import torch
    from PIL import Image

    from backend.app.providers import hy15

    if not REF_IMAGE.exists():
        print(f"[expB] 参考图缺失：{REF_IMAGE}", flush=True)
        # 兜底：合成一个简单主体，保证实验能跑
        img = Image.new("RGB", (512, 512), (200, 200, 200))
        print("[expB] 用合成灰图代替", flush=True)
    else:
        img = Image.open(REF_IMAGE).convert("RGBA")
        print(f"[expB] 参考图 {REF_IMAGE.name} 尺寸={img.size}", flush=True)

    clips = max(1, math.ceil((FRAMES - WINDOW) / (WINDOW - 1)) + 1)
    print(f"[expB] 目标 {FRAMES} 帧 / 分窗 {WINDOW} → 预计 {clips} 段，{STEPS} 步/段", flush=True)

    print("[expB] 编码文本…", flush=True)
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)
    print(f"[expB] embeds cached={embeds.get('cached')}", flush=True)

    if torch.cuda.is_available():
        torch.cuda.reset_peak_memory_stats()
    t0 = time.time()
    frames, meta = hy15.run_i2v(
        img, embeds, frames=FRAMES, steps=STEPS, guidance=1.0, seed=SEED, window=WINDOW)
    dt = time.time() - t0

    peak = round(torch.cuda.max_memory_allocated() / 2**30, 2) if torch.cuda.is_available() else None
    resv = round(torch.cuda.max_memory_reserved() / 2**30, 2) if torch.cuda.is_available() else None

    print(f"\n[expB] === 结果 ===", flush=True)
    print(f"  输出帧数      : {len(frames)}（目标 {FRAMES}）{'OK' if len(frames)==FRAMES else 'MISMATCH'}", flush=True)
    print(f"  分窗 window   : {meta.get('window')}  → {clips} 段", flush=True)
    print(f"  单帧尺寸      : {meta.get('size')}", flush=True)
    print(f"  总墙钟        : {dt:.1f}s  平均 {dt/clips:.1f}s/段", flush=True)
    print(f"  峰值显存      : {peak}GB  reserved {resv}GB（整段 61 帧实测 20.4GB；121 整段会 OOM）", flush=True)
    print(f"  meta          : { {k:meta[k] for k in ('frames','steps','seconds','window') if k in meta} }", flush=True)

    # 接缝条带：每个 clip 边界处取 [末帧, 次新帧 | 新段首帧(丢), 新段第2帧]
    w, h = meta.get("size") or (frames[0].width, frames[0].height)
    boundaries = []
    idx = 0
    for c in range(clips - 1):
        # 第 c 段覆盖 [idx .. idx+WINDOW-1]，下一段从 idx+WINDOW-1 接续
        b = idx + WINDOW - 1
        boundaries.append((c + 1, b))
        idx = b
    strip_frames = []
    labels = []
    for c, b in boundaries:
        for off in (-2, -1, 0, 1, 2):
            j = min(max(b + off, 0), len(frames) - 1)
            strip_frames.append(frames[j].resize((w // 4, h // 4)))
            labels.append(f"c{c}@{j}")
    if strip_frames:
        cols = 5
        rows = len(strip_frames) // cols
        sheet = Image.new("RGB", (cols * (w // 4), rows * (h // 4)), (30, 30, 30))
        for i, fr in enumerate(strip_frames):
            sheet.paste(fr, ((i % cols) * (w // 4), (i // cols) * (h // 4)))
        out = ROOT / "out/hy15_chunk_seam.png"
        out.parent.mkdir(parents=True, exist_ok=True)
        sheet.save(out)
        print(f"  接缝条带      : {out}（每行一个 clip 边界：左2帧=上一段末尾，右2帧=下一段开头）", flush=True)

    try:
        hy15.release()
    except Exception:
        pass
    print("[expB] done", flush=True)


if __name__ == "__main__":
    main()
