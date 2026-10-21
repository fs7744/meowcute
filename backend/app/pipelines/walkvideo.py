"""八方向行走 · 阶段一：方向立绘推导 + Wan 图生视频。

链路（对应模板 dir_walk）：
  生图/选图 ──→ 行走视频 ──→ 序列帧 ──→ 精灵整图 ──→ 可操控预览

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


def direction_instruction(direction: str) -> str:
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
    if direction == "down":
        return "保持画面中的角色正面全身立绘不变。" + keep
    if direction == "left":
        return (
            "把画面中的角色转换成左侧视角：角色整体向左旋转90度，"
            "展现完整的侧面轮廓，全身站立。侧面的造型全部来自参考图中这个角色本身，"
            "身体的各个部位按它本来的样子推演，参考图里没有的东西不要出现。" + keep
        )
    if direction == "right":
        return (
            "把画面中的角色转换成右侧视角：角色整体向右旋转90度，"
            "展现完整的侧面轮廓，全身站立。侧面的造型全部来自参考图中这个角色本身，"
            "身体的各个部位按它本来的样子推演，参考图里没有的东西不要出现。" + keep
        )
    if direction == "up":
        return (
            "把画面中的角色转换成背面视角：角色整体旋转180度，背对观众站立，"
            "从正背后观察。脸部、眼睛和鼻子完全不可见；头部的造型按参考图中这个角色"
            "的原样自然延续到背面。背面只呈现角色自己已有的造型，参考图里没有的衣物、"
            "配件和装饰一件都不要出现。角色手里原本拿着东西就随身体一起转到背面，"
            "仍然握在同一只手里；原本空着手就保持空手，不要凭空添一件道具。" + keep
        )
    if direction == "down-left":
        return (
            "把画面中的角色向左旋转约45度：四分之三侧面视角，身体侧向画面左侧，"
            "脸部保留一部分正面特征，全身站立。侧面的造型全部来自参考图中这个角色本身，"
            "身体的各个部位按它本来的样子推演，参考图里没有的东西不要出现。" + keep
        )
    if direction == "down-right":
        return (
            "把画面中的角色向右旋转约45度：四分之三侧面视角，身体侧向画面右侧，"
            "脸部保留一部分正面特征，全身站立。侧面的造型全部来自参考图中这个角色本身，"
            "身体的各个部位按它本来的样子推演，参考图里没有的东西不要出现。" + keep
        )
    if direction == "up-left":
        return (
            "把画面中的角色转换成背面视角后再向左旋转约45度：从背后观察，"
            "身体侧向画面左侧，脸部、眼睛和鼻子完全不可见，全身站立。"
            "背面的造型全部来自参考图中这个角色本身，参考图里没有的东西不要出现。" + keep
        )
    if direction == "up-right":
        return (
            "把画面中的角色转换成背面视角后再向右旋转约45度：从背后观察，"
            "身体侧向画面右侧，脸部、眼睛和鼻子完全不可见，全身站立。"
            "背面的造型全部来自参考图中这个角色本身，参考图里没有的东西不要出现。" + keep
        )
    raise ValueError(f"未知方向：{direction}")


def walk_prompt(action: str = "行走") -> str:
    """Wan 图生视频的动作提示词（移植自归档项目 video 步骤的实测版本）。

    关键约束是「原地」：角色位置、大小、朝向始终不变，没有位移、没有镜头
    运动——抽帧拼精灵表时，角色在画布里的位置必须是稳的，否则每帧都要
    重新对锚点。动作本身写具体（双脚交替蹬地、手臂与腿反向摆动、衣摆晃动），
    两字词的「行走」对视频底座的反应不够明确。
    """
    act = (action or "行走").strip() or "行走"
    return (
        f"画面中的角色在原地{act}，双脚交替蹬地迈步，手臂与腿反向摆动，"
        "发饰和衣摆随步伐轻微晃动；角色位置、大小、朝向始终不变，"
        "没有位移、没有镜头运动、没有起停；背景保持纯色。"
    )


def directions_of(dir_mode: str) -> List[str]:
    """方向模式 → 本次要产出的方向列表（按顺时针顺序）。"""
    dirs = DIR_MODES.get(dir_mode) or DIR_MODES["eight"]
    return [d for d in DIR_ORDER if d in dirs]


# --------------------------------------------------------------------------- #
# 单步执行
# --------------------------------------------------------------------------- #
async def derive_direction(
    base: Image.Image,
    direction: str,
    *,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    width: int = 512,
    height: int = 768,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """由正视图接力生成指定方向的立绘（编辑底座指令接力）。

    走 Qwen-Image-Edit-2511：外观保持最好，且不需要重新描述角色。
    provider 带 refs 时会自动路由到编辑底座。
    """
    from ..providers.diffusers_local import DiffusersProvider

    if direction not in DIR_ORDER:
        raise ValueError(f"未知方向：{direction}")
    if direction == "down":
        # 正面就是参考图本身，再跑一次编辑只会引入无谓的漂移
        return base.convert("RGB"), {"direction": direction, "mode": "passthrough"}

    provider = DiffusersProvider(provider_cfg or {})
    instruction = direction_instruction(direction)
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
        "instruction": instruction,
        "steps": rmeta.get("steps"), "seed": seed,
        "model": rmeta.get("model"),
        "autoRouted": bool(rmeta.get("autoRouted")),
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
) -> Tuple[List[Image.Image], Dict[str, Any]]:
    """Wan2.2 TI2V-5B：方向立绘 + 动作提示词 → 行走视频帧序列。

    视频底座必须单独指定：provider_cfg 里的 model 是文生图底座，这里按
    resolve_video_model() 覆盖成 Wan。两个底座交替时装载会互相驱逐，
    所以调用方要「先推导全部方向立绘，再集中生成全部视频」（run_walk
    的顺序即为此）。
    """
    from ..providers.diffusers_local import DiffusersProvider, resolve_video_model

    cfg = dict(provider_cfg or {})
    cfg["model"] = resolve_video_model(cfg.get("videoModel"))
    provider = DiffusersProvider(cfg)
    prompt = walk_prompt(action)

    t = time.time()
    result = await provider.generate_video(
        prompt, view,
        width=width, height=height,
        steps=steps, seed=seed,
        num_frames=num_frames, fps=fps, max_side=max_side,
        task_id=task_id,
    )
    rmeta = dict(result.meta or {})
    return list(result.images), {
        "prompt": prompt, "mode": "i2v", "frames": len(result.images),
        "fps": rmeta.get("fps"), "seconds": round(time.time() - t, 1),
        "model": rmeta.get("model"), "size": rmeta.get("size"),
        "steps": rmeta.get("steps"), "seed": seed,
    }


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
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """跑完一个方向集合，返回每方向的帧序列。

    返回 directions: [{dir, label, frames: [PIL], seed, seconds, mirrored,
    mirroredFrom, size, fps, derive/walk 的 meta}]。镜像方向的 frames 是
    来源方向整段帧的水平翻转。

    sources: {方向: {"frames": [PIL], "fps": float, "file": 文件名}}——该方向
    直接用现成视频（自己以前生成的 walk-*.gif 或上传的 mp4 等），跳过「推导
    立绘 + Wan 生成」两步；没给来源的方向照常走 Wan。一个模型都不加载也是
    合法的（全部有来源时），用来快速换参数重跑下游的序列帧/精灵整图。

    执行顺序刻意分成两轮：先推导全部方向立绘（编辑底座常驻），再集中生成
    全部视频（Wan 常驻）。两个底座交替加载会互相驱逐显存，每次切换都要
    重载 fp8 缓存，八方向能慢出几分钟。
    """
    dirs = directions_of(dir_mode)
    src_map = {k: v for k, v in (sources or {}).items()
               if k in dirs and v and v.get("frames")}
    real_dirs = [d for d in dirs if not DIR_MIRROR_OF.get(d)]
    gen_dirs = [d for d in real_dirs if d not in src_map]
    src_real = [d for d in real_dirs if d in src_map]
    # 进度按「现成视频算 1 步、生成方向算 2 步」累计，和下面 done 的增量对齐；
    # 只按 gen_dirs 算会让全现成时出现 1/0、混合时进度超过 100%
    total_steps = len(src_real) + len(gen_dirs) * 2
    done = 0
    t0 = time.time()

    # 第零轮：现成视频（不加载任何模型）
    clips: Dict[str, List[Image.Image]] = {}
    derive_meta: Dict[str, Dict[str, Any]] = {}
    walk_meta: Dict[str, Dict[str, Any]] = {}
    for d in real_dirs:
        if d not in src_map:
            continue
        src = src_map[d]
        frames = normalize_source_frames(src["frames"], width=width, height=height)
        if not frames:
            raise RuntimeError(f"方向 {DIR_LABELS[d]} 的现成视频没有可用帧")
        clips[d] = frames
        derive_meta[d] = {"direction": d, "label": DIR_LABELS[d], "mode": "existing"}
        walk_meta[d] = {
            "mode": "existing", "file": src.get("file"), "frames": len(frames),
            "fps": src.get("fps") or fps, "seconds": 0.0, "model": "现成视频",
            "size": [width, height],
        }
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}使用现成视频（{len(frames)} 帧）")

    # 第一轮：方向立绘（编辑底座）
    views: Dict[str, Image.Image] = {}
    for d in gen_dirs:
        img, meta = await derive_direction(
            base, d, steps=steps, seed=seed,
            width=width, height=height, max_seq_len=max_seq_len,
            provider_cfg=provider_cfg, task_id=task_id)
        views[d] = img
        derive_meta[d] = meta
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}立绘完成（{done}/{total_steps}）")

    # 第二轮：行走视频（Wan 常驻）
    for d in gen_dirs:
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}行走视频（Wan）…")
        frames, meta = await generate_walk(
            views[d], action=action, steps=steps, seed=seed,
            width=width, height=height, num_frames=num_frames,
            fps=fps, max_side=max_side,
            provider_cfg=provider_cfg, task_id=task_id)
        clips[d] = frames
        walk_meta[d] = meta
        done += 1
        progress_mod.report(task_id, done, total_steps,
                            f"{DIR_LABELS[d]}行走视频完成（{done}/{total_steps}）")

    # 组装输出（含镜像方向）
    out: List[Dict[str, Any]] = []
    for d in dirs:
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
            frames = [f.transpose(Image.FLIP_LEFT_RIGHT) for f in clips[src]]
            entry = {
                "dir": d, "label": DIR_LABELS[d], "frames": frames,
                "fps": walk_meta[src].get("fps") or fps,
                "seed": walk_meta[src].get("seed"),
                "mirrored": True, "mirroredFrom": src,
                "derive": {"direction": d, "mode": "mirror"},
                "walk": dict(walk_meta[src], mirrored=True),
            }
        out.append(entry)

    meta = {
        "dirMode": dir_mode, "dirModeLabel": DIR_MODE_LABELS.get(dir_mode, dir_mode),
        "action": action, "directions": len(out),
        "realDirections": len(real_dirs),
        "mirroredDirections": len(out) - len(real_dirs),
        "existingDirections": len([d for d in real_dirs if d in src_map]),
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
