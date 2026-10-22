# -*- coding: utf-8 -*-
"""实验：Qwen-Image-Layered 拆角色部件 —— 用「生成部件」替代「算法抠部件」。

背景：rigwalk2.py 证明算法切割扛不住披风+粘连腿（固定关节模板对不上 chibi
比例，披风像素被分给四肢骨骼一转就撕）。本实验换路线：让分层生成模型直接
产出带 alpha 的部件层——语义拆分交给模型（它擅长的），切割问题直接消失。

步骤：
  1. 参考图（.verify/rigwalk2/front-char.png，正面 A-pose 像素角色）
  2. QwenImageLayeredPipeline（fp8 transformer + cpu offload，模型已预下载）
  3. layers=6：头 / 躯干 / 左臂 / 右臂 / 左腿 / 右腿
  4. 每层存 RGBA PNG；各层按顺序叠加 vs 原图并排存校验图

产出：.verify/layered/layer-*.png + recompose-check.png
后续（不在本脚本）：层→部件锚点标定 → rigwalk2 骨架 → 行走 GIF
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

OUT = ROOT / ".verify" / "layered"
OUT.mkdir(parents=True, exist_ok=True)

MODEL = str(ROOT / "models" / "qwen-image-layered")
REF = ROOT / ".verify" / "rigwalk2" / "front-char.png"

PROMPT = (
    "把图中像素游戏角色分解为身体部件层，共 6 层：头部、躯干（含披风）、"
    "左臂、右臂、左腿、右腿。每个部件单独占一层，部件之间不重叠，"
    "保持原有像素风格和颜色，层叠加后能完整还原角色。"
)


def main():
    import torch
    from PIL import Image

    from diffusers import QwenImageLayeredPipeline
    from backend.app.providers.diffusers_local import (
        DiffusersProvider, _enable_offload, _release_vram)

    t0 = time.time()
    prov = DiffusersProvider({})
    transformer = prov._load_qwen_transformer_fp8(MODEL)
    t_load = time.time() - t0
    print(f"[t] fp8 transformer 加载 {t_load:.0f}s", flush=True)

    pipe = QwenImageLayeredPipeline.from_pretrained(
        MODEL, torch_dtype=torch.bfloat16, transformer=transformer)
    _enable_offload(pipe, "qwen_layered")
    pipe.set_progress_bar_config(disable=True)
    print(f"[t] 管线就绪 {time.time() - t0:.0f}s", flush=True)

    ref = Image.open(REF).convert("RGBA")
    ref = ref.resize((640, 640), Image.LANCZOS)
    ref.save(OUT / "ref-640.png")

    gen = torch.Generator("cpu").manual_seed(42)
    try:
        torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass

    t1 = time.time()
    out = pipe(
        image=ref,
        prompt=PROMPT,
        negative_prompt=" ",   # 必须传（哪怕空串）才真正启用 true_cfg_scale
        layers=6,
        num_inference_steps=40,
        true_cfg_scale=4.0,
        generator=gen,
    )
    dt = time.time() - t1
    print(f"[t] 推理 {dt:.0f}s", flush=True)

    imgs = out.images if hasattr(out, "images") else out
    print(f"[i] 输出类型 {type(imgs).__name__} len={len(imgs)}", flush=True)
    for i, im in enumerate(imgs):
        if isinstance(im, (list, tuple)):
            print(f"[i] 第 {i} 组是嵌套列表 len={len(im)}", flush=True)
            for j, sub in enumerate(im):
                sub.save(OUT / f"layer-{i}-{j}.png")
                print(f"    saved layer-{i}-{j}.png mode={sub.mode} size={sub.size}", flush=True)
        else:
            im.save(OUT / f"layer-{i}.png")
            print(f"    saved layer-{i}.png mode={im.mode} size={im.size}", flush=True)

    # 重构校验：把所有层按顺序 alpha 叠加，与原图并排
    flat = []
    for im in imgs:
        flat.extend(im) if isinstance(im, (list, tuple)) else flat.append(im)
    if flat:
        base = Image.new("RGBA", ref.size, (255, 255, 255, 255))
        for im in flat:
            rgba = im.convert("RGBA")
            if rgba.size != ref.size:
                rgba = rgba.resize(ref.size, Image.LANCZOS)
            base.alpha_composite(rgba)
        check = Image.new("RGB", (ref.width * 2 + 8, ref.height), (32, 32, 32))
        check.paste(ref, (0, 0))
        check.paste(base.convert("RGB"), (ref.width + 8, 0))
        check.save(OUT / "recompose-check.png")
        print(f"[i] 重构校验图 recompose-check.png", flush=True)

    try:
        torch.cuda.synchronize()
        peak = torch.cuda.max_memory_allocated() / 2**30
        print(f"[vram] peak={peak:.1f}GB", flush=True)
    except Exception:
        pass
    _release_vram()
    print(f"DONE 总耗时 {time.time() - t0:.0f}s 输出目录 {OUT}", flush=True)


if __name__ == "__main__":
    main()
