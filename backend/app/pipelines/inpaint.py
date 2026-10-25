"""图片局部重绘：涂抹一块区域 + 一句提示词 → 只重画这一块。

**为什么不走管线原生 mask**：本仓库唯一的图片底座是 Qwen-Image-2.1（7B / 32 层 /
hidden 4096），它的 `QwenImage21Pipeline.__call__` 签名里**没有 `mask_image`**；
diffusers 里那些 `QwenImageInpaintPipeline` / `QwenImageEditInpaintPipeline` 属于
上一代 20B Qwen-Image 家族（transformer 层数与通道都不同），拿 2.1 的权重去装配
只会报 shape 不匹配。同类教训在 LoRA 上已经付过学费（跨代必挂），不值得再试一次。

所以走「模型画 + 确定性代码抠回选区」这条路——也正是本项目一贯的分工：
**模型负责像不像，后处理负责只改该改的地方、且看不出来改过。**

四个步骤，每一步解决一个真实缺陷：

1. **蒙版规整**：用户涂的区域往往紧贴要改的东西，模型没有「往哪长」的余地。
   先把蒙版外扩 `expand` px，再按包围盒取「选区 + 上下文」（crop 模式），
   并把边长对齐 16 的倍数（VAE 8× + patch 2× 的硬约束）。
2. **重绘**：crop 模式只送「选区 + 上下文」，同样的显存能看到更多局部细节，
   适合改一块具体的东西；full 模式整图重画，适合改光照/氛围/风格这类需要
   全局信息才能一致的活。两种模式贴回逻辑完全一样。
3. **接缝带色彩校正**：贴回用羽化蒙版，但**色彩校正只作用在边界向内的一条带**
   （`band = feather × 3`），区域中心保持模型给的新颜色。
   这条是实测结论：早期对整块选区做均值/方差匹配，「把衣服改成红色」会被
   拉回原色——校正的本意是消接缝，不该把用户要的改动一起抹平。
4. **色板对齐**（可选）：像素素材重画后必然出现新颜色，量化回源图色板
   就又是同一套颜色，拼贴感立刻消失。
"""
from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image, ImageFilter

from .. import progress as progress_mod
from . import palette as palette_mod

# 尺寸对齐：Qwen-Image-2.1 要求 32 的倍数（VAE 8× × patch 4×）。写成 16 时
# 管线会打印 "have to be divisible by 32 ... will be resized accordingly" 并
# **静默改尺寸**（2026-10-25 实测：304×320 被改动），出来的图再缩回原样就会
# 有细微的拉伸误差。按 32 对齐，管线拿到什么尺寸就按什么尺寸画。
ALIGN = 32
# 蒙版判有效的阈值：前端画的是纯白/纯透明，中间值只来自缩放插值
MASK_THRESH = 127
# 裁区域的最小边长（太小模型没有上下文），与最大边长（显存 / 时间上限）
CROP_MIN = 320
CROP_MAX = 1024

# --------------------------------------------------------------------------- #
# 发给模型的文本：全部可覆盖
# --------------------------------------------------------------------------- #
# 约束条款：这是「局部重绘」的立命之本——没有它，模型倾向于把整张图重画一遍，
# 结果就是选区外也跟着变（用户反馈里最刺眼的一类）。默认值写在程序里是因为
# 每条都是踩坑换来的，但界面上必须能改（提示词不能有藏起来的部分）。
DEFAULT_CONSTRAINT = (
    "这是一次局部重绘：只重画标记区域内的内容，标记区域以外的像素保持原样。"
    "区域内的新内容要延续区域外的既有信息——同一条轮廓线走向、同一个光源方向、"
    "同一套材质颗粒与明暗对比，边界处不要出现接缝、亮边或色块拼接感。"
    "不要改变画面构图、镜头、整体色调，不要添加标记区域以外的任何物体。"
)

# 负面词：只放「重绘最容易出的四种错」，不放画风词（画风由原图自己决定）
INPAINT_NEGATIVE = (
    "接缝, 边缘亮边, 色块拼接感, 明显色差, 模糊, 重影, 重复的肢体, "
    "多余的角色, 文字, 水印, 签名"
)

# 有多张参考图时追加的声明：不说清楚，模型会把参考图的构图一起抄进来
REF_CLAUSE = (
    "第 2 张起是参考图：只借用其中的造型、材质或配色，不要照抄它们的构图、"
    "背景与视角；成图仍以第 1 张（待重绘的原图）为准。"
)


