"""Edit-2511 + ControlNet：把「姿势」和「身份」拆成两条独立通道。

上一轮已证明（`tools/probe_ctrl.py`）：原版 20B 文生图 + ControlNet-Union 能把 12 帧
的朝向/迈步逐帧跟住，但角色只来自提示词。这一轮要补最后一块——身份来自用户的
四视图。

diffusers 没有 Edit + ControlNet 的现成管线，但 `QwenImageTransformer2DModel.forward`
本来就有 `controlnet_block_samples` 参数（ControlNet 的管线也是靠它注入残差）。所以
这里不改 diffusers 源码，而是**包一层 transformer.forward**：每步先跑一次 controlnet
拿到 block_samples，再带着它调原 forward。

两个必须注意的细节：
· Edit 管线把参考图 latent **拼在 latents 后面**（`cat([latents, image_latents])`），
  而 ControlNet 是在「只有生成图」的序列上训练的。所以喂给 controlnet 的
  hidden_states 要切回前 N 个 token，img_shapes 也只给它生成图那一条。
· controlnet 的 cond 必须按 control 管线的口径编码：VAE encode → (x-mean)/std → pack。

用法：.venv/Scripts/python.exe tools/probe_edit_ctrl.py [帧号] [控制图类型]
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import torch
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc          # noqa: E402
from tools_path import board_cells, sheet_cells    # noqa: E402

BASE = ROOT / "models" / "qwen-image-20b"                  # TE / VAE / scheduler 复用
EDIT = ROOT / "models" / "qwen-image-edit-2511"            # transformer（fp8）
CNET = ROOT / "models" / "qwen-image-controlnet-union"
OUT = ROOT / "out"
SIZE = 512
STEPS = 30

PROMPT = (
    "把图1 里的角色画成控制图里那个姿势：同一个角色（发型、发色、服装配色、佩饰、武器"
    "完全照图1），但身体朝向、重心、四肢方位、手脚落点全部照控制图。"
    "全身像素风游戏精灵立绘，纯白背景，不要文字、不要水印、不要多个角色。"
)
NEG = "模糊, 低质量, 水印, 文字, 多个角色, 四视图, 分屏, 网格线, 地面, 阴影, 灰色剪影"


def control_image(src: Image.Image, kind: str) -> Image.Image:
    """与 probe_ctrl 同口径：黑底白剪影（实测比白底深灰更贴合官方 pose 示例）。"""
    import numpy as np
    a = np.asarray(src.convert("RGBA"))
    m = a[..., 3] > 8
    if kind == "black":
        flat = np.zeros((*m.shape, 3), np.uint8)
        flat[m] = (255, 255, 255)
        return Image.fromarray(flat)
    if kind == "sil":
        flat = np.full((*m.shape, 3), 255, np.uint8)
        flat[m] = (110, 110, 110)
        return Image.fromarray(flat)
    raise SystemExit(f"未知控制图 {kind}")


def pack_latents(latents, b, c, h, w):
    x = latents.view(b, c, h // 2, 2, w // 2, 2)
    x = x.permute(0, 2, 4, 1, 3, 5)
    return x.reshape(b, (h // 2) * (w // 2), c * 4)


def encode_control(vae, img: Image.Image, h: int, w: int, device, dtype):
    """control 图 → packed latent（口径抄 pipeline_qwenimage_controlnet.py）。"""
    im = img.convert("RGB").resize((w, h), Image.LANCZOS)
    x = torch.from_numpy(
        __import__("numpy").asarray(im).astype("float32") / 127.5 - 1.0
    ).permute(2, 0, 1)[None, :, None]                     # (1,3,1,h,w)
    x = x.to(device=device, dtype=dtype)
    # 必须 no_grad：不包的话 VAE 编码会把整条前向的激活图留在显存里等反向传播，
    # 512² 的 Qwen 3D VAE 一次就吃 4.0GB（实测 0.24GB → 4.26GB 且 empty_cache 不回落）。
    with torch.no_grad():
        lat = vae.encode(x).latent_dist.mode()
    z_dim = vae.config.z_dim
    mean = torch.tensor(vae.config.latents_mean, device=device).view(1, z_dim, 1, 1, 1)
    std = 1.0 / torch.tensor(vae.config.latents_std, device=device).view(1, z_dim, 1, 1, 1)
    lat = (lat - mean) * std
    lat = lat.permute(0, 2, 1, 3, 4)
    # 通道数取 z_dim（不是 permute 后的 shape[1]，那一位是时间维=1）
    lat = pack_latents(lat, 1, z_dim, lat.shape[3], lat.shape[4])
    return lat.to(dtype=dtype)


def load_vae():
    """先把 VAE 单独挂到显存：控制图/参考图都要编码，必须在 transformer 加载前做完，
    否则 512² 的编码临时张量会和 19.1GB 权重抢显存，直接顶到 24GB 静默卡死
    （实测：管线就绪后 4 分钟零输出，GPU 100%）。"""
    from diffusers import AutoencoderKLQwenImage
    vae = AutoencoderKLQwenImage.from_pretrained(
        str(BASE / "vae"), torch_dtype=torch.bfloat16).to("cuda")
    vae.enable_tiling()
    return vae


def build(vae):
    from diffusers import (QwenImageControlNetModel, QwenImageEditPlusPipeline,
                           FlowMatchEulerDiscreteScheduler)
    from transformers import (Qwen2_5_VLForConditionalGeneration, Qwen2Tokenizer,
                              Qwen2VLProcessor)

    from app.providers.diffusers_local import DiffusersProvider
    from q20b import load_fp8_transformer

    t0 = time.time()
    print("[edit] 加载 Edit-2511 fp8 transformer…", flush=True)
    tf8 = Path(os.environ.get("EDIT_TF8") or (EDIT / "transformer-fp8"))
    transformer = load_fp8_transformer(tf8)
    print(f"[edit] transformer 就绪 {time.time() - t0:.0f}s", flush=True)

    controlnet = QwenImageControlNetModel.from_pretrained(str(CNET), torch_dtype=torch.bfloat16)
    DiffusersProvider._quantize_transformer_gpu(controlnet)
    controlnet.to("cuda")

    scheduler = FlowMatchEulerDiscreteScheduler.from_pretrained(str(BASE / "scheduler"))
    tokenizer = Qwen2Tokenizer.from_pretrained(str(BASE / "tokenizer"))
    processor = Qwen2VLProcessor.from_pretrained(str(EDIT / "processor"))
    text_encoder = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        str(BASE / "text_encoder"), torch_dtype=torch.bfloat16,
        low_cpu_mem_usage=True).eval()

    pipe = QwenImageEditPlusPipeline(
        scheduler=scheduler, vae=vae, text_encoder=text_encoder, tokenizer=tokenizer,
        processor=processor, transformer=transformer)
    pipe.set_progress_bar_config(disable=True)
    print(f"[edit] 管线就绪 {time.time() - t0:.0f}s，"
          f"显存 {torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)

    # 文本编码器留在 CPU（bf16 15.8GB 进显存会把 transformer 挤掉）。Edit 管线的
    # encode_prompt 是「图文一起进 VLM」，内部按 device 搬张量，所以只需把 device
    # 强制成 cpu，再把产出的 embeds 送回显存。不这么做会卡在 embed 阶段不动
    # （2026-10-27 实测：管线就绪后 4 分钟没有任何输出）。
    _enc = pipe.encode_prompt

    def _enc_cpu(*a, **kw):
        kw["device"] = "cpu"
        e, m = _enc(*a, **kw)
        return e.to("cuda"), (m.to("cuda") if m is not None else None)

    pipe.encode_prompt = _enc_cpu
    return pipe, controlnet


def attach_control(pipe, controlnet, cond, scale=1.0):
    """把 controlnet 的残差注入 Edit 管线的 transformer 前向。

    **只能挂一次**：每次调用都会把上一层的 forward 当成 orig 包起来，连挂 N 次
    就变成每步跑 N 次 controlnet（显存和耗时都翻倍）——批量跑时表现为
    「第二帧开始越来越慢、最后不动」。所以 cond/scale 允许传一个可变 dict，
    换帧时只改它的内容，不重新包 forward。
    """
    if getattr(pipe.transformer, "_cn_attached", False):
        holder = pipe.transformer._cn_holder
        holder["cond"] = cond if not isinstance(cond, dict) else cond["cond"]
        holder["scale"] = scale
        return
    holder = {"cond": cond["cond"] if isinstance(cond, dict) else cond, "scale": scale}
    pipe.transformer._cn_holder = holder
    pipe.transformer._cn_attached = True
    orig = pipe.transformer.forward

    def patched(*args, **kw):
        hs = kw["hidden_states"]
        shapes = kw["img_shapes"]
        cond = holder["cond"]
        scale = holder["scale"]
        # 生成图那一段 token 数 = (h/16) * (w/16)；Edit 把参考图 token 拼在后面
        n_lat = shapes[0][0][1] * shapes[0][0][2]
        cn = controlnet(
            hidden_states=hs[:, :n_lat],
            controlnet_cond=cond,
            conditioning_scale=scale,
            encoder_hidden_states=kw.get("encoder_hidden_states"),
            encoder_hidden_states_mask=kw.get("encoder_hidden_states_mask"),
            timestep=kw.get("timestep"),
            img_shapes=[shapes[0][:1]],
            return_dict=False,
        )   # return_dict=False 时直接返回 block_samples 列表，**不要再取 [0]**
            # （取 [0] 拿到的是第一个 block 的张量，后面会被当成列表去迭代）
        # Edit 管线把参考图 token 拼在生成 latent **后面**，而 controlnet 是在
        # 「只有生成图」的序列上算的，残差只有 n_lat 个 token。直接相加会报
        # `size of tensor a (5120) must match tensor b (1024)`。给残差尾部补零，
        # 参考图那段的 hidden_states 就等于没被控制网改动，语义正好。
        total = hs.shape[1]
        if total > n_lat:
            def _pad(c, _total=total, _n=n_lat):
                if c.shape[1] == _total:
                    return c
                shape = (c.shape[0], _total - _n) + tuple(c.shape[2:])
                return torch.cat([c, c.new_zeros(shape, dtype=c.dtype)], dim=1)
            cn = [_pad(c) for c in cn]
        kw["controlnet_block_samples"] = cn
        return orig(*args, **kw)

    pipe.transformer.forward = patched


def main() -> None:
    k = int(sys.argv[1]) if len(sys.argv) > 1 else 4
    kind = sys.argv[2] if len(sys.argv) > 2 else "black"
    src, _ = pc._ensure_fg(sheet_cells()[k])
    # 四视图第一格（正面）当身份参考。**必须先去 alpha**：直接喂 RGBA 会让 VAE 收到
    # 4 通道输入，报 `expected input[1,4,...] to have 3 channels`。透明区铺白底。
    cell = board_cells()[int(os.environ.get("REF_CELL", "0"))].convert("RGBA")
    bg = Image.new("RGBA", cell.size, (255, 255, 255, 255))
    cell = Image.alpha_composite(bg, cell).convert("RGB")
    vae = load_vae()
    t0 = time.time()
    cond = encode_control(vae, control_image(src, kind), SIZE, SIZE, "cuda", vae.dtype)
    torch.cuda.empty_cache()
    print(f"[edit] 控制图编码 {time.time() - t0:.0f}s，"
          f"显存 {torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)

    # 参考图的 latent 会跟生成图的 latent **拼成一条序列**送进 transformer：
    # 管线默认按 VAE_IMAGE_SIZE=1024² 编码参考图 = 4096 个 token，加上 1024 个
    # 生成 token 就是 5120 长序列，24GB 卡直接顶满（实测卡在第一步不动）。
    # 参考图本来只有 256px 的立绘，缩到 512²（1024 token）不掉信息量，序列砍一半。
    import diffusers.pipelines.qwenimage.pipeline_qwenimage_edit_plus as _ep
    _ep.VAE_IMAGE_SIZE = int(os.environ.get("REF_AREA", "512")) ** 2

    pipe, controlnet = build(vae)
    attach_control(pipe, controlnet, cond, scale=float(os.environ.get("CN_SCALE", "1.0")))

    t1 = time.time()

    def _cb(pipe_, step, t, kw, _t0=[t1]):
        print(f"      step {step + 1}/{STEPS} {time.time() - _t0[0]:.0f}s "
              f"{torch.cuda.memory_allocated() / 2**30:.1f}GB", flush=True)
        return kw

    out = pipe(
        callback_on_step_end=_cb,
        image=[cell],
        prompt=PROMPT, negative_prompt=NEG,
        height=SIZE, width=SIZE, num_inference_steps=STEPS,
        true_cfg_scale=4.0,
        generator=torch.Generator("cuda").manual_seed(1234),
    ).images[0].convert("RGB")
    name = f"editctrl_{kind}_{k}_s{os.environ.get('CN_SCALE', '1.0')}_r{os.environ.get('REF_CELL', '0')}"
    out.save(OUT / f"{name}.png")
    print(f"   {name}: {time.time() - t1:.0f}s 峰值 "
          f"{torch.cuda.max_memory_allocated() / 2**30:.1f}GB", flush=True)
    print("done", flush=True)


if __name__ == "__main__":
    main()
