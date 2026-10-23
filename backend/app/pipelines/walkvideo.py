"""八方向行走 · 阶段一：方向立绘推导 + Wan 图生视频。

链路（对应模板 dir_walk）：
  选择图片 ──→ 行走视频 ──→ 序列帧 ──→ 精灵整图 ──→ 可操控预览
  （把 char_three_view 的拼板接到行走节点前面，那四张图就成了零推导锚点）

为什么用视频底座而不是逐帧编辑（归档项目实测结论）：
- 逐帧编辑每帧都是独立推理，帧间没有运动连续性，快速动作会抖；
- Wan2.2 TI2V-5B 一次生成整段动作，肢体路径天然连贯，抽帧后就是现成的
  循环素材。

八方向的来源（dir_mode）：
- eight：左侧半个圆 + 正/背共 5 个方向各自推导立绘、各自生成视频，
  右侧 3 个方向用左侧对应方向**整段帧水平镜像**得到。行走循环镜像后
  仍然合理（左右腿交替的相位会互换，但循环看起来是对的），且省一半
  视频时间——视频生成是整条链路里最慢的一步；
- four：下/上/左 三个真实方向 + 右镜像（归档项目四方向工作流的对应）；
- single：只跑正面一个方向，用来快速验证链路与参数。

镜像的是整段帧而不是只镜像首帧：只翻第一帧的话，后续帧里角色的朝向、
佩饰位置都会翻回左侧，播起来会「闪一下」。

现成视频（sources）：每个方向可以指定一段项目里已有的视频，跳过该方向的
立绘推导与 Wan 生成。用途：① 换参数重跑下游（序列帧/精灵整图）不必重新
等视频；②  Wan 某个方向效果不好，手工换一段；③ 完全用外部素材（手机实拍
循环、下载的行走动画）拼精灵表。镜像方向也遵循「显式来源优先」。

指令层面的坑（移植自 threeview 的教训）：方向指令只描述「视角怎么变」，
不点名任何具体服装、配件或颜色——点名了就会变成「添加这个物品」的生成
指令，正面没有披风的角色转侧面也会长出披风来。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

from .. import progress as progress_mod

# --------------------------------------------------------------------------- #
# 方向定义
# --------------------------------------------------------------------------- #
# 顺时针一圈：下 → 左下 → 左 → 左上 → 上 → 右上 → 右 → 右下。
# 「真实生成」的 5 个方向是左半边 + 正/背；右半边靠镜像（见模块文档）。
DIR_ORDER: Tuple[str, ...] = (
    "down", "down-left", "left", "up-left", "up", "up-right", "right", "down-right",
)
DIR_LABELS: Dict[str, str] = {
    "down": "正面", "down-left": "左下", "left": "左侧", "up-left": "左上",
    "up": "背面", "up-right": "右上", "right": "右侧", "down-right": "右下",
}
# 每个方向的镜像来源（None = 自己真实生成）
DIR_MIRROR_OF: Dict[str, Optional[str]] = {
    "down": None, "down-left": None, "left": None, "up-left": None, "up": None,
    "down-right": "down-left", "right": "left", "up-right": "up-left",
}

DIR_MODES: Dict[str, List[str]] = {
    # 5 个真实 + 3 个镜像
    "eight": ["down", "down-left", "left", "up-left", "up",
              "up-right", "right", "down-right"],
    # 3 个真实 + 1 个镜像（归档四方向工作流的对应）
    "four": ["down", "left", "up", "right"],
    # 只有正面：快速验证链路
    "single": ["down"],
}

DIR_MODE_LABELS: Dict[str, str] = {
    "eight": "八方向（5 实拍 + 3 镜像）",
    "four": "四方向（3 实拍 + 1 镜像）",
    "single": "单方向（仅正面）",
}


# 朝向角度（度）：正面=0，逆时针（向左转）为正
DIR_ANGLE = {
    "down": 0,          # 正面
    "down-left": 45,    # 左前 45°
    "left": 90,         # 正左
    "up-left": 135,     # 左后 45°
    "up": 180,          # 背面
    "up-right": 225,    # 右后 45°
    "right": 270,       # 正右
    "down-right": 315,  # 右前 45°
}
ANCHOR_ANGLE = {"front": 0, "side": 90, "back": 180, "side_r": 270}


def anchor_for(direction: str, have: Optional[set] = None) -> Tuple[str, int]:
    """给一个目标朝向挑最省事的锚点：返回 (锚点角色, 与锚点的角度差)。

    角度差 0 = 这张锚点图本身就是该朝向，直接用、不用推导；
    45° 只需微调，比从正视图硬转 90/180 度稳得多（后者最容易长出部件、
    丢装备、画风漂）。这正是「先出四视图再进行走视频」的价值：八个方向里
    有四个是零推导（正/左/背/右直通对应锚点），剩下四个也只转 45 度。
    """
    target = DIR_ANGLE[direction]
    pool = [r for r in ("front", "side", "back", "side_r")
            if have is None or r in have] or ["front"]
    # 并列时按「朝向同一侧」优先：左后/右后这种带背面特征的方向，应该从背面
    # 锚点转 45 度，而不是从侧面锚点转 45 度——背面的发型、背包、披风只有
    # 背面图里有。少了这条，up-left 会挑中 side，背面细节就丢了。
    prefer = "back" if 90 < target < 270 else "front"
    best = None
    for role in pool:
        d = (target - ANCHOR_ANGLE[role] + 180) % 360 - 180   # 归一到 -180..180
        score = abs(d) + (0.0 if role == prefer else 0.5)
        if best is None or score < best[2]:
            best = (role, int(d), score)
    return (best[0], best[1]) if best else ("front", target)


def direction_instruction(direction: str, anchor: str = "front") -> str:
    """编辑底座的中文指令：只转朝向，外观/服装/配色/画风零改动。

    与 threeview._view_instruction 同源：不写「保持不变」（编辑器会把整条
    当成「什么都别改」，朝向纹丝不动），也不复述具体角色的造型（那会成为
    凭空添加装备的指令）。只描述「转到哪个朝向」+「造型以参考图为准」。
    """
    keep = (
        "只改变角色朝向：角色的造型、配色、体型比例和画风全部以参考图为准，"
        "不新增也不删除任何部件。背景保持纯色不变，角色全身站立、身体居中，"
        "画面中仍然只有一个角色，不要添加文字和水印。"
    )
    _role, delta = anchor_for(direction, {anchor})
    ad = abs(delta)
    # 用「最终朝向偏画面哪一侧」来描述，而不是用旋转方向。
    # 旋转方向在中文里是有歧义的（「向左转」可能是角色自身的左、也可能是
    # 画面左），而「身体侧向画面左侧」是编辑底座唯一不会误解的说法。
    lean = "左" if 0 < DIR_ANGLE[direction] < 180 else "右"
    # 目标朝向带背面特征时（左后/正后/右后）要显式交代「脸不可见」，
    # 否则编辑器会画出一张转过去的脸
    backish = 90 < DIR_ANGLE[direction] < 270

    if ad == 0:
        # 锚点图本身就是这个朝向（四视图直接命中），不做任何旋转
        return (
            "保持画面中的角色当前朝向与全身站立姿势不变：造型、配色、体型比例和"
            "画风与参考图完全一致。" + keep
        )

    take_from_ref = (
        "旋转后的造型全部来自参考图中这个角色本身，身体的各个部位按它本来的样子"
        "推演，参考图里没有的东西不要出现。"
    )

    if ad == 45:
        if backish:
            return (
                f"把画面中的角色向画面{lean}侧旋转约45度：从背后观察，"
                f"身体侧向画面{lean}侧，脸部、眼睛和鼻子完全不可见，全身站立。"
                "背面的造型全部来自参考图中这个角色本身，参考图里没有的东西不要出现。"
                + keep
            )
        return (
            f"把画面中的角色向画面{lean}侧旋转约45度：四分之三侧面视角，"
            f"身体侧向画面{lean}侧，脸部保留一部分正面特征，全身站立。"
            + take_from_ref + keep
        )

    if ad == 90:
        return (
            f"把画面中的角色转换成{lean}侧视角：角色整体转向画面{lean}侧，"
            "展现完整的侧面轮廓，全身站立。" + take_from_ref + keep
        )

    if ad == 135:
        # 只有手上没有背面锚点、又要出左后/右后时才会走到这
        return (
            f"把画面中的角色转换成背面视角后再向画面{lean}侧旋转约45度：从背后观察，"
            f"身体侧向画面{lean}侧，脸部、眼睛和鼻子完全不可见，全身站立。"
            "背面的造型全部来自参考图中这个角色本身，参考图里没有的东西不要出现。"
            + keep
        )

    # ad == 180：整体转到背面
    return (
        "把画面中的角色转换成背面视角：角色整体旋转180度，背对观众站立，"
        "从正背后观察。脸部、眼睛和鼻子完全不可见；头部的造型按参考图中这个角色"
        "的原样自然延续到背面。背面只呈现角色自己已有的造型，参考图里没有的衣物、"
        "配件和装饰一件都不要出现。角色手里原本拿着东西就随身体一起转到背面，"
        "仍然握在同一只手里；原本空着手就保持空手，不要凭空添一件道具。" + keep
    )


def walk_prompt(action: str = "行走", *, motion_emphasis: bool = False) -> str:
    """Wan 图生视频的动作提示词。

    历史踩坑（用户反馈「没有正常行走」）：早先写「角色位置、大小、朝向始终
    不变，**没有位移**，不转身、不镜头运动」——视频底座把「没有位移」理解成
    「别动」，结果出的是一张几乎静止的图。

    正确做法分两层，且必须用**不同主语**说清，否则模型偷懒定格：
    1) 角色的运动（主语=角色）：双腿持续交替迈步、膝盖屈伸、脚掌离地落地、
       身体随步伐上下颠动、手臂反向摆动——这是要强调的「动」；
    2) 镜头的静止（主语=取景框/背景）：取景框与背景不动、角色始终居中、
       体型不变。**只说镜头/背景静止，绝不写「角色不动」**——
       「原地行走」≠「静止」，后者是模型最爱的偷懒解。

    motion_emphasis=True 用于动作不达标时的重试：进一步点名「全程动态、绝不
    定格、每帧都在变」，配合上调的 guidance_scale 把动作幅度逼出来。
    """
    act = (action or "行走").strip() or "行走"
    motion = (
        f"角色在原地{act}：双腿持续交替迈步，膝盖随步伐明显弯曲伸展，"
        "脚掌明确地反复抬起、离地、再落地；身体随重心上下颠动，"
        "手臂与腿反向自然摆动，发饰和衣摆随步伐轻微晃动。"
        "动作连贯成循环，一只脚落地时另一只脚抬起，步态清晰可见。"
    )
    if motion_emphasis:
        motion = (
            "强烈的持续行走动作：" + motion +
            " 全程保持动态，绝不定格、绝不站桩、绝不变成单张静态姿势；"
            "每一帧肢体的位置都在变化，步伐明显而有力。"
        )
    stable = (
        "保持取景框与背景完全静止，角色始终位于画面中心、体型大小不变"
        "（原地行走，不向画面任何方向平移、不旋转身体、镜头不动、背景为纯色）。"
    )
    return motion + stable



def directions_of(dir_mode: str) -> List[str]:
    """方向模式 → 本次要产出的方向列表（按顺时针顺序）。"""
    dirs = DIR_MODES.get(dir_mode) or DIR_MODES["eight"]
    return [d for d in DIR_ORDER if d in dirs]


def _silhouette(img: Image.Image):
    """按四角底色抠出剪影掩码（96×96 布尔矩阵）。纯色背景下足够准。"""
    import numpy as np

    a = np.asarray(img.convert("RGB").resize((96, 96)), dtype=np.int16)
    return (np.abs(a - a[0, 0]).sum(axis=2) > 45)


def motion_stats(frames: List[Image.Image]) -> Dict[str, float]:
    """量这段视频「动没动」和「稳不稳」，两项都要，缺一不可。

    为什么不用像素差：视频底座吐一帧糊的，整帧一亮，像素差就飙到 31，可角色
    一步没走——那个数会把「闪烁」误判成「动作好」。改成比**剪影**：

    - motion：相邻帧剪影的 1-IoU（0~100）。只反映形体变化，对整体明暗免疫。
      真在走实测 5~20；几乎不动 <3。
    - instability：剪影面积的最大波动（百分比）。画面被冲淡/重影时剪影会暴涨
      （实测稳定 20~27%，崩坏时 15~100%），这是「闪烁糊了」的量化指标。
    """
    import numpy as np

    if len(frames) < 2:
        return {"motion": 0.0, "instability": 0.0}
    masks = [_silhouette(f) for f in frames]
    areas = np.array([m.sum() / 96 / 96 for m in masks], dtype=np.float32)
    # 中位数为 0 说明整段都没抠出主体（极短/极糊的片段）：这时比值会炸成 inf，
    # 序列化成 JSON 会变 null。直接给 0，让上层按「没有信息」处理。
    med = float(np.median(areas))
    if med <= 1e-4:
        return {"motion": 0.0, "instability": 0.0}
    ious = []
    for i in range(len(masks) - 1):
        a, b = masks[i], masks[i + 1]
        union = np.logical_or(a, b).sum()
        ious.append(100.0 * (1.0 - np.logical_and(a, b).sum() / max(1, union)))
    instab = 100.0 * (float(areas.max()) - float(areas.min())) / med
    return {
        "motion": round(float(np.mean(ious)) if ious else 0.0, 2),
        "instability": round(min(999.0, instab), 1),
    }


# --------------------------------------------------------------------------- #
# 单步执行
# --------------------------------------------------------------------------- #
async def derive_direction(
    base: Image.Image,
    direction: str,
    *,
    anchor: str = "front",
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    width: int = 512,
    height: int = 768,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """从锚点图接力生成指定方向的立绘（编辑底座指令接力）。

    anchor 是这张参考图本身的朝向（front/side/back/side_r）。有了四视图之后，
    多数方向的角度差只有 0 或 45 度——比从正视图硬转 90/180 度稳得多：
    旋转幅度越小，编辑器越不容易改动服装、配件和画风。

    走 Qwen-Image-2.1 指令改图：外观保持最好，且不需要重新描述角色。
    provider 带 refs 时会自动路由到编辑底座。
    """
    from ..providers.diffusers_local import DiffusersProvider

    if direction not in DIR_ORDER:
        raise ValueError(f"未知方向：{direction}")
    _role, delta = anchor_for(direction, {anchor})
    if delta == 0:
        # 锚点图本身就是这个朝向（四视图命中），再跑一次编辑只会引入无谓的漂移
        return base.convert("RGB"), {"direction": direction, "mode": "passthrough",
                                     "anchor": anchor}

    provider = DiffusersProvider(provider_cfg or {})
    instruction = direction_instruction(direction, anchor)
    progress_mod.report(task_id, 0, 0, f"{DIR_LABELS[direction]}立绘（编辑底座接力）…")

    t = time.time()
    result = await provider.generate(
        prompt=instruction,
        width=max(64, int(width) // 8 * 8),
        height=max(64, int(height) // 8 * 8),
        steps=steps, seed=seed, n=1, refs=[base],
        task_id=task_id, max_seq_len=max_seq_len,
    )
    rmeta = dict(result.meta or {})
    return result.images[0].convert("RGB"), {
        "direction": direction, "label": DIR_LABELS[direction], "mode": "edit",
        "anchor": anchor, "rotation": delta,
        "instruction": instruction,
        "steps": rmeta.get("steps"), "seed": seed,
        "model": rmeta.get("model"),
        "seconds": round(time.time() - t, 1),
    }


async def generate_walk(
    view: Image.Image,
    *,
    action: str = "行走",
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    width: int = 384,
    height: int = 512,
    num_frames: int = 81,
    fps: int = 24,
    max_side: int = 512,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    pixel_art: bool = False,
    guidance_scale: Optional[float] = None,
    motion_threshold: float = 6.0,
    retries: int = 2,
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """Wan2.2 TI2V-5B：方向立绘 + 动作提示词 → 行走视频帧序列。

    轻量控制闭环（不上更大模型也能治「没正常走」）：先按正常 CFG 生成，用
    motion_stats 量化「动没动」，分数低于阈值就升级 guidance_scale（并加重
    动作提示词、适度加步数）重跑，直到达标或用尽重试次数。这样即便 5B 底座
    偶尔偷懒定格，也会被自动逼出真正在走的版本——最终交付动作幅度最高的那次。

    见模块文档：选视频底座是为了动作连贯、不闪；这个闭环是在不引入新模型的
    前提下，把「动作幅度」这条指标拉到合格线之上。
    """
    from ..providers.diffusers_local import DiffusersProvider, resolve_video_model

    cfg = dict(provider_cfg or {})
    cfg["model"] = resolve_video_model(cfg.get("videoModel"))
    provider = DiffusersProvider(cfg)
    base_prompt = walk_prompt(action)
    # 负向词直接对准用户反馈的现象：糊掉（涂抹/重影/低清）与闪烁/变形，
    # 再加「站桩/单帧定格/姿势不变/动作僵硬」——把模型最爱偷懒的静止解
    # 也写进黑名单，和正向的「持续运动」形成夹击。
    negative = ("模糊, 重影, 涂抹, 低清, 噪点, 闪烁, 变形, 静止不动, 镜头晃动, "
                "站桩, 单帧定格, 姿势不变, 动作僵硬")

    t = time.time()
    base_gs = 5.0 if guidance_scale is None else float(guidance_scale)
    max_attempts = 1 + max(0, int(retries))
    frames: List[Image.Image] = []
    rmeta: Dict[str, Any] = {}
    best_frames = frames
    best_motion = -1.0
    last_motion = 0.0
    for attempt in range(max_attempts):
        emphasis = attempt > 0
        prompt = walk_prompt(action, motion_emphasis=emphasis) if emphasis else base_prompt
        # 重试逐次加压：CFG 每次 +1.5（封顶 9.0），步数不够时每次 +5（封顶 50）。
        # CFG 上调对「运动幅度」是最直接的杠杆——这正是 _run_wan 把 CFG 改可调控的原因。
        cur_gs = min(9.0, base_gs + attempt * 1.5)
        cur_steps = None
        if steps is not None:
            cur_steps = min(50, int(steps) + attempt * 5)
        if attempt > 0:
            progress_mod.report(task_id, 0, 0,
                                f"动作幅度不足（{last_motion} < {motion_threshold}），"
                                f"重试加强（CFG {cur_gs:.1f}）…")
        result = await provider.generate_video(
            prompt, view,
            width=width, height=height,
            steps=cur_steps, seed=seed,
            num_frames=num_frames, fps=fps, max_side=max_side,
            task_id=task_id, pixel_art=pixel_art,
            negative=negative, guidance_scale=cur_gs,
        )
        frames = list(result.images)
        stats = motion_stats(frames)
        last_motion = stats["motion"]
        # 交付动作幅度最高的那次（CFG 高不一定运动大，以实测分数为准）
        if stats["motion"] > best_motion:
            best_motion = stats["motion"]
            best_frames = frames
            rmeta = dict(result.meta or {})
        if stats["motion"] >= motion_threshold:
            frames = best_frames
            break
    frames = best_frames
    out = {
        "prompt": base_prompt, "mode": "i2v", "frames": len(frames),
        "fps": rmeta.get("fps"), "seconds": round(time.time() - t, 1),
        "model": rmeta.get("model"), "size": rmeta.get("size"),
        "steps": rmeta.get("steps"), "seed": seed,
        "guidanceScale": rmeta.get("cfg"),
        "motionScore": round(last_motion, 2),
        "motionBest": round(best_motion, 2),
        "motionThreshold": motion_threshold,
        "retriesUsed": max(0, max_attempts - 1),
    }
    if rmeta.get("vram"):
        out["vram"] = rmeta["vram"]
    return frames, out


async def run_walk(
    base: Image.Image,
    *,
    dir_mode: str = "eight",
    action: str = "行走",
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    width: int = 384,
    height: int = 512,
    num_frames: int = 81,
    fps: int = 24,
    max_side: int = 512,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    sources: Optional[Dict[str, Dict[str, Any]]] = None,
    view_anchors: Optional[Dict[str, Image.Image]] = None,
    dir_images: Optional[Dict[str, Image.Image]] = None,
    pixel_art: bool = False,
    guidance_scale: Optional[float] = None,
    motion_threshold: float = 6.0,
    retries: int = 2,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """跑完一个方向集合，返回每方向的帧序列。

    返回 directions: [{dir, label, frames: [PIL], seed, seconds, mirrored,
    mirroredFrom, size, fps, derive/walk 的 meta}]。镜像方向的 frames 是
    来源方向整段帧的水平翻转。

    sources: {方向: {"frames": [PIL], "fps": float, "file": 文件名}}——该方向
    直接用现成视频（自己以前生成的 walk-*.gif 或上传的 mp4 等），跳过「推导
    立绘 + Wan 生成」两步；没给来源的方向照常走 Wan。一个模型都不加载也是
    合法的（全部有来源时），用来快速换参数重跑下游的序列帧/精灵整图。

    view_anchors: {"front"/"side"/"back"/"side_r": PIL}——四视图锚点。这是角色一致性的
    关键：每个方向从**离它最近的锚点**推导，而不是一律从正视图转。有侧/背
    视图时，正面、正左、背面三个方向是零推导（直接拿锚点图），左前/左后只
    转 45 度，既快又不容易走形。

    dir_images: {方向: PIL}——用户为该方向直接指定的立绘图，跳过推导，
    但仍然会用它作为参考图去跑 Wan。用来手动修某个方向。

    执行顺序刻意分成两轮：先推导全部方向立绘（编辑底座常驻），再集中生成
    全部视频（Wan 常驻）。两个底座交替加载会互相驱逐显存，每次切换都要
    重载 fp8 缓存，八方向能慢出几分钟。
    """
    dirs = directions_of(dir_mode)
    # 四视图锚点：缺哪个角色就退回正视图（单张图进来的老链路照常工作）
    view_map = {k: v for k, v in (view_anchors or {}).items() if v is not None}
    if "front" not in view_map:
        view_map["front"] = base
    have_views = set(view_map)
    # 每个真实方向用哪个锚点、要转多少度
    plan = {d: anchor_for(d, have_views) for d in dirs if not DIR_MIRROR_OF.get(d)}
    src_map = {k: v for k, v in (sources or {}).items()
               if k in dirs and v and v.get("frames")}
    # 任何方向都能给来源（含右半边镜像方向）：显式来源优先于镜像，
    # 这样「左侧用 Wan、右侧用自己录的视频」这种事也说得通
    src_dirs = [d for d in dirs if d in src_map]
    real_dirs = [d for d in dirs if not DIR_MIRROR_OF.get(d)]
    gen_dirs = [d for d in real_dirs if d not in src_map]
    # 进度按「现成视频算 1 步、生成方向算 2 步」累计，和下面 done 的增量对齐；
    # 只按 gen_dirs 算会让全现成时出现 1/0、混合时进度超过 100%
    total_steps = len(src_dirs) + len(gen_dirs) * 2
    done = 0
    t0 = time.time()

    # 第零轮：现成视频（不加载任何模型）
    clips: Dict[str, List[Image.Image]] = {}
    derive_meta: Dict[str, Dict[str, Any]] = {}
    walk_meta: Dict[str, Dict[str, Any]] = {}
    failures: List[Dict[str, Any]] = []

    def _fail(d: str, stage: str, err: Exception) -> None:
        """单个方向失败不连累整批：八方向是分钟级任务，OOM 掉一个方向就把
        已完成的全丢掉太浪费——记下来，能用的先交付。"""
        failures.append({"dir": d, "label": DIR_LABELS.get(d, d),
                         "stage": stage, "error": str(err)[:300]})

    for d in src_dirs:
        src = src_map[d]
        try:
            frames = normalize_source_frames(src["frames"], width=width, height=height)
            if not frames:
                raise RuntimeError("没有可用帧")
        except Exception as e:
            _fail(d, "existing", e)
            continue
        clips[d] = frames
        derive_meta[d] = {"direction": d, "label": DIR_LABELS[d], "mode": "existing"}
        walk_meta[d] = {
            "mode": "existing", "file": src.get("file"), "frames": len(frames),
            "fps": src.get("fps") or fps, "seconds": 0.0, "model": "现成视频",
            "size": [width, height], **motion_stats(frames),
        }
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}使用现成视频（{len(frames)} 帧）")

    # 第一轮：方向立绘（编辑底座）
    views: Dict[str, Image.Image] = {}
    for d in gen_dirs:
        # 用户直接指定了这个方向的立绘图 → 不推导，但仍会拿它去跑 Wan
        if d in (dir_images or {}):
            views[d] = dir_images[d].convert("RGB")
            derive_meta[d] = {"direction": d, "label": DIR_LABELS[d], "mode": "user"}
            done += 1
            progress_mod.report(task_id, done, total_steps,
                                f"{DIR_LABELS[d]}用指定立绘")
            continue
        anchor_role, _delta = plan[d]
        src_img = view_map.get(anchor_role) or base
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}立绘推导中…")
        try:
            img, meta = await derive_direction(
                src_img, d, anchor=anchor_role, steps=steps, seed=seed,
                width=width, height=height, max_seq_len=max_seq_len,
                provider_cfg=provider_cfg, task_id=task_id)
        except progress_mod.CancelledError:
            raise
        except Exception as e:
            _fail(d, "derive", e)
            continue
        views[d] = img
        derive_meta[d] = meta
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}立绘完成（{done}/{total_steps}）")

    # 第二轮：行走视频（Wan 常驻）
    for d in gen_dirs:
        if d not in views:
            continue                     # 立绘就失败的方向不用再跑视频
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}行走视频（Wan）…")
        try:
            frames, meta = await generate_walk(
                views[d], action=action, steps=steps, seed=seed,
                width=width, height=height, num_frames=num_frames,
                fps=fps, max_side=max_side,
                provider_cfg=provider_cfg, task_id=task_id,
                pixel_art=pixel_art,
                guidance_scale=guidance_scale,
                motion_threshold=motion_threshold, retries=retries)
        except progress_mod.CancelledError:
            raise
        except Exception as e:
            _fail(d, "walk", e)
            continue
        clips[d] = frames
        walk_meta[d] = dict(meta, **motion_stats(frames))
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}行走视频完成（{done}/{total_steps}）")

    # 视频出完就把 Wan 卸掉：下游（序列帧 / 精灵整图）是纯 CPU 计算，
    # 抱着 17GB 底座纯属占着茅坑——也让这一步之后的显存立刻回到基线。
    if gen_dirs:
        try:
            from ..providers.diffusers_local import DiffusersProvider

            DiffusersProvider.unload()
            progress_mod.report(task_id, done, total_steps, "已释放视频底座显存")
        except Exception:
            pass

    if not clips:
        detail = "；".join(f"{f['label']}：{f['error']}" for f in failures[:3])
        raise RuntimeError(f"全部方向都失败了（{detail or '没有可用输入'}）")

    # 组装输出（含镜像方向）
    out: List[Dict[str, Any]] = []
    for d in dirs:
        # 这一方向实际用的立绘图：节点要能预览它，用户也要能看出哪一步走形了。
        # 现成视频没有独立立绘，用首帧代替；镜像方向给翻过来的那张。
        if d in views:
            view_img: Optional[Image.Image] = views[d]
        elif d in clips:
            view_img = clips[d][0]
        else:
            src = DIR_MIRROR_OF.get(d)
            base_view = views.get(src) if src else None
            if base_view is None and src in clips:
                base_view = clips[src][0]
            view_img = base_view.transpose(Image.FLIP_LEFT_RIGHT) if base_view else None
        if d in clips:
            # 现成视频优先于镜像：用户显式指定右侧视频时不该被左侧覆盖
            entry: Dict[str, Any] = {
                "dir": d, "label": DIR_LABELS[d], "frames": clips[d],
                "fps": walk_meta[d].get("fps") or fps,
                "seed": walk_meta[d].get("seed"),
                "mirrored": False, "mirroredFrom": None,
                "derive": derive_meta[d], "walk": walk_meta[d],
            }
        else:
            src = DIR_MIRROR_OF.get(d)
            if not src or src not in clips:
                # 镜像源本身失败了：这个方向跟着缺席，不要拿空帧去填
                if not any(f["dir"] == d for f in failures):
                    _fail(d, "mirror",
                          RuntimeError(f"镜像源 {DIR_LABELS.get(src or '', src)} 没有产出"))
                continue
            frames = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in clips[src]]
            entry = {
                "dir": d, "label": DIR_LABELS[d], "frames": frames,
                "fps": walk_meta[src].get("fps") or fps,
                "seed": walk_meta[src].get("seed"),
                "mirrored": True, "mirroredFrom": src,
                "derive": {"direction": d, "mode": "mirror"},
                "walk": dict(walk_meta[src], mirrored=True),
            }
        if view_img is not None:
            entry["view"] = view_img
        out.append(entry)

    meta = {
        "dirMode": dir_mode, "dirModeLabel": DIR_MODE_LABELS.get(dir_mode, dir_mode),
        "action": action, "directions": len(out),
        "realDirections": len(real_dirs),
        "mirroredDirections": len([d for d in out if d["mirrored"]]),
        "existingDirections": len(src_dirs),
        "anchors": sorted(have_views),
        "anchorPlan": {d: {"anchor": r, "rotation": delta} for d, (r, delta) in plan.items()},
        "userImages": sorted((dir_images or {}).keys()),
        "failures": failures,
        "fps": fps, "numFrames": num_frames,
        "size": [width, height], "maxSide": max_side,
        "seconds": round(time.time() - t0, 1),
    }
    return out, meta


def normalize_source_frames(frames: List[Image.Image], *, width: int,
                            height: int) -> List[Image.Image]:
    """按节点参数规整现成视频的帧：等比缩放 + 居中裁到 width×height。

    外部视频的尺寸/帧率和节点参数不一致是常态（手机竖屏 1080×1920、30fps）。
    不规整的话精灵表单元格会尺寸不一，共享缩放与锚点全部失准。
    """
    tw, th = int(width), int(height)
    out: List[Image.Image] = []
    for f in frames:
        f = f.convert("RGBA")
        scale = max(tw / max(1, f.width), th / max(1, f.height))
        if abs(scale - 1) > 1e-3:
            # 放大用 NEAREST（像素图不插值，保持硬边）；缩小用 LANCZOS——
            # 把 1080p 手机视频直接 NEAREST 抽到 384 会大面积丢线（锯齿 + 断笔），
            # 而缩小的抗锯齿代价最终会被精灵表的硬边裁切吃掉
            resample = Image.NEAREST if scale > 1 else Image.LANCZOS
            f = f.resize((max(1, round(f.width * scale)),
                          max(1, round(f.height * scale))), resample)
        x = max(0, (f.width - tw) // 2)
        y = max(0, (f.height - th) // 2)
        out.append(f.crop((x, y, x + tw, y + th)))
    return out
