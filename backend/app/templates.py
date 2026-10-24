"""工作流模板定义：新建项目必须先选一个模板。

模板是「一条可执行的生产链」的说明书 + 默认参数集合，不是页面代码。
前端拿模板渲染介绍页与新建项目弹窗；画布按模板初始化节点。

当前可用：pixel_sprite_txt2img（像素精灵 · 文字生图）、
pixel_refine_pipeline（图片转像素）、matte_cutout（图片转透明底）。
后续模板（逐帧动作、四视图、场景元素）先在介绍页亮出规划，不假装备可用——
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
            "用本地 Qwen-Image-2.1 底座把文字描述变成游戏素材，再用确定性管线把 AI 的"
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
            {"n": 2, "title": "本地推理", "detail": "同一个 Qwen-Image-2.1：无参考图文生图、带参考图指令改图（40 步）"},
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
        "name": "角色四视图",
        "tagline": "正 / 侧 / 背 / 右四视图自动拼板",
        "status": "available",
        "accent": "#FFD500",
        "icon": "views",
        "description": (
            "正视图由「生成图」或「选择图片」产出；侧 / 背 / 右由编辑底座从正视推导，"
            "保证是同一个角色；最后按共同基线拼成一张横向四视图板。"
            "「四张、不重叠、同一角色」由结构保证：每张图只有一个角色的约束 + "
            "编辑底座的外观保持指令 + 拼板是确定性像素操作，不靠模型自觉。"
        ),
        "highlights": [
            "一张图里画四个视角在蒸馏模型上基本不可控，这里改成四张独立生成再拼板",
            "侧 / 背 / 右走指令接力：只转视角，造型 / 配色 / 画风零改动",
            "指令不点名任何具体服装与颜色——点名会变成「给角色加一件」的生成指令",
            "抽卡成套：正视图 n 张 → 每个视角也 n 张，拼板可按第 k 组一键挑齐（同一套）",
            "拼板前每张先抠底色 + 补封闭孔洞 + 洗边缘溢色，接缝看不到异色描边",
            "主体底部对齐共同基线、水平居中：模型出图高低不齐，对齐后才可用",
        ],
        "inputs": [
            {"key": "image", "label": "角色图", "required": True,
             "hint": "「选择图片」上传一张角色立绘，或直接投入四视图拼板"
                     "（自动拆成正/侧/背/右四个锚点，一致性最好）"},
            {"key": "style", "label": "游戏画风", "required": False,
             "hint": "只作用于正视图；侧 / 背 / 右靠编辑指令继承画风"},
            {"key": "viewgen", "label": "视角生成", "required": False,
             "hint": "参数全部复用正视图（尺寸 / 步数 / 种子 / 背景），无需填写；"
                     "正视图抽卡几张就生成几张"},
            {"key": "threeview", "label": "拼板参数", "required": False,
             "hint": "底板色 / 间距 / 基线；每个视角可挑用哪张，也能按第 k 组一键对齐"
                     "（缺右视图按三视图拼板）"},
        ],
        "steps": [
            {"n": 1, "title": "正视图", "detail": "生成图节点 txt2img 自由创作，或选择图片上传"},
            {"n": 2, "title": "生成侧视图", "detail": "编辑底座指令接力：转成侧面，外观不变"},
            {"n": 3, "title": "生成背面视图", "detail": "同链路转 180 度；脸部不可见，不添装备"},
            {"n": 4, "title": "生成右视图", "detail": "同链路转向画面右侧，与侧视图镜像，补全四视图"},
            {"n": 5, "title": "四视图拼板", "detail": "逐张抠底 → 统一暗绿底板 → 基线 / 居中对齐"},
        ],
        "outputs": [
            {"label": "横向四视图板", "detail": "正 / 侧 / 背 / 右等宽并排，底部对齐同一基线"},
            {"label": "单视角图", "detail": "侧 / 背 / 右视图单独落盘，可单独取用"},
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
            "画布是一条直线：生成图 / 选择图片 → 侧视图 → 右视图 → 背视图 → 拼板。",
            "每个节点的运行按钮只跑自己那一步（点生成不会再拉着整条链一起跑）；想一次跑完全部，用画布顶部「运行工作流」。",
            "视角节点永远以「正视图」为参考——链条中间隔着别的视角节点也会自动找到最上游那张。",
            "侧 / 背 / 右复用正视图的画风、尺寸、步数、种子与背景，节点上不用再填一遍。",
            "拼板节点把整条链上的正 / 侧 / 背 / 右都收进来；缺哪张会在节点上报出来。",
            "背景不能选透明：拼板会抠底并统一底板色，与透明底互斥。",
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
                {"id": "sd1", "type": "viewgen", "x": 620, "y": 40,
                 "viewgen": {"enabled": True, "view": "side", "styleId": None,
                             "steps": None, "seed": None,
                             "width": 512, "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "rt1", "type": "viewgen", "x": 620, "y": 290,
                 "viewgen": {"enabled": True, "view": "side_r", "styleId": None,
                             "steps": None, "seed": None,
                             "width": 512, "height": 768, "maxSeqLen": None},
                 "result": None},
                {"id": "bk1", "type": "viewgen", "x": 620, "y": 540,
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
                {"from": "sd1", "to": "rt1"},
                {"from": "rt1", "to": "bk1"},
                {"from": "bk1", "to": "tv1"},
                {"from": "tv1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "txt_video",
        "name": "文本生视频",
        "tagline": "一句话 + 可选参考图 → 一段视频",
        "status": "available",
        "accent": "#5B57EB",
        "icon": "walk",
        "description": (
            "用本地视频底座把文字描述直接变成一段视频。"
            "参考图可选：不连就是纯文生视频（HunyuanVideo-1.5 的 T2V 管线，"
            "默认优先；也可切 Wan2.2 TI2V-5B——文/图统一权重），"
            "连了上游图就切图生视频——第一帧锚定你给的那张图，后面让它按提示词动起来。"
            "适合角色待机动画、场景氛围镜头、像素游戏过场的快速验证。"
        ),
        "highlights": [
            "纯文本也能跑：hy15 T2V 管线（默认）+ Wan2.2 文/图统一权重",
            "hy15 默认优先：一致性和细节更好（不闪烁、不变形），帧数上限 33",
            "Wan 可选：帧数可到 193、像素风严格保留，适合长循环",
            "参考图可选：连上就走图生视频（首帧锚定），不连就是纯文本",
            "帧数 4k+1 自动对齐：时序 VAE 的硬约束，不用用户操心",
            "像素保真开关：像素素材用最近邻缩放，不被视频底座糊掉",
            "引导强度可调：动作偏弱时上调加强运动幅度",
        ],
        "inputs": [
            {"key": "prompt", "label": "提示词", "required": True,
             "hint": "画面内容 + 动作描述，整段自己写；程序不拼接（长文本截尾会丢条款）"},
            {"key": "ref", "label": "参考图（可选）", "required": False,
             "hint": "连「选择图片」或「生成图」：第一帧锚定这张图；不连则纯文本生成"},
            {"key": "format", "label": "视频格式", "required": False,
             "hint": "宽高、帧数、帧率、种子、引导强度；帧数内部对齐 4k+1"},
            {"key": "engine", "label": "视频引擎", "required": False,
             "hint": "自动（默认 HunyuanVideo-1.5，权重不全回退 Wan）/ 二选一"},
        ],
        "steps": [
            {"n": 1, "title": "写提示词", "detail": "整段用户输入，程序不拼接；负面词可单独覆盖"},
            {"n": 2, "title": "选管线", "detail": "有参考图 → 图生视频（首帧 latent 锚定）；无 → 纯文生视频"},
            {"n": 3, "title": "本地推理", "detail": "hy15：12 步无 CFG（约 1 分钟/21 帧）；Wan：30 步 / CFG 5.0"},
            {"n": 4, "title": "落盘成卡", "detail": "帧序列存成动画 GIF，结果卡可直接预览播放"},
        ],
        "outputs": [
            {"label": "视频 GIF", "detail": "整段帧序列，浏览器原生可播"},
            {"label": "质量指标", "detail": "帧数 / 步数 / 峰值显存 / 纯文本还是图生视频"},
        ],
        "defaults": {
            "width": 832, "height": 480, "numFrames": 81, "fps": 24,
        },
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 140, "refs": [],
                 "label": "参考图（可选）"},
                {"id": "tx1", "type": "txtvideo", "x": 520, "y": 120,
                 "txtvideo": {"enabled": True, "prompt": "", "negative": "",
                              "steps": None, "seed": None,
                              "width": 848, "height": 480,
                              "numFrames": 21, "fps": 12, "maxSide": 848,
                              "maxSeqLen": None, "pixelArt": False,
                              "guidanceScale": None,
                              "quality": "standard", "resolution": "480p",
                              "interpolate": False, "upscale": False,
                              "format": "mp4",
                              "engine": None},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 1020, "y": 140,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "tx1"},
                {"from": "tx1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "dir_walk",
        "name": "八方向行走",
        "tagline": "一张角色图 → 八方向行走视频 → 精灵表 → 可操控预览",
        "status": "available",
        "accent": "#FF8A4C",
        "icon": "walk",
        "description": (
            "入口是「选择图片」：手上已经有角色立绘（自己画的、别的工具出的）就直接"
            "传进来，不用先生成。每个方向由底座从这张图推导朝向，再用 Wan2.2 图生视频"
            "生成原地行走，抽帧抠图后拼成每方向一张横排精灵表，最后变成能用方向键"
            "操控的行走播放器。"
            "只有一张参考图时，正 / 侧 / 背 / 右都要各自转一次，背面转 180 度最容易"
            "走形——对一致性要求高的话，用「角色四视图」模板先生成四视图，再把那四张"
            "图接到这条链上（行走节点会沿链回溯收锚点），四个方向就变成零推导、"
            "斜向只转 45 度。"
            "为什么用视频底座而不是逐帧编辑：逐帧每帧独立推理，帧间没有运动连续性，"
            "行走循环会抖；视频底座一次生成整段动作，肢体路径天然连贯。"
        ),
        "highlights": [
            "入口是「选择图片」：现成的角色立绘直接进来跑，不用先生成图",
            "单图也能出八方向：每个方向各自转朝向；要更稳就把四视图模板生成的图接到这条链上",
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
            {"key": "image", "label": "角色图", "required": True,
             "hint": "「选择图片」上传一张角色立绘；要四视图锚点就用「角色四视图」模板"},
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
            {"n": 1, "title": "选择图片", "detail": "上传一张角色立绘，或投入「角色四视图」模板产出的拼板（自动拆成 4 个锚点）"},
            {"n": 2, "title": "行走视频", "detail": "每个方向推导立绘 + Wan 原地行走；八方向里镜像省一半视频"},
            {"n": 3, "title": "序列帧", "detail": "每方向等距抽帧 + 相邻去重 + 抠图 → 一小摞透明底帧"},
            {"n": 4, "title": "精灵整图", "detail": "共享缩放规整 → 每方向一张横排精灵表 + 八方向总览"},
            {"n": 5, "title": "可操控预览", "detail": "方向键 / 按钮切换八个方向播放行走循环"},
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
            "画布是一条直线：选择图片 → 行走视频 → 序列帧 → 精灵整图 → 可操控预览。",
            "行走视频节点的参考就是「选择图片」里那张图，所有方向都从它推导。",
            "只有一张图时背面要转 180 度，最容易走形；对一致性要求高就用「角色四视图」模板先生成四视图，再把行走节点接在拼板后面（它会沿链回溯收四张锚点）。",
            "没有 Wan2.2 TI2V-5B 底座时跑不了第 2 步：节点会给出缺模型的明确提示。",
            "想先验证链路可以把方向模式调成「仅正面」：只跑一个方向，几分钟出结果。",
        ],
        # 内置节点图：选择图片 → 行走视频 → 序列帧 → 精灵整图 → 可操控预览。
        # 入口刻意不是「生成图 + 三个视角节点」：手上已有立绘的人不必先花三次
        # 推导生四视图；只有一张图时行走节点会退回单图推导，四视图锚点这条
        # 更强的路径留给 char_three_view 模板（把拼板接到行走节点前面即可）。
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 240, "refs": [],
                 "label": "角色图"},
                {"id": "wk1", "type": "walkvideo", "x": 620, "y": 240,
                 "walkvideo": {"enabled": True, "dirMode": "eight", "action": "行走",
                               "engine": "hy15",
                               "styleId": None, "steps": None, "seed": None,
                               "width": 384, "height": 512, "numFrames": 21,
                               "fps": 12, "maxSide": 512, "maxSeqLen": None,
                               "pixelArt": False},
                 "result": None},
                {"id": "sq1", "type": "sequence", "x": 1160, "y": 240,
                 "sequence": {"enabled": True, "framesPerDir": 8, "fps": 8,
                              "matte": True, "tolerance": 40},
                 "result": None},
                {"id": "sh1", "type": "spritesheet", "x": 1700, "y": 240,
                 "spritesheet": {"enabled": True, "size": 128, "cols": 0,
                                 "sharedScale": True, "anchor": "bottom"},
                 "result": None},
                {"id": "pl1", "type": "play", "x": 2240, "y": 240,
                 "play": {"enabled": True, "fps": 8, "zoom": 2},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 2780, "y": 240,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "wk1"},
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
