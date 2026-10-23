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

CATEGORIES = ["角色描述", "场景描述", "道具图标", "动作姿态", "画风补充",
              "行走视频"]

# 行走视频（八方向行走节点）的完整提示词示例。
# 与文生图示例的区别：这里是**整段直接喂给视频底座**的提示词，用户拿来即可改，
# 不存在程序拼接。写法要点全部来自实测踩坑（见各条 note）：
# 1) 运动与静止必须**分属不同主语**：角色的动作说「动」，取景框/背景说「静」。
#    绝不给角色写「不动」——视频底座会把「原地」理解成「定格」。
# 2) **不要用强调词**（明确地/明显/大幅度）：实测会把自然步态推成高抬腿猛踏步，
#    出来是「静止→猛抬腿→静止」的突发放射，反而不像走。
# 3) **手臂摆动要写条件句**：双手持枪/扶武器的角色手臂被占用，无条件写
#    「手臂与腿反向摆动」与参考图矛盾，模型要么忽略、要么做怪异补偿晃动。
# 4) 结尾必须单独锚定「镜头不动、角色居中、体型不变」——这是静止的那一半。
_WALK_SEED: List[Dict[str, Any]] = [
    {
        "id": "builtin-walk-walk",
        "category": "行走视频",
        "title": "原地行走（通用起点）",
        "content": "角色在原地行走：双腿以自然步幅持续交替迈步，膝盖随步伐屈伸，"
                   "双脚依次离地、前摆、落地；身体随重心轻微上下颠动；手臂随步伐"
                   "自然摆动，双手持枪或扶握武器时双臂与武器保持稳定；头发和衣摆随"
                   "步伐轻微晃动。动作循环往复，一只脚落地时另一只脚抬起，步态平稳"
                   "连贯。保持取景框与背景完全静止，角色始终位于画面中心、体型大小"
                   "不变，不向画面任何方向平移、不旋转身体、镜头不动。",
        "note": "最通用的起点。四段结构：腿怎么走 → 身体怎么起伏 → 手臂/摆动物 → "
                "镜头与构图不动。改动作只改第一句（原地行走 → 原地奔跑），后面三 "
                "段是骨架，别动。",
        "builtin": True,
    },
    {
        "id": "builtin-walk-rifle",
        "category": "行走视频",
        "title": "举枪行走（战术行进）",
        "content": "角色在原地举枪行走：上身与武器保持稳定不晃动，双手握持的步枪"
                   "始终维持在原来的位置与角度；双腿以自然步幅持续交替迈步，膝盖"
                   "随步伐屈伸，双脚依次离地、前摆、落地，步态平稳有节奏；身体随"
                   "重心轻微上下颠动；肩带、背心系绳和裤脚随步伐轻微晃动。动作循环"
                   "往复，一只脚落地时另一只脚抬起。保持取景框与背景完全静止，角色"
                   "始终位于画面中心、体型大小不变，不向画面任何方向平移、不旋转"
                   "身体、镜头不动。",
        "note": "持械行进的关键：把「上身与武器稳定」放在**第一句**。武器是参考图里 "
                "最显眼的部件，不明确让它稳，模型会让它跟着腿一起晃，看起来像在跳 "
                "舞而不是行军。腿的描写保持「自然步幅」——写「高抬腿」会出正步。",
        "builtin": True,
    },
    {
        "id": "builtin-walk-run",
        "category": "行走视频",
        "title": "原地奔跑",
        "content": "角色在原地奔跑：双腿大幅度交替蹬地，膝盖明显屈伸，双脚用力蹬地"
                   "后腾空再落地；身体随重心上下颠动，上身略微前倾；手臂屈肘前后"
                   "快速摆动，手持物品时握紧不脱手；披风、围巾和头发随动作明显向后"
                   "扬起。动作循环往复、节奏快而连贯。保持取景框与背景完全静止，"
                   "角色始终位于画面中心、体型大小不变，不向画面任何方向平移、不旋转"
                   "身体、镜头不动。",
        "note": "奔跑才允许用「大幅度/明显」——慢走的克制词在这里是对的。手臂改为 "
                "「屈肘前后快速摆动」：奔跑时摆臂是主要动源，不写就只剩腿在动， "
                "看起来像在原地跳。",
        "builtin": True,
    },
    {
        "id": "builtin-walk-idle",
        "category": "行走视频",
        "title": "待机呼吸（微动）",
        "content": "角色静止站立待机：双脚不动，仅有极轻微的呼吸起伏——胸口随呼吸"
                   "缓慢起伏，肩膀微微起伏，头发和披风随极轻微的气流缓慢飘动；偶尔"
                   "眨眼一次。身体其余部分保持完全稳定。保持取景框与背景完全静止，"
                   "角色始终位于画面中心、体型大小不变，不旋转身体、镜头不动。",
        "note": "待机不是「完全不动」：完全不动视频底座会输出一张糊掉的静态图。给一 "
                "个极轻微的动源（呼吸 + 飘动），画面才是活的。",
        "builtin": True,
    },
    {
        "id": "builtin-walk-cautious",
        "category": "行走视频",
        "title": "蹑手蹑脚（潜行）",
        "content": "角色在原地蹑手蹑脚地潜行：放低重心、屈膝弓身，脚步轻而慢，"
                   "每一步都缓慢抬起、轻轻落地，几乎没有颠动；双臂微微张开保持"
                   "平衡，手持武器时枪口朝下；披风和衣摆几乎不动，只有极轻微的晃动。"
                   "动作循环往复，一步一顿、节奏很慢。保持取景框与背景完全静止，"
                   "角色始终位于画面中心、体型大小不变，不向画面任何方向平移、不旋转"
                   "身体、镜头不动。",
        "note": "「一步一顿、节奏很慢」是慢动作的节拍描述——比写「非常慢」有效，后者 "
                "只是程度词。潜行的对比在「脚步轻」与「身体其余稳定」。",
        "builtin": True,
    },
]