def build_prompt(instruction: str, constraint: str = "",
                 ref_count: int = 0) -> Dict[str, Any]:
    """组装送进模型的整段提示词。

    返回拆解，界面要能显示「我写的 / 程序加的 / 参考图声明」分别是哪几段——
    提示词不允许有用户看不见的部分。

    顺序即优先级：用户正文在前（模型对开头更敏感），约束居中，参考图声明在后。
    """
    body = (instruction or "").strip()
    cons = (constraint or "").strip() or DEFAULT_CONSTRAINT
    ref_clause = REF_CLAUSE if ref_count > 0 else ""
    parts = [p for p in (body, cons, ref_clause) if p]
    return {
        "body": body,
        "constraint": cons,
        "defaultConstraint": DEFAULT_CONSTRAINT,
        "constraintCustom": bool((constraint or "").strip()),
        "refClause": ref_clause,
        "refCount": int(ref_count),
        "full": "\n".join(parts),
    }


# --------------------------------------------------------------------------- #
# 蒙版运算
# --------------------------------------------------------------------------- #
def load_mask(src: Any) -> Image.Image:
    """任意输入 → 单通道 L 蒙版（>127 为重绘区）。"""
    if isinstance(src, Image.Image):
        img = src
    else:
        img = Image.open(src)
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        # 有 alpha 的蒙版：按 alpha 判有效（前端导出的蒙版是白 + 透明）
        a = np.asarray(img.getchannel("A"))
        if a.min() < MASK_THRESH:
            return Image.fromarray(a, "L")
        return img.convert("L")
    return img.convert("L")


def mask_bbox(mask: Image.Image, thresh: int = MASK_THRESH
              ) -> Optional[Tuple[int, int, int, int]]:
    """蒙版的包围盒 (x0, y0, x1, y1)，右下开区间；空蒙版返回 None。"""
    a = np.asarray(mask.convert("L")) > thresh
    if not a.any():
        return None
    ys = np.nonzero(a.any(axis=1))[0]
    xs = np.nonzero(a.any(axis=0))[0]
    return int(xs[0]), int(ys[0]), int(xs[-1]) + 1, int(ys[-1]) + 1


def mask_coverage(mask: Image.Image, thresh: int = MASK_THRESH) -> float:
    a = np.asarray(mask.convert("L")) > thresh
    return round(float(a.mean()), 4) if a.size else 0.0


def dilate(mask: Image.Image, px: int) -> Image.Image:
    """二值膨胀（MaxFilter）。

    为什么用 PIL 的 MaxFilter 而不是 cv2：cv2 只在 GPU 依赖里（可选的），
    这个函数必须永远可用。MaxFilter 的核是方的，px 形参按「边长的一半」算，
    视觉上和圆形核差别可以忽略。
    """
    px = max(0, int(px))
    if px <= 0:
        return mask
    k = px * 2 + 1
    # MaxFilter 只接受奇数且 ≤ 9 左右的核；更大就用多次小核叠加
    out = mask
    while k > 1:
        step = min(k, 9)
        if step % 2 == 0:
            step -= 1
        if step < 3:
            break
        out = out.filter(ImageFilter.MaxFilter(step))
        k -= step - 1
    return out


def feather(mask: Image.Image, px: int) -> Image.Image:
    """羽化：模糊半径 ≥1 才做（0 = 硬边）。"""
    px = int(px)
    if px <= 0:
        return mask
    return mask.filter(ImageFilter.GaussianBlur(max(0.5, px / 2.0)))


