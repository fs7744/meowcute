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
- 补「被轮廓围住、却和背景连成一片」的区域（`_restore_enclosed_flat`）：768²
  的背视图里，头占 9% 画布且**不是封闭孔洞**（u2net 把描边一起啃了，头在
  alpha 里和外部背景连通），按面积补洞补不到 → 拼板上是「没有脑袋的披风」。
  判据换成轮廓边（Canny + 膨胀当墙），从四边 flood fill，够不着的区域就是
  被轮廓围住的身体内部，恢复不透明（RGB 用抠图前的原色，不需要猜）。
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

**第三轮：抽卡配对 + 背覆体积守恒 + 图像条件修正**

1. **抽卡要成套**：正视图一次生成 n 张时，四个视角各只取 `images[0]` 是错的
   ——用户在正视图里挑不出来的「好那几张」永远进不了四视图。现在
   `ViewDerivePayload.sources` 收多张正视图，按顺序逐张接力，返回的每张视角图
   带 `pairIndex` 指回源图；拼板节点逐视角选图，并能「按第 k 组」一键对齐
   （第 k 张正视图 + 各视角第 k 张 = 同一套）。配对必须靠序号，不能靠看图猜。
2. **背覆体积守恒**：模型对「正面只看见领口一条边、背面完全不知道长什么样」
   的部件默认走省略——披风要么整个消失、要么缩成领口下一圈窄边。新增条款用
   **几何位置**描述这一类部件（从肩背垂下、盖住后背、向两侧张开），给出可
   验证的画面事实（连接点/下垂高度/张开宽度不变、至少与肩同宽、要有外表面
   和褶皱），并且写成条件句——**不写「披风」这个名词**，否则没披风的角色
   会凭空长一件（归档项目的坑）。
3. **图像条件比文字更能决定「像不像」**：provider 里参考图原先被
   `convert("RGB")`（alpha=0 压成黑底）和 `resize((w,h))`（拉伸变形）处理，
   等于先把参考图弄坏再让模型照着画。现在统一走 `_prep_ref_images`
   （等比缩放居中 + 按目标底色铺平）。这条对四视图和普通参考图生成同时生效。
4. **背景句前置**：文本超长时截的是尾部，而透明双段式声明/纯色背景句恰恰
   是最不能丢的。`_apply_background` 改成把背景要求拼在指令最前面。
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
# Qwen-Image-2.1 走 true_cfg_scale=4.0，负面词真的参与 CFG（历史上另一条
# guidance 蒸馏底座 negative 恒无效，才逼出「把结果说成画面事实」的写法）。
# 「地面/接地阴影」是实测最顽固的一项：角色在中灰底上站着，模型几乎必然
# 补一圈接触阴影，正面描述压不住时靠它兜。
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

# 背景要求现在前置拼在指令最前面（见 _apply_background），指令正文里不再含
# 「背景保持纯色不变」这类锚点句——它曾经是字符串替换的锚点，模型偶尔会把
# 这句当成「照抄参考图地面」的许可，透明底上直接长出投影。


def _character_clause(character: str) -> str:
    """角色设定：只用于**辨认**参考图上的部件，不作为增删依据。

    把它当「部件清单，一个都不能少」用过，结果是两头都出错：
    - 描述里写了某件东西、参考图没画 → 每个视角都多出一件（用户看到的
      「没有披风却给我加上了」）；
    - 描述没写、图上有的细长配件 → 模型按清单核对，把它当成清单外的东西
      在重画身体时吃掉。
    所以这里必须**同时**堵住两个方向：图里有、没写到的要保留；
    图里没有、写到了的不要画。
    """
    text = (character or "").strip().rstrip("。. ，,")
    if not text:
        return ""
    # 描述只承担「辨认」职责，不值得为它把守恒条款挤出文本预算。
    # 上限与前端提示共用 prompt_limits.CHARACTER_MAX（改一处两边一起变）：
    # 920 字指令 + 380 字描述 + 110 字透明声明 ≈ 1897 token，落在 1920 以内。
    _limit = 380
    try:
        from ..prompt_limits import CHARACTER_MAX as _limit  # noqa: PLC0415
    except Exception:
        pass
    if len(text) > _limit:
        text = text[:_limit]
    return (
        "角色设定（只用来帮你认出参考图上的部件分别是什么，不是让你照着文字补齐）："
        + text + "。"
        "参考图里有的部件，下面没写到也必须保留；参考图里没有的部件，"
        "下面写到了也不许画。其中「正面」「面向观众」这类视角词在本次转换中作废，"
        "以本张图的视角为准。"
    )


