"""动作复刻：把别人的动作序列，套到自己角色身上（逐帧多图编辑）。

用的是什么能力：Qwen-Image-Edit（2509 / Plus）原生支持 1~3 张参考图的
**多图合成**，官方推荐用法就是「按编号明确引用」——"图1 的角色 / 图2 的姿势"。
所以这里每次只喂两张：①要复刻的动作帧 ②目标角色的设定板。喂四张单独的
四视图会掉进「参考图超过 3 张、一致性下降」的坑里，这也是为什么界面上建议
直接传一张拼好的四视图。

为什么不「整张精灵图一次改完」（用户最先想到的省事路径）：实测这类模型对
「N 个姿势 + 严格网格布局」的保持力很弱——它会重排、合并、少画几格，而格子
错位对精灵图是致命的（引擎切图全靠等距网格）。所以默认逐帧复刻，每帧一次
推理，可控、可单帧重跑；整图一次改只作为「草稿预览」选项保留（见 mode）。

复刻完必做一件事：**把生成帧平移对齐回源帧的落脚点**（`_align_to`）。
不加这一步，模型每帧会自己重新构图，角色在画面里上下游走，重新落格之后
就是明显的整体抖动——这和之前查出的「逐帧 bbox 居中放大抖动」是同一类问题，
但方向相反：那次是后处理引入的，这次是模型引入的，必须先按源帧锚点拉回来。
对齐只做**整体平移**（整数像素吸附），不做逐帧缩放：缩放会把真实动作里
本来就有的大小变化（下蹲、抬腿）一起抹掉。
"""
from __future__ import annotations

import math
import time
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod

# 默认指令：界面上必须可见、可改（项目规矩：送给模型的文本不允许写死在程序里）。
# 占位符用 {帧号} {总帧数} {背景} 这种中文花括号，避免和用户正文里的 {} 打架，
# 替换走 str.replace 而不是 format。
DEFAULT_INSTRUCTION = (
    # 开头这两句是整条链最关键的地方（2026-10-27 用用户真实素材实测出来的）。
    # 老指令只说「用图2 的角色一比一复刻图1」，从没说过**图1 里的角色不是要画的
    # 对象**。用户的动作源是「光头肉色像素小人」、四视图是「青蓝古风剑客」，
    # 结果模型每次都把那个光头小人原样抄一遍（肤色占比 0.56、青蓝 0.00）。
    # 明确写「图1 只给姿势、它的外观一个都不要保留」之后，肤色降到 0.00。
    "图1 是**动作参考**，它只提供姿势、朝向、以及在画面中的位置和大小。\n"
    "特别注意：图1 里出现的那个角色（可能是灰色剪影、光头、肤色人台、"
    "或者任何别的角色）**不是要画的对象**——它的发型、肤色、服装、体型、"
    "配色，一个都不要保留。\n"
    "图2 是目标角色的立绘（同一个角色的一个视角），"
    "它是角色外观的**唯一来源**。\n"
    "请画出这样的画面：**用图2 里的角色，摆出图1 的姿势**"
    "（第 {帧号}/{总帧数} 帧）。\n"
    "· 外观：脸型、发型、发色、瞳色、服装款式与配色、配饰、武器、纹样，"
    "全部以图2 为准，不要改动、不要简化；\n"
    "· 姿势：身体姿态、四肢角度、躯干倾斜、重心、朝向、视线方向，"
    "与图1 完全一致，不要美化、不要加戏；\n"
    "· 构图：角色在画面中的位置、大小比例、朝向，与图1 一致；\n"
    "· 画风：与图2 一致。\n"
    # 拓扑锁定：这三条是单独补的。只说「四肢角度一致」是不够的——模型常常做出
    # 骨骼大致对、但左右手互换 / 佩剑换了只手 / 空手凭空多出武器的结果，帧内看
    # 着正常，连起来播会发现持剑手每帧在两只手之间跳。方法来自 2dimg2motion 的
    # topology lock：左右以**画面**说、持物要指定归属手、空手必须保持空。
    "· 左右方位：以画面为准——图1 里在画面左侧的手脚或持物，生成后仍在画面左侧，"
    "不要左右镜像、不要交换四肢；\n"
    "· 持物归属：武器、道具、盾牌必须由图1 中同一侧的手以同样的方式握持；"
    "图1 里空着的手保持空着，不要凭空添加武器或道具；\n"
    "· 配饰跟随身体：披风、裙摆、发丝、飘带的朝向与穿戴位置跟随图1 的姿态，"
    "不要改到另一侧；\n"
    "图2 只是参考，**不要把它的多个视角一起画出来**，只画一个角色、一个姿势。\n"
    "画面里只有这一个角色，{背景}，没有地面、没有投影、没有边框、没有文字、"
    "没有其他人物。"
)

DEFAULT_NEGATIVE = (
    "多个角色, 四视图, 分屏, 拼图, 网格线, 文字, 水印, 边框, 地面, 阴影, "
    "多余的手脚, 肢体扭曲, 姿势改变, 动作简化, 站桩, 换装, 换发型, 换个角色, "
    # 拓扑类负面词（对应指令里的三条锁定）：左右镜像/持物换手/凭空加武器
    "左右镜像, 左右手互换, 持物换手, 多余的武器, 多余的道具, 换背景, "
    "抗锯齿, 渐变背景, 半透明"
)

