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
        "id": "image_inpaint",
        "name": "局部重绘 · 涂抹改局部",
        "tagline": "选图 → 涂出要改的地方 → 一句话 → 只重画这一块",
        "status": "available",
        "accent": "#E86AF0",
        "icon": "brush",
        "description": (
            "手上有一张差不多的图，只差某一块（表情、衣服、道具、破洞、招牌文字）："
            "不用重画整张，也不用会画图。「选择图片」→「局部重绘」用笔刷把要改的地方"
            "涂出来 → 写一句「这块要变成什么」（可选再挂 1-2 张参考图借造型/材质）"
            "→ 只重画涂到的部分，区域外的像素一个都不动。"
            "为什么能做到「外面不动」：模型只负责画，贴回由确定性代码做——羽化向内、"
            "接缝带做色彩校正，所以涂与不涂的边界看不出来，改动范围也完全可控。"
        ),
        "highlights": [
            "笔刷涂抹选区：大小可调、橡皮擦、矩形选区、撤销重做、清空/反选",
            "只重画涂到的地方：区域外像素逐位不变，涂抹边界向内羽化",
            "两种重绘范围：局部（只送选区+上下文，细节更好）/ 整图（改全局光照、氛围）",
            "可选参考图 1-2 张：只借造型 / 材质 / 配色，不照抄构图",
            "接缝带色彩校正：只修边界，不改区域中心（要改红就真的是红）",
            "像素素材友好：硬边模式 + 色板吸附，重绘区不冒出新的杂色",
            "提示词零隐藏：约束条款与负面词都显示在卡上，可整段改",
        ],
        "inputs": [
            {"key": "image", "label": "原图", "required": True,
             "hint": "上传一张待改的图，或直接连「生成图 / 像素精修 / 扣除背景」的产出"},
            {"key": "mask", "label": "涂抹区域", "required": True,
             "hint": "点「涂抹区域」打开笔刷编辑器：涂过的地方会被重画"},
            {"key": "prompt", "label": "提示词", "required": True,
             "hint": "这块要变成什么；写具体内容（材质 / 颜色 / 细节），不用重复描述整张图"},
            {"key": "refs", "label": "参考图（0-2 张）", "required": False,
             "hint": "可选：借造型 / 材质 / 配色，程序会追加「不要照抄构图」的声明"},
            {"key": "params", "label": "贴回参数", "required": False,
             "hint": "重绘范围 / 上下文 / 羽化 / 外扩 / 硬边 / 色彩校正 / 色板吸附"},
        ],
        "steps": [
            {"n": 1, "title": "选择图片", "detail": "上传要改的图（也接受上游节点的产出）"},
            {"n": 2, "title": "涂抹区域", "detail": "笔刷圈出要重画的地方，可擦可撤销"},
            {"n": 3, "title": "写提示词", "detail": "这块要变成什么；可选挂参考图"},
            {"n": 4, "title": "局部重绘", "detail": "模型重画 → 羽化向内贴回 → 接缝色彩校正"},
            {"n": 5, "title": "图片结果", "detail": "原图 / 重绘一键对比，可放大与下载"},
        ],
        "outputs": [
            {"label": "重绘后的 PNG", "detail": "尺寸与原图一致，只有涂到的区域变了"},
            {"label": "重绘指标", "detail": "选区覆盖率、实际送模型的尺寸、色板吸附比例"},
            {"label": "最终提示词", "detail": "含程序追加的约束条款与参考图声明，可复制"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：选择图片 → 局部重绘 → 图片结果。上游也可以接「生成图 / "
            "像素精修 / 扣除背景」，本节点只要求一张同尺寸的源图。",
            "涂抹范围要略微包住要改的东西：模型在边界处需要参照周围的颜色与线条，"
            "贴着边缘涂死反而更容易出现生硬的接缝。",
            "改颜色时把「接缝色彩校正」调低或关掉：它的作用是把新内容拉回周围的色调，"
            "调得太用力会把你要的红拉成原来的蓝。",
            "像素素材建议：硬边 + 色板吸附（源图），重绘区就不会冒出画面里没有的颜色。",
            "换源图之后一定要重新涂抹：蒙版是和源图同尺寸存的，换图后旧蒙版直接报错。",
        ],
        # 内置节点图：选择图片 → 局部重绘 → 图片结果。
        # 与其它模板不同，这个节点的核心输入不是参数而是**手绘蒙版**，
        # 所以模板只给出结构，蒙版必须用户自己涂（没有合理默认值）。
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 200, "refs": [],
                 "label": "待改的图片"},
                {"id": "ip1", "type": "inpaint", "x": 520, "y": 160,
                 "inpaint": {"enabled": True, "instruction": "",
                             "constraint": "", "negative": "", "refs": [],
                             "mode": "crop", "context": 0.35, "feather": 4,
                             "expand": 8, "hard": False, "matchColor": True,
                             "matchStrength": 0.45, "palette": "none",
                             "paletteColors": 48, "steps": None, "seed": None,
                             "maxSeqLen": None, "maskFile": "", "mask": None},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 1020, "y": 200,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "ip1"},
                {"from": "ip1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "object_erase",
        "name": "去除局部 · 擦水印/杂物",
        "tagline": "选图 → 涂掉要去掉的东西 → 用周围内容补上",
        "status": "available",
        "accent": "#4FB6FF",
        "icon": "brush",
        "description": (
            "图上有不想留的东西——水印、字幕、招牌文字、电线、路人、logo："
            "「选择图片」→「去除局部」用笔刷把它涂掉 → 选择补的方式 → 涂到的地方被"
            "周围内容补上。"
            "默认走**非 AI** 的三条路线（扩散修复 / 纹理平移 / 镜像），毫秒级、不占显存，"
            "小面积改动（水印、划痕、小杂物）本来就只需要参考周围区域；"
            "周围没有可借的内容时（去掉人物、大物体）再切「AI 补全」，走本地模型重画。"
        ),
        "highlights": [
            "四种补法，按场景选：扩散修复（平滑背景）/ 纹理平移（重复纹理）/ 镜像（对称）/ AI 补全",
            "非 AI 三条路线毫秒级、不占显存：小改动不必等模型",
            "蒙版自动外扩 6px：水印边缘的抗锯齿比笔画宽，不外扩会留一圈残影",
            "选区外像素逐位不变（外扩范围之外），改动范围完全可控",
            "接缝色彩匹配 + 可选色板吸附：补完的区域不会比周围更亮或更花",
            "面积过大或找不到重复纹理时会提示改用 AI，而不是硬给一张涂抹感的图",
            "AI 补全是两段式（先补平再收尾），不会把「一条白条」画成一整块暗方块",
        ],
        "inputs": [
            {"key": "image", "label": "原图", "required": True,
             "hint": "上传一张待处理的图，或连「生成图 / 像素精修 / 扣除背景」的产出"},
            {"key": "mask", "label": "涂抹区域", "required": True,
             "hint": "笔刷涂掉水印/杂物；略微涂大一点，把半透明的边缘也盖住"},
            {"key": "method", "label": "补法", "required": False,
             "hint": "扩散修复（默认）/ 纹理平移 / 镜像 / AI 补全，卡片上有适用场景"},
        ],
        "steps": [
            {"n": 1, "title": "选择图片", "detail": "上传要去除东西的图（也接受上游节点的产出）"},
            {"n": 2, "title": "涂抹", "detail": "笔刷涂掉水印/杂物，可擦可撤销，蒙版色可换"},
            {"n": 3, "title": "选补法", "detail": "平滑底用扩散、重复纹理用平移、对称用镜像、大物体用 AI"},
            {"n": 4, "title": "补上并贴回", "detail": "填充 → 羽化向内贴回 → 接缝色彩匹配"},
            {"n": 5, "title": "图片结果", "detail": "原图 / 处理后一键对比，可放大与下载"},
        ],
        "outputs": [
            {"label": "处理后的 PNG", "detail": "尺寸与原图一致，只有涂到的区域变了"},
            {"label": "处理指标", "detail": "用的哪种补法、走了多少像素、匹配到的平移量"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：选择图片 → 去除局部 → 图片结果。上游也可以是「生成图 / 像素精修 / 扣除背景」。",
            "涂的时候**略微涂大一圈**：水印、logo 的边缘有半透明抗锯齿，只涂实心部分会留一圈淡影。",
            "纯色或渐变背景（海报、截图、UI 素材）直接用「扩散修复」，几乎看不出改过。",
            "背景有重复纹理（砖墙、木纹、金属板、条纹）时选「纹理平移」，比扩散更像原图。",
            "去掉人物、大物体这类「周围没有可借的内容」，才需要「AI 补全」（几十秒）。"
            "AI 那条是两段式：先把区域补平，再让模型把这块的质感收成和周围一致——"
            "直接让模型「擦掉一条白条」会得到一整块暗方块（实测），所以不让它去擦，只让它收尾。",
            "像素素材建议开「硬边 + 色板吸附」，补出来的区域不会冒出画面里没有的颜色。",
        ],
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 200, "refs": [],
                 "label": "要去除东西的图片"},
                {"id": "er1", "type": "erase", "x": 520, "y": 160,
                 "erase": {"enabled": True, "method": "telea", "expand": 6,
                           "feather": 2, "hard": False, "matchColor": True,
                           "matchStrength": 0.6, "palette": "none",
                           "paletteColors": 48, "instruction": "",
                           "constraint": "", "negative": "", "aiMode": "crop",
                           "context": 0.35, "steps": None, "seed": None,
                           "maxSeqLen": None, "maskFile": "", "mask": None},
                 "result": None},
                {"id": "rs1", "type": "result", "x": 1020, "y": 200,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "er1"},
                {"from": "er1", "to": "rs1"},
            ],
        },
    },
    {
        "id": "layer25d_model",
        "name": "2.5D 分层模型 · See-Through",
        "tagline": "静态动漫图 → 分层 PSD → 会动的 2.5D 角色",
        "status": "available",
        "accent": "#B98BFF",
        "icon": "layers",
        "description": (
            "手上有一张画好的动漫立绘，想让它动起来。专业工作流里这一步叫"
            "「分层」：把角色拆成脸、头发、上衣、左右手……十几个图层，每层补全"
            "被挡住的部分，排好前后顺序，再交给 Live2D / 引擎去驱动——一个角色"
            "手工做要几小时。\n"
            "这条链用 **See-Through**（MIT 系实验室的开源方案）把这件事自动化："
            "「选择图片」→ 抠掉背景 →「2.5D 分层」跑模型拆层 → 交付一个**分层 "
            "PSD**（每层都是补全过的完整图形，层序 = 深度顺序），外加每层的"
            "透明 PNG 与深度数据。\n"
            "**慢是真慢**：一次 2~3 分钟（要跑 LayerDiff 分层 + Marigold 深度），"
            "但它是确定性流程——同样的图 + 同样的种子，出来的是同一份分层。"
        ),
        "highlights": [
            "分层 PSD：Live2D 语义层（face / topwear / handwear-l / hair…），"
            "层序按单目深度排好，丢进 PS 或 Live2D 能直接用",
            "每层都补全了被遮挡的区域：层一分开摆动不会露出白洞"
            "（这是人工分层最费时的部分）",
            "深度数据一起给：每层的 depth_median 就是它的前后位置",
            "左右拆分：眼睛/耳朵/手饰/头发拆成 l / r 两层，做眨眼和侧摆的前提",
            "附每层透明 PNG + 深度 PSD：想自己再加工就直接拿走",
            "画布里能实时预览 2.5D 效果：拖动滑杆看各层怎么错位、呼吸",
        ],
        "inputs": [
            {"key": "image", "label": "角色立绘", "required": True,
             "hint": "单人、正面或 3/4 侧、纯色或简单背景的动漫立绘效果最好"},
            {"key": "layer25d", "label": "分层参数", "required": False,
             "hint": "分辨率（512~2048）/ 步数 / 种子 / 左右拆分"},
        ],
        "steps": [
            {"n": 1, "title": "选择图片", "detail": "上传角色立绘（也可连「生成图」的产出）"},
            {"n": 2, "title": "分层", "detail": "See-Through 跑图层分解 + 深度估计（2~3 分钟）"},
            {"n": 3, "title": "看层", "detail": "层列表 + 2.5D 实时预览，确认前后顺序和错位"},
            {"n": 4, "title": "下载", "detail": "分层 PSD / 深度 PSD / 每层 PNG / manifest"},
        ],
        "outputs": [
            {"label": "分层 PSD", "detail": "可直接在 Photoshop / Live2D 里接着绑骨"},
            {"label": "每层透明 PNG", "detail": "按层名分文件，附带画布坐标"},
            {"label": "深度与顺序 JSON", "detail": "每层的 depth_median 与绘制顺序"},
            {"label": "深度 PSD", "detail": "灰度深度图，做 3D / 视差用"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：选择图片 → 2.5D 分层 → 图片结果。",
            "**输入图越干净越好**：纯色底、单个角色、轮廓清楚。背景复杂会带进层里。",
            "分辨率 1024 是质量与速度的折中；官方推荐 1280，但要跑更久。",
            "同一张图 + 同一个种子 = 同一份分层，可以放心重跑对比。",
            "分层只解决「拆开」，不解决「动起来」：绑骨 / 摆动参数是下一步的事，"
            "画布里的预览只按深度做视差与呼吸，用来确认分层是否合理。",
        ],
        "graph": {
            "nodes": [
                {"id": "in1", "type": "input", "x": 80, "y": 220, "refs": [],
                 "label": "角色立绘"},
                {"id": "l2d", "type": "layer25d", "x": 520, "y": 180,
                 "layer25d": {"enabled": True, "resolution": 1024, "steps": 30,
                              "seed": 42, "lrsplit": True},
                 "result": None, "images": [], "meta": None},
                {"id": "rs1", "type": "result", "x": 1000, "y": 220,
                 "images": [], "prompt": "", "negative": "", "meta": {}},
            ],
            "edges": [
                {"from": "in1", "to": "l2d"},
                {"from": "l2d", "to": "rs1"},
            ],
        },
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
                              "width": 832, "height": 480,
                              "numFrames": 21, "fps": 12, "maxSide": 832,
                              "maxSeqLen": None, "pixelArt": False,
                              "guidanceScale": None,
                              "quality": "standard", "resolution": "480p",
                              "interpolate": False, "upscale": False,
                              "format": "mp4",
                              "engine": None, "fitAspect": None},
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
        "id": "clip_to_sprite",
        "name": "视频转素材 · 序列帧精灵图",
        "tagline": "视频 / GIF → 拆帧挑帧 → 精灵图 → 动画预览 → Godot 4 工程包",
        "status": "available",
        "accent": "#5AD2A0",
        "icon": "sprite",
        "description": (
            "手上有一段动作视频或 GIF（AI 生成的、录屏的、别处下载的），想把它变成"
            "游戏里能用的动画素材：这条链就是干这个的。"
            "「选择视频」把片段导进来（可先播一遍确认）→「拆解帧」把它拆成一摞帧，"
            "你可以用「跳帧步长」和「近似度过滤」两把筛子快速去重，再逐张勾选真正"
            "想要的那几帧 →「精灵图」把选中的帧按网格拼成一张（附带引擎索引 JSON）"
            "→「动画预览」按你设的帧率循环播放，看动作顺不顺 →「Godot 导出」"
            "把精灵图变成能直接双击运行的 AnimatedSprite2D 场景（.tres + .tscn + zip）。"
            "为什么不让程序自动抽 8 帧就完事：动作素材的取舍是审美决定——哪几个"
            "姿势构成一个完整循环，只有你自己看得到。所以这里的筛子是「帮你把 200 帧"
            "压到 20 帧」，最后一刀留给你。"
        ),
        "highlights": [
            "任何视频 / GIF 都能进：mp4 / mov / webm / gif，最长边自动压到设定值",
            "先播再拆：源片段在原位播放，确认对不对再动手",
            "两道筛子：跳帧步长（每 N 帧取 1）+ 近似度过滤（和上一张太像就丢掉）",
            "筛完可以逐张勾选 / 反选 / 只留筛选结果，选哪些帧完全由你定",
            "近似度在浏览器里实时计算（每帧带 16×16 指纹），拖滑杆即出结果",
            "抠图可选：默认 u2net 去背景，直接得到透明底素材",
            "共享缩放：抬腿的帧不会被放大，整段循环里角色尺寸恒定",
            "索引 JSON 双格式：自带帧坐标表 + Phaser 3 (JSON Array) 兼容段",
            "Godot 4 一键出包：SpriteFrames(.tres) + AnimatedSprite2D 场景(.tscn)",
            "动画预览可调帧率与倍率，逐帧步进看接缝",
        ],
        "inputs": [
            {"key": "clip", "label": "视频 / GIF", "required": True,
             "hint": "上传一段视频或 GIF，或从项目里已有的片段中挑"},
            {"key": "framesplit", "label": "拆帧与挑帧", "required": False,
             "hint": "帧率上限 / 最大帧数 / 起止时间 / 抠图 / 跳帧步长 / 近似度阈值"},
            {"key": "spritegrid", "label": "精灵图", "required": False,
             "hint": "单元格边长 / 每行列数 / 帧间距 / 外边距 / 锚点 / 共享缩放"},
            {"key": "animpreview", "label": "动画预览", "required": False,
             "hint": "播放帧率 / 显示倍率 / 循环 / 底色"},
            {"key": "godot2d", "label": "Godot 导出", "required": False,
             "hint": "动画名 / 文件名 / 图集或逐帧 / res:// 目录 / 原点 / 纹理过滤"},
        ],
        "steps": [
            {"n": 1, "title": "选择视频", "detail": "导入视频或 GIF，先点播放确认片段对不对"},
            {"n": 2, "title": "拆解帧", "detail": "拆成帧后按相似度与跳帧步长筛，再逐张勾选"},
            {"n": 3, "title": "精灵图", "detail": "选中帧按网格拼图 + 导出引擎索引 JSON"},
            {"n": 4, "title": "动画预览", "detail": "按设定帧率循环播放，逐帧检查接缝"},
            {"n": 5, "title": "Godot 导出", "detail": "生成 SpriteFrames + AnimatedSprite2D 场景，打包成 zip"},
        ],
        "outputs": [
            {"label": "精灵图 PNG", "detail": "透明底、网格对齐，直接进引擎"},
            {"label": "Godot 4 工程包", "detail": ".tres + .tscn + zip：解到工程根目录，双击场景就能播"},
            {"label": "索引 JSON", "detail": "帧坐标 + pivot，Phaser / Unity 都能吃"},
            {"label": "透明底序列帧", "detail": "每一帧单独成文件，不想用图集时可以直接用"},
            {"label": "动画预览", "detail": "画布上直接循环播放，验证循环是否闭合"},
        ],
        "defaults": {
            "styleId": "pixel_16bit",
            "width": 512,
            "height": 512,
            "n": 1,
            "background": "gray",
            "maxSeqLen": None,
        },
        "notes": [
            "画布是一条直线：选择视频 → 拆解帧 → 精灵图 → 动画预览 → Godot 导出。",
            "拆帧是任务制的（抠图较重），带进度与取消；精灵图与 Godot 导出是同步的，点完立刻出。",
            "近似度阈值默认 0（不过滤）。拆完之后节点会给出「本段素材的帧间差异中位数」，照着它调最省事。",
            "循环动画记得首尾不要选重复姿势：末帧和首帧太像，播起来会卡一下。",
            "Godot 导出默认文件落在 res://sprites/ —— 解压时把这个文件夹放到工程根目录，"
            "节点上会显示确切的 res:// 路径，路径对不上 Godot 会报资源缺失。",
            "这条链刻意不放「图片结果」卡：精灵图和动画预览本身就是看得见的产出，"
            "多摆一张空卡只会让人以为漏了东西。",
        ],
        # 内置节点图：选择视频 → 拆解帧 → 精灵图 → 动画预览 → Godot 导出。
        # 入口刻意是「选择视频」而不是「选择图片」：这条链面对的是已经拍好/
        # 生成好的视频，不涉及任何模型推理。
        "graph": {
            "nodes": [
                {"id": "in1", "type": "clipsrc", "x": 80, "y": 240,
                 "clipsrc": {"file": "", "kind": "video", "durationSec": 0,
                             "fps": 0, "width": 0, "height": 0}},
                {"id": "fs1", "type": "framesplit", "x": 560, "y": 240,
                 "framesplit": {"enabled": True, "fpsCap": 12, "maxFrames": 120,
                                "start": 0, "end": None, "matte": True,
                                "tolerance": 40, "maxSide": 384,
                                "step": 1, "simPct": 0, "picks": []},
                 "frames": [], "result": None},
                {"id": "sg1", "type": "spritegrid", "x": 1180, "y": 240,
                 "spritegrid": {"enabled": True, "cell": 128, "cols": 0,
                                "padding": 0, "margin": 0, "anchor": "bottom",
                                "sharedScale": True, "fps": 12},
                 "result": None},
                {"id": "ap1", "type": "animpreview", "x": 1720, "y": 240,
                 "animpreview": {"enabled": True, "fps": 12, "zoom": 4,
                                 "loop": True, "bg": "checker"}},
                {"id": "gd1", "type": "godot2d", "x": 2180, "y": 240,
                 "godot2d": {"enabled": True, "animName": "default",
                             "name": "walk", "fps": 12, "loop": True,
                             "mode": "atlas", "folder": "sprites",
                             "origin": "bottom", "textureFilter": "nearest"}},
            ],
            "edges": [
                {"from": "in1", "to": "fs1"},
                {"from": "fs1", "to": "sg1"},
                {"from": "sg1", "to": "ap1"},
                {"from": "ap1", "to": "gd1"},
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