_SEED: List[Dict[str, Any]] = [
    # ---------------- 角色描述 ----------------
    {
        "id": "builtin-char-cat-knight",
        "category": "角色描述",
        "title": "猫武士（像素风经典题材）",
        "content": "灰白英短猫武士，红色短披风，左腰侧挎武士刀，皮质护腕，圆耳竖立，琥珀色眼睛，三头身比例，正面站立，表情专注",
        "note": "物种 + 主色 + 服装/道具 + 五官特征 + 体型比例 + 姿态。像素画信息量有限，"
                "每类特征给 1-2 个就够，堆太多会糊成一团。"
                "单侧配件一定要写清在哪一侧（左腰/右肩/右手）：只写「腰挎武士刀」时，"
                "模型会自己挑一边，后面推导侧视图/背视图时没有任何依据可守，"
                "配件最容易被搬到另一侧或者干脆消失。",
        "builtin": True,
    },
    {
        "id": "builtin-char-slime",
        "category": "角色描述",
        "title": "史莱姆（极简轮廓练习）",
        "content": "半透明蓝色史莱姆，圆润水滴形身体，两只黑色小圆眼，嘴角上扬，底部扁平贴地，左上角有高光，轮廓用同色系更深的蓝，不要粗描边",
        "note": "半透明/软体题材最容易翻车：写「半透明」时不要再要求粗描边——"
                "像素画里的通透感靠 2-3 级色阶（外缘亮、内部深、顶部高光）表达，"
                "粗描边会把果冻感压成一块实心色块。",
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
    # 旧版内置文案 → 新文案的迁移表（只迁移用户没改过的条目）。
    # 旧文案要求「深蓝描边」，与半透明主体的像素契约冲突，会让史莱姆变实心块。
    _LEGACY_CONTENT = {
        "builtin-char-slime": "半透明蓝色史莱姆，圆润水滴形身体，两只黑色小圆眼，"
                              "嘴角上扬，底部扁平贴地，高光在左上角，轮廓为深蓝描边",
    }
    for seed in _SEED + _WALK_SEED:
        cur = existing.get(seed["id"])
        if cur is None:
            existing[seed["id"]] = dict(seed)
            changed = True
        elif (cur.get("builtin")
              and seed["id"] in _LEGACY_CONTENT
              and cur.get("content") == _LEGACY_CONTENT[seed["id"]]):
            cur["content"] = seed["content"]
            cur["note"] = seed["note"]
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
    data["examples"] = user_items + [dict(s) for s in _SEED + _WALK_SEED]
    _save_raw(data)
    return load_examples()
