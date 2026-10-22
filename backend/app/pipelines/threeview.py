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
  就是原图的脚掌颜色，不需要任何颜色猜测。上限后来从 2% 提到 8%：灰猫的
  **后脑勺整块是 7.0% 画布**（灰填充 #7C7879 与中灰底 #787878 只差 3 个色阶，
  u2net 必然判成背景），2% 补不到 → 拼板上出现「只剩黑描边的空心脑袋」。
- 边缘溢色清洗（matte.clean_edge_spill）：描边外圈常带「黑+底色」的抗锯齿
  混色，抠完底色变透明就留下一圈异色边。判据是「比离自身内侧颜色更接近
  底色」，纯黑描边离内侧为 0 永远不会被洗掉。

指令层面的坑（已修）：`_view_instruction` 曾把示例角色（灰白英短猫武士）的
造型写死进指令——「后脑勺纯灰色」「红色披风背部完整可见」「手中兵器」。
这些句子对任何角色都会生效，于是正面没有披风的角色转背面也一定长出红披风、
后脑被刷成灰。现在指令只描述「视角怎么变」和「造型以参考图为准」，
不点名任何具体服装、配件或颜色。

**单侧配件为什么会跑丢/镜像（第二轮修正）**：只写「不新增也不删除任何部件」
是纯否定约束，模型默认按「画面左右」理解配件位置，而正视图是镜像关系——
正视图画面右侧其实是角色左侧。于是「腰挎武士刀」的角色一转侧面：刀要么
整个消失（被当成正面细节丢掉），要么搬到新图的同一画面侧（= 镜到另一条
腿的腰上），要么被「为了让你看清」画到身体轮廓外面。这些都是与角色无关
的通用失效模式，所以修法也必须通用：指令里给出三条**几何守恒**——配件守恒
（同一侧/同一高度/同一佩戴方式/刚体只跟着转）、遮挡守恒（该挡就挡，看不见
不等于补齐）、自身左右锚定（用角色的左右判断，不用画面左右，并写明每个
视角能看到哪一侧）。这三条对「腰挎刀/单肩包/单侧护具/只握一只手的东西」
一视同仁，不点名任何具体道具，换任何角色都成立。

真机对照（768²，qwen-image-2.1 编辑接力）：
- 用户那张正视图（灰猫 + 红披风 + 左腰挎刀）用旧指令转左视图，腰侧的刀
  **整把消失**——模型把离观众最近的胳膊当成了遮挡物，把刀收进身体里；
- 补上「近侧配件在这一侧的最外层、画在胳膊和腿外面」后，2 个变体 × 3 个
  种子共 6 张左视图全部保留刀具：刀柄朝前、刀鞘顺腰线向下、与正视图同侧，
  没有一张镜像到另一条腿；
- 背视图同理：加「仍然露在身体轮廓之外，不要被衣服/布料/头发盖住」之前，
  刀只剩轮廓边上的一个小角，之后完整露在衣服外面、位于画面左侧（角色左侧）。

**已知边界（指令救不回来的那类）**：正视图自己就把配件画在身体中线、斜跨在
腰带上的时候（三维位置本来就模糊，看不出挂在哪条腿的腰上），侧视图仍会把它
丢掉——实测 2/2 张都丢，把参考图先铺白/铺灰再喂进去也没用（那是画面的问题，
不是参考图污染的问题）。可操作的解法在正视图那一侧：提示词里写清侧别
（「左腰侧挎武士刀」而不是「腰挎武士刀」），出图时配件就会落在腰侧，后续
推导才有一个明确的锚点。示例库的猫武士词条已按这个口径改过并写了原因。
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

# 视角接力的负面词。只放「这个视角绝不该出现的东西」，不放画风词：
# 编辑底座走 true_cfg_scale=4.0，负面词真的参与 CFG（不像 Z-Image 是
# guidance 蒸馏、负面词恒无效）。「地面/接地阴影」是实测最顽固的一项：
# 角色在中灰底上站着，模型几乎必然补一圈接触阴影，正面描述压不住时靠它兜。
VIEW_NEGATIVE = "地面, 地面线, 接地阴影, 椭圆投影, 倒影, 两个角色, 重复的配件, 文字, 水印"