def expand_box(box: Tuple[int, int, int, int], pad: int,
               size: Tuple[int, int], align: int = ALIGN
               ) -> Tuple[int, int, int, int]:
    """包围盒向外扩 pad，再按 align 对齐并夹到图内。"""
    x0, y0, x1, y1 = box
    pad = max(0, int(pad))
    x0 -= pad
    y0 -= pad
    x1 += pad
    y1 += pad
    W, H = size
    if align > 1:
        x0 = (x0 // align) * align
        y0 = (y0 // align) * align
        x1 = -((-x1) // align) * align
        y1 = -((-y1) // align) * align
    x0 = max(0, min(x0, W - align))
    y0 = max(0, min(y0, H - align))
    x1 = max(x0 + align, min(x1, W))
    y1 = max(y0 + align, min(y1, H))
    return int(x0), int(y0), int(x1), int(y1)


def fit_size(size: Tuple[int, int], *, lo: int = CROP_MIN,
             hi: int = CROP_MAX, align: int = ALIGN) -> Tuple[int, int]:
    """把要送模型的尺寸规整成「16 的倍数，长边落在 [lo, hi]」。

    小区域会被放大：模型在 512 上画的细节比在 160 上画得好，而放大再缩回
    （LANCZOS）丢的信息远少于让模型在极小画布上凑合。
    """
    w, h = max(1, int(size[0])), max(1, int(size[1]))
    long_side = max(w, h)
    scale = 1.0
    if long_side < lo:
        scale = lo / long_side
    elif long_side > hi:
        scale = hi / long_side
    w2 = int(round(w * scale / align)) * align
    h2 = int(round(h * scale / align)) * align
    return max(align, w2), max(align, h2)


# --------------------------------------------------------------------------- #
# 贴回：羽化 + 接缝带色彩校正
# --------------------------------------------------------------------------- #
def band_weight(mask: Image.Image, band: int) -> np.ndarray:
    """接缝带权重（0~1，float32）：蒙版边界**向内** band px 的过渡带。

    做法：blur(mask) 与 mask 相减——只有边界附近非零；取绝对值归一化后
    再乘回蒙版本身，保证只在选区内生效。区域中心权重≈0，所以中心的新颜色
    不会被拉走。
    """
    m = np.asarray(mask.convert("L"), dtype=np.float32) / 255.0
    if band <= 0:
        return np.zeros_like(m)
    blur = np.asarray(feather(mask, band * 2).convert("L"), dtype=np.float32) / 255.0
    w = np.clip(np.abs(m - blur) * 2.2, 0.0, 1.0) * m
    return w.astype(np.float32)


def color_transfer(gen: np.ndarray, src: np.ndarray, weight: np.ndarray,
                   strength: float) -> np.ndarray:
    """按权重把 gen 的通道均值/方差校正到 src（Reinhard 的局部版）。

    gen/src: (H,W,4) float32；weight: (H,W) float32；strength: 0~1。
    统计只在 weight>0.3 的像素上做——它们就是接缝带。
    """
    if strength <= 0:
        return gen
    sel = weight > 0.3
    if not sel.any():
        return gen
    out = gen.copy()
    for c in range(3):                       # alpha 不动：透明关系不能靠均值修
        g = gen[..., c][sel]
        s = src[..., c][sel]
        mg, sg = float(g.mean()), float(g.std())
        ms, ss = float(s.mean()), float(s.std())
        if sg < 1e-3:
            continue
        ratio = min(2.5, max(0.4, ss / sg))  # 方差比夹紧：防伪影被放大成噪点
        pred = (gen[..., c] - mg) * ratio + ms
        k = (weight * strength).astype(np.float32)
        out[..., c] = gen[..., c] * (1 - k) + pred * k
    return np.clip(out, 0, 255)


def composite(src: Image.Image, gen: Image.Image, mask: Image.Image, *,
              feather_px: int = 4, hard: bool = False,
              match_color: bool = True, match_strength: float = 0.45
              ) -> Image.Image:
    """把 gen 按蒙版贴回 src（尺寸必须已对齐）。

    羽化**只向内**（羽化后的权重再乘一次二值蒙版）：这样「选区外的像素一个都不
    变」是可以逐像素验证的硬承诺。向外羽化虽然过渡更软，但会把模型内容推到用户
    没涂的地方——那正是局部重绘最不该发生的事。

    alpha 也按同一权重混合：素材图带透明时，新区域的透明关系由模型结果决定。
    """
    if gen.size != src.size:
        gen = gen.resize(src.size, Image.LANCZOS)
    s = np.asarray(src.convert("RGBA"), dtype=np.float32)
    g = np.asarray(gen.convert("RGBA"), dtype=np.float32)

    inside = (np.asarray(mask.convert("L")) > MASK_THRESH).astype(np.float32)
    if hard:
        a = inside
        w = np.zeros_like(a)
    else:
        soft = np.asarray(feather(mask, feather_px).convert("L"), dtype=np.float32) / 255.0
        a = soft * inside
        w = (band_weight(mask, max(1, int(feather_px) * 3)) if match_color
             else np.zeros_like(a))
    if match_color and not hard:
        g = color_transfer(g, s, w, match_strength)

    a3 = a[..., None]
    out = s * (1 - a3) + g * a3
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGBA")


def snap_palette(gen: Image.Image, src: Image.Image, region: Image.Image,
                 colors: int = 48, tol: int = 30
                 ) -> Tuple[Image.Image, List[Tuple[int, int, int]], float]:
    """把重绘区里「接近已有颜色」的杂色吸回源图色板。

    **不是全量量化**。像素素材重画后常带一层新色（模型不受控），把颜色数压回
    源图色板就没有拼贴感；但如果用户就是要把这块改成红色、源图里根本没有红，
    全量量化会把红色按最近距离吸成灰——「要的功能被后处理吃掉」。
    所以只吸附**距离够近**（tol 以内，按 R2G4B3 加权）的颜色，差得远的新颜色
    原样保留。返回值里的比例就是「吸附了多少像素」，方便界面/指标解释。

    region 限定只改重绘区：区域外连 ±1 的量化抖动都不该出现。
    """
    arr = np.asarray(gen.convert("RGB"), dtype=np.uint8)
    pal = palette_mod.extract_palette([src], colors=colors, ignore_background=False)
    pal_arr = np.asarray(pal, dtype=np.int32)
    h, w, _ = arr.shape
    flat = arr.reshape(-1, 3).astype(np.int32)

    nearest = np.empty_like(flat)
    dist = np.empty(len(flat), dtype=np.float32)
    block = 65536
    for s in range(0, len(flat), block):
        chunk = flat[s:s + block]
        d = ((chunk[:, None, 0] - pal_arr[None, :, 0]) ** 2 * 2
             + (chunk[:, None, 1] - pal_arr[None, :, 1]) ** 2 * 4
             + (chunk[:, None, 2] - pal_arr[None, :, 2]) ** 2 * 3)
        idx = np.argmin(d, axis=1)
        nearest[s:s + block] = pal_arr[idx]
        dist[s:s + block] = d[np.arange(len(chunk)), idx]

    # 容差换算成加权距离：per-channel tol 时，三项平方和 ≈ tol²×(2+4+3)
    thr = float(tol) ** 2 * 9.0
    take = dist <= thr
    reg = np.asarray(region.convert("L")).reshape(-1) > MASK_THRESH
    take &= reg
    out = flat.copy()
    out[take] = nearest[take]
    changed = float(take.sum()) / max(1, int(reg.sum()))
    rgb = Image.fromarray(out.astype(np.uint8).reshape(h, w, 3), "RGB").convert("RGBA")
    rgb.putalpha(gen.convert("RGBA").getchannel("A"))
    return rgb, pal, round(changed, 3)


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def _generate(img: Image.Image, prompt: str, negative: Optional[str],
                    refs: Sequence[Image.Image], *,
                    steps: Optional[int], seed: Optional[int],
                    max_seq_len: Optional[int],
                    provider_cfg: Optional[Dict[str, Any]],
                    task_id: Optional[str],
                    progress: Optional[Dict[str, Any]] = None
                    ) -> Tuple[Image.Image, Dict[str, Any]]:
    """送底座重画一张（crop 或整图，由调用方决定送什么）。"""
    from ..providers.diffusers_local import DiffusersProvider

    provider = DiffusersProvider(provider_cfg or {})
    result = await provider.generate(
        prompt=prompt,
        width=img.width, height=img.height,
        steps=steps, seed=seed, n=1,
        refs=[img, *refs],
        negative=negative,
        progress=progress,
        task_id=task_id, max_seq_len=max_seq_len,
    )
    return result.images[0].convert("RGBA"), dict(result.meta or {})


async def run_inpaint(
    src: Image.Image,
    mask: Image.Image,
    *,
    instruction: str,
    constraint: str = "",
    negative: str = "",
    refs: Sequence[Image.Image] = (),
    mode: str = "crop",
    context: float = 0.35,
    feather_px: int = 4,
    expand: int = 8,
    hard: bool = False,
    match_color: bool = True,
    match_strength: float = 0.45,
    palette: str = "none",
    palette_colors: int = 48,
    palette_tol: int = 30,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    generate: Optional[Callable[..., Any]] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """局部重绘：返回 (合成结果, 元数据)。

    mask 必须是**单通道 L**、和 src 同尺寸；白色=重绘。
    `generate` 是测试注入点（默认走本地底座），签名与 `_generate` 一致。
    """
    t0 = time.time()
    src = src.convert("RGBA")
    mask = load_mask(mask)
    if mask.size != src.size:
        # 尺寸不一致一定是换了源图但没重涂蒙版：缩放在这里毫无意义
        raise ValueError(
            f"蒙版尺寸 {mask.size[0]}×{mask.size[1]} 与源图 "
            f"{src.width}×{src.height} 不一致：换图后要重新涂抹")
    # 「只有约束条款、没有用户正文」是合法的：去除局部的默认条款本身就是一句
    # 完整指令（「把这块的东西去掉，补成周围背景」），不能按空提示词拦掉。
    if not (instruction or "").strip() and not (constraint or "").strip():
        raise ValueError("提示词是空的：先写一句要重绘成什么")

    bbox = mask_bbox(mask)
    if bbox is None:
        raise ValueError("蒙版是空的：先用笔刷涂出要重画的区域")

    progress_mod.check_cancel(task_id)
    prompt_info = build_prompt(instruction, constraint, len(refs))
    seq_len = max_seq_len
    if not seq_len:
        from ..prompt_limits import fit_seq_len
        seq_len = fit_seq_len(prompt_info["full"])
    neg = (negative or "").strip() or INPAINT_NEGATIVE

    # 1) 蒙版规整：外扩给模型留余地；包围盒再向外带一圈上下文
    work = dilate(mask, expand)
    bx0, by0, bx1, by1 = mask_bbox(work) or bbox
    ctx = int(round(max(bx1 - bx0, by1 - by0) * max(0.0, min(float(context), 2.0))))
    box = expand_box((bx0, by0, bx1, by1), ctx, src.size)

    # 2) 决定送什么给模型
    if mode == "full":
        src_work = src
        box = (0, 0, src.width, src.height)
        target = fit_size(src.size, lo=256, hi=CROP_MAX)
    else:
        src_work = src.crop(box)
        target = fit_size(src_work.size)

    payload = src_work.resize(target, Image.LANCZOS) if src_work.size != target else src_work

    progress_mod.report(task_id, 0, 1, "重绘中…")
    gen_fn = generate or _generate
    gen_small, gmeta = await gen_fn(
        payload, prompt_info["full"], neg, list(refs),
        steps=steps, seed=seed, max_seq_len=seq_len,
        provider_cfg=provider_cfg, task_id=task_id,
        progress={"label": "局部重绘", "base": 0, "grand": 1},
    )
    progress_mod.check_cancel(task_id)

    # 3) 缩回原尺寸并贴回
    gen = gen_small if gen_small.size == src_work.size else gen_small.resize(
        src_work.size, Image.LANCZOS)
    mask_is_crop = mode != "full"
    mask_used = mask.crop(box) if mask_is_crop else mask
    base = src_work if mask_is_crop else src
    merged = composite(base, gen, mask_used,
                       feather_px=feather_px, hard=hard,
                       match_color=match_color and not hard,
                       match_strength=match_strength)

    warnings: List[str] = []
    palette_used = 0
    palette_snapped = 0.0
    if palette == "source":
        merged, pal, palette_snapped = snap_palette(
            merged, src, mask_used, colors=palette_colors, tol=palette_tol)
        palette_used = len(pal)
        if palette_snapped < 0.02:
            warnings.append(
                "色板对齐几乎没有生效：新颜色离源图色板都太远。"
                "想强行统一色数就用「像素精修」节点，而不是这里")

    if mask_is_crop:
        out = src.copy()
        out.paste(merged, box)
    else:
        out = merged

    cov = mask_coverage(mask)
    # 羽化太大时，贴回会把「区域外」的模型内容也带进来（羽化是对蒙版整体模糊），
    # 相对选区面积越大越明显。超过选区短边的 1/4 就提示——不拦，但要让人知道。
    min_side = min(bx1 - bx0, by1 - by0)
    if not hard and feather_px > max(2, min_side // 4):
        warnings.append(
            f"羽化 {feather_px}px 相对选区（短边 {min_side}px）偏大："
            "改动会溢出选区边缘一圈，想要干净边界就调小")
    if palette == "source" and palette_used > 96:
        warnings.append(f"源图色板有 {palette_used} 色，量化效果有限（像素素材通常 <64 色）")

    meta = {
        "mode": mode if mode in ("crop", "full") else "crop",
        "box": list(box),
        "cropSize": [box[2] - box[0], box[3] - box[1]],
        "targetSize": list(target),
        "coverage": cov,
        "context": context,
        "expand": expand,
        "feather": feather_px,
        "hard": bool(hard),
        "matchColor": bool(match_color) and not hard,
        "matchStrength": match_strength,
        "palette": palette,
        "paletteColors": palette_used,
        "paletteSnapped": palette_snapped,
        "prompt": prompt_info["full"],
        "promptBody": prompt_info["body"],
        "constraint": prompt_info["constraint"],
        "constraintCustom": prompt_info["constraintCustom"],
        "refCount": len(refs),
        "negative": neg,
        "seqLen": seq_len,
        "steps": gmeta.get("steps"),
        "seed": seed,
        "model": gmeta.get("model"),
        "seconds": round(time.time() - t0, 1),
        "warnings": warnings,
    }
    if gmeta.get("vram"):
        meta["vram"] = gmeta["vram"]
    progress_mod.report(task_id, 1, 1, "完成")
    return out, meta
