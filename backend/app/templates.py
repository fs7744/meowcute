"""工作流模板定义：新建项目必须先选一个模板。

模板是「一条可执行的生产链」的说明书 + 默认参数集合，不是页面代码。
前端拿模板渲染介绍页与新建项目弹窗；画布按模板初始化节点。

当前可用：pixel_sprite_txt2img（像素精灵 · 文字生图）、
pixel_refine_pipeline（图片转像素）、matte_cutout（图片转透明底）。
后续模板（逐帧动作、三视图、场景元素）先在介绍页亮出规划，不假装备可用——
「摆一个点了没反应的入口」比没有更伤信任。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "pixel_refine_pipeline",
        "name": "像素精修 · 图片转像素",
        "tagline": "导入图片 → 网格对齐 + 调色板 → 结果卡",
        "status": "available",
        "accent": "#40E0D0",
        "icon": "sprite",
        "description": (
            "手上已经有图（插画 / AI 出图 / 截图）时最直接的链路："
            "「选择图片」→「像素精修」→「图片结果」。精修会自动检测网格、"
            "每格取中位色、共享调色板量化，输出原生像素尺寸。"
        ),
        "highlights": [
            "三步链路：选图 → 精修 → 结果，开箱即用",
            "网格自动检测，也可手动指定网格 / 输出尺寸",
            "背景可一键透明，直接得到可用素材",
        ],
        "inputs": [
            {"key": "image", "label": "图片", "required": True,
             "hint": "上传 1-4 张待像素化的图片"},
            {"key": "pixel", "label": "精修参数", "required": False,
             "hint": "色数 / 固定色板 / 网格 / 输出尺寸 / 背景透明"},
        ],
        "steps": [
            {"n": 1, "title": "选择图片", "detail": "上传要像素化的图片"},
            {"n": 2, "title": "像素精修", "detail": "网格对齐 + 调色板量化 + 去噪点"},
            {"n": 3, "title": "图片结果", "detail": "输出原生像素 PNG，可直接用于引擎"},
        ],
        "outputs": [
            {"label": "原生像素 PNG", "detail": "如 64×64，可无损用于引擎"},
            {"label": "精修指标", "detail": "网格尺寸、色数、色板外颜色数"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
            "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                      "palette": "auto", "gridSize": None, "targetSize": None,
                      "transparentBg": False},
        },
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 140, "refs": [],
                 "label": "待像素化图片"},
                {"id": "rf1", "type": "refine", "x": 520, "y": 120,
                 "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                           "palette": "auto", "gridSize": None, "targetSize": None,
                           "transparentBg": False},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 980, "y": 120, "images": [],
                 "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "rf1"},
                {"from": "rf1", "to": "rs1"},
            ],
        },
        "notes": [
            "排在模板列表第一位，所以新建项目时会预选它，不用额外去找。",
            "精修只做确定性后处理，不跑模型，所以速度很快。",
        ],
    },
    {
        "id": "matte_cutout",
        "name": "扣除背景 · 图片转透明底",
        "tagline": "导入图片 → 抠掉背景 → 结果卡",
        "status": "available",
        "accent": "#5B57EB",
        "icon": "cutout",
        "description": (
            "手上有一张图（AI 出图 / 插画 / 截图），只要脱背景拿素材时最直接的链路："
            "「选择图片」→「扣除背景」→「图片结果」。抠图走 u2net 语义分割，"
            "认的是「画面里哪个是主体」，所以灰色渐变底、有接地阴影的图也能抠干净。"
        ),
        "highlights": [
            "三步链路：选图 → 抠图 → 结果，开箱即用",
            "语义分割优先：灰底灰角色、软投影都不会留下来",
            "抠完可选「裁到主体」，直接得到贴边素材",
        ],
        "inputs": [
            {"key": "image", "label": "图片", "required": True,
             "hint": "上传 1-4 张要抠图的图片"},
            {"key": "matte", "label": "抠图参数", "required": False,
             "hint": "方法 / 容差 / 硬边 / 边缘去污 / 裁到主体"},
        ],
        "steps": [
            {"n": 1, "title": "选择图片", "detail": "上传要抠图的图片"},
            {"n": 2, "title": "扣除背景", "detail": "u2net 语义分割 → GrabCut → 颜色键，硬边 alpha + 边缘去污"},
            {"n": 3, "title": "图片结果", "detail": "透明底 PNG，可放大预览 / 右键另存"},
        ],
        "outputs": [
            {"label": "透明底 PNG", "detail": "棋盘格预览即透明区域，可直接用于引擎"},
            {"label": "抠图指标", "detail": "走的是哪条路径、保留比例、去污像素数"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
            "matte": {"enabled": True, "method": "auto", "tolerance": 80,
                      "hard": True, "feather": 0, "despill": True, "trim": False,
                      "edge": 0, "bottomTrim": 0},
        },
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 140, "refs": [],
                 "label": "待抠图图片"},
                {"id": "mt1", "type": "matte", "x": 520, "y": 140,
                 "matte": {"enabled": True, "method": "auto", "tolerance": 80,
                           "hard": True, "feather": 0, "despill": True, "trim": False,
                           "edge": 0, "bottomTrim": 0},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 940, "y": 140, "images": [],
                 "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "mt1"},
                {"from": "mt1", "to": "rs1"},
            ],
        },
        "notes": [
            "节点上的「使用此节点」开关关掉后，这条链路会原样把原图传给结果卡，"
            "方便你在「抠 / 不抠」之间切换对比。",
            "如果抠出来中间是空的：多半是背景被当成了主体，把方法改成「颜色键」"
            "并调低容差通常能救回来。",
        ],
    },
    {
        "id": "pixel_sprite_txt2img",
        "name": "像素精灵 · 文字生图",
        "tagline": "一句话 → 一张可达标的像素游戏素材",
        "status": "available",
        "accent": "#00FF88",
        "icon": "sprite",
        "description": (
            "用本地 Z-Image 底座把文字描述变成游戏素材，再用确定性管线把 AI 的"
            "「像素感」收成真像素：自动检测网格、每格取主色、限定调色板、"
            "alpha 二值化。支持多张参考图（自动路由到编辑底座）与 15 种游戏画风。"
        ),
        "highlights": [
            "提示词五段式：画风前置 + 主体描述 + 像素契约 + 负面清单 + 参数",
            "像素精修：FFT 网格检测 + Sobel 吸附 + 共享调色板，输出原生像素尺寸",
            "扣除背景：u2net 语义分割 → GrabCut → 颜色键，默认开启，出品即透明底素材",
            "参考图 1-4 张：自动切换 Qwen-Image-Edit 指令编辑，保持角色一致",
            "结果卡可放大预览，原图 / 精修一键对比",
        ],
        "inputs": [
            {"key": "prompt", "label": "文字描述", "required": True,
             "hint": "主体、服装、道具、动作；画风用下面的选择器，不要写进正文"},
            {"key": "examples", "label": "优秀示例", "required": False,
             "hint": "角色 / 场景 / 道具 / 动作 / 画风补充，点击即插入"},
            {"key": "refs", "label": "参考图（0-4 张）", "required": False,
             "hint": "选了参考图自动走编辑底座，保持角色外观一致"},
            {"key": "format", "label": "图片格式", "required": False,
             "hint": "输出宽高、生成张数、随机种子"},
            {"key": "style", "label": "游戏画风", "required": False,
             "hint": "15 种画风，像素类会自动建议精修参数"},
            {"key": "matte", "label": "扣背景", "required": False,
             "hint": "默认开启：生成结果先扣掉底再进结果卡；可在画布上关掉"},
        ],
        "steps": [
            {"n": 1, "title": "组装提示词", "detail": "画风词前置 → 主体 → 像素契约；纯字符串拼接，可预览"},
            {"n": 2, "title": "本地推理", "detail": "无参考图走 Z-Image（9 步）；有参考图自动走 Qwen-Image-Edit（40 步）"},
            {"n": 3, "title": "像素精修", "detail": "FFT 检测网格 → 网格线吸附 → 每格取中位色 → 共享色板量化 → alpha 二值化"},
            {"n": 4, "title": "扣除背景", "detail": "u2net 语义分割（退化到 GrabCut / 颜色键）+ 硬边 alpha + 边缘去污"},
            {"n": 5, "title": "落盘成卡", "detail": "结果以卡片落到无限画布，保留原图与精修两版，可放大对比"},
        ],
        "outputs": [
            {"label": "透明底像素 PNG", "detail": "默认扣掉背景，可直接拖进引擎"},
            {"label": "AI 原图", "detail": "精修前版本，用于对比与再精修"},
            {"label": "质量指标", "detail": "网格尺寸、色数、色板外颜色数、抠图方法与残留"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
            "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                      "palette": "auto", "gridSize": None, "targetSize": None},
        },
        # 内置节点图：生成 → 扣背景 → 结果。
        # 扣背景节点默认开启，所以「生成结果都要先扣」这件事在模板层面就成立，
        # 不需要用户每次手动加节点；不想要就右键…（节点上的开关）关掉它。
        "graph": {
            "nodes": [
                {"id": "gen1", "type": "generate", "x": 80, "y": 140, "prompt": "",
                 "styleId": "pixel_16bit", "refs": [],
                 "params": {"width": 512, "height": 512, "n": 1, "steps": None,
                            "seed": None, "background": "gray", "maxSeqLen": None},
                 "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                           "palette": "auto", "gridSize": None, "targetSize": None,
                           "transparentBg": False}},
                {"id": "mt1", "type": "matte", "x": 580, "y": 140,
                 "matte": {"enabled": True, "method": "auto", "tolerance": 80,
                           "hard": True, "feather": 0, "despill": True, "trim": False,
                           "edge": 0, "bottomTrim": 0},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 960, "y": 140, "images": [],
                 "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "gen1", "to": "mt1"},
                {"from": "mt1", "to": "rs1"},
            ],
        },
        "notes": [
            "像素风的关键不是「模型画得像」，而是「后处理收得回来」——"
            "本模板默认开启像素精修，关掉它会得到一张有像素质感的插画。",
            "生成结果默认先过「扣除背景」再进结果卡：AI 画的底再均匀也不纯，"
            "颜色键抠不干净，所以走的是语义分割（u2net）；没有权重时自动退化到"
            "GrabCut / 颜色键，节点上能看到当前走的是哪条路径。",
            "参考图会走编辑底座：指令以「保持角色外观不变」为主，",
            "适合「已有角色，换姿势 / 换道具 / 出不同素材」的场景。",
        ],
    },
    {
        "id": "frame_action_sheet",
        "name": "逐帧动作精灵表",
        "tagline": "角色锚点图 → N 帧动作 → 精灵表",
        "status": "planned",
        "accent": "#FF3366",
        "icon": "frames",
        "description": (
            "以一张角色锚点图为身份母版，逐帧生成动作（行走 / 攻击 / 跳跃），"
            "再抠图规整、拼成精灵表并导出引擎包。逐帧生成而非一次出多帧——"
            "模型没有「第 N 格画什么」的定位能力。"
        ),
        "highlights": [
            "身份由同一张参考图钉住，帧间不漂移",
            "共享缩放 + 底部对齐，播放不抖动",
            "循环接缝诊断（首尾帧差异 / 卡住帧）",
        ],
        "inputs": [], "steps": [], "outputs": [],
        "defaults": {},
        "notes": ["规划中：依赖动画帧一致性验证与精灵表合成管线。",
                  "行走类循环可先用已上线的「八方向行走」模板（视频底座链路）。"],
    },
    {
        "id": "char_three_view",
        "name": "角色三视图",
        "tagline": "正 / 侧 / 背三视图自动拼板",
        "status": "available",
        "accent": "#FFD500",
        "icon": "views",
        "description": (
            "正视图由「生成图」或「选择图片」产出；侧 / 背由编辑底座从正视推导，"
            "保证是同一个角色；最后按共同基线拼成一张横向三视图板。"
            "「三张、不重叠、同一角色」由结构保证：每张图只有一个角色的约束 + "
            "编辑底座的外观保持指令 + 拼板是确定性像素操作，不靠模型自觉。"
        ),
        "highlights": [
            "一张图里画三个视角在蒸馏模型上基本不可控，这里改成三张独立生成再拼板",
            "侧 / 背走指令接力：只转视角，造型 / 配色 / 画风零改动",
            "指令不点名任何具体服装与颜色——点名会变成「给角色加一件」的生成指令",
            "拼板前每张先抠底色 + 补封闭孔洞 + 洗边缘溢色，接缝看不到异色描边",
            "主体底部对齐共同基线、水平居中：模型出图高低不齐，对齐后才可用",
        ],
        "inputs": [
            {"key": "image", "label": "正视图", "required": True,
             "hint": "「生成图」节点写角色描述；或换「选择图片」上传已有的正视图"},
            {"key": "style", "label": "游戏画风", "required": False,
             "hint": "只作用于正视图；侧 / 背靠编辑指令继承画风"},
            {"key": "viewgen", "label": "视角生成", "required": False,
             "hint": "侧 / 背视图的尺寸、步数与种子；开关在节点上"},
            {"key": "threeview", "label": "拼板参数", "required": False,
             "hint": "底板色 / 间距 / 基线；三张图按正 → 侧 → 背排"},
        ],
        "steps": [
            {"n": 1, "title": "正视图", "detail": "生成图节点 txt2img 自由创作，或选择图片上传"},
            {"n": 2, "title": "生成侧视图", "detail": "编辑底座指令接力：转成侧面，外观不变"},
            {"n": 3, "title": "生成背面视图", "detail": "同链路转 180 度；脸部不可见，不添装备"},
            {"n": 4, "title": "三视图拼板", "detail": "逐张抠底 → 统一暗绿底板 → 基线 / 居中对齐"},
        ],
        "outputs": [
            {"label": "横向三视图板", "detail": "正 / 侧 / 背等宽并排，底部对齐同一基线"},
            {"label": "单视角图", "detail": "侧 / 背视图单独落盘，可单独取用"},
            {"label": "拼板指标", "detail": "每张的抠图方法、包围盒与对齐偏移"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 768,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：生成图 / 选择图片 → 侧视图 → 背视图 → 拼板。",
            "视角节点永远以「正视图」为参考——链条中间隔着别的视角节点也会自动找到最上游那张。",
            "拼板节点把整条链上的正 / 侧 / 背都收进来；缺哪张会在节点上报出来。",
        ],
        # 内置节点图：正视图 → 侧视图 → 背视图 → 拼板。
        # 线性链条就是用户描述的工作流；视角节点与拼板节点都会沿链回溯，
        # 所以侧 / 背都以正视图为参考，拼板能一次收齐三张。
        "graph": {
            "nodes": [
                {"id": "gen1", "type": "generate", "x": 80, "y": 220,
                 "prompt": "", "styleId": "pixel_16bit", "refs": [],
                 "params": {"width": 512, "height": 768, "n": 1, "steps": None,
                            "seed": None, "background": "gray", "maxSeqLen": None},
                 "pixel": {"enabled": False, "gridSnap": True, "colors": 16,
                           "palette": "auto", "gridSize": None, "targetSize": None,
                           "transparentBg": False}},
                {"id": "sd1", "type": "viewgen", "x": 620, "y": 60,
                 "viewgen": {"enabled": True, "view": "side", "styleId": None,
                             "steps": None, "seed": None,
                             "width": 512, "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "bk1", "type": "viewgen", "x": 620, "y": 520,
                 "viewgen": {"enabled": True, "view": "back", "styleId": None,
                             "steps": None, "seed": None,
                             "width": 512, "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "tv1", "type": "threeview", "x": 1180, "y": 290,
                 "threeview": {"enabled": True, "bg": "1B4332", "gap": 12,
                               "baseline": 0.93, "cell": None},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 1740, "y": 290,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "gen1", "to": "sd1"},
                {"from": "sd1", "to": "bk1"},
                {"from": "bk1", "to": "tv1"},
                {"from": "tv1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "dir_walk",
        "name": "八方向行走",
        "tagline": "一张角色图 → 三视图 → 八方向行走视频 → 精灵表 → 可操控预览",
        "status": "available",
        "accent": "#FF8A4C",
        "icon": "walk",
        "description": (
            "先出正 / 侧 / 背三视图，再以它们为锚点做八方向：正面、正左、背面三个"
            "方向直接取用三视图（零推导），左前 / 左后只从最近的锚点转 45 度，"
            "而不是一律从正视图硬转 90 / 180 度——旋转幅度越小，角色越不容易走形。"
            "每个方向再用 Wan2.2 图生视频底座生成原地行走视频，抽帧抠图后拼成"
            "每方向一张横排精灵表，最后变成能用方向键操控的行走播放器。"
            "为什么用视频底座而不是逐帧编辑：逐帧每帧独立推理，帧间没有运动"
            "连续性，行走循环会抖；视频底座一次生成整段动作，肢体路径天然连贯。"
        ),
        "highlights": [
            "三视图当锚点：三个方向零推导，斜向只转 45 度，推导次数由 4 降到 2",
            "八方向 = 左半边 5 个方向真实生成 + 右半边 3 个整段帧镜像，视频时间省一半",
            "每个真实方向的立绘图都会存下来，可放大查看，也可以自己指定一张替换",
            "方向指令只描述「转到哪个朝向」，不点名服装 / 配色——点名会变成「给角色加一件」",
            "Wan 提示词钉住「原地」：角色位置 / 大小 / 朝向始终不变，没有镜头运动",
            "序列帧等距取样 + 相邻去重：同姿势的帧放两颗，循环会卡一下",
            "共享缩放规整：抬腿的帧不会被放大，角色在整段循环里尺寸恒定",
            "拼板前透明区 RGB 清零 + 逐格自检：引擎双线性采样不会渲出色晕",
            "可操控预览：方向键 / WASD 转向，QEZC 斜向，空格暂停",
        ],
        "inputs": [
            {"key": "image", "label": "正视图", "required": True,
             "hint": "「生成图」节点写角色描述；或换「选择图片」上传已有的正视图"},
            {"key": "style", "label": "游戏画风", "required": False,
             "hint": "作用于正视图与各方向立绘；行走视频由 Wan 底座接管"},
            {"key": "walkvideo", "label": "行走视频", "required": False,
             "hint": "方向模式（八 / 四 / 单）、动作、Wan 帧数与帧率、种子"},
            {"key": "sequence", "label": "序列帧", "required": False,
             "hint": "每方向抽几帧、循环帧率、是否逐帧抠图"},
            {"key": "spritesheet", "label": "精灵整图", "required": False,
             "hint": "单元格边长、每行帧数、共享缩放与锚点"},
            {"key": "play", "label": "可操控预览", "required": False,
             "hint": "播放帧率与显示倍率"},
        ],
        "steps": [
            {"n": 1, "title": "正视图", "detail": "生成图节点 txt2img 自由创作，或选择图片上传"},
            {"n": 2, "title": "侧视图", "detail": "编辑底座从正视图接力：只转朝向，造型以参考图为准"},
            {"n": 3, "title": "背视图", "detail": "从侧视图继续接力；背面不出现脸，手持物跟随身体"},
            {"n": 4, "title": "行走视频", "detail": "以三视图为锚点：正/侧/背直接用，斜向转 45 度；再跑 Wan 原地行走"},
            {"n": 5, "title": "序列帧", "detail": "每方向等距抽帧 + 相邻去重 + 抠图 → 一小摞透明底帧"},
            {"n": 6, "title": "精灵整图", "detail": "共享缩放规整 → 每方向一张横排精灵表 + 八方向总览"},
            {"n": 7, "title": "可操控预览", "detail": "方向键 / 按钮切换八个方向播放行走循环"},
        ],
        "outputs": [
            {"label": "八方向精灵表", "detail": "每方向一张横排循环表 + 一张八方向总览"},
            {"label": "行走视频", "detail": "每方向一段动画 GIF，可直接播放或另存"},
            {"label": "序列帧", "detail": "每方向一小摞透明底 PNG，引擎可直接用"},
            {"label": "可操控预览", "detail": "画布上就能用键盘操控角色走向任意方向"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 768,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：生成图 / 选择图片 → 行走视频 → 序列帧 → 精灵整图 → 可操控预览。",
            "行走视频节点的参考永远是最上游那张正视图（和视角节点一样沿链往前找）。",
            "没有 Wan2.2 TI2V-5B 底座时跑不了第 2 步：节点会给出缺模型的明确提示。",
            "想先验证链路可以把方向模式调成「仅正面」：只跑一个方向，几分钟出结果。",
        ],
        # 内置节点图：正视图 → 行走视频 → 序列帧 → 精灵整图 → 可操控预览。
        # 直线链就是用户描述的工作流；每个节点只收上一步的产物，
        # 方向信息随图片上的 walkDir 与节点 meta 一起往下传。
        "graph": {
            "nodes": [
                {"id": "gen1", "type": "generate", "x": 80, "y": 240,
                 "prompt": "", "styleId": "pixel_16bit", "refs": [],
                 "params": {"width": 512, "height": 768, "n": 1, "steps": None,
                            "seed": None, "background": "gray", "maxSeqLen": None},
                 "pixel": {"enabled": False, "gridSnap": True, "colors": 16,
                           "palette": "auto", "gridSize": None, "targetSize": None,
                           "transparentBg": False}},
                {"id": "sd1", "type": "viewgen", "x": 620, "y": 180,
                 "viewgen": {"enabled": True, "view": "side", "styleId": None,
                             "steps": None, "seed": None, "width": 512,
                             "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "bk1", "type": "viewgen", "x": 1160, "y": 180,
                 "viewgen": {"enabled": True, "view": "back", "styleId": None,
                             "steps": None, "seed": None, "width": 512,
                             "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "wk1", "type": "walkvideo", "x": 1700, "y": 180,
                 "walkvideo": {"enabled": True, "dirMode": "eight", "action": "行走",
                               "styleId": None, "steps": None, "seed": None,
                               "width": 384, "height": 512, "numFrames": 81,
                               "fps": 24, "maxSide": 512, "maxSeqLen": None,
                               "pixelArt": False},
                 "result": None},
                {"id": "sq1", "type": "sequence", "x": 1160, "y": 180,
                 "sequence": {"enabled": True, "framesPerDir": 8, "fps": 8,
                              "matte": True, "tolerance": 40},
                 "result": None},
                {"id": "sh1", "type": "spritesheet", "x": 1700, "y": 180,
                 "spritesheet": {"enabled": True, "size": 128, "cols": 0,
                                 "sharedScale": True, "anchor": "bottom"},
                 "result": None},
                {"id": "pl1", "type": "play", "x": 2240, "y": 160,
                 "play": {"enabled": True, "fps": 8, "zoom": 2},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 2780, "y": 240,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "gen1", "to": "sd1"},
                {"from": "sd1", "to": "bk1"},
                {"from": "bk1", "to": "wk1"},
                {"from": "wk1", "to": "sq1"},
                {"from": "sq1", "to": "sh1"},
                {"from": "sh1", "to": "pl1"},
                {"from": "pl1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "scene_layers",
        "name": "场景语义分层",
        "tagline": "一张场景图 → 多层视差素材",
        "status": "planned",
        "accent": "#5B57EB",
        "icon": "layers",
        "description": (
            "把同一张场景图拆成背景 / 中景 / 前景图层（而非各生成一遍），"
            "天然坐标对齐，可直接做视差滚动。"
        ),
        "highlights": ["同图拆层无接缝", "视差工程导出"],
        "inputs": [], "steps": [], "outputs": [],
        "defaults": {},
        "notes": ["规划中：依赖 Qwen-Image-Layered 底座。"],
    },
]

_BY_ID: Dict[str, Dict[str, Any]] = {t["id"]: t for t in TEMPLATES}


def list_templates() -> List[Dict[str, Any]]:
    return TEMPLATES


def get_template(tid: str) -> Optional[Dict[str, Any]]:
    return _BY_ID.get(tid)


def available_templates() -> List[Dict[str, Any]]:
    return [t for t in TEMPLATES if t["status"] == "available"]