# 视角元数据：名称 + 正视图姿势词
_VIEW_META: Dict[str, Dict[str, str]] = {
    "front": {
        "cn": "正视图", "en": "front view",
        # 不写「双脚踩地」：这句 + 模型对全身站立的先验，会让它顺手补一圈接地
        # 阴影（脚下那块永远比别处暗）。只说「同一水平线」，不提地面。
        "pose_cn": "角色面向观众，全身站立，双脚并拢在同一水平线上，身体居中",
        "pose_en": "character facing the viewer, full body standing, feet level with each other, centered",
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

# 视角指令里那句「背景保持纯色不变」是 `_apply_background` 的替换锚点：
# 透明底上它必须换成透明语义（否则模型会照抄参考图的地面）。写成常量，
# 免得两句文案各自漂移后替换静默失效。
BG_KEEP = "背景保持纯色不变，"


def _character_clause(character: str) -> str:
    """把正视图的角色设定原句变成「部件清单」附在视角指令里（可为空）。

    为什么要把已经画在图上的东西再说一遍：编辑底座对**细长小配件**（腰挎的
    刀、背上的弓、手上的短杖）经常在重画身体时直接漏掉，给它一个名词（尤其
    带侧别：「左腰侧挎武士刀」）比让它从图里自己认要稳。但原句通常带姿势词
    （「正面站立」「面向观众」），必须当场声明作废，否则模型会退回到正面站姿。
    """
    text = (character or "").strip().rstrip("。. ，,")
    if not text:
        return ""
    return (
        "角色设定清单（只用来核对这个角色身上有哪些部件；清单里「正面」「面向"
        "观众」这类视角词在本次转换中作废，以本张图的视角为准）：" + text + "。"
        "清单里的部件一个都不能少，但被身体挡住的部分只露出看得到的部分，"
        "位置和佩戴方式以参考图为准。"
    )


def _view_instruction(view: str, character: str = "") -> str:
    """编辑底座的中文指令：只转视角，外观/服装/配色/画风零改动。

    编辑器（Qwen-Image-Edit-2511 / Qwen-Image-2.1）吃中文指令。指令由六段
    组成，顺序即优先级：转到哪 → 这一侧能看到什么（自身左右锚定）→ 部件
    清单 → 配件守恒 → 遮挡守恒 → 外观/姿势不变。除部件清单（来自用户自己的
    角色描述）外全是通用几何约束，不点名任何具体道具与颜色（点名过的角色
    造型会成为「凭空添加」的指令，见模块注释）。
    """
    # 1) 转到哪个视角。说「像转身一样整体转 90 度、镜头不动」，避免模型换姿势
    turn = {
        "side": "把画面中的角色转换成左侧面视图：角色像原地转身一样整体转向"
                "画面左侧90度，镜头不动，看到的是角色的左侧面。",
        "side_r": "把画面中的角色转换成右侧面视图：角色像原地转身一样整体转向"
                  "画面右侧90度，镜头不动，看到的是角色的右侧面。",
        "back": "把画面中的角色转换成背面视图：角色原地整体旋转180度，背对观众，"
                "从正背后观察。",
    }[view]

    # 2) 自身左右锚定：模型默认按「画面左右」理解配件位置，而正视图是镜像的
    #    （正视图画面右侧 = 角色左侧）。把每个视角能看到哪一侧说成事实，
    #    模型才有依据决定「哪些该露、哪些该挡」。
    #
    #    近侧必须写明「在肢体外面、不要藏到胳膊后面」：实测左视图里那条刀
    #    整把消失，就是模型把「离观众最近的胳膊」当成了遮挡物，把腰侧的刀
    #    收进了身体里。物理上挂在腰侧的配件在近侧一定是最外层。
    #    「正视图画面右侧 = 角色左侧」这句镜像映射必须留着：不给映射，模型
    #    无法把参考图里的位置翻译成角色的左右，结果仍是靠猜。
    side_fact = {
        "side": "这个角度里离观众最近的是角色的左臂、左腿和左肩。正视图的画面右侧"
                "就是角色的左侧：挂在那里的配件（比如一侧腰上的刀）在这个视角里离"
                "观众最近、在这一侧的最外层，画在胳膊和腿的外面、不被它们挡住；"
                "挂在角色右侧的配件被"
                "身体挡住，只露出没被挡住的部分。",
        "side_r": "这个角度里离观众最近的是角色的右臂、右腿和右肩。正视图的画面左侧"
                  "就是角色的右侧：挂在那里的配件（比如一侧腰上的刀）在这个视角里离"
                  "观众最近、在这一侧的最外层，画在胳膊和腿的外面、不被它们挡住；"
                  "挂在角色左侧的配件被"
                  "身体挡住，只露出没被挡住的部分。",
        "back": "背面看时角色的左右与画面左右一致，和正视图正好相反：正视图里"
                "出现在画面右侧的配件，转到背面后会出现在画面左侧，反之亦然。"
                "挂在一侧腰间的配件在背后看仍然露在身体轮廓之外，不要被衣服、"
                "身后的布料或头发盖住，也不要缩成一个小角。",
    }[view]

    # 3) 配件守恒。条件句「参考图里如果有」是在防「凭空添加」：不点颜色、
    #    不点件数，只说这类配件的固定方式必须原样保留。
    keep_props = (
        "参考图里如果有只属于身体一侧的配件（挎在一侧腰间的刀剑、单肩背的包或"
        "披挂、单侧护具、只戴在一只耳朵或一只手上的饰物），它必须仍然在角色的"
        "同一侧、同一高度，用同样的方式穿戴和固定，形状、长度和相对身体的倾角"
        "都不变，只是跟着身体一起转；不能换到另一侧、不能左右各出现一个、不能"
        "从腰间挪到手上或从手上挂回腰间。配件在正视图里如果斜跨着身体、一眼看不"
        "出偏向哪边，就按它固定的一端（系在腰带上、挂在肩上的那一头）所在的那侧"
        "算，绝不能因为看不清就把它省掉。身体本身左右对称的部分（双肩、双臂、"
        "双腿、衣摆两侧）按参考图的样式对称延续，不要一边一种。"
    )

    # 4) 遮挡守恒：模型倾向于把被挡住的配件「挪出来给人看」，或者因为
    #    看不见就整个删掉。两种情况都要在这一句里堵住。
    keep_occlusion = (
        "露出多少由这个视角真实的三维关系决定：被身体挡住的部分就让它被挡住，"
        "不要为了让配件完整就把它挪到身体另一侧、抬到轮廓外面，也不要挪动挡住它"
        "的那条胳膊或那条腿。"
    )

    detail = {
        "side": "侧脸只露出一只眼睛和一只耳朵，鼻口在轮廓最前方凸出。",
        "side_r": "侧脸只露出一只眼睛和一只耳朵，鼻口在轮廓最前方凸出。",
        "back": "脸、眼睛和鼻子完全不可见；头部的造型按参考图原样自然延续到后脑，"
                "不要在后脑上保留五官。",
    }[view]

    keep_look = (
        "外观以参考图为准：造型、配色、体型比例和画风全部保持原样，不新增也不"
        "删除任何部件；站姿、重心、四肢张开的角度和头部朝向都不变，不要改成"
        "走姿、不要抬手、不要换一个姿势重新构图。"
    )

    tail = BG_KEEP + "角色居中站立，全身完整入画，画面中只有一个角色，不要添加文字和水印。"

    return (turn + detail + side_fact + _character_clause(character)
            + keep_props + keep_occlusion + keep_look + tail)


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


def _fill_enclosed_holes(cut: Image.Image, max_ratio: float = 0.08
                         ) -> Tuple[Image.Image, int]:
    """补回被角色完全围住的透明孔洞（只改 alpha，RGB 原样）。

    上限按画布比例给到 8%：**背视图的后脑勺实测占 7.0%**——中灰底的灰猫，
    后脑填充色（#7C7879）和中灰底（#787878）只差 3 个色阶，u2net/grabcut
    都会把它判成背景，抠完就是一个「只剩黑描边、里面全透明」的空心脑袋，
    拼板上看像被咬掉了。2% 的上限补不到它。
    代价：手臂叉腰围出来的真背景窗口如果小于 8% 也会被补上（表现为身体上
    一块底色补丁）。两边都是错，但空心比补丁严重得多，且围合窗口在站姿
    角色里少见。
    """
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


def _fit_seq_len(instruction: str, requested: Optional[int]) -> int:
    """视角指令需要的文本序列长度：不短于指令本身，也不短于用户选的值。

    文本编码超长会被管线**静默截断**（截的是尾部，正好是最关键的背景句和
    遮挡条款），表现成「指令写了没用」。中文约 1.3 token/字 + 64 余量，
    向 128 取整，上限 1536（与 provider 的估算口径一致）。
    """
    est = int(len(instruction or "") * 1.3) + 64
    est = (est + 127) // 128 * 128
    return max(int(requested or 0), 512, min(1536, est))


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
        # 并显式要求轮廓以外**全部**完全透明（只写「不要阴影」对蒸馏/编辑
        # 底座都太弱，要把结果说成可验证的画面事实）。
        body = instruction.replace(
            BG_KEEP,
            "背景完全透明、不画任何底色，角色轮廓以外的像素全部完全透明，"
            "不要地面投影、不要阴影、不要环境元素，")
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
    character: str = "",
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """由正视图接力生成侧/背/右视图。

    走编辑底座（Qwen-Image-Edit-2511 / Qwen-Image-2.1）的指令接力：外观保持最好，
    且不需要重新描述角色。provider 带 refs 时会自动路由到编辑底座，Z-Image 纯文生图
    底座不支持参考图，会直接抛出可读的配置提示。

    character = 正视图那句角色描述原文（可为空）：只用来给模型补一份「部件
    清单」，细长小配件（腰挎的刀/背上的弓/手上的杖）不会在重画身体时被漏掉；
    里面的姿势词由 `_character_clause` 当场声明作废。

    background 复用正视图的背景设置：侧/背/右必须和正视图同底色，拼板时
    抠底逻辑才对得上——正视图中灰底而侧视图被模型补了个场景背景，拼出来
    就是一块异色补丁。transparent 走 Qwen-Image-2.1 原生 RGBA 双段式声明。
    """
    from .. import progress as progress_mod
    from ..providers.diffusers_local import DiffusersProvider

    if view not in ("side", "back", "side_r"):
        raise ValueError(f"未知视角：{view}")

    provider = DiffusersProvider(provider_cfg or {})
    instruction = _apply_background(_view_instruction(view, character), background)
    # 序列长度取「正视图设置」与「本指令实际长度」的较大值：视角指令现在有
    # 五段恒守条款（约 470 字 ≈ 610 token），沿用正视图的 512 会把尾部的
    # 背景句整段截掉——截断不报错，只会表现为「背景又不对了」。
    seq_len = _fit_seq_len(instruction, max_seq_len)
    # 背景选「无」时模型可以自由补场景，此时禁「地面」是自相矛盾的 → 不传，
    # 让 provider 回落到设置里的默认负面词。
    negative = None if background == "none" else VIEW_NEGATIVE
    progress_mod.report(task_id, 0, 0,
                        f"{_VIEW_META[view]['cn']}（编辑底座接力）…")

    t = time.time()
    result = await provider.generate(
        prompt=instruction,
        width=max(64, int(width) // 8 * 8),
        height=max(64, int(height) // 8 * 8),
        steps=steps, seed=seed, n=1, refs=[base],
        negative=negative,
        task_id=task_id, max_seq_len=seq_len,
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
