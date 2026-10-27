"""12 帧全跑：验证 ControlNet 能不能把一整套行走循环的「朝向 + 姿态」都跟住。

只跑一次模型加载（40s），之后每帧 512×512 / 30 步约 22s，12 帧约 5 分钟。
控制图默认用 black（黑底白剪影），实测它比白底深灰更贴合官方 pose 示例的

用法：.venv/Scripts/python.exe tools/probe_ctrl12.py [black|depth|sil|skel]
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "backend"))

from app.pipelines import poseclone as pc          # noqa: E402
from probe_ctrl import CHAR, NEG, SIZE, STEPS, build_pipeline, control_image  # noqa: E402
from tools_path import sheet_cells                  # noqa: E402

OUT = ROOT / "out"


def main() -> None:
    kind = sys.argv[1] if len(sys.argv) > 1 else "black"
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    pipe = build_pipeline()
    t0 = time.time()
    pos, pos_mask = pipe.encode_prompt(CHAR, device="cpu", max_sequence_length=512)
    neg, neg_mask = pipe.encode_prompt(NEG, device="cpu", max_sequence_length=512)
    print(f"[20B] 文本编码 {time.time() - t0:.0f}s", flush=True)

    outs = []
    for k, src in enumerate(cells):
        t1 = time.time()
        out = pipe(
            prompt=None, negative_prompt=None,
            prompt_embeds=pos.to("cuda"), prompt_embeds_mask=pos_mask,
            negative_prompt_embeds=neg.to("cuda"), negative_prompt_embeds_mask=neg_mask,
            control_image=control_image(src, kind),
            controlnet_conditioning_scale=1.0,
            height=SIZE, width=SIZE, num_inference_steps=STEPS,
            true_cfg_scale=4.0,
            generator=torch.Generator("cuda").manual_seed(1234),
        ).images[0].convert("RGB")
        out.save(OUT / f"ctrls_{kind}_{k:02d}.png")
        outs.append(out)
        print(f"   帧 {k:02d} {time.time() - t1:.0f}s", flush=True)

    # 12 帧四列排开（3 列 × 4 行 对应源精灵图的布局），方便一眼看出朝向是否跟着变
    W = SIZE // 2
    mont = Image.new("RGB", (W * 6, W * 4), (255, 255, 255))
    for i, im in enumerate(outs):
        mont.paste(im.resize((W, W), Image.LANCZOS), ((i % 6) * W, (i // 6) * W))
    mont.save(OUT / f"ctrls_{kind}_12.png")
    print("done", flush=True)


if __name__ == "__main__":
    main()
