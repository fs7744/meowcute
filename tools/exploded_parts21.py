# -*- coding: utf-8 -*-
"""用 Qwen-Image-2.1 生成「爆炸拆解图」并切成 6 个骨骼部件。

背景：edit-2511 已从项目移除（权重归档），但爆炸图这条路线仍是拆部件最可靠的做法
（连通域切块，不依赖任何抠图算法）。本脚本把生成步骤换成当前在役的
Qwen-Image-2.1，切块与角色定位直接复用 exp_exploded_parts.py 里已验证的函数。

为什么部件要单独生成而不是从原图抠：见 tools/exp_exploded_parts.py 的模块文档——
「部件之间的分离是生成出来的，不是算出来的」。

用法：
  python tools/exploded_parts21.py --ref .verify/hy15/cell0.png --out .verify/panda/parts
  python tools/exploded_parts21.py --ref ... --out ... --seed 11 --steps 40
产出：<out>/exploded.png、<out>/<role>.png、<out>/parts.json（rigwalk_ghibli 直接读）
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

from exp_exploded_parts import (  # noqa: E402
    ROLES, assign_roles, complete_with_mirror, split_parts,
)

QWEN = ROOT / "models" / "qwen-image-2.1"

PROMPT = (
    "把参考图里的角色画成一张爆炸拆解图（exploded view），用于角色部件动画。"
    "请严格按下面的网格摆放 6 个互不接触、各自完整的部件，背景纯白、没有装饰、"
    "部件之间留出明显空隙：\n"
    "【第一行·最上方中央】头部（含发/耳/头盔等头部所有元素）——头部必须是独立漂浮的"
    "孤岛，下巴和脖子以下完全悬空，与下方躯干之间留出一块清晰的纯白横向空隙\n"
    "【第二行·左右两侧】左臂在画面左、右臂在画面右（每侧一条完整手臂，带手和手套）\n"
    "【第三行·正中】躯干（无头无臂，肩膀处断开，保留所有躯干装具、背心、背包）\n"
    "【第四行·左右两侧】左腿在画面左、右腿在画面右（每侧一条完整腿，含鞋）\n"
    "要求：六件必须全部出现、左右肢体都要有、缺一不可；严格保持参考图里角色的长相、"
    "配色、服装细节与画风；手中或身上的武器/道具跟着它所在的部件一起画"
    "（拿在手里的武器归到那只手臂或躯干），不要单独成件；正面平视，不要背面、侧面、"
    "姿势歪斜、肢体粘连，不要解剖图风格。"
)
NEG = "低清，模糊，肢体粘连，头部连接躯干，脖子相连，头和身体连成一体，缺件，多余部件，网格线，标注文字，阴影"


def build_pipeline():
    """按组件分别加载 + fp8，同 exp_qwen21.build_pipeline（避免 31G 一次性进内存）。"""
    import torch

    from diffusers import QwenImage21Pipeline, QwenImage21Transformer2DModel
    from transformers import Qwen3VLForConditionalGeneration

    from backend.app.providers.diffusers_local import DiffusersProvider, _enable_offload

    if not QWEN.is_dir():
        raise SystemExit(f"模型不存在：{QWEN}")
    provider = DiffusersProvider({"model": str(QWEN)})
    print("加载 Qwen transformer（fp8）…", flush=True)
    tf = provider._load_component_fp8(str(QWEN), "transformer", QwenImage21Transformer2DModel, "qwen21.transformer")
    print("加载 Qwen text encoder（fp8）…", flush=True)
    te = Qwen3VLForConditionalGeneration.from_pretrained(str(QWEN / "text_encoder"), torch_dtype=torch.bfloat16)
    provider._quantize_fp8(te, "qwen21.text_encoder")
    pipe = QwenImage21Pipeline.from_pretrained(str(QWEN), transformer=tf, text_encoder=te,
                                              torch_dtype=torch.bfloat16)
    _enable_offload(pipe, "qwen21")
    pipe.set_progress_bar_config(disable=True)
    return pipe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--steps", type=int, default=40)
    ap.add_argument("--cfg", type=float, default=4.0)
    ap.add_argument("--size", type=int, default=768, help="生成边长")
    args = ap.parse_args()

    ref_path = Path(args.ref)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    ref = Image.open(ref_path).convert("RGBA")
    if ref.getextrema()[-1][0] < 255:                      # 有透明：铺白底再喂
        bg = Image.new("RGBA", ref.size, (255, 255, 255, 255))
        ref = Image.alpha_composite(bg, ref)
    ref = ref.convert("RGB")
    ref.save(out_dir / "ref.png")

    pipe = build_pipeline()
    t0 = time.time()
    img = pipe(
        image=ref, prompt=PROMPT, negative_prompt=NEG,
        width=args.size, height=args.size, output_resolution=args.size,
        num_inference_steps=args.steps, true_cfg_scale=args.cfg,
        generator=__import__("torch").Generator("cpu").manual_seed(args.seed),
    ).images[0]
    print(f"爆炸图 {time.time()-t0:.1f}s", flush=True)
    img.save(out_dir / "exploded.png")

    parts = split_parts(img, min_area=300)
    print(f"[i] 连通域 {len(parts)} 块", flush=True)
    roles = assign_roles(parts)
    mirrored = complete_with_mirror(roles)
    present = {r: roles[r] is not None for r in ROLES}
    print(f"[i] 角色定位：{present}", flush=True)
    print(f"[i] 镜像补件：{[r for r in ROLES if mirrored[r]]}", flush=True)

    meta = {}
    for r in ROLES:
        if roles[r] is None:
            continue
        fp = out_dir / f"{r}.png"
        roles[r]["img"].save(fp)
        meta[r] = {"file": fp.name, "bbox": list(roles[r]["bbox"]),
                   "area": roles[r]["area"], "mirrored": mirrored[r]}
    (out_dir / "parts.json").write_text(
        json.dumps({"roles": meta, "ref": str(ref_path), "prompt": PROMPT},
                   ensure_ascii=False, indent=2), encoding="utf-8")

    # 调试条带：每个槽位一张，带标签
    cells = [(roles[r]["img"], f"{r}{'*' if mirrored[r] else ''}") for r in ROLES if roles[r]]
    pad = 10
    cw = max(im.width for im, _ in cells) + pad * 2
    ch = max(im.height for im, _ in cells) + pad * 2 + 16
    strip = Image.new("RGB", (cw * len(cells), ch), (255, 255, 255))
    dr = ImageDraw.Draw(strip)
    for i, (im, tag) in enumerate(cells):
        strip.paste(im, (i * cw + pad, pad), im)
        dr.text((i * cw + pad, pad + im.height + 2), tag, fill=(120, 120, 120))
    strip.save(out_dir / "parts-strip.png")

    missing = [r for r in ROLES if roles[r] is None]
    print(f"缺失部件：{missing or '无'}", flush=True)
    print(f"DONE {out_dir}", flush=True)
    return 0 if not missing else 2


if __name__ == "__main__":
    raise SystemExit(main())
