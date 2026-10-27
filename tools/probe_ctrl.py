"""ControlNet 可行性验证：原版 Qwen-Image 20B + InstantX ControlNet-Union 能不能定姿势。

要回答的问题只有一个（不管身份像不像）：
    「给一张侧身迈步的姿势控制图，20B 会不会真的画成侧身迈步？」
如果连这个都做不到，后面「换 Edit 底座做身份融合」就没必要投入了。

身份退化成文字描述（这是 t2i 底座的固有限制），所以提示词里把角色写全：
黑发束起、青蓝色宽袖长袍、腰佩长剑的东方女剑客。

用法：.venv/Scripts/python.exe tools/probe_ctrl.py [帧号] [控制图类型...]
      控制图类型：sil（剪影纯色图）/ src（原图）/ edge（轮廓线）
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc          # noqa: E402
from q20b import load_fp8_transformer              # noqa: E402
from tools_path import board_cells, sheet_cells    # noqa: E402

BASE = ROOT / "models" / "qwen-image-20b"
CNET = ROOT / "models" / "qwen-image-controlnet-union"
OUT = ROOT / "out"
SIZE = 512
STEPS = 30

CHAR = ("像素风游戏角色立绘：一位黑发高高束起、穿青蓝色宽袖长袍、腰佩长剑的东方女剑客，"
        "全身像，站在纯白背景前，没有任何文字和水印。")
NEG = "模糊, 低质量, 水印, 文字, 多个角色, 四视图, 分屏, 网格线, 地面, 阴影"


def control_image(src: Image.Image, kind: str) -> Image.Image:
    """把一帧动作源图变成 ControlNet 的结构控制图。

    官方示例（conds/pose.png）是 DWPose 那种「黑底 + 彩虹四肢」的渲染，
    但 DWPose 在 120px 像素小人身上提不出关键点（这是这条路线的硬约束）。
    所以这里只用**不依赖检测器**的几种结构图，看哪一种能让 20B 跟着动：
        sil   白底深灰剪影（最接近"深度/分割"的观感）
        black 黑底白剪影（贴近 pose/depth 示例的配色习惯）
        depth 距离变换当深度图（中间亮、边缘暗）
        skel  中轴线（用 skimage 骨骼化，单色描边，最弱的骨架近似）
    """
    a = np.asarray(src.convert("RGBA"))
    m = a[..., 3] > 8
    if kind == "src":
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[m] = a[..., :3][m]
        return Image.fromarray(flat)
    if kind == "sil":
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[m] = (110, 110, 110)
        return Image.fromarray(flat)
    if kind == "black":
        flat = np.zeros((*m.shape, 3), np.uint8)
        flat[m] = (255, 255, 255)
        return Image.fromarray(flat)
    if kind == "depth":
        from scipy import ndimage
        d = ndimage.distance_transform_edt(m)
        d = (d / max(d.max(), 1e-6) * 255).astype(np.uint8)
        return Image.fromarray(np.repeat(d[..., None], 3, axis=2))
    if kind == "skel":
        from skimage.morphology import skeletonize
        sk = skeletonize(m)
        flat = np.zeros((*m.shape, 3), np.uint8)
        flat[sk] = (255, 255, 255)
        # 骨骼线太细（1px），膨胀一点，否则 VAE 编码后基本消失
        from scipy import ndimage
        grown = ndimage.binary_dilation(sk, iterations=2)
        flat[grown] = (255, 255, 255)
        return Image.fromarray(flat)
    if kind == "edge":
        from scipy import ndimage
        edge = m & ~ndimage.binary_erosion(m, iterations=1)
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[edge] = (0, 0, 0)
        return Image.fromarray(flat)
    raise SystemExit(f"未知控制图类型 {kind}")


def build_pipeline():
    from diffusers import (AutoencoderKLQwenImage, QwenImageControlNetModel,
                           QwenImageControlNetPipeline,
                           FlowMatchEulerDiscreteScheduler)
    from transformers import Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer

    from app.providers.diffusers_local import DiffusersProvider

    t0 = time.time()
    print("[20B] 加载 fp8 transformer…", flush=True)
    transformer = load_fp8_transformer(BASE / "transformer-fp8")
    print(f"[20B] transformer 就绪 {time.time() - t0:.0f}s", flush=True)

    controlnet = QwenImageControlNetModel.from_pretrained(
        str(CNET), torch_dtype=torch.bfloat16)
    DiffusersProvider._quantize_transformer_gpu(controlnet)
    controlnet.to("cuda")
    print(f"[20B] controlnet 就绪（fp8）{time.time() - t0:.0f}s", flush=True)

    vae = AutoencoderKLQwenImage.from_pretrained(
        str(BASE / "vae"), torch_dtype=torch.bfloat16).to("cuda")
    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(str(BASE / "scheduler"))
    tokenizer = Qwen2Tokenizer.from_pretrained(str(BASE / "tokenizer"))
    # 文本编码器留在 CPU（bf16 15.8GB 进显存会直接挤爆 transformer），
    # encode_prompt 支持显式传 device，编完只把 embeds 交给 GPU。
    text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        str(BASE / "text_encoder"), torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True).eval()
    pipe = QwenImageControlNetPipeline(
        scheduler=scheduler, vae=vae, text_encoder=text_encoder,
        tokenizer=tokenizer, transformer=transformer, controlnet=controlnet)
    pipe.set_progress_bar_config(disable=True)
    # VAE 解码要额外 1~3GB，而 transformer(19.1) + controlnet(1.7) 已经吃掉 21GB。
    # 不做分块时实测会「步进跑完 30/30 然后卡在解码」——GPU 100%、显存顶着
    # 24GB、不报错也不出图（就是老问题里的静默卡死）。分块解码只多花 1~2s。
    try:
        pipe.vae.enable_tiling()
    except Exception as e:
        print(f"[20B] VAE 分块不可用（{e}）", flush=True)
    print(f"[20B] 管线就绪 {time.time() - t0:.0f}s，"
          f"显存 {torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)
    return pipe


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    example = "--example" in sys.argv
    k = int(args[0]) if args else 4
    kinds = args[1:] or ["sil", "src"]
    cells = sheet_cells()
    src, _ = pc._ensure_fg(cells[k])
    pipe = build_pipeline()

    if example:
        # 先用官方示例自证管线：同一张 conds/pose.png 应该画出"坐在窗台上的男人"。
        # 如果连这一步都不像，问题在加载/管线，不在我们的控制图。
        ctrl = Image.open(CNET / "conds" / "pose.png").convert("RGB")
        text = ("一个年轻男人坐在矮墙上，双腿自然垂向地面，身穿黑色皮夹克、灰色T恤、"
                "棕色长裤、白色运动鞋、棕色鸭舌帽；身后是米色墙面和一个巨大的圆形窗户，"
                "窗外是黄昏城市天际线")
        neg_text = "模糊, 低质量, 水印, 文字"
        outs = [("example", ctrl)]
    else:
        text, neg_text = CHAR, NEG
        outs = [(kind, control_image(src, kind)) for kind in kinds]

    t0 = time.time()
    pos, pos_mask = pipe.encode_prompt(text, device="cpu", max_sequence_length=512)
    neg, neg_mask = pipe.encode_prompt(neg_text, device="cpu", max_sequence_length=512)
    print(f"[20B] 文本编码 {time.time() - t0:.0f}s（CPU）", flush=True)

    for tag, ctrl in outs:
        t1 = time.time()
        torch.cuda.reset_peak_memory_stats()

        def _cb(pipe_, step, t, kw, _t0=[t1]):
            if step % 5 == 0 or step == STEPS - 1:
                print(f"      step {step + 1}/{STEPS} {time.time() - _t0[0]:.0f}s "
                      f"{torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)
            return kw          # 必须回传：管线拿返回值当下一步的输入字典

        out = pipe(
            prompt=None, negative_prompt=None,
            prompt_embeds=pos.to("cuda"), prompt_embeds_mask=pos_mask,
            negative_prompt_embeds=neg.to("cuda"), negative_prompt_embeds_mask=neg_mask,
            control_image=ctrl, controlnet_conditioning_scale=1.0,
            height=SIZE, width=SIZE, num_inference_steps=STEPS,
            true_cfg_scale=4.0, generator=torch.Generator("cuda").manual_seed(1234),
            callback_on_step_end=_cb,
        ).images[0].convert("RGB")
        name = f"ctrl_{tag}" if example else f"ctrl_{tag}_{k}"
        out.save(OUT / f"{name}.png")
        print(f"   {name}: {time.time() - t1:.0f}s 峰值显存 "
              f"{torch.cuda.max_memory_allocated() / 2**30:.1f}GB", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