_BG_PHRASE = {
    "transparent": "背景完全透明（只要角色，不要任何底色）",
    "white": "纯白背景",
    "gray": "中灰纯色背景",
    "black": "纯黑背景",
    "": "背景完全透明（只要角色，不要任何底色）",
    "none": "背景完全透明（只要角色，不要任何底色）",
}

# 已删：这里的「首帧预热」方案在实测里反而更糟——128px 空跑之后紧接着
# 256px 推理会撞上「GPU 100%、显存 23.8G、一步不推进」的静默卡死
# （同一现象在视频链 720p 贴着上限跑时出现过）。而「第一次推理必挂」这个
# 假设本身也不成立：实测有一次第 1 帧就是 0.944。所以改成 run_clone 里的
# 「多次尝试取最好」，不动引擎状态。


def build_instruction(template: str, *, index: int, total: int,
                      background: str = "transparent",
                      action: str = "") -> str:
    """把占位符换成实际值。用户把占位符删了也不报错，只是少了那一句。"""
    text = template or DEFAULT_INSTRUCTION
    bg = _BG_PHRASE.get((background or "").strip().lower(),
                        _BG_PHRASE["transparent"])
    text = (text.replace("{帧号}", str(index))
                .replace("{总帧数}", str(total))
                .replace("{背景}", bg))
    if action.strip():
        text = text.replace("{动作}", action.strip())
    else:
        text = text.replace("动作是：{动作}", "").replace("{动作}", "")
    return text.strip()


def _fg_mask(img: Image.Image) -> np.ndarray:
    """前景掩膜。透明图用 alpha；不透明图按四角底色差（与 spritesplit 同口径）。"""
    from .spritesplit import _fg_mask as fg
    return fg(img)[0]