def _view_instruction(view: str, character: str = "") -> str:
    """编辑底座的中文指令：只转视角，外观/服装/配色/画风零改动。

    编辑器（Qwen-Image-2.1）吃中文指令。顺序即优先级：
    任务总纲 → 转到哪 → 这一侧看到什么 → 角色设定（仅用于辨认）→
    单侧配件守恒 → 背覆守恒 → 遮挡守恒 → 身份锚点 → 造型姿势不变 → 收尾。

    两条硬规则都是踩坑换来的：
    1. **绝不无条件断言角色身上有什么。** 上一版背面段写「角色穿着从肩部垂下
       的外层布料时…」，本意是条件句，模型读到「穿着…外层布料」就给没有披风
       的角色补了一件。条件必须写成双分支：参考图里有就怎么画、没有就怎么画。
    2. **通用条款不点名具体道具。** 写「刀剑」会给没有佩剑的角色加一把；
       名词只允许来自用户自己的角色设定（那一段还被限制成「仅用于辨认」）。
    """
    # 1) 任务总纲：把「不增不减」说成这次转换的定义，而不是末尾的补充说明。
    #    模型对首段的权重最高，配件丢失/凭空增加都优先靠这一句压。
    task = (
        "这是同一个角色的转面，不是重新设计：参考图里有的部件一件都不能少，"
        "参考图里没有的部件一件都不能加。唯一依据是参考图本身。"
    )

    # 2) 转到哪个视角。说「像转身一样整体转 90 度、镜头不动」，避免模型换姿势
    turn = {
        "side": "把画面中的角色转换成左侧面视图：角色像原地转身一样整体转向"
                "画面左侧90度，镜头不动，看到的是角色的左侧面。",
        "side_r": "把画面中的角色转换成右侧面视图：角色像原地转身一样整体转向"
                  "画面右侧90度，镜头不动，看到的是角色的右侧面。",
        "back": "把画面中的角色转换成背面视图：角色原地整体旋转180度，背对观众，"
                "从正背后观察。",
    }[view]

    # 3) 这个视角的画面事实。只写「哪些部位看得见/看不见」，不写穿着什么——
    #    凡是「角色穿着…」的句式，模型都会当成生成目标，哪怕前面加了「如果」。
    detail = {
        "side": "侧脸只露出一只眼睛和一只耳朵，鼻口在轮廓最前方凸出。",
        "side_r": "侧脸只露出一只眼睛和一只耳朵，鼻口在轮廓最前方凸出。",
        "back": "脸、眼睛和鼻子完全不可见，后脑不要保留五官；肩线、背脊、腰线、"
                "臀部、腿后侧按参考图的体型推演，衣物的层次、开口与配件相对位置"
                "都和参考图一致。",
    }[view]

    # 4) 自身左右锚定：模型默认按「画面左右」理解配件位置，而正视图是镜像的
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
                "就是角色的左侧：挂在那里的部件在这个视角里离观众最近、在这一侧的"
                "最外层，画在胳膊和腿的外面、不被它们挡住；挂在角色右侧的部件被"
                "身体挡住，只露出没被挡住的部分。",
        "side_r": "这个角度里离观众最近的是角色的右臂、右腿和右肩。正视图的画面左侧"
                  "就是角色的右侧：挂在那里的部件在这个视角里离观众最近、在这一侧的"
                  "最外层，画在胳膊和腿的外面、不被它们挡住；挂在角色左侧的部件被"
                  "身体挡住，只露出没被挡住的部分。",
        "back": "背面看时角色的左右与画面左右一致，与正视图正好相反：正视图画面"
                "右侧的部件，转到背面在画面左侧。挂在一侧腰间的部件在背后仍露在"
                "身体轮廓之外，不要被衣物、头发或身体盖住，也不要缩成小角。",
    }[view]

    # 5) 单侧配件守恒。**不点名具体道具**（「刀剑」这类名词会给没有它的角色
    #    加一把），只描述几何关系：同一侧、同一高度、同样的固定方式。
    #    额外给一条可验证的画面事实：细长物件在能看到它的那一侧，至少一端要
    #    伸到身体轮廓外——这是「整根被身体吃掉」唯一能被肉眼核对的判据。
    keep_props = (
        "只属于身体一侧的部件（挎在腰间的细长物件、单肩包、单侧护具、单边饰物）"
        "必须仍在同一侧、同一高度、同样的固定方式，形状长度倾角不变，只跟着身体转；"
        "不能换边、不能左右各一个、不能挪到手上。正视图里斜跨看不出偏向时，"
        "按固定端所在的那侧算。细长物件在能看到它的那一侧，至少有一端（柄、"
        "鞘尾、带扣）要露在身体轮廓之外，不能整根收进身体或头发里，也不能因为"
        "一部分被挡就整件省略。"
    )

    # 6) 背覆部件守恒。上一版写成了「参考图里如果有…它必须原样保留…背面看时它
    #    是背后面积最大的东西之一，至少与肩同宽」——条件句里全是强断言，模型
    #    直接照着补一件。这一版改成**双分支**，把「没有」这一支也说成具体画面：
    #    没有就画参考图里的后背，不许补任何垂下来的东西。
    keep_volume = (
        "从肩膀、领口或腰后垂下、盖住后背、或向两侧张开的成片大体量部件，"
        "完全按参考图判断：图里有，就按连接点、下垂高度、张开宽度画出厚度和"
        "外表面；图里没有，后背就按参考图的样子画，不要补任何垂下来的布料或"
        "披挂，也不要为了让背面不空而加东西。"
    )

    # 7) 遮挡守恒：模型倾向于把被挡住的配件「挪出来给人看」，或者因为看不见就
    #    整个删掉。两种情况都要堵；同时限定「遮挡只作用于被挡的那一段」，
    #    否则它会拿「被挡住了」当省略整件配件的理由。
    keep_occlusion = (
        "被身体挡住的部分就让它被挡住，不要为了画完整就挪到另一侧或轮廓外，"
        "也不要挪动挡住它的胳膊或腿；但「挡住」只作用于被挡的那一段，不能因为"
        "一部分被挡就把整件省掉。"
    )

    # 8) 身份锚点：换角度最容易「换一个长得像的角色」，把这些可逐项核对的
    #    特征点名要求保持一致。仍然不点具体颜色，只说「以参考图为准」。
    identity = (
        "身份特征逐项一致：脸型、耳/角形状与位置、眼睛间距、尾巴有无与长度、"
        "毛色花纹走向、体型与四肢比例都以参考图为准，不能换一套花纹或改比例。"
    )

    keep_look = (
        "造型、配色、比例和画风全部保持原样，站姿、重心、四肢张开的角度和头部"
        "朝向都不变，不要改成走姿、不要抬手、不要重新构图。"
    )

    tail = "角色居中站立，全身完整入画，画面中只有一个角色，不要添加文字和水印。"

    # 角色设定放在视角事实之后、守恒条款之前：靠前的位置让其中的名词真正参与
    # 辨认，紧跟着的守恒条款再把它约束成「只认图、不照抄」。
    return (task + turn + detail + _character_clause(character) + side_fact
            + keep_props + keep_volume + keep_occlusion + identity + keep_look + tail)


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
    # 全向量化（面积 + 触边都走 bincount / unique，最后 LUT 映射）：
    # 旧写法每个域扫一遍全图，孔洞多时直接把拼板拖慢（同 matte._fill_small_holes）。
    areas = np.bincount(lab.ravel(), minlength=n)
    border = np.zeros(n, bool)
    for edge in (lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]):
        border[np.unique(edge)] = True
    ok = (areas > 0) & (areas <= limit) & ~border
    ok[0] = False
    if not ok.any():
        return cut, 0
    filled = int(areas[ok].sum())
    a[ok[lab], 3] = 255
    return Image.fromarray(a, "RGBA"), filled


