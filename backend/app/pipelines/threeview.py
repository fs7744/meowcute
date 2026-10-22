"""角色四视图：正/侧/背/右四张独立生成 → 确定性拼横向板。

为什么不再「一张图里画三个视角」（移植自归档项目的实测结论）：
单图提示词让模型一次画齐正/侧/背，在 turbo 蒸馏模型上基本不可可控——
三个角色经常糊成一团（重叠）、数量不对、视角缺失，裁切也没法裁。所以：

  帧 1 正视图  → 画布上的「生成图 / 选择图片」节点产出（txt2img 底座自由创作）
  帧 2 侧视图  → 本模块 derive_view：Qwen-Image-Edit 指令接力「转成侧面视图」
  帧 3 背视图  → 同上，「转成背面视图」
  帧 4 右视图  → 同上，「转成右侧面视图」（四视图新增，与侧视图镜像）
  拼板        → 抠掉各自底色 → 统一暗绿底 → 基线/水平居中对齐

「四张、不重叠、同一角色」由结构保证：每张图只有一个角色的约束 +
编辑底座的外观保持指令 + 拼板是确定性像素操作，不靠模型自觉。

侧视图（side）与右视图（side_r）的命名约定：side = 角色面向画面左侧
（与 walkvideo 的 left/90° 锚点、历史项目数据 vs-/vb- 兼容），
side_r = 角色面向画面右侧（与 walkvideo 的 right/270° 对应）。

抠图边缘实测审计（u2net，512×768 真实出图，两轮）——以下修法都不动 alpha
形状，所以不会啃掉胡须/触角：
- 铺边距再抠：AI 出图的角色经常踩到画布底边（脚/影子被裁切），u2net 会把
  「触及画面边界的肢体」当成出画部分直接截掉（实测背视图白色脚掌抠完只剩
  黑色描边，脚部 alpha 保留仅 8.6%）。铺 12% 边距让脚不碰边再抠，裁回原尺寸。
- 补封闭孔洞：背视图两片白色脚掌被 u2net 判成背景，mask 在脚上只剩一圈黑
  描边，脚掌内部成了 1760/1744px 的封闭孔洞，拼板上看就是「脚的白色被抠没了」。
  孔洞边界完全在画面内部（不接边）即可判定为角色内部，补回不透明后露出的
  就是原图的脚掌颜色，不需要任何颜色猜测。上限 2% 画布：更大的封闭区域
  （如身侧空隙）保留透明，避免糊成一块。
- 边缘溢色清洗（matte.clean_edge_spill）：描边外圈常带「黑+底色」的抗锯齿
  混色，抠完底色变透明就留下一圈异色边。判据是「比离自身内侧颜色更接近
  底色」，纯黑描边离内侧为 0 永远不会被洗掉。

指令层面的坑（已修）：`_view_instruction` 曾把示例角色（灰白英短猫武士）的
造型写死进指令——「后脑勺纯灰色」「红色披风背部完整可见」「手中兵器」。
这些句子对任何角色都会生效，于是正面没有披风的角色转背面也一定长出红披风、
后脑被刷成灰。现在指令只描述「视角怎么变」和「造型以参考图为准」，
不点名任何具体服装、配件或颜色。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PIL import Image

from . import matte as matte_pipe
from ..styles import background_phrase

# 统一底板色：暗绿 #1B4332（与归档项目模板/工作流提示词里的「暗绿色纯色背景」一致）
DEFAULT_BG = (27, 67, 50)

# 视角元数据：名称 + 正视图姿势词
_VIEW_META: Dict[str, Dict[str, str]] = {
    "front": {
        "cn": "正视图", "en": "front view",
        "pose_cn": "角色面向观众，全身站立，双脚踩地，身体居中",
        "pose_en": "character facing the viewer, full body standing, feet on the ground, centered",
    },
    "side": {
        "cn": "侧视图", "en": "side view",
        "pose_cn": "角色面向画面左侧，展现完整侧面轮廓，全身站立，身体居中",
        "pose_en": "character in profile facing left, full side silhouette, full body standing, centered",
    },
    "back": {
        "cn": "背视图", "en": "back view",
        "pose_cn": "角色背对观众，展现完整背部轮廓，全身站立，身体居中",
        "pose_en": "character seen from behind, full back silhouette, full body standing, centered",
    },
    "side_r": {
        "cn": "右视图", "en": "right side view",
        "pose_cn": "角色面向画面右侧，展现完整侧面轮廓，全身站立，身体居中",
        "pose_en": "character in profile facing right, full side silhouette, full body standing, centered",
    },
}

# 四视图顺序：正 → 左 → 背 → 右（环形顺序，拼板按此排列）
VIEW_ORDER = ("front", "side", "back", "side_r")
# 拼板必需的老三张（右视图可选：缺了按三视图拼板，向后兼容旧项目）
REQUIRED_VIEWS = ("front", "side", "back")


def _view_instruction(view: str) -> str:
    """编辑底座的中文指令：只转视角，外观/服装/配色/画风零改动。

    编辑器（Qwen-Image-Edit-2511）吃中文指令。背视图是最容易翻车的视角：
    模型常见失败是「只抹掉脸」——后脑勺上留着正面的五官或图案。指令里用
    「背面该看到什么」的正向描述钉住，而不是复述某个具体角色的造型
    （那会成为凭空添加装备的指令）。
    """
    keep = (
        "只改变观察角度：角色的造型、配色、体型比例和画风全部以参考图为准，"
        "不新增也不删除任何部件。背景保持纯色不变，角色居中站立，"
        "画面中仍然只有一个角色，不要添加文字和水印。"
    )
    if view in ("side", "side_r"):
        lean = "左" if view == "side" else "右"
        return (
            f"把画面中的角色转换成侧面视图：角色整体转向画面{lean}侧，展现完整的侧面轮廓，"
            "全身站立。侧面的造型全部来自参考图中这个角色本身，身体的各个部位按它"
            "本来的样子推演，参考图里没有的东西不要出现。" + keep
        )
    return (
        "把画面中的角色转换成背面视图：角色整体旋转180度，背对观众站立，从正背后观察。"
        "脸部、眼睛和鼻子完全不可见；头部的造型按参考图中这个角色的原样自然延续到背面。"
        "背面只呈现角色自己已有的造型，参考图里没有的衣物、配件和装饰一件都不要出现。"
        "角色手里原本拿着东西就随身体一起转到背面，仍然握在同一只手里；原本空着手就"
        "保持空手，不要凭空添一件道具。" + keep
    )


def front_view_prompt(subject: str, style_words: str = "") -> str:
    """正视图提示词（模板里「生成图」节点的默认值）。

    画风词不在这里拼：build_prompt 会把所选画风的 prompt 前置，这里只负责
    主体与「只画一个角色」的约束。写死画风词会和画风选择器打架。
    """
    m = _VIEW_META["front"]
    subj = (subject or "").strip().rstrip("。.，, ")
    head = f"画风：{style_words}。" if style_words else ""
    return (
        f"{head}"
        f"游戏角色{m['cn']}立绘。画面中只有一个角色，绝对不能出现两个或多个人物。"
        f"角色设定：{subj or '（请描述角色）'}。"
        f"{m['pose_cn']}。"
    )


# --------------------------------------------------------------------------- #
# 拼板：抠像 → 统一底色 → 基线/居中对齐
# --------------------------------------------------------------------------- #
def _border_bg(img: Image.Image) -> Tuple[int, int, int]:
    """取画面边框上出现最多的颜色作为底色（角色碰边时也比中位数稳）。"""
    arr = np.asarray(img.convert("RGB"))
    border = np.concatenate([
        arr[0, :, :].reshape(-1, 3), arr[-1, :, :].reshape(-1, 3),
        arr[:, 0, :].reshape(-1, 3), arr[:, -1, :].reshape(-1, 3),
    ])
    q = border // 16 * 16
    colors, counts = np.unique(q, axis=0, return_counts=True)
    base = colors[counts.argmax()]
    return tuple(int(v) for v in base + 8)


def _fill_enclosed_holes(cut: Image.Image, max_ratio: float = 0.02
                         ) -> Tuple[Image.Image, int]:
    """补回被角色完全围住的透明孔洞（只改 alpha，RGB 原样）。"""
    a = np.asarray(cut).copy()
    kept = a[:, :, 3] > 8
    holes = (~kept).astype(np.uint8)
    try:
        import cv2
        n, lab = cv2.connectedComponents(holes, connectivity=4)
    except Exception:
        # 无 cv2：scipy 的 4 邻域连通域兜底（标签从 1 开始，与 cv2 对齐）
        try:
            from scipy import ndimage
        except Exception:
            return cut, 0  # 两个都没有就放弃补洞，别把整条拼板链路带崩
        lab, count = ndimage.label(
            holes, structure=np.array([[0, 1, 0], [1, 1, 1], [0, 1, 0]]))
        n = int(count) + 1
    h, w = kept.shape
    limit = int(max_ratio * h * w)
    filled = 0
    for i in range(1, n):
        comp = lab == i
        ys, xs = np.nonzero(comp)
        if not len(ys):
            continue
        if ys.min() == 0 or xs.min() == 0 or ys.max() == h - 1 or xs.max() == w - 1:
            continue  # 触边 = 外部背景
        area = int(comp.sum())
        if area > limit:
            continue
        a[comp, 3] = 255
        filled += area
    if not filled:
        return cut, 0
    return Image.fromarray(a, "RGBA"), filled


def _matte_padded(img: Image.Image, pad_frac: float, tolerance: int
                  ) -> Tuple[Image.Image, dict]:
    """铺一圈底色边距后再抠图，裁回原尺寸（防踩边肢体被 u2net 截掉）。"""
    w, h = img.size
    p = max(8, int(min(w, h) * pad_frac))
    bg = _border_bg(img)
    canvas = Image.new("RGB", (w + 2 * p, h + 2 * p), bg)
    canvas.paste(img.convert("RGB"), (p, p))
    cut, meta = matte_pipe.remove_background(
        canvas, tolerance=tolerance, feather=0, hard=True,
        method="auto", despill=True)
    cut = cut.crop((p, p, p + w, p + h))
    cut = matte_pipe.drop_bg_islands(cut)
    cut, filled = _fill_enclosed_holes(cut)
    cut, recolored = matte_pipe.clean_edge_spill(cut, bg)
    meta = dict(meta, holesRestored=filled, edgePixelsRecolored=recolored)
    return cut, meta


def _place(canvas: Image.Image, im: Image.Image, pos: Tuple[int, int]) -> None:
    """按自身 alpha 把部件叠到画布上（越界部分裁掉）。

    透明画布必须走 alpha_composite 的 over 合成：paste(im, pos, im) 会把
    alpha 再乘一次自身，半透明边缘被二次压暗、描边发硬。
    """
    x, y = int(pos[0]), int(pos[1])
    if canvas.mode != "RGBA":
        canvas.paste(im, (x, y), im)
        return
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(canvas.width, x + im.width), min(canvas.height, y + im.height)
    if x1 <= x0 or y1 <= y0:
        return
    src = im.crop((x0 - x, y0 - y, x1 - x, y1 - y))
    canvas.alpha_composite(src, dest=(x0, y0))


def compose_view_board(
    views: List[Image.Image],
    *,
    cell: Optional[Tuple[int, int]] = None,
    bg: Optional[Tuple[int, int, int]] = DEFAULT_BG,
    gap: int = 12,
    baseline: float = 0.93,
    tolerance: int = 40,
    pad_frac: float = 0.12,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """把正/侧/背（/右）多张单视角图拼成一张横向视图板。

    - 每张先抠底色，消除图与图之间色差的可见接缝。抠图走 auto 链
      （u2net 语义 → grabcut → 颜色键）：实测颜色键容差 72 会把「深色裤子/
      黑披风」这类与暗绿底距离近的配件当成背景咬掉，优先语义抠图；
      key 兜底容差收紧到 40，保住深色小配件；抠图前先铺边距，防踩边肢体被截；
    - 已经自带透明背景的输入（生成时选了「透明」或上游扣过底）**直接用它的
      alpha**，不再抠一次：二次抠底会把已成立的边缘啃掉，也白白浪费语义抠图；
    - 主体底部对齐到共同基线、水平居中——模型出图的角色高低不齐，
      平移对齐后才是可用的视图基准；
    - 抠空（背景没识别出来）时退回原图等比贴入，绝不丢视图；
    - `bg=None` 输出 RGBA 透明视图板（底板全透明，主体 alpha 保留）：
      每条 alpha 硬边原样带过去，下游引擎可直接用，不必再抠一次底；
    - 张数不限（3=老三视图，4=四视图），按传入顺序排列。
    """
    if not 2 <= len(views) <= 4:
        raise ValueError(f"视图板需要 2~4 张图，收到 {len(views)} 张")
    order = VIEW_ORDER[:len(views)]
    imgs = [v.convert("RGBA") for v in views]

    if cell is None:
        cw = max(i.width for i in imgs)
        ch = max(i.height for i in imgs)
    else:
        cw, ch = int(cell[0]), int(cell[1])

    mats: List[Tuple[Image.Image, Optional[Tuple[int, int, int, int]], str]] = []
    for im in imgs:
        # 透明像素占比超 2% 即视为「已带透明背景」：真透明图整片是 0，
        # 不透明图只有边缘零星抗锯齿像素，两者不会误判
        has_alpha = float((np.asarray(im)[:, :, 3] < 16).mean()) > 0.02
        cut: Optional[Image.Image] = None
        method = "alpha"
        if not has_alpha:
            method = "raw"
            try:
                cut, mmeta = _matte_padded(im.convert("RGB"), pad_frac, tolerance)
                method = mmeta.get("method", "key")
            except Exception:
                cut = None
        if cut is not None:
            a = np.asarray(cut)[:, :, 3]
            ys, xs = np.where(a > 8)
            if len(xs):
                mats.append((cut, (int(xs.min()), int(ys.min()),
                                   int(xs.max()), int(ys.max())), method))
                continue
        if has_alpha:
            # alpha 输入抠空只可能是整张全透明，仍按原图贴入，不静默丢视图
            a = np.asarray(im)[:, :, 3]
            ys, xs = np.where(a > 8)
            box = None if len(xs) == 0 else (int(xs.min()), int(ys.min()),
                                             int(xs.max()), int(ys.max()))
            mats.append((im, box, "alpha"))
        else:
            # 抠空兜底：整图当主体（不裁不缩放，只做平移对齐）
            mats.append((im, None, "raw"))

    baseline_y = int(ch * baseline)
    width = cw * len(imgs) + gap * (len(imgs) - 1)
    if bg is None:
        strip = Image.new("RGBA", (width, ch), (0, 0, 0, 0))
    else:
        strip = Image.new("RGB", (width, ch), bg)
    infos: List[Dict[str, Any]] = []
    for i, (rgba, box, method) in enumerate(mats):
        x0 = i * (cw + gap)
        if box is None:
            # 兜底：等比缩放置中（只缩不放，避免小图被放大发虚）
            scale = min((cw - 8) / rgba.width, (ch - 8) / rgba.height, 1.0)
            nw, nh = max(1, int(rgba.width * scale)), max(1, int(rgba.height * scale))
            small = rgba.resize((nw, nh), Image.LANCZOS)
            _place(strip, small, (x0 + (cw - nw) // 2, (ch - nh) // 2))
            infos.append({"view": order[i], "matte": method, "bbox": None,
                          "offset": [(cw - nw) // 2, (ch - nh) // 2], "scaled": [nw, nh]})
            continue
        bx0, by0, bx1, by1 = box
        # 目标：主体水平中心 → 格子中心；主体底部 → 共同基线
        dx = int(round(cw / 2 - (bx0 + bx1) / 2))
        dy = int(round(baseline_y - by1))
        # 钳位：任何情况下不许把主体推出画布
        dx = max(min(dx, cw - bx1 - 1), -bx0)
        dy = max(min(dy, ch - by1 - 1), -by0)
        _place(strip, rgba, (x0 + dx, dy))
        infos.append({"view": order[i], "matte": method, "bbox": list(box),
                      "offset": [dx, dy]})
    return strip, {
        "cell": [cw, ch], "gap": gap, "baseline": baseline_y,
        "bg": "transparent" if bg is None else "#%02X%02X%02X" % tuple(int(v) for v in bg),
        "alpha": bg is None,
        "size": list(strip.size), "views": infos,
    }


# 向后兼容别名（老调用方/老项目数据里的三视图拼板）
compose_three_view = compose_view_board


def cell_size(width: int, height: int) -> Tuple[int, int]:
    """对齐到 16 的倍数（下采样约束），最小 64。"""
    return max(64, int(width) // 16 * 16), max(64, int(height) // 16 * 16)


# --------------------------------------------------------------------------- #
# 单步执行：侧/背/右视图（编辑底座指令接力）
# --------------------------------------------------------------------------- #
def _apply_background(instruction: str, background: str) -> str:
    """给视角指令拼上正视图的背景要求（侧/背/右复用正视图参数）。

    各张图底色必须和正视图一致，拼板的抠底逻辑才对得上——正视图中灰底而
    侧视图被模型补了个场景背景，拼出来就是一块异色补丁。
    transparent 用 Qwen-Image-2.1 官方双段式 RGBA 声明激活 VAE 第 4 通道
    （只说「透明背景」实测 alpha 恒为 255，见 .verify/exp-b）。
    """
    if background == "transparent":
        # 视角指令里那句「背景保持纯色不变」在透明底上是有害的：模型会理解成
        # 「照抄参考图的地面/底色」→ 实测右视图脚下留了一个椭圆地面投影，
        # 直接污染 alpha（精灵导入引擎后就是一坨紫椭圆）。这里换成透明语义，
        # 并显式禁掉投影/阴影/环境元素。
        body = instruction.replace(
            "背景保持纯色不变，",
            "背景完全透明、不画任何底色，不要地面投影、不要阴影、不要环境元素，")
        return (
            "This is an RGBA image with transparency. "
            + body
            + " The image has alpha channel and the background is transparent."
        )
    phrase = background_phrase(background)
    if phrase:
        return instruction + "背景要求：" + phrase + "。"
    return instruction


async def derive_view(
    view: str,
    base: Image.Image,
    *,
    style_id: Optional[str] = None,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    width: int = 512,
    height: int = 768,
    max_seq_len: Optional[int] = None,
    background: str = "gray",
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """由正视图接力生成侧/背/右视图。

    走编辑底座（Qwen-Image-Edit-2511 / Qwen-Image-2.1）的指令接力：外观保持最好，
    且不需要重新描述角色。provider 带 refs 时会自动路由到编辑底座，Z-Image 纯文生图
    底座不支持参考图，会直接抛出可读的配置提示。

    background 复用正视图的背景设置：侧/背/右必须和正视图同底色，拼板时
    抠底逻辑才对得上——正视图中灰底而侧视图被模型补了个场景背景，拼出来
    就是一块异色补丁。transparent 走 Qwen-Image-2.1 原生 RGBA 双段式声明。
    """
    from .. import progress as progress_mod
    from ..providers.diffusers_local import DiffusersProvider

    if view not in ("side", "back", "side_r"):
        raise ValueError(f"未知视角：{view}")

    provider = DiffusersProvider(provider_cfg or {})
    instruction = _apply_background(_view_instruction(view), background)
    progress_mod.report(task_id, 0, 0,
                        f"{_VIEW_META[view]['cn']}（编辑底座接力）…")

    t = time.time()
    result = await provider.generate(
        prompt=instruction,
        width=max(64, int(width) // 8 * 8),
        height=max(64, int(height) // 8 * 8),
        steps=steps, seed=seed, n=1, refs=[base],
        task_id=task_id, max_seq_len=max_seq_len,
    )
    rmeta = dict(result.meta or {})
    # 保留 alpha：正视图是透明底时（选图上传 RGBA / 旧项目数据），强制转 RGB
    # 会把透明区抹成黑色，拼板的边框取色再把黑色当底色，整块板发黑
    img = result.images[0].convert("RGBA")
    out = {
        "view": view, "name": _VIEW_META[view]["cn"], "mode": "edit",
        "instruction": instruction,
        "steps": rmeta.get("steps"), "seed": seed,
        "model": rmeta.get("model"),
        "background": background,
        "autoRouted": bool(rmeta.get("autoRouted")),
        "seconds": round(time.time() - t, 1),
    }
    # 透明背景只有 Qwen-Image-2.1（qwen21 族）能原生输出；旧底座会忽略
    # RGBA 声明给出不透明图，在 meta 里明说，别让用户对着黑底纳闷
    if background == "transparent" and rmeta.get("family") != "qwen21":
        out["bgWarning"] = ("当前底座不支持原生透明输出，已按不透明图处理；"
                            "透明背景需要 Qwen-Image-2.1 底座")
    if rmeta.get("vram"):
        out["vram"] = rmeta["vram"]
    return img, out
