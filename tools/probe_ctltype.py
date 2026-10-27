"""控制图类型对照：剪影 vs 官方训练过的 canny / depth / soft_edge。

ControlNet-Union v2 官方只训了四种条件：canny / depth / pose / soft_edge。
剪影（黑底白块）是**离分布**输入，控制信号天然弱——这大概就是姿势一直带不动的
原因之一。这里用同一帧、同样的身份参考，只换控制图类型做对照。

用法：.venv/Scripts/python.exe tools/probe_ctltype.py [帧号,帧号,...]
"""
from __future__ import annotations

import sys
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
ROW_CELL = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]
KINDS = ["black", "depth", "canny", "softedge"]


def main() -> None:
    want = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [4, 10]
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]

    eng = Qwen20EditControl(size=512, steps=30, cfg=4.0)
    eng.ensure()
    cache = eng.warm_prompts([DEFAULT_PROMPT, DEFAULT_NEGATIVE])
    pos, pos_mask = cache[DEFAULT_PROMPT]
    neg, neg_mask = cache[DEFAULT_NEGATIVE]
    embeds = {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}
    print("[ctl] 提示词就绪", flush=True)

    for k in want:
        ref, src = refs[ROW_CELL[k]], cells[k]
        for kind in KINDS:
            img, meta = eng.run_frame(control=src, ref=ref, kind=kind,
                                      embeds=embeds, seed=1000 + k)
            img.save(OUT / f"ct_{kind}_{k:02d}.png")
            print(f"[ctl] {kind} 帧{k} {meta['seconds']}s", flush=True)
        # 顺手把控制图导出一张
        from app.pipelines.qwen20_edit import _control_image
        _control_image(src, kind).save(OUT / f"ctmap_{kind}_{k:02d}.png")


if __name__ == "__main__":
    main()