def _restore_enclosed_flat(cut: Image.Image, source: Image.Image,
                           max_ratio: float = 0.25,
                           edge_lo: int = 30, edge_hi: int = 90,
                           grow: int = 2) -> Tuple[Image.Image, int]:
    """把「被角色轮廓围住、却和外部背景连成一片」的区域恢复成不透明。

    为什么需要这一步（`_fill_enclosed_holes` 补不到的那类）：灰猫站在中灰底上，
    头部填充色 #7B7777 和底色 #808080 只差 5-7 个色阶，u2net 直接判成背景；
    描边只有 2px，语义掩码连描边一起啃掉，于是「头」在 alpha 里和外部背景连成
    一片——它不是封闭孔洞，按面积补洞补不到，拼板上就是「没有脑袋的披风」
    （实测 768² 背视图：头占 9% 画布，整块丢）。

    判据换成**轮廓边**（Canny + 膨胀 2px 当墙）：从四边 flood fill 所有非边
    像素，够不着的区域就是被轮廓围住的身体内部。只把「当前已判透明」的像素
    恢复不透明，RGB 用抠图前原图的颜色（抠图本身不动 RGB，实测头像素仍是
    #7B7777），所以补回来就是原色，不需要猜。
    上限 25% 画布：头再大也在这个量级以内；更大的闭合区域（披风围出来的大环）
    宁可不动，避免糊成一块底色补丁。
    只在「大面积平涂底色」的图上启用（同为底色的像素 > 35%）：照片/场景图
    的 Canny 边缘是细节不是轮廓，做了只会恢复出一堆噪点。
    """
    a = np.asarray(cut).copy()
    src = source.convert("RGB")
    bg = _border_bg(src)
    rgb = np.asarray(src).astype(np.int16)
    bg_like = (np.abs(rgb - np.array(bg, np.int16)).max(axis=2) <= 40)
    if float(bg_like.mean()) < 0.35:
        return cut, 0
    try:
        import cv2
    except Exception:
        return cut, 0
    edges = cv2.dilate(cv2.Canny(np.asarray(src.convert("L")), edge_lo, edge_hi),
                       np.ones((grow, grow), np.uint8))
    free = (edges == 0).astype(np.uint8)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(free, connectivity=4)
    if n <= 1:
        return cut, 0
    border_labels = set(lab[0, :].tolist()) | set(lab[-1, :].tolist())
    border_labels |= set(lab[:, 0].tolist()) | set(lab[:, -1].tolist())
    h, w = free.shape
    limit = int(max_ratio * h * w)
    keep = np.zeros((h, w), bool)
    for i in range(1, n):
        if i in border_labels:
            continue  # 能连到画面边缘 = 外部背景
        if int(stats[i, cv2.CC_STAT_AREA]) > limit:
            continue
        keep |= (lab == i)
    if not keep.any():
        return cut, 0
    # 闭运算把「膨胀出来的 2px 边墙」一起补上：不然恢复出来的头内部会留下
    # 一堆细白缝（实测头发高光/明暗交界处最明显），像被划了线
    keep = cv2.morphologyEx(keep.astype(np.uint8), cv2.MORPH_CLOSE,
                            np.ones((2 * grow + 1, 2 * grow + 1), np.uint8)).astype(bool)
    comp = keep & (a[:, :, 3] <= 8)      # 只碰当前透明的像素
    restored = int(comp.sum())
    if not restored:
        return cut, 0
    a[comp, 3] = 255
    return Image.fromarray(a, "RGBA"), restored


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
    cut, enclosed = _restore_enclosed_flat(cut, img)
    cut, recolored = matte_pipe.clean_edge_spill(cut, bg)
    meta = dict(meta, holesRestored=filled, enclosedRestored=enclosed,
                edgePixelsRecolored=recolored)
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
    """视角指令需要的文本序列长度（口径与生成卡共用 prompt_limits.fit_seq_len）。

    文本编码超长会被管线**静默截断**（截的是尾部，正好是最关键的配件守恒与
    背覆守恒条款），表现成「指令写了没用」。上限 1920 的依据见 prompt_limits。
    """
    from ..prompt_limits import fit_seq_len
    return fit_seq_len(instruction, requested)


