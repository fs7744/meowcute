"""批量跑 12 帧：一个进程、一次加载、逐帧出图（稳定性验证 + 产物）。

用法：.venv/Scripts/python.exe tools/run_batch20.py [尺寸] [帧号,帧号,...]
默认 512 尺寸、跑全部 12 帧。每帧打印耗时/降级情况，最后拼 montage 和 gif。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc                      # noqa: E402
from app.pipelines.qwen20_edit import (DEFAULT_NEGATIVE, DEFAULT_PROMPT,  # noqa: E402
                                       Qwen20EditControl)
from tools_path import board_cells, sheet_cells                 # noqa: E402

OUT = ROOT / "out"
# 源精灵图 3 列 × 4 行 = 12 帧，按行是「正面 / 侧面 / 侧面 / 背面」，
# 参考格必须跟着行选，否则 Edit 的"复制立绘"先验会把每一帧都画成正脸站桩。
ROW_CELL = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]


def main() -> None:
    size = int(sys.argv[1]) if len(sys.argv) > 1 else 512
    want = ([int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2
            else list(range(12)))
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]

    eng = Qwen20EditControl(size=size, steps=30, cfg=4.0)
    eng.ensure()
    t0 = time.time()
    pos, pos_mask = eng.pipe.encode_prompt(DEFAULT_PROMPT, device="cpu", max_sequence_length=512)
    neg, neg_mask = eng.pipe.encode_prompt(DEFAULT_NEGATIVE, device="cpu", max_sequence_length=512)
    eng.drop_text_encoder()
    embeds = {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}
    print(f"[batch] 提示词就绪 {time.time() - t0:.0f}s", flush=True)

    outs, stats = [], []
    for k in want:
        t1 = time.time()
        img, meta = eng.run_frame(control=cells[k], ref=refs[ROW_CELL[k]],
                                  embeds=embeds, seed=1000 + k)
        img.save(OUT / f"b20_{size}_{k:02d}.png")
        outs.append(img)
        stats.append((k, meta))
        print(f"[batch] 帧 {k:02d} {time.time() - t1:.0f}s size={meta['size']} "
              f"cfg={meta['cfg']} degraded={meta['degraded']}", flush=True)

    if len(outs) == 12:
        W = size // 2
        mont = Image.new("RGB", (W * 6, W * 2), (255, 255, 255))
        for i, im in enumerate(outs):
            mont.paste(im.resize((W, W), Image.LANCZOS), ((i % 6) * W, (i // 6) * W))
        mont.save(OUT / f"b20_{size}_montage.png")
        outs[0].save(OUT / f"b20_{size}_walk.gif", save_all=True,
                     append_images=outs[1:], duration=140, loop=0, optimize=True)
    bad = [k for k, m in stats if m["degraded"] or m.get("stalled")]
    print(f"[batch] 完成 {len(outs)} 帧，降级/卡死 {bad or '无'}", flush=True)


if __name__ == "__main__":
    main()
