"""优秀提示词示例库：设置页可维护，生成节点里一键引用。

设计取舍：
- 示例**只放主体描述**，不放画风词。画风由画风选择器统一管理（前置到提示词
  开头）；示例里再写死画风就是两个画风打架，模型只认前一个。
- 每条带 ``note`` 说明「为什么这么写」，这是示例库真正的价值——用户不是
  在抄一句话，而是在学写法。
- 内置示例可恢复（``reset``），用户新增的示例永久保留；删除是软删除
  （标记 hidden），避免误删后无法找回。
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .config import PROMPT_EXAMPLES_PATH, ensure_dirs
from .jsonlimits import parse_bounded, dumps_bounded

CATEGORIES = ["角色描述", "场景描述", "道具图标", "动作姿态", "画风补充"]

_SEED: List[Dict[str, Any]] = [
    # ---------------- 角色描述 ----------------
    {
        "id": "builtin-char-cat-knight",
        "category": "角色描述",
        "title": "猫武士（像素风经典题材）",
        "content": "灰白英短猫武士，红色短披风，腰挎武士刀，皮质护腕，圆耳竖立，琥珀色眼睛，三头身比例，正面站立，表情专注",
        "note": "物种 + 主色 + 服装/道具 + 五官特征 + 体型比例 + 姿态。像素画信息量有限，"
                "每类特征给 1-2 个就够，堆太多会糊成一团。",
        "builtin": True,
    },
    {
        "id": "builtin-char-slime",
        "category": "角色描述",
        "title": "史莱姆（极简轮廓练习）",
        "content": "半透明蓝色史莱姆，圆润水滴形身体，两只黑色小圆眼，嘴角上扬，底部扁平贴地，高光在左上角，轮廓为深蓝描边",
        "note": "极简题材最适合验证像素精修效果：色块少、轮廓简单，"
                "网格对齐好不好一眼看得出来。",
        "builtin": True,
    },
    {
        "id": "builtin-char-merchant",
        "category": "角色描述",
        "title": "旅行商人（NPC 模板）",
        "content": "矮胖的人类旅行商人，棕色皮斗篷，背着一个比人还高的行囊，挂满瓶瓶罐罐，圆脸络腮胡，笑容憨厚，正面站立",
        "note": "NPC 的关键是「一眼看出身份」：行囊和瓶罐就是身份符号。"
                "像素风里靠剪影区分职业，比靠细节更可靠。",
        "builtin": True,
    },
    # ---------------- 场景描述 ----------------
    {
        "id": "builtin-scene-clocktown",
        "category": "场景描述",
        "title": "黄昏钟楼小镇（等距场景）",
        "content": "黄昏时分的废弃钟楼小镇广场，石砖地面覆满青苔，中央有一座破损的喷泉，四周是低矮的石屋，远处钟楼剪影高耸，暖橙色天光与冷蓝阴影形成对比，等距视角",
        "note": "时间/光线 + 地面材质 + 主体建筑 + 前景中景远景层次 + 视角。"
                "等距视角适合做可平铺的地图素材。",
        "builtin": True,
    },
    {
        "id": "builtin-scene-dungeon",
        "category": "场景描述",
        "title": "地牢入口（关卡起点）",
        "content": "山壁上的古老地牢入口，拱形石门半掩，门缝透出幽绿光芒，两侧各有一尊石像守卫，地面散落碎石与枯骨，藤蔓垂挂，正视角，暗色调",
        "note": "游戏场景要「告诉玩家这里能进去」：门缝光、对称守卫、地面杂物"
                "都是引导视线的手段。暗色调场景注意保留高对比光源。",
        "builtin": True,
    },
    {
        "id": "builtin-scene-forest",
        "category": "场景描述",
        "title": "精灵森林（横版背景）",
        "content": "清晨的精灵森林，巨大的古树盘根错节，树冠间漏下束状阳光，地面铺满发光蘑菇与苔藓，一条小溪从左向右流过，远景有薄雾，横版侧视角",
        "note": "横版背景的关键是「可重复的层次」：前景暗、中景主体、远景雾。"
                "束状阳光是最省力的氛围手段。",
        "builtin": True,
    },
    # ---------------- 道具图标 ----------------
    {
        "id": "builtin-item-sword",
        "category": "道具图标",
        "title": "锈蚀骑士剑（装备图标）",
        "content": "一把生锈的骑士长剑，十字护手，缠布握柄，剑身有两处缺口，刃口泛着暗红光，居中构图，纯白背景",
        "note": "图标类必须写「居中构图 + 纯背景」——不写的话模型会画成一整幅"
                "插画，裁切后主体只剩一半。",
        "builtin": True,
    },
    {
        "id": "builtin-item-potion",
        "category": "道具图标",
        "title": "治疗药水（消耗品）",
        "content": "圆底玻璃瓶装的红色治疗药水，软木塞封口，瓶身有十字形凸起纹样，液面有气泡，瓶口系着麻绳标签，居中构图，纯白背景",
        "note": "消耗品靠「容器形状 + 液体颜色」传递功能：红=治疗、蓝=法力。"
                "加一个几何纹样能让图标在 32×32 下仍然可读。",
        "builtin": True,
    },
    # ---------------- 动作姿态 ----------------
    {
        "id": "builtin-action-slash",
        "category": "动作姿态",
        "title": "挥剑横斩（战斗帧）",
        "content": "猫武士双手握刀向右横斩，身体前倾重心压低，披风向后扬起，双脚前后开立，刀身拖出弧形残影",
        "note": "写动作要写「重心 + 肢体朝向 + 附属物动态」三项。残影在像素风里"
                "用 2-3 个递减色块表达，不要写「模糊」。",
        "builtin": True,
    },
    {
        "id": "builtin-action-jump",
        "category": "动作姿态",
        "title": "起跳蓄力（跳跃帧）",
        "content": "猫武士下蹲蓄力，双膝弯曲，手臂向后摆动，尾巴上翘保持平衡，视线向上，准备起跳",
        "note": "跳跃是「蓄力-滞空-落地」三帧里最容易被画错的一帧："
                "必须明确「下蹲 + 手臂后摆」，否则模型会画成站姿。",
        "builtin": True,
    },
    # ---------------- 画风补充 ----------------
    {
        "id": "builtin-style-warm",
        "category": "画风补充",
        "title": "暖色调补充（可接任意画风后）",
        "content": "以暖橙与琥珀色为主色调，阴影偏冷紫，高光偏奶油白，整体明度高、对比适中",
        "note": "画风选择器管「画什么风格」，这条补充管「用什么颜色」。"
                "接在主体描述后面即可，不要放在最前面（会顶掉画风词）。",
        "builtin": True,
    },
    {
        "id": "builtin-style-outline",
        "category": "画风补充",
        "title": "粗描边强调（图标 / Q 版适用）",
        "content": "所有主体使用统一宽度的深色粗描边，内部平涂无渐变，阴影只用单级硬边",
        "note": "描边统一是「看起来是一套素材」的关键。不同图描边粗细不一致，"
                "放在一起立刻露馅。",
        "builtin": True,
    },
]


def _load_raw() -> Dict[str, Any]:
    ensure_dirs()
    if PROMPT_EXAMPLES_PATH.exists():
        try:
            data = parse_bounded(PROMPT_EXAMPLES_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("examples"), list):
                return data
        except Exception:
            pass
    return {"version": 1, "examples": []}


def _save_raw(data: Dict[str, Any]) -> None:
    ensure_dirs()
    PROMPT_EXAMPLES_PATH.write_text(dumps_bounded(data), encoding="utf-8")


def load_examples() -> List[Dict[str, Any]]:
    """读取示例库；首次运行/文件损坏时用内置示例播种。

    内置示例按 id 对齐：用户改过内置示例的内容就保留用户的版本（不覆盖），
    只补缺失的。这样「恢复默认」和「我的修改」可以共存。
    """
    data = _load_raw()
    existing = {e.get("id"): e for e in data.get("examples", []) if isinstance(e, dict)}
    changed = False
    for seed in _SEED:
        if seed["id"] not in existing:
            existing[seed["id"]] = dict(seed)
            changed = True
    examples = list(existing.values())
    # 稳定排序：分类顺序 → 内置优先 → 创建时间
    cat_order = {c: i for i, c in enumerate(CATEGORIES)}
    examples.sort(key=lambda e: (
        cat_order.get(e.get("category", ""), 99),
        0 if e.get("builtin") else 1,
        e.get("createdAt") or 0,
        e.get("title", ""),
    ))
    if changed:
        _save_raw({"version": 1, "examples": examples})
    return [e for e in examples if not e.get("hidden")]


def upsert_example(payload: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_raw()
    examples = {e.get("id"): e for e in data.get("examples", []) if isinstance(e, dict)}
    now = time.time()
    eid = str(payload.get("id") or "").strip()
    if eid and eid in examples:
        cur = examples[eid]
        for k in ("category", "title", "content", "note"):
            if k in payload and isinstance(payload[k], str):
                cur[k] = payload[k].strip()
        cur["updatedAt"] = now
        cur.pop("hidden", None)
        item = cur
    else:
        eid = eid or f"u-{uuid.uuid4().hex[:10]}"
        item = {
            "id": eid,
            "category": str(payload.get("category") or CATEGORIES[0]),
            "title": str(payload.get("title") or "未命名示例").strip(),
            "content": str(payload.get("content") or "").strip(),
            "note": str(payload.get("note") or "").strip(),
            "builtin": False,
            "createdAt": now,
            "updatedAt": now,
        }
        examples[eid] = item
    data["examples"] = list(examples.values())
    _save_raw(data)
    return item


def delete_example(eid: str) -> bool:
    """软删除：内置示例可恢复，用户示例也留档（hidden=true）。"""
    data = _load_raw()
    hit = False
    for e in data.get("examples", []):
        if e.get("id") == eid:
            e["hidden"] = True
            hit = True
    if hit:
        _save_raw(data)
    return hit


def reset_examples() -> List[Dict[str, Any]]:
    """恢复内置示例（只清掉用户对内置条目的改动与隐藏，保留用户自建条目）。"""
    data = _load_raw()
    user_items = [e for e in data.get("examples", [])
                  if isinstance(e, dict) and not e.get("builtin")]
    data["examples"] = user_items + [dict(s) for s in _SEED]
    _save_raw(data)
    return load_examples()
