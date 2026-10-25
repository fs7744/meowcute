"""美术画风库 + 像素提示词契约。

继承归档 PixelForge 的 15 种画风（都是中文原生底座，风格词统一中文），
并增加两块新东西：

1. **风格词前置**：Qwen-Image-2.1 的 Qwen3-VL 文本编码器是因果结构，尾部
   token 对整体语义影响最弱——把画风放提示词开头，一句话定调整张图。这是
   实测结论，不是理论推测。
2. **像素契约（pixel contract）**：选像素类画风时，在提示词末尾追加一段
   技术约束（精确网格、每格单色、限定色板、无抗锯齿、硬轮廓）。参考
   perfectpixel-studio 的「契约式提示词」——正面规格写清楚，负面清单交给
   负面词，比只说「像素风」有效得多。

pixelize 字段是**后处理建议**：像素类画风出图后应接网格对齐 + 调色板量化，
参数随画风返回给前端预填。HD-2D 是唯一例外（像素感来自景深与光照，
整图量化会把虚化和光晕压成色块，3D 感直接消失）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

STYLES: List[Dict[str, Any]] = [
    # ---------------- 像素类（本项目重点） ----------------
    {
        "id": "pixel_16bit",
        "name": "复古 16-bit 像素",
        "tags": ["角色", "场景", "道具"],
        "hint": "FC/SFC 时代像素风，32×32 网格感，限定调色板，出图后自动 8× 像素精修",
        "prompt": "复古16-bit像素艺术风格，硬边缘像素块，有限调色板（16色以内），无抗锯齿，无渐变；"
                  "像素块之间不要画出网格线或格子边框，整幅图是连续的画面而不是一个个方格",
        "pixelize": {"block": 8, "colors": 16, "grid": 64},
        "pixelClass": True,
    },
    {
        "id": "pixel_hd",
        "name": "高清像素（HD Pixel）",
        "tags": ["角色", "场景"],
        "hint": "现代独立游戏像素风（Owlboy/Sea of Stars 感），大色块+选择性高光，出图后自动 4× 像素精修",
        "prompt": "高清像素艺术风格，大块饱满色块，选择性高光与轮廓光，细腻色阶但保持像素硬边，现代独立游戏美术",
        "pixelize": {"block": 4, "colors": 48, "grid": 128},
        "pixelClass": True,
    },
    {
        "id": "hd2d",
        "name": "HD-2D 幻想",
        "tags": ["场景", "角色"],
        "hint": "像素角色站在立体场景里：移轴微缩景深 + 泛光 + 体积光，日式 RPG 实机质感（不做像素化）",
        "prompt": (
            "HD-2D 风格，像素颗粒清晰的 2D 素材置于有纵深感的 3D 立体场景之中，"
            "移轴微缩景深：远景与近景虚化、主体所在焦平面保持清晰，"
            "泛光与体积光束，柔和暗角，高饱和明快的幻想配色，戏剧化明暗对比，"
            "日式幻想RPG实机画面质感"
        ),
        "pixelClass": False,
    },
    # ---------------- 2D 动漫/卡通 ----------------
    {
        "id": "chibi",
        "name": "日系 Q 版卡通",
        "tags": ["角色"],
        "hint": "3 头身萌系，粗黑描边平涂，游戏角色立绘常用",
        "prompt": "二维日系Q版卡通风格，3头身萌系比例，头部放大，四肢简化圆润，粗黑色外轮廓线，明亮饱和色彩，大面积平涂色块，硬边阴影",
        "pixelClass": False,
    },
    {
        "id": "anime_cel",
        "name": "日系动画赛璐璐",
        "tags": ["角色", "场景"],
        "hint": "TV 动画赛璐璐上色，干净线稿+两级阴影，Galgame/二次元手游立绘感",
        "prompt": "日系动画赛璐璐风格，干净流畅线稿，平涂上色，两级硬边阴影，高光点缀，动画原画质感",
        "pixelClass": False,
    },
    {
        "id": "manga_bw",
        "name": "日式黑白漫画",
        "tags": ["角色", "场景"],
        "hint": "黑白网点纸漫画风，速写线条+拟声字排版空间",
        "prompt": "日式黑白漫画风格，墨线勾勒，网点纸灰阶，高对比黑白，漫画分镜感，无彩色",
        "pixelClass": True,
        "pixelize": {"block": 8, "colors": 4, "grid": 64},
    },
    {
        "id": "us_cartoon",
        "name": "美式卡通",
        "tags": ["角色", "场景"],
        "hint": "Cartoon Network / 迪士尼 TV 动画风，粗轮廓+夸张动态",
        "prompt": "美式卡通动画风格，粗黑轮廓线，夸张动态造型，明亮色块，富有表现力的面部，电视动画质感",
        "pixelClass": False,
    },
    # ---------------- UI / 图标 ----------------
    {
        "id": "flat_vector",
        "name": "扁平矢量插画",
        "tags": ["UI", "场景", "道具"],
        "hint": "手游 UI/宣传图常用，无描边大色块，简洁现代",
        "prompt": "扁平矢量插画风格，无描边，简洁几何形，明快大色块，现代手游UI美术，干净留白",
        "pixelClass": False,
    },
    {
        "id": "outline_icon",
        "name": "粗描边游戏图标",
        "tags": ["UI", "道具"],
        "hint": "App 图标/物品图标风，粗描边+高饱和+居中构图，透明底友好",
        "prompt": "游戏图标风格，粗黑描边，高饱和配色，居中构图，简洁造型，图标级可读性，平涂",
        "pixelClass": True,
        "pixelize": {"block": 4, "colors": 16, "grid": 128},
    },
    # ---------------- 写实/3D ----------------
    {
        "id": "painterly",
        "name": "厚涂半写实",
        "tags": ["角色", "场景"],
        "hint": "数字厚涂，笔触可见，暗黑奇幻/ARPG 立绘感",
        "prompt": "半写实数字厚涂风格，可见笔触，丰富中间色调，戏剧化光影，暗黑奇幻游戏原画质感",
        "pixelClass": False,
    },
    {
        "id": "realistic",
        "name": "写实 3A 风",
        "tags": ["角色", "场景"],
        "hint": "3A 写实渲染方向，PBR 材质描述，概念设计稿",
        "prompt": "写实3A游戏概念设计，照片级材质细节，次表面散射皮肤，体积光，电影级构图，虚幻引擎渲染质感",
        "pixelClass": False,
    },
    {
        "id": "lowpoly",
        "name": "低多边形 3D",
        "tags": ["场景", "道具"],
        "hint": "低面数 3D，等距视角友好，独立游戏场景常用",
        "prompt": "低多边形3D风格，少面数几何体，平面着色，等距视角，柔和 pastel 配色，独立游戏场景",
        "pixelClass": False,
    },
    {
        "id": "voxel",
        "name": "体素风",
        "tags": ["角色", "场景"],
        "hint": "MagicaVoxel 立方体风（Crossy Road 感），等距场景出彩",
        "prompt": "体素艺术风格，均匀立方体单元，等距视角，明亮配色，3D像素方块构成，干净边缘",
        "pixelClass": False,
    },
    # ---------------- 手绘 ----------------
    {
        "id": "watercolor",
        "name": "水彩手绘",
        "tags": ["场景", "UI"],
        "hint": "透明水彩层叠，纸纹留白，绘本/治愈系游戏感",
        "prompt": "水彩手绘风格，透明颜料层叠，湿画法晕染，纸纹与留白，柔和边缘，绘本插画感",
        "pixelClass": False,
    },
    {
        "id": "concept_sketch",
        "name": "概念速写",
        "tags": ["角色", "场景"],
        "hint": "铅笔/炭笔速写，设计稿阶段用，线稿+灰调",
        "prompt": "概念设计速写风格，铅笔线稿，轻灰调，构图探索感，设计稿质感，未上色或局部上色",
        "pixelClass": False,
    },
]

_BY_ID: Dict[str, Dict[str, Any]] = {s["id"]: s for s in STYLES}

STYLE_GROUPS: List[str] = ["像素 · HD-2D", "动漫卡通", "写实 · 3D", "UI · 图标", "手绘"]
_STYLE_GROUP: Dict[str, str] = {
    "pixel_16bit": "像素 · HD-2D",
    "pixel_hd": "像素 · HD-2D",
    "hd2d": "像素 · HD-2D",
    "chibi": "动漫卡通",
    "anime_cel": "动漫卡通",
    "manga_bw": "动漫卡通",
    "us_cartoon": "动漫卡通",
    "painterly": "写实 · 3D",
    "realistic": "写实 · 3D",
    "lowpoly": "写实 · 3D",
    "voxel": "写实 · 3D",
    "flat_vector": "UI · 图标",
    "outline_icon": "UI · 图标",
    "watercolor": "手绘",
    "concept_sketch": "手绘",
}


def style_group(style_id: str) -> str:
    return _STYLE_GROUP.get(style_id, "其他")


def get_style(style_id: Optional[str]) -> Optional[Dict[str, Any]]:
    return _BY_ID.get(style_id) if style_id else None


def style_words(style_id: Optional[str]) -> str:
    style = get_style(style_id)
    return style["prompt"] if style else ""


def is_pixel_style(style_id: Optional[str]) -> bool:
    style = get_style(style_id)
    return bool(style and style.get("pixelClass"))


def list_styles() -> List[Dict[str, Any]]:
    """画风列表（前端 chips / 画廊用）。

    words：实际拼进提示词的风格词。放出来是为了让「选了画风」变成可验证的
    事实——前端直接展示这句话会被加在提示词最前面，而不是只亮一个 chip。
    """
    return [
        {
            "id": s["id"],
            "name": s["name"],
            "group": style_group(s["id"]),
            "tags": s["tags"],
            "hint": s["hint"],
            "words": s["prompt"],
            "pixelClass": bool(s.get("pixelClass")),
            "pixelize": s.get("pixelize"),
        }
        for s in STYLES
    ]


# --------------------------------------------------------------------------- #
# 像素提示词契约
# --------------------------------------------------------------------------- #
# 参考 perfectpixel-studio 的 pixel contract：把「什么算像素画」写成可执行
# 的规格，而不是一个风格形容词。实测对 Qwen-Image-2.1 尤其有效：
# 不加契约时模型倾向画「有像素质感的插画」（带抗锯齿、渐变、上百色）。
# 画布尺寸按目标网格反推：512 图配 64 格 → 每格 8px。
#
# 背景预设来自归档 PixelForge 的实测教训：**模型画不出纯色底**，绿幕会被
# 染进角色轮廓（绿皮角色更明显），所以默认给中性灰；绿幕留给愿意后处理
# 色键的人，选「无」则不对背景做任何约束（模型可能补上场景元素）。
#
# 「脚下不要阴影」这一条踩过两轮坑：
# 1. 早期写「角色悬空不落地」——与正视图姿势词「双脚踩地」直接打架，模型
#    折中成「站着但飘一点」，反而更容易补一圈接地阴影；
# 2. 只写「不要投影/不要椭圆投影」是纯否定清单，模型对全身站立的先验里
#    几乎必然带接触阴影（实测 4/4）；早期还踩过「负面词根本到不了模型」的
#    坑（distilled 底座 negative 恒无效），所以不能指望否定句兜底。
# 现在的写法是把结果说成**可验证的画面事实**：轮廓以外只有一个颜色，
# 不许有更暗/更亮的过渡。这样「背景」在提示词里成了二分结构（角色 + 单一
# 底色），阴影没有位置可长；否定句只留一句自然语言的兜底。
NO_GROUND_PHRASE = (
    "整幅图只有角色和这块纯色背景两部分：角色轮廓以外的所有像素都是同一个"
    "背景色，不能出现更暗或更亮的过渡区域——脚下没有地面，也没有角色投下的影子"
)
# 半透明主体（史莱姆/果冻/水晶）要留接触阴影才有贴地感，上面的「只有一个
# 颜色」会把主体压成漂浮的一块，所以换一套。
NO_GROUND_TRANSLUCENT = (
    "整幅图只有角色和这块纯色背景两部分，背景颜色均匀；主体底部允许 1-2 格"
    "接触阴影体现贴地，其余位置不出现地面线与环境元素"
)

BACKGROUND_PRESETS: List[Dict[str, Any]] = [
    {
        "id": "gray", "label": "中灰", "swatch": "#808080", "default": True,
        "phrase": "中性中灰背景（#808080）满铺，颜色均匀、无渐变、无纹理、无杂物；"
                  + NO_GROUND_PHRASE,
        "hint": "最稳：模型画不纯绿时不会把背景色染进角色，适合后续按语义抠图",
    },
    {
        "id": "green", "label": "绿幕", "swatch": "#00FF00",
        "phrase": "纯绿色背景（#00FF00）满铺，颜色均匀、无渐变、无纹理、无杂物；"
                  + NO_GROUND_PHRASE,
        "hint": "适合色键抠图；但绿色会渗进轮廓，绿皮角色请改用中灰",
    },
    {
        "id": "none", "label": "无", "swatch": "transparent",
        "phrase": "",
        "hint": "不指定背景：模型可能自行补场景元素，出图更「好看」但不好抠",
    },
    {
        "id": "transparent", "label": "透明", "swatch": "transparent",
        # Qwen-Image-2.1 原生 RGBA：VAE 解码器就是 4 通道，但必须用官方推荐的
        # 双段式声明（开头 "This is an RGBA image with transparency." + 结尾
        # "The image has alpha channel and the background is transparent."）
        # 才能激活 alpha 通道——只说「透明背景」不够，实测 alpha 会恒为 255
        # （.verify/exp-b/alpha-test.png：加声明后 68.5% 像素真正透明）。
        # 包装在 main._build_generation_prompt 里做，这里只写背景约束。
        # 透明底上阴影不是「颜色变暗」而是「一块 alpha=255 的深灰椭圆」，
        # 所以这里的约束是针对 alpha 的：轮廓以外必须全透明。
        "phrase": "背景完全透明：不绘制任何底色、渐变、纹理与场景元素；"
                  "整幅图只有角色本身，角色轮廓以外的所有像素都完全透明——"
                  "脚下没有地面，也没有角色投下的影子",
        "hint": "Qwen-Image-2.1 原生 RGBA：直接输出透明通道，省去后续抠图（旧底座不支持，效果等同「无」）",
    },
]

_BG_BY_ID = {b["id"]: b for b in BACKGROUND_PRESETS}


def background_phrase(bg_id: Optional[str]) -> str:
    if not bg_id:
        return ""
    return str((_BG_BY_ID.get(bg_id) or {}).get("phrase") or "")


# 半透明 / 软体材质关键词：命中后换 translucnet 契约（见 pixel_contract）。
# 这些主体的共同点是「通透感来自色阶而不是 alpha」，用默认的实心契约会被
# 压成一块带粗描边的色块。
TRANSLUCENT_WORDS = (
    "半透明", "透明", "史莱姆", "果冻", "凝胶", "水晶", "玻璃", "幽灵",
    "气泡", "水球", "水母", "能量体", "光体", "烟雾", "云朵", "冰块",
    "冰晶", "露珠", "液体", "黏液", "水滴", "宝石", "灵力", "发光体",
)


def detect_material(prompt: str) -> str:
    """从主体描述猜材质：translucent（半透明/软体）或 solid。

    「不透明」和「透明背景」里的「透明」不是材质，先排除，避免把实心角色
    误判成半透明主体（那样会漏掉「半透明」负面词，画面发雾）。
    """
    text = prompt or ""
    text = text.replace("不透明", "").replace("透明背景", "").replace("背景透明", "")
    return "translucent" if any(w in text for w in TRANSLUCENT_WORDS) else "solid"


def pixel_contract(
    grid: Optional[List[int]] = None,
    colors: int = 16,
    background: str = "",
    material: str = "solid",
    edit: bool = False,
) -> str:
    """像素契约。

    material="translucent" 时换一套规格：半透明主体（史莱姆/果冻/水晶/幽灵）
    如果照搬默认契约的「禁止半透明像素 + 1 像素深色轮廓 + 单级硬边阴影」，
    模型只能画成实心色块 + 粗描边，通透感全丢——这是实机踩到的坑。
    半透明在像素画里应该用**色阶**表达（外缘亮、内部深、顶部高光），
    而不是 alpha；轮廓也应同色系、1 格宽，而不是粗黑边。

    edit=True 用于参考图编辑（Qwen-Image-Edit）：编辑任务的第一目标是
    「保持参考图构图与角色位置」，不能再塞「居中/主体占 65%/无场景」这类
    重构图指令，否则模型会把参考图重新编排一遍，角色一致性直接丢掉。
    """
    g = grid or [64, 64]
    gw, gh = max(8, int(g[0])), max(8, int(g[1]))
    bg = (background or "").strip().rstrip("。")
    tail = f"{bg}。" if bg else ""
    # 透明背景下「禁止半透明像素」要收窄成「禁止渐变式半透明」：alpha 通道
    # 本身就是半透明机制，一刀切禁掉会让模型把透明底画成不透明纯色。
    # 像素画的透明必须是二进制硬边（全透明或全不透明）。
    no_trans = ("禁止渐变式半透明像素（alpha 通道仅允许全透明或全不透明，硬边）"
                if "透明" in bg else "禁止半透明像素")

    if edit:
        translucent = ("半透明材质用 2-3 级色阶表现（外缘亮、内部深、顶部高光），"
                       if material == "translucent" else "")
        return (
            f"保持参考图的构图、角色位置与体型比例不变，按 {gw}×{gh} 像素网格重绘："
            "每个网格单元只允许一种纯色，图案边界落在网格上但不要画出网格线本身；"
            f"全图不超过 {int(colors)} 种颜色，使用有限的共享调色板；"
            "禁止抗锯齿、平滑渐变、模糊与噪点纹理；"
            f"{translucent}硬边平涂，剪影清晰可读。{tail}"
        )

    if material == "translucent":
        # 半透明主体允许接触阴影：把背景句里「轮廓以外只有一个颜色」换成允许
        # 贴地阴影的版本，否则同一段提示词自相矛盾（实测会出成悬浮的一块）。
        bg_t = bg.replace(NO_GROUND_PHRASE, NO_GROUND_TRANSLUCENT) if bg else ""
        tail_t = f"{bg_t}。" if bg_t else ""
        return (
            f"严格像素画规格：画布精确划分为 {gw}×{gh} 的像素网格，"
            f"每个网格单元只允许一种纯色，图案边界落在网格上但不要画出网格线本身；"
            f"全图不超过 {int(colors)} 种颜色，使用有限的共享调色板；"
            "禁止抗锯齿、禁止模糊、禁止大面积渐变、禁止噪点纹理；"
            "半透明材质用 2-3 级色阶表现：外缘与顶部偏亮、内部偏深、"
            "高光呈通透的浅色块，不要用真正的半透明像素或模糊过渡；"
            "轮廓最多 1 格宽，用同色系更深一档的颜色，不要粗黑描边；"
            "允许主体底部 1-2 格接触阴影以体现贴地，背景其余部分保持纯净；"
            "主体占画面约 65%，居中构图，四周留安全边距，完整不裁切；正面平视；"
            "背景无地面、无环境元素、无文字；剪影清晰可读。"
            f"{tail_t}"
        )

    return (
        f"严格像素画规格：画布精确划分为 {gw}×{gh} 的像素网格，"
        f"每个网格单元只允许一种纯色，图案边界落在网格上但不要画出网格线本身；"
        f"全图不超过 {int(colors)} 种颜色，使用有限的共享调色板；"
        f"禁止抗锯齿、禁止渐变、禁止模糊、{no_trans}、禁止噪点纹理；"
        "深色连续外轮廓（1 像素宽），内部为平涂色块与单级硬边阴影；"
        "主体占画面约 65%，居中构图，四周留安全边距，完整不裁切；正面平视；"
        "背景无地面、无环境元素、无文字；剪影清晰可读。"
        f"{tail}"
    )


def build_prompt(
    prompt: str,
    style_id: Optional[str],
    *,
    pixel: bool = False,
    grid: Optional[List[int]] = None,
    colors: int = 16,
    background: str = "",
    material: str = "solid",
    edit: bool = False,
) -> Dict[str, Any]:
    """组装最终提示词：风格词前置 → 主体描述 → 背景约束 → 像素契约。

    幂等：同一段风格词已经在提示词里就不再重复拼（用户手动粘过），
    避免两遍同样的画风描述把提示词预算吃掉；背景句与契约同理。

    返回 {prompt, styleWords, contract, breakdown}：breakdown 给前端
    「预览生成提示词」面板分段展示，让用户看清每一段从哪来。
    """
    style = get_style(style_id)
    words = style["prompt"] if style else ""
    body = (prompt or "").strip()
    bg = (background or "").strip().rstrip("。")

    already_styled = bool(words) and body.startswith(words)
    head = "" if already_styled else words

    contract = ""
    if pixel and "严格像素画规格" not in body:
        contract = pixel_contract(grid=grid, colors=colors, background=bg,
                                  material=material, edit=edit)
    # 未开像素契约时，背景约束单独成句（仍要能控制底色，否则抠图无从谈起）
    bg_clause = ""
    if bg and not contract and bg not in body:
        bg_clause = bg

    parts = [p for p in (head, body, bg_clause, contract) if p]
    final = ("。".join(p.rstrip("。") for p in parts) + "。") if parts else ""
    return {
        "prompt": final,
        "styleWords": words,
        "contract": contract,
        "breakdown": [
            {"label": "画风（前置）",
             "text": ("（提示词已含画风词，未重复添加）" if already_styled
                      else (head or "（未选画风）"))},
            {"label": "主体描述", "text": body or "（空）"},
            {"label": "背景约束",
             "text": (bg or "（随契约未指定）") if contract else (bg_clause or "（未指定）")},
            {"label": "像素契约", "text": contract or "（未开启 / 已包含）"},
        ],
    }


def pixel_negative_extra(pixel: bool, material: str = "solid") -> str:
    """像素契约自带的那半负面词（与用户/设置里的基础负面词分开返回）。

    拆出来是为了让界面能说清「最终负面词里哪半是契约自动加的」——
    用户可以在生成卡上整段覆盖负面词，这时必须知道原本有什么被换掉了。
    """
    if not pixel:
        return ""
    if material == "translucent":
        # 半透明靠离散色阶表达：只否定「平滑渐变」，不否定「色阶」
        return "抗锯齿, 平滑渐变, 模糊, 噪点, 平滑过渡, 写实照片"
    return "抗锯齿, 渐变, 模糊, 噪点, 平滑过渡, 写实照片, 半透明"


def resolve_pixel_negative(base_negative: str, pixel: bool,
                           material: str = "solid") -> str:
    """像素模式下的负面词：把「像素画最怕的瑕疵」显式否定。

    「半透明」只在非半透明主体时否定：史莱姆/果冻/水晶这类主体如果把它挂进
    负面词，等于正面要求「半透明」、负面又禁止「半透明」，模型只能折中成
    实心色块——这是实机效果差的直接原因之一。
    """
    base = (base_negative or "").strip().strip("，, ")
    extra = pixel_negative_extra(pixel, material)
    if not extra:
        return base
    return f"{base}, {extra}" if base else extra
