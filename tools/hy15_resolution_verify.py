"""分辨率验证 v2：预热 + 解方程测真实单步（临时脚本，可删）。

v1 的教训（又踩了一次固定开销）：
  第一个用例 43.7s 里含约 30s 的**模型懒加载**，而后续用例复用已加载管线，
  于是「3.38x/5.53x」被加载时间严重高估。

v2 做法：
  1) 先跑一次小尺寸预热（把模型加载掉，不计时）；
  2) 每个分辨率测 T(4) 与 T(12)，S=(T12-T4)/8 消掉每次调用的固定开销
     （VAE 编码参考图 + SigLIP + latent 准备 + VAE 解码）；
  3) 顺便核对实际输出尺寸是否等于用户所选。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

REF = ROOT / "data/projects/p-0d3f091b4c3c/images/vs-20261025-221119-46635a63.png"
PROMPT = "角色侧视行走循环：双腿自然交替迈步，步态平稳连贯，取景框与背景完全静止。"
NEGATIVE = "模糊, 重影, 低清, 闪烁, 肢体变形, 站桩, 单帧定格"
FRAMES = 21


def main() -> None:
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)

    def run(w, h, steps):
        torch.cuda.synchronize()
        t0 = time.time()
        frames, meta = hy15.run_i2v(img, embeds, frames=FRAMES, steps=steps,
                                    guidance=1.0, seed=42, width=w, height=h)
        torch.cuda.synchronize()
        return time.time() - t0, meta, len(frames)

    print("[v2] 预热（加载模型，不计时）…", flush=True)
    run(256, 256, 4)

    cases = [(0, 0, "旧行为auto"), (384, 512, "UI默认"), (256, 256, "极小档")]
    rows = []
    for w, h, label in cases:
        dt4, meta4, n4 = run(w, h, 4)
        dt12, meta12, n12 = run(w, h, 12)
        s = (dt12 - dt4) / 8.0
        o = dt4 - 4 * s
        size = meta12.get("size")
        rows.append((label, w, h, size, s, o, n12, dt12))
        print(f"[v2] {label:10s} 请求({w},{h}) → 实际{size}  "
              f"真实单步 {s:5.2f}s  固定开销≈{o:5.1f}s  12步总时 {dt12:6.1f}s", flush=True)

    base = rows[0]
    print("\n[v2] === 汇总（真实单步，已消固定开销）===", flush=True)
    for label, w, h, size, s, o, n12, dt12 in rows:
        print(f"  {label:10s} ({w},{h})→{size}  单步 {s:5.2f}s  "
              f"vs 旧行为 {base[4]/s:.2f}x（看单步，不看总时）", flush=True)

    hy15.release()
    print("[v2] done", flush=True)


if __name__ == "__main__":
    main()