def _bbox_of(mask: np.ndarray) -> Optional[Tuple[int, int, int, int]]:
    if not mask.any():
        return None
    ys, xs = np.nonzero(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def silhouette_iou(a: Image.Image, b: Image.Image) -> float:
    """两张图的剪影 IoU（只比形状，不比像素）。这是复刻「像不像」最稳的客观指标。"""
    ma, mb = _fg_mask(a), _fg_mask(b)
    if ma.shape != mb.shape:
        return 0.0
    union = int(np.logical_or(ma, mb).sum())
    if union == 0:
        return 0.0
    return float(np.logical_and(ma, mb).sum() / union)


def _has_transparency(img: Image.Image) -> bool:
    """这张图真的带透明区吗（1% 以上透明像素就算）。"""
    a = np.asarray(img.convert("RGBA"))
    return float((a[:, :, 3] < 8).mean()) > 0.01


def _match_scale(gen: Image.Image, ref: Image.Image,
                 *, lo: float = 0.88, hi: float = 1.14
                 ) -> Tuple[Image.Image, Dict[str, Any]]:
    """把生成帧的剪影等比缩放到源帧的大小，**再**平移到落脚点。

    为什么必须做（2026-10-26 实测）：同一套参数连跑三次，实测出现
    「角色被画成源帧 55% 大小」「角色被画成 1.8 倍」两种结果，剪影重合度
    0.94 → 0.74 → 0.30 全靠运气。不修正的话重新落格时每帧大小不一，
    播起来就是「呼吸式忽大忽小」，比位置漂移更扎眼。

    「逐帧缩放会不会把真实动作抹平」——不会，这里缩放的目标是**该帧自己的
    源帧**：源帧之间本来就有的大小变化（下蹲/跃起）照样保留，被抹掉的只是
    模型这一次跑偏的比例。之前那条「不做逐帧缩放」的顾虑针对的是「所有帧
    统一到同一个高度」，是两回事。

    相似度在 [lo, hi] 内就不动：轻微差异属于正常复刻误差，动它反而引入抖动。

    **但「比例接近」不等于「装得下」**（2026-10-26 修的真 bug）：源帧角色顶部在
    y=3、底边在 y=254，可用高度 254；模型画出 270px 高的角色，比例只有 1.08
    （落在 [0.88, 1.14] 内，判为「不用动」），底边对齐后顶部越界 → 角色头顶
    被 `alpha_composite` 直接裁掉 16px。拼成精灵图后头顶平一块。
    所以判据要同时满足「比例在容差内」**且**「绝对高度不超过可用空间」。
    可用空间 = 源帧底边到画布顶的距离（底边对齐后角色能向下伸展的全部余量）。
    """
    mb, mr = _fg_mask(gen), _fg_mask(ref)
    bb, br = _bbox_of(mb), _bbox_of(mr)
    if bb is None or br is None:
        return gen, {"scaled": False, "ratio": None}
    gh = max(1, bb[3] - bb[1])
    rh = max(1, br[3] - br[1])
    # 底边对齐后，角色最多能有多高：底边到画布顶（留 1px 余量给抗锯齿边）
    cap = max(1, br[3] - 1)
    target_h = min(rh, cap)
    ratio = gh / rh
    info: Dict[str, Any] = {"scaled": False, "ratio": round(float(ratio), 3),
                            "cap": int(cap)}
    if lo <= ratio <= hi and gh <= cap:
        return gen, info
    s = target_h / gh
    nw = int(round(gen.width * s))
    nh = int(round(gen.height * s))
    if not (16 <= nw <= 4096 and 16 <= nh <= 4096):
        return gen, info
    return gen.resize((nw, nh), Image.LANCZOS), {
        **info, "scaled": True, "clipped": gh > cap}


def _touching_edges(img: Image.Image) -> List[str]:
    """剪影碰到哪几条画布边。用来抓「角色被裁」——这是最容易被忽略的瑕疵：
    精灵图里头顶平一块、脚被切掉一截，播放时非常扎眼，但剪影重合度可能还很高
    （裁掉的是边缘几像素），只看 IoU 发现不了。
    2dimg2motion 把 canvas-too-small 列为硬失败，我们这边补上检测。
    """
    m = _fg_mask(img)
    if not m.any():
        return []
    ys, xs = np.nonzero(m)
    edges: List[str] = []
    if int(ys.min()) <= 0:
        edges.append("上")
    if int(ys.max()) >= m.shape[0] - 1:
        edges.append("下")
    if int(xs.min()) <= 0:
        edges.append("左")
    if int(xs.max()) >= m.shape[1] - 1:
        edges.append("右")
    return edges


def _corners_clean(img: Image.Image) -> bool:
    """四角是否全透明。四角不透明 = 背景没抠干净（这块会在精灵图里变成实底色块）。"""
    a = np.asarray(img.convert("RGBA"))
    return bool(a[0, 0, 3] < 8 and a[0, -1, 3] < 8
                and a[-1, 0, 3] < 8 and a[-1, -1, 3] < 8)


def _align_to(gen: Image.Image, ref: Image.Image, *,
              anchor: str = "bottom",
              match_scale: bool = True) -> Tuple[Image.Image, Dict[str, Any]]:
    """把生成帧对齐到源帧的落脚点（等比修正尺寸 + 整数像素平移）。

    锚点默认 bottom = 剪影包围盒的**底边中心**：游戏素材的水平基准是「脚站在
    哪」，而不是「像素质心在哪」——举手的帧质心会偏上偏左，用它当锚会让身体
    跟着漂。水平方向用 bbox 中心而不是质心，同样是为了避开「衣摆往哪边飘，
    质心就往哪边跑」。

    输出**永远与源帧同尺寸**：下游落格要的是等尺寸帧。
    """
    work, sinfo = (_match_scale(gen, ref) if match_scale else (gen, {"scaled": False}))
    mg, mr = _fg_mask(work), _fg_mask(ref)
    bg, br = _bbox_of(mg), _bbox_of(mr)
    if bg is None or br is None:
        return work, {**sinfo, "dx": 0, "dy": 0, "aligned": False,
                      "why": "有一帧找不到前景，跳过对齐"}

    dx = int(round((br[0] + br[2]) / 2 - (bg[0] + bg[2]) / 2))
    if anchor == "center":
        dy = int(round((br[1] + br[3]) / 2 - (bg[1] + bg[3]) / 2))
    else:  # bottom
        dy = int(br[3] - bg[3])

    out = Image.new("RGBA", ref.size, (0, 0, 0, 0))
    out.alpha_composite(work, (dx, dy))
    return out, {**sinfo, "dx": dx, "dy": dy, "aligned": bool(dx or dy),
                 "edge": _touching_edges(out)}


# 推理尺寸的下限。**128 会直接崩**，不是效果差的问题：
# 实测（2026-10-26，用户 120×120 的精灵图格子，size=0 → 抬到 128）12 帧全部报
# `shape mismatch: value tensor of shape [256, 4096] cannot be broadcast to
# indexing result of shape [1, 832, 4096]`；同一批数据改 256 / 384 / 512 全部
# 成功（IoU 0.93 / 0.89 / 0.92）。所以小帧也要在 256 上推理，出完再缩回源尺寸。
MIN_INFER_SIZE = 256


def _friendly_error(e: Exception) -> str:
    """把模型的天书报错翻成能照着改的话。

    `shape mismatch: value tensor of shape [256, 4096] cannot be broadcast to
    indexing result of shape [1, 832, 4096]` 这种对用户毫无意义——它其实只是
    「输出尺寸太小，模型的图像 token 数对不上」（实测 128 必崩、256 起正常）。
    """
    s = str(e)
    low = s.lower()
    if "shape mismatch" in low or "cannot be broadcast to indexing" in low:
        return (f"出图尺寸太小，模型内部对不上（{s[:80]}）。"
                f"请把「输出长边」设为 ≥{MIN_INFER_SIZE}，"
                f"或把上游精灵图的单元格调大（现在这帧不到 {MIN_INFER_SIZE}px）")
    if "out of memory" in low or "cuda" in low and "memory" in low:
        return f"显存不够（{s[:60]}）：把「输出长边」调小，或关掉占显卡的程序"
    return s[:300]


def _fit_size(w: int, h: int, size: int = 0) -> Tuple[int, int]:
    """输出尺寸：默认跟随源帧（圆整到 16），也可由用户指定长边。

    下限 MIN_INFER_SIZE=256：Qwen-Image-2.1 在 128×128 上会因为 image token 数
    与联合序列槽位对不上而崩（见常量处的实测）。小于 256 的源帧照样在 256 上
    推理、出完再缩回源尺寸——虽然不会凭空多出细节，但至少跑得出来。
    """
    if size and size > 0:
        base = max(MIN_INFER_SIZE, min(int(size), 1536))
        scale = base / max(1, max(w, h))
        nw, nh = int(round(w * scale)), int(round(h * scale))
    else:
        nw, nh = w, h
    return (max(MIN_INFER_SIZE, int(round(nw / 16)) * 16),
            max(MIN_INFER_SIZE, int(round(nh / 16)) * 16))


def _fit_seq_len(text: str, requested: Optional[int]) -> int:
    """指令长度按中文 1.3 token/字估，最少 640（默认 512 会截掉尾部条款）。"""
    est = int(len(text) * 1.35) + 96
    want = max(int(requested) if requested else 0, est, 640)
    return max(512, min(want, 1920))


def repack_board(img: Image.Image, *, views: int = 4) -> Tuple[Image.Image, str]:
    """把长条形的四视图板重排成接近正方形的板。

    为什么必须做：参考图进管线前会被 `_prep_ref_images` **等比缩放 + letterbox**
    到输出尺寸。实测一张 4120×1024 的四视图板塞进 256×256 的输出，会被压成
    256×64 —— 每个视角只有 64px 高，模型根本看不清发型和服装细节，复刻出来
    的只能是个"配色差不多的陌生人"。重排成 2×2 之后同样 256×256 的预算里
    每视角有 128×128，**有效像素直接翻 4 倍**。

    只在长宽比极端（≥2.5:1 或 ≤1:2.5）时动手：用户自己排好的 2×2 板保持原样。
    """
    w, h = img.size
    if views < 2:
        return img, "as-is"
    ratio = w / max(1, h)
    if 1 / 2.5 < ratio < 2.5:
        return img, "as-is"
    cols, rows = (views, 1) if ratio >= 1 else (1, views)
    pw, ph = w // cols, h // rows
    if pw < 8 or ph < 8:
        return img, "as-is"
    out_cols = int(math.ceil(math.sqrt(views)))
    out_rows = int(math.ceil(views / out_cols))
    canvas = Image.new("RGBA", (pw * out_cols, ph * out_rows), (0, 0, 0, 0))
    for i in range(views):
        piece = img.crop((i * pw, 0, (i + 1) * pw, ph)) if rows == 1 \
            else img.crop((0, i * ph, pw, (i + 1) * ph))
        canvas.paste(piece, ((i % out_cols) * pw, (i // out_cols) * ph))
    return canvas, f"{cols}x{rows}→{out_cols}x{out_rows}"


def split_board_cells(board: Image.Image) -> List[Image.Image]:
    """把 2×2 的四视图板切成 4 个单视角立绘；不像板就原样返回 1 张。

    为什么必须切（2026-10-27 实测，用户真实素材）：动作源帧是「光头肉色像素
    小人」，四视图是「青蓝古风长袍剑客」，要求用四视图的角色做源帧的动作。
    给整张板时模型**根本不用它**——
        A 整板 + 老指令 → 直接复制光头小人（肤色占比 0.56）
        C 整板 + 强指令 → 画了个灰剪影（听懂"图1 不是对象"，但没用四视图）
        E 单视角 + 强指令 → **青蓝占比 0.83、肤色 0.01**，正确画出剑客
    原因是分辨率：板 512×508 塞进 256×256 的输出预算，每格只剩 128×127，
    模型读不出发型服装；单格 256×254 几乎占满，有效像素翻 4 倍，注意力也
    只落在一个角色上，不会被"四格该跟哪个"分散。
    """
    w, h = board.size
    if w % 2 or h % 2:
        return [board]
    cw, ch = w // 2, h // 2
    cells: List[Image.Image] = []
    for r in range(2):
        for c in range(2):
            cells.append(board.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))
    # 四格都得有内容，否则说明这不是四视图板（比如只是一张方形立绘）
    for cell in cells:
        if float(_fg_mask(cell).mean()) < 0.01:
            return [board]
    return cells


def _color_hist(img: Image.Image) -> np.ndarray:
    """不透明区的颜色直方图（4×4×4），用来判断「这张出图的配色像不像参考立绘」。"""
    a = np.asarray(img.convert("RGBA")).astype(np.float32)
    m = a[..., 3] > 128
    if not m.any():
        return np.zeros(64)
    h, _ = np.histogramdd(a[m][:, :3], bins=(4, 4, 4), range=((0, 256),) * 3)
    return (h / max(1.0, h.sum())).ravel()


def _hist_dist(a: np.ndarray, b: np.ndarray) -> float:
    """两个直方图的 L1 距离（0 = 完全一样，1 = 毫无重叠）。"""
    return float(np.abs(a - b).sum()) / 2.0


async def run_clone(
    frames: Sequence[Dict[str, Any]],
    char_refs: Sequence[Image.Image],
    *,
    instruction: str = "",
    negative: str = "",
    steps: Optional[int] = None,
    size: int = 0,
    background: str = "transparent",
    max_seq_len: Optional[int] = None,
    anchor: str = "bottom",
    ref_mode: str = "auto",
    ref_view: str = "auto",
    seed: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    on_frame: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """逐帧复刻。

    frames：spritesplit.run_split 的结果（每项含 image / i）
    char_refs：目标角色参考图（建议 1 张拼好的四视图；≤3 张都用得上）

    **on_frame**：每帧复刻完成就回调一次（传这一帧的字典）。调用方应在这里
    落盘并把 `item["image"]` 置空释放内存——否则整批帧会一直堆在内存里：
    96 帧 × 1024² RGBA 就是 400MB，中途崩了还一帧都拿不到（前面跑的几十秒
    全白费）。本函数在 `on_frame` 之后仍会把 image 保留在返回值里，方便
    纯库调用；调用方主动置 None 才是「边跑边落盘」的用法。

    返回 (out_frames, meta)。out_frames 元素：
        {i, image, iou, dx, dy, seconds, instruction}
    """
    from ..providers.diffusers_local import DiffusersProvider

    if not frames:
        raise RuntimeError("没有动作帧：先在「拆解精灵图」里拆帧并勾选")
    refs = [r for r in (char_refs or []) if r is not None]
    if not refs:
        raise RuntimeError("没有角色参考图：把「选择图片」节点（正面/侧面/背面/右侧 四视图）"
                           "连到本节点的角色端口")

    pack_note = "as-is"
    # 逐张判断，而不是只在「恰好 1 张参考图」时才做：实测用户常传两张
    # （一张立绘 + 一张长条四视图板），原来那个 len(refs)==1 的条件会让那张
    # 1024×254 的板完全不重排 → letterbox 进 256×256 后只剩 64px 高，
    # 模型看不清发型服装，复刻出来就是「配色差不多的陌生人」。
    # 不是长条的图 repack_board 自己会返回 as-is，逐张调用是安全的。
    if ref_mode != "as-is":
        notes: List[str] = []
        for _i in range(len(refs)):
            refs[_i], _note = repack_board(refs[_i])
            if _note != "as-is":
                notes.append(f"第 {_i + 1} 张 {_note}")
        if notes:
            pack_note = "；".join(notes)

    # ---- 四视图板 → 单视角立绘 ----
    # 给整张板时模型几乎不读它（见 split_board_cells 的实测）。切成单格之后
    # 参考图在同样的输出预算里有效像素翻 4 倍，身份才迁得过来。
    # ref_view: auto = 先用第一帧试 4 个视角，选配色最像参考立绘的那个；
    #           指定 front/side/back/side2 就直接用；board = 完全不动（老行为）。
    view_note = ""
    cells: List[Image.Image] = []
    if ref_view != "board" and refs:
        cells = split_board_cells(refs[0])
        if len(cells) == 4:
            if ref_view in ("front", "side", "back", "side2"):
                idx = {"front": 0, "side": 1, "back": 2, "side2": 3}[ref_view]
                refs = [cells[idx]]
                view_note = f"用第 {idx + 1} 格视角"
            else:
                # auto：留到 provider 就绪后 probe（下面做）
                refs = [cells[0]]
                view_note = "待挑视角"
        else:
            cells = []
            view_note = "单张立绘（未切格）"

    provider = DiffusersProvider(provider_cfg or {})
    total = len(frames)
    use_steps = 40 if not steps else max(20, min(int(steps), 50))
    grand = use_steps * total
    custom = (instruction or "").strip()
    # 负面词**必须非空**：Qwen-Image-2.1 的 true_cfg_scale=4.0 靠
    # `do_true_cfg = true_cfg_scale > 1 and has_neg_prompt` 判定，传空串等于
    # 关掉 CFG，于是「不要四视图 / 不要分屏 / 不要多个角色」这些约束一个字
    # 都不生效——实测第 1 帧直接把参考板原样画了出来（4 个视角并排）。
    # 这个坑在 threeview 那边已经踩过一次（negative 只进了 meta 没进模型）。
    neg = (negative or "").strip() or DEFAULT_NEGATIVE

    # ---- auto 挑视角：拿第一帧试 4 个视角，选「配色最像参考立绘」的那个 ----
    # 为什么不能靠"剪影最像"来选：源帧是正面光头小人时，正面立绘与它剪影最像，
    # 而实测正是正面立绘会让模型直接照抄源帧（肤色 0.57）。真正该看的是
    # 「出图颜色像不像参考立绘」——像，才说明角色被迁过来了。
    if cells and ref_view == "auto" and len(cells) == 4:
        probe_src = frames[0]["image"]
        pw, ph = _fit_size(probe_src.width, probe_src.height, size)
        probe_steps = max(12, use_steps // 2)
        best: Optional[Tuple[float, int]] = None
        ref_hists = [_color_hist(c) for c in cells]
        for ci, cell in enumerate(cells):
            progress_mod.report(task_id, 0, grand,
                                f"挑角色视角 {ci + 1}/4（用第 1 帧试画）…")
            try:
                r = await provider.generate(
                    prompt=build_instruction(custom or DEFAULT_INSTRUCTION,
                                             index=1, total=1,
                                             background=background),
                    width=pw, height=ph, steps=probe_steps, seed=seed, n=1,
                    refs=[probe_src, cell], negative=neg,
                    ref_bg=(None if background in ("transparent", "", "none")
                            else _bg_rgb(background)),
                    task_id=task_id,
                    max_seq_len=_fit_seq_len(
                        build_instruction(custom or DEFAULT_INSTRUCTION, index=1,
                                          total=1, background=background),
                        max_seq_len))
                got = r.images[0].convert("RGBA")
                d = _hist_dist(_color_hist(got), ref_hists[ci])
            except progress_mod.CancelledError:
                raise
            except Exception:
                continue
            if best is None or d < best[0]:
                best = (d, ci)
        if best is not None:
            refs = [cells[best[1]]]
            view_note = (f"自动选中第 {best[1] + 1} 格（配色最接近参考）")

    out: List[Dict[str, Any]] = []
    warnings: List[str] = []
    retried = 0
    auto_matted = 0
    clipped = 0
    dirty_corners = 0
    clip_notes: List[str] = []
    baselines: List[int] = []
    want_transparent = background in ("transparent", "", "none")
    t_start = time.time()

    for k, fr in enumerate(frames):
        progress_mod.check_cancel(task_id)
        src: Image.Image = fr["image"]
        w, h = _fit_size(src.width, src.height, size)
        text = build_instruction(custom or DEFAULT_INSTRUCTION, index=k + 1,
                                 total=total, background=background)
        label = f"复刻第 {k + 1}/{total} 帧"
        t0 = time.time()

        async def _attempt(seed_use: Optional[int],
                           _text: str = text, _w: int = w, _h: int = h,
                           _k: int = k, _label: str = label, _src=src):
            res = await provider.generate(
                prompt=_text,
                width=_w, height=_h,
                steps=use_steps, seed=seed_use, n=1,
                refs=[_src, *refs],
                negative=neg,
                # 参考图铺底色：动作帧是透明底，`convert("RGB")` 会把它压成
                # 黑底，模型把黑当背景画进结果（暗部/地面），和「透明背景」打架
                ref_bg=(None if want_transparent else _bg_rgb(background)),
                progress={"label": _label, "base": use_steps * _k, "grand": grand},
                task_id=task_id,
                max_seq_len=_fit_seq_len(_text, max_seq_len),
            )
            return res.images[0].convert("RGBA"), dict(res.meta or {})

        gen: Optional[Image.Image] = None
        rmeta: Dict[str, Any] = {}
        iou = 0.0
        ainfo: Dict[str, Any] = {}
        tries = 0
        # 最多试 3 次，取剪影重合最高的那张。实测同一份输入跑三次能拿到
        # 0.11 / 0.60 / 0.95 这种跨度，所以「多跑几次挑最好」比「猜哪个参数
        # 能让它稳定」有效得多——而且失败是随机的，换 seed 就够了。
        best: Optional[Tuple[float, Image.Image, Dict[str, Any]]] = None
        max_tries = 3
        while tries < max_tries:
            tries += 1
            progress_mod.report(task_id, use_steps * k, grand,
                                f"{label}（多图编辑{'·第 ' + str(tries) + ' 次' if tries > 1 else ''}）…")
            try:
                cur, rmeta = await _attempt(seed)
            except progress_mod.CancelledError:
                raise
            except Exception as e:
                if tries >= max_tries:
                    # 单帧失败不毁整批：记下来、跳过，其余帧照跑。精灵图少一帧
                    # 还能用，整批作废则要用户从头再来一次。
                    gen = None
                    warnings.append(f"第 {k + 1} 帧复刻失败：{str(e)[:120]}")
                    break
                continue

            # 背景没透明 → 主动抠掉。实测同一套参数连跑三次，有一次模型把背景
            # 画成了不透明的浅色（alpha 占比 0.996），剪影重合度直接从 0.94
            # 掉到 0.30——而用户要的本来就是透明底素材，这一步是确定性后处理。
            if want_transparent and not _has_transparency(cur):
                try:
                    from . import matte as matte_pipe
                    cur, _mm = matte_pipe.remove_background(
                        cur, tolerance=40, feather=0, hard=True,
                        method="auto", despill=True)
                    auto_matted += 1
                except Exception:
                    pass

            cur, ainfo = _align_to(cur, src, anchor=anchor)
            cur_iou = silhouette_iou(cur, src)
            if best is None or cur_iou > best[0]:
                best = (cur_iou, cur, ainfo)
            if cur_iou >= 0.6:
                break
            if tries < max_tries:
                retried += 1
                # 重试必须换种子。seed=None（默认）时自己造一个：之前写成
                # `if ... or seed is None: break`，于是默认配置下永远不重试。
                seed = (int(seed) + 101) % (2**31 - 1) if seed is not None \
                    else int(time.time() * 1000) % (2**31 - 1)

        if best is not None:
            iou, gen, ainfo = best

        if gen is None:
            out.append({"i": fr["i"], "image": None,
                        "error": _friendly_error(e),
                        "seconds": round(time.time() - t0, 1)})
            continue

        out.append({
            "i": fr["i"],
            "image": gen,
            "iou": round(iou, 4),
            "dx": ainfo.get("dx", 0), "dy": ainfo.get("dy", 0),
            "sizeRatio": ainfo.get("ratio"),
            "scaled": bool(ainfo.get("scaled")),
            "tries": tries,
            "seconds": round(time.time() - t0, 1),
            "instruction": text,
            "mode": rmeta.get("mode"),
            "vram": rmeta.get("vram"),
        })
        # ---- 结构校验（三项，全部毫秒级）：贴边 / 四角 / 脚底线 ----
        # 2dimg2motion 把这几项列为硬失败，而只算剪影重合是发现不了的——
        # 裁掉头顶 3px 的帧 IoU 可能仍有 0.95。
        src_edges = _touching_edges(src)
        new_edges = [e for e in (ainfo.get("edge") or []) if e not in src_edges]
        if new_edges:
            clipped += 1
            if len(clip_notes) < 4:
                clip_notes.append(f"第 {k + 1} 帧{''.join(new_edges)}边被裁")
        if want_transparent and not _corners_clean(gen):
            dirty_corners += 1
        _b = _bbox_of(_fg_mask(gen))
        if _b:
            baselines.append(int(_b[3]))
        progress_mod.report(task_id, use_steps * (k + 1), grand,
                            f"{label} 完成（剪影重合 {iou * 100:.0f}%"
                            f"{'，已修正尺寸' if ainfo.get('scaled') else ''}"
                            f"{'，' + ''.join(new_edges) + '边贴到画布' if new_edges else ''}）")
        # 先落盘、再继续下一帧：调用方会在这里存盘并置空 image 释放内存
        if on_frame is not None:
            try:
                on_frame(out[-1])
            except Exception:
                pass

    ok = [f for f in out if f.get("image") is not None]
    ious = [f["iou"] for f in ok]
    if ok and background in ("transparent", "", "none") and any(
            np.asarray(f["image"])[:, :, 3].min() > 8 for f in ok):
        warnings.append("有帧没有拿到透明背景（模型偶尔会补底色）："
                        "可以在下游「精灵图」里打开「统一落格」+ 抠图，或把该帧重跑")
    if ious and min(ious) < 0.55:
        # 用下标找最差帧：按 dict 值找会命中重复项，把第 3 帧报成第 1 帧
        worst_i, worst = min(enumerate(ok), key=lambda kv: kv[1]["iou"])
        tail = ("已换 seed 重跑过一次仍偏低" if int(worst.get("tries") or 1) > 1
                else "重试没能触发，可以手动换 seed 再跑这一帧")
        warnings.append(
            f"第 {worst_i + 1} 帧剪影重合只有 {worst['iou'] * 100:.0f}%（{tail}）："
            "这一帧的姿势可能被模型改写了，建议单独重跑或干脆不选它")
    if auto_matted:
        warnings.append(
            f"有 {auto_matted} 帧模型没画出透明背景，已自动抠掉（结果已修正，"
            "但发丝边缘可能不如原生透明干净）")
    # 源帧比推理尺寸小得多时说清楚：推理会在 256 上做、再缩回源尺寸，
    # 不会凭空多出细节。别让用户以为「复刻完变清晰了」。
    tiny = [f["i"] for f in frames
            if max((f.get("image") or getattr(f, "width", 0)).width
                   if hasattr(f.get("image"), "width") else 0, 0) < MIN_INFER_SIZE]
    if tiny:
        warnings.append(
            f"源帧只有 {len(tiny)} 帧的尺寸小于 {MIN_INFER_SIZE}px：复刻会在 "
            f"{MIN_INFER_SIZE} 上推理再缩回原尺寸，动作能对齐但**不会变清晰**。"
            "想要更清楚，把上游「拆解精灵图」的单元格调大（或换一张格子更大的精灵图）")

    if retried:
        warnings.append(
            f"有 {retried} 帧第一次跑飞了（剪影重合 <60%），已换 seed 自动重跑")
    # ---- 结构校验的三条结论（对应 2dimg2motion 的硬失败清单）----
    if clipped:
        detail = "；".join(clip_notes)
        warnings.append(
            f"有 {clipped} 帧角色贴到了画布边（{detail}）：模型把角色画得比源帧大，"
            "超出部分已被裁掉——精灵图里会看到头顶/手脚被切平。"
            "把这几帧重跑，或换一组留白更多的动作素材")
    if dirty_corners:
        warnings.append(
            f"有 {dirty_corners} 帧四角不透明（背景没抠干净）：拼成精灵图后"
            "会出现实底色块，建议重跑这几帧或在「精灵图」里打开抠图")
    if len(set(baselines)) > 1:
        warnings.append(
            f"脚底线不齐（出现在第 {sorted(set(baselines))} 行）：逐帧对齐有 1~2px"
            "的取整误差，循环播放时角色会轻微上下跳")

    meta: Dict[str, Any] = {
        "count": len(ok),
        "failed": len(out) - len(ok),
        "steps": use_steps,
        "size": [ok[0]["image"].width, ok[0]["image"].height] if ok else None,
        "anchor": anchor,
        "background": background,
        "iouMean": round(float(np.mean(ious)), 4) if ious else None,
        "iouMin": round(float(np.min(ious)), 4) if ious else None,
        "seconds": round(time.time() - t_start, 1),
        "refCount": len(refs) + 1,
        "refPack": pack_note,
        "refView": view_note or "board（整板）",
        # 后处理统计：这两项说明「看起来不对」的帧有多少是被自动修过的
        "retried": retried,
        "autoMatted": auto_matted,
        # 结构校验（毫秒级，不看剪影重合也能发现的问题）
        "baselineRows": sorted(set(baselines)),
        "baselineStable": len(set(baselines)) <= 1,
        "clipped": clipped,
        "cornersDirty": dirty_corners,
        "instruction": build_instruction(custom or DEFAULT_INSTRUCTION,
                                        index=1, total=total,
                                        background=background),
        "negative": neg,
        "warnings": warnings,
    }
    return out, meta


def _bg_rgb(background: str) -> Optional[Tuple[int, int, int]]:
    """参考图铺底色：不透明背景时按设置铺，透明时不铺（让 alpha 原样进管线）。"""
    return {"white": (255, 255, 255),
            "gray": (128, 128, 128),
            "black": (0, 0, 0)}.get((background or "").strip().lower())


async def clone_whole_sheet(
    sheet: Image.Image,
    char_refs: Sequence[Image.Image],
    grid: Dict[str, Any],
    *,
    instruction: str = "",
    negative: str = "",
    steps: Optional[int] = None,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """整张精灵图一次性复刻（草稿路径，仅供预览）。

    为什么只当草稿：模型对「N 个姿势 + 严格等距网格」的保持力很弱，实测常见
    结果是重排 / 合并 / 少画几格。好处是 1 次推理（约 20s，对比 12 帧的 4 分钟），
    适合先看角色像不像，再决定要不要走逐帧。**不要用它出最终素材**——网格一错，
    引擎切图全乱。

    出图后会自己跑一遍网格识别做闭环校验：认出来的行列数与输入不一致就明确
    告警「这张别用」。这是这套草稿路径唯一诚实的价值——让人 20 秒就知道该不该
    花 4 分钟走逐帧。
    """
    from ..providers.diffusers_local import DiffusersProvider

    refs = [r for r in (char_refs or []) if r is not None]
    if not refs:
        raise RuntimeError("没有角色参考图：把「选择图片」节点连到角色端口")

    provider = DiffusersProvider(provider_cfg or {})
    w16 = max(64, int(round(sheet.width / 16)) * 16)
    h16 = max(64, int(round(sheet.height / 16)) * 16)
    text = (instruction or "").strip() or (
        f"图1 是一张游戏角色动作序列帧精灵图，共 {grid.get('cols')} 列 × "
        f"{grid.get('rows')} 行，每格 {grid.get('cellW')}×{grid.get('cellH')} 像素。"
        "图2 是目标角色的四视图设定板。请把图1 里每一格的角色的形象换成图2 中的角色，"
        "同时严格保持：格数、每格的排列位置、每格的动作姿势、朝向、尺寸与画面占比完全不变。"
        "图2 只是设定参考，不要把它的四个视角画出来。"
        "输出仍然是一张同样格数与排布的序列帧精灵图，背景透明。")
    use_steps = 40 if not steps else max(20, min(int(steps), 50))
    progress_mod.report(task_id, 0, use_steps, "整图一次复刻（草稿）…")
    t0 = time.time()
    result = await provider.generate(
        prompt=text, width=w16, height=h16, steps=use_steps, seed=None, n=1,
        refs=[sheet, *refs],
        # 同 run_clone：负面词不能为空，否则 CFG 静默失效（模型会把参考板原样画出来）
        negative=(negative or "").strip() or DEFAULT_NEGATIVE,
        ref_bg=None, task_id=task_id,
        max_seq_len=_fit_seq_len(text, max_seq_len),
    )
    gen = result.images[0].convert("RGBA")
    if (gen.width, gen.height) != sheet.size:
        gen = gen.resize(sheet.size, Image.LANCZOS)

    from .spritesplit import detect_grid
    got = detect_grid(gen)
    want = (int(grid.get("cols") or 1), int(grid.get("rows") or 1))
    same = (got["cols"], got["rows"]) == want
    warnings: List[str] = []
    if not same:
        warnings.append(
            f"网格被改动了：输入 {want[0]}×{want[1]}，出图识别成 "
            f"{got['cols']}×{got['rows']}。这张只能当预览——最终素材请走逐帧复刻。")
    meta = {
        "mode": "whole-sheet",
        "grid": {"cols": got["cols"], "rows": got["rows"],
                 "cellW": got["cellW"], "cellH": got["cellH"]},
        "gridKept": same,
        "seconds": round(time.time() - t0, 1),
        "steps": use_steps,
        "instruction": text,
        "warnings": warnings,
    }
    return gen, meta