def _apply_background(instruction: str, background: str) -> str:
    """把背景要求**前置**到视角指令上（侧/背/右复用正视图参数）。

    各张图底色必须和正视图一致，拼板的抠底逻辑才对得上——正视图中灰底而
    侧视图被模型补了个场景背景，拼出来就是一块异色补丁。

    为什么放最前而不是最后：文本编码超长时截的是**尾部**，而背景/透明声明
    恰恰是最不能丢的一句（透明底双段式声明被截掉，alpha 直接不激活）。
    放在开头还有一个好处：注意力权重更高，模型不会把背景当次要修饰。

    transparent 用 Qwen-Image-2.1 官方双段式 RGBA 声明激活 VAE 第 4 通道
    （只说「透明背景」实测 alpha 恒为 255，见 .verify/exp-b）。
    """
    if background == "transparent":
        return (
            "This is an RGBA image with transparency. "
            "背景完全透明、不画任何底色，角色轮廓以外的像素全部完全透明，"
            "不要地面投影、不要阴影、不要环境元素。"
            + instruction
            + " The image has alpha channel and the background is transparent."
        )
    phrase = background_phrase(background)
    if phrase:
        return "背景要求：" + phrase + "。" + instruction
    return instruction


def _ref_fill(background: str) -> Optional[Tuple[int, int, int]]:
    """参考图铺底用什么颜色：和正视图的底色对齐。

    透明底也要铺一层（用中灰）：provider 里 `convert("RGB")` 会把 alpha=0 的
    区域压成黑色，模型看到「黑底角色」就会顺手把黑当背景画进结果。铺中灰后
    我们看到的是和灰底完全一样的一张图，只是输出仍要求 RGBA。
    选「无」时返回 None，让 provider 按参考图自己的边框色判断。
    """
    return {
        "gray": (128, 128, 128),
        "green": (0, 255, 0),
        "transparent": (128, 128, 128),
    }.get(background)


