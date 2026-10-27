"""模式 A：原版 20B 文生图 + ControlNet（姿势跟得住、身份靠文字）。

和模式 B（Edit-2511 + ControlNet，身份准、姿势会漂）做 A/B 对照用的脚本。
关键差异：这里**没有参考图**和模型打架，控制网说了算——实测 12 帧朝向全部跟住。

角色描述照着用户那张四视图写（黑发高髻、淡青广袖长袍、白内衬、金腰带、黑短靴、佩剑），
所以出图虽然不是「本人」，但配色/配饰一致。

用法：.venv/Scripts/python.exe tools/probe_t2i12.py [帧号,帧号,...]
"""
from __future__ import annotations

import gc
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc                      # noqa: E402
from app.pipelines.qwen20_edit import _control_image          # noqa: E402
from app.pipelines.qwen20_loader import load_fp8_transformer   # noqa: E402
from tools_path import sheet_cells                            # noqa: E402

OUT = ROOT / "out"
BASE = ROOT / "models" / "qwen-image-20b"
CNET = ROOT / "models" / "qwen-image-controlnet-union"

PROMPT = ("像素风游戏角色立绘：一位黑发高高束起的东方女剑客，穿淡青色广袖长袍、白色内衬、"
          "金色腰带、黑色短靴，腰间佩一柄直刃长剑，剑柄系青绿色剑穗；"
          "全身像，纯白背景，不要文字，不要水印。")
NEG = "模糊, 低质量, 水印, 文字, 多个角色, 四视图, 分屏, 网格线, 地面, 阴影"
SIZE = 512
STEPS = 30


def main() -> None:
    want = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else list(range(12))
    kind = "src" if "--src" in sys.argv else "black"
    tag = "a20s" if kind == "src" else "a20"
    from diffusers import (AutoencoderKLQwenImage, FlowMatchEulerDiscreteScheduler,
                           QwenImageControlNetModel, QwenImageControlNetPipeline)
    from transformers import Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer

    from app.providers.diffusers_local import DiffusersProvider

    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    t0 = time.time()
    transformer = load_fp8_transformer(BASE / "transformer-fp8")
    controlnet = QwenImageControlNetModel.from_pretrained(str(CNET), torch_dtype=torch.bfloat16)
    DiffusersProvider._quantize_transformer_gpu(controlnet)
    controlnet.to("cuda")
    vae = AutoencoderKLQwenImage.from_pretrained(str(BASE / "vae"), torch_dtype=torch.bfloat16).to("cuda")
    vae.enable_tiling()
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(str(BASE / "scheduler"))
    tokenizer = Qwen2Tokenizer.from_pretrained(str(BASE / "tokenizer"))
    text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        str(BASE / "text_encoder"), torch_dtype=torch.bfloat16, low_cpu_mem_usage=True).eval()
    pipe = QwenImageControlNetPipeline(
        scheduler=scheduler, vae=vae, text_encoder=text_encoder, tokenizer=tokenizer,
        transformer=transformer, controlnet=controlnet)
    pipe.set_progress_bar_config(disable=True)
    print(f"[t2i] 管线就绪 {time.time() - t0:.0f}s", flush=True)

    # 提示词先编好，编完把 15.8GB 文本编码器扔掉（否则内存被压死，帧会慢到像卡死）
    enc = pipe.encode_prompt

    def _enc_cpu(*a, **kw):
        kw["device"] = "cpu"
        e, m = enc(*a, **kw)
        return e.to("cuda"), (m.to("cuda") if m is not None else None)

    pipe.encode_prompt = _enc_cpu
    pos, pos_mask = pipe.encode_prompt(PROMPT, device="cpu", max_sequence_length=512)
    neg, neg_mask = pipe.encode_prompt(NEG, device="cpu", max_sequence_length=512)
    pipe.text_encoder = None
    text_encoder = None
    gc.collect()
    print("[t2i] 提示词就绪，已释放文本编码器", flush=True)

    for k in want:
        ctrl = _control_image(cells[k], kind)
        t1 = time.time()
        out = pipe(
            prompt=None, negative_prompt=None,
            prompt_embeds=pos, prompt_embeds_mask=pos_mask,
            negative_prompt_embeds=neg, negative_prompt_embeds_mask=neg_mask,
            control_image=ctrl, controlnet_conditioning_scale=1.0,
            height=SIZE, width=SIZE, num_inference_steps=STEPS,
            true_cfg_scale=4.0, generator=torch.Generator("cuda").manual_seed(1000 + k),
        ).images[0].convert("RGB")
        out.save(OUT / f"{tag}_{k:02d}.png")
        print(f"[t2i] 帧 {k:02d} {time.time() - t1:.0f}s", flush=True)
        gc.collect()
        torch.cuda.empty_cache()


if __name__ == "__main__":
    main()
