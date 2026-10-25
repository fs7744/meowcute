"""按 UI 提示里的三个配置实测 hy15 耗时（临时脚本，可删）。

UI 上那段提示是「384x512 约 55s/方向 / 512x704 约 100s/方向」的旧数字，
但那是在 hy15 还没接通分辨率（实际一直跑 640x640）时留下的，已失真。
本脚本用 hy15 在 49 帧 / 12 步下实测三个配置的实际输出尺寸与耗时，
供把提示改成真实数字。

做法：先小尺寸预热（吃掉模型加载），再逐个配置计时（含 VAE 编解码等固定开销，
即端到端「一方向一条视频」的真实耗时）。
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
FRAMES, STEPS = 49, 12
CASES = [(384, 512, "速度优先"), (480, 640, "折中"), (512, 704, "画质优先")]


def main() -> None:
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    embeds = hy15.encode_texts(PROMPT, NEGATIVE)

    print("[timing] 预热（加载模型，不计时）…", flush=True)
    hy15.run_i2v(img, embeds, frames=13, steps=2, guidance=1.0, seed=1,
                 width=256, height=256)

    for w, h, label in CASES:
        torch.cuda.empty_cache()
        torch.cuda.reset_peak_memory_stats()
        torch.cuda.synchronize()
        t0 = time.time()
        frames, meta = hy15.run_i2v(img, embeds, frames=FRAMES, steps=STEPS,
                                    guidance=1.0, seed=42, width=w, height=h)
        torch.cuda.synchronize()
        dt = time.time() - t0
        peak = (meta.get("vram") or {}).get("peakGB")
        # 顺带量动作幅度 / 稳定性（motion=1-IoU，instability=剪影面积波动%）
        try:
            from backend.app.pipelines.walkvideo import motion_stats
            ms = motion_stats(frames)
            m, ins = round(ms.get("motion", 0), 1), round(ms.get("instability", 0), 1)
        except Exception as e:
            m, ins = None, None
        print(f"[timing] {label}({w}x{h}) → 实际{meta.get('size')}  {len(frames)}帧  "
              f"{dt:6.1f}s  峰值{peak}GB  motion={m} instability={ins}%", flush=True)

    hy15.release()
    print("[timing] done", flush=True)


if __name__ == "__main__":
    main()