def fit_view_size(width: int, height: int, ref_w: int, ref_h: int) -> Tuple[int, int]:
    """视角输出尺寸：长边沿用节点设置，**宽高比强制等于参考图**。

    视角接力的默认参数是 512×768（竖图），正视图一旦改成 768×768（方图），
    转出来的视角会被 letterbox 成竖图、角色缩在中间——拼板时和正视图对不上，
    看起来就是「换了个体型」。像素量级仍按节点设置，只把比例钉在参考图上。
    """
    if ref_w <= 0 or ref_h <= 0:
        return max(64, int(width or 512)), max(64, int(height or 512))
    want = max(int(width or 0), int(height or 0), 64)
    ratio = ref_w / ref_h
    if ratio >= 1:
        return want, max(64, int(round(want / ratio)))
    return max(64, int(round(want * ratio))), want


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
    batch_index: int = 0,
    batch_total: int = 1,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """由正视图接力生成侧/背/右视图。

    走 Qwen-Image-2.1 的指令改图接力：外观保持最好，且不需要重新描述角色。
    provider 带 refs 就是改图模式，同一个模型；没有参考图会直接报错。

    character = 正视图那句角色描述原文（可为空）：只作为「辨认用设定」——
    给模型一个名词去对上图里的部件（腰挎的刀 / 背上的弓 / 手上的杖），
    **不作为增删依据**：图里没有的，写了也不画；图里有而没写的，也要保留。
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

    width, height = fit_view_size(width, height, base.width, base.height)
    # 序列长度取「正视图设置」与「本指令实际长度」的较大值，上限 1920。
    # 这里曾经有个更隐蔽的问题：provider 收到 max_seq_len 后**没有传给管线**，
    # 管线默认 max_sequence_length=512（中文约 390 字），1000+ 字的视角指令
    # 被静默截掉一半——配件守恒、背覆守恒、姿势不变全在尾部，表现成「指令
    # 写了没用」。2026-10-23 已在 provider 侧修好，这里算的长度才真正生效。
    seq_len = _fit_seq_len(instruction, max_seq_len)
    # 背景选「无」时模型可以自由补场景，此时禁「地面」是自相矛盾的 → 不传，
    # 让 provider 回落到设置里的默认负面词。
    negative = None if background == "none" else VIEW_NEGATIVE
    label = (f"{_VIEW_META[view]['cn']} {batch_index + 1}/{batch_total}"
             if batch_total > 1 else _VIEW_META[view]['cn'])
    # 批量时把进度映射到整批：第 2 张从 base=40 报到 80（grand=160），
    # 进度条一路向前，不会每张图都从 0 冲到 100% 再退回去
    use_steps = 40 if not steps else max(20, min(int(steps), 50))
    grand = use_steps * max(1, batch_total)
    prog = {"label": label, "base": use_steps * batch_index, "grand": grand}
    progress_mod.report(task_id, use_steps * batch_index, grand,
                        f"{label}（编辑底座接力）…")

    t = time.time()
    result = await provider.generate(
        prompt=instruction,
        width=max(64, int(width) // 8 * 8),
        height=max(64, int(height) // 8 * 8),
        steps=steps, seed=seed, n=1, refs=[base],
        negative=negative,
        # 参考图铺底色：透明底不铺会被 convert("RGB") 压成黑底，
        # 模型把黑色当背景画进结果（暗部/地面），和「透明背景」直接打架
        ref_bg=_ref_fill(background),
        progress=prog,
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
