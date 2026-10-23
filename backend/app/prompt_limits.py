"""提示词长度上限：前后端共用的一套口径。

为什么需要单独一块：管线对文本有硬上限（不传时 Qwen 默认 512 token），超出会被
**静默截断**——用户以为写了，模型只看到前半段。要么把上限告诉用户、要么让用户
在毫不知情的情况下丢一半内容，没有第三种选择。

口径：
- 中文约 1.3 token/字（保守上限，实际通常更低），另留 64 token 给特殊符号与模板句；
- 生成卡：预算 = 当前「提示词质量」档位（seqLen，自动=1920），**减去**画风词与
  像素契约的固定开销——这两段是 build_prompt 拼进去的，同样占 token，不扣就会
  出现「输入框还有 100 字，但实际已经被截」；
- 四视图角色描述：视角指令本体约 920 字 + 透明声明约 110 字，剩下才是描述的位置。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

CHARS_PER_TOKEN = 1.3
OVERHEAD_TOKENS = 64
SEQ_LEN_MAX = 1920
# 四视图的「角色描述」上限：920（指令本体）+ 380 + 110（透明声明）= 1410 字
# ≈ 1897 token，正好落在 1920 以内。比原来的 240 宽松，也不至于挤掉守恒条款。
CHARACTER_MAX = 380
# 行走动作（Wan/umT5）：提示词模板本身约 60 字，226 token 预算下留足余量
WALK_ACTION_MAX = 40


def chars_for_tokens(tokens: Optional[int]) -> int:
    """token 预算换算成中文字符预算（含 64 token 余量）。"""
    t = int(tokens or SEQ_LEN_MAX)
    return max(120, int((t - OVERHEAD_TOKENS) / CHARS_PER_TOKEN))


def fit_seq_len(text: str, requested: Optional[int] = None, *,
                limit: int = SEQ_LEN_MAX, floor: int = 512) -> int:
    """把一段最终提示词换算成需要的文本序列长度（向 128 取整）。

    `requested` 为空 = 「自动」：按文本实际长度估算，不再沿用管线默认 512。
    512 只装得下约 344 字（含风格词与像素契约），而生成卡默认就要 200+ 字
    固定开销——「自动」档如果等于 512，用户写 200 字就会被截掉一半。
    """
    est = int(len(text or "") * CHARS_PER_TOKEN) + OVERHEAD_TOKENS
    est = (est + 127) // 128 * 128
    return max(int(requested or 0), floor, min(limit, est))


def style_fixed_chars(style_id: str, *, pixel: bool = False,
                      grid=None, colors: int = 16,
                      background: str = "gray", material: str = "solid") -> int:
    """画风词 + 背景句 + 像素契约占掉的字符数（用探针正文量增量）。

    不能只量空提示词：`build_prompt` 的背景句与像素契约是有正文时才追加的，
    用空串会低估，用户就会在「还有余量」的假象下被截断。
    """
    from .styles import build_prompt

    probe = "__probe__"
    try:
        full = build_prompt(probe, style_id, pixel=pixel, grid=grid, colors=colors,
                            background=background, material=material)["prompt"]
    except Exception:
        return 0
    return max(0, len(full) - len(probe))


def snapshot() -> Dict[str, Any]:
    """一次给全：常量 + 每个画风的固定开销（前端本地算，不用每次都请求）。"""
    from .styles import list_styles

    styles: Dict[str, Dict[str, int]] = {}
    for st in list_styles():
        sid = str(st.get("id") or "")
        if not sid:
            continue
        styles[sid] = {
            "plain": style_fixed_chars(sid, pixel=False),
            "pixel": style_fixed_chars(sid, pixel=True),
        }
    return {
        "charsPerToken": CHARS_PER_TOKEN,
        "overheadTokens": OVERHEAD_TOKENS,
        "seqLenMax": SEQ_LEN_MAX,
        "seqLenOptions": [512, 768, 1024, 1536],
        "styles": styles,
        "character": {
            "maxChars": CHARACTER_MAX,
            "note": "四视图只把这段描述当「辨认清单」：用于认出图里的佩剑、包、护具，"
                    "不作为增补依据（图里没有的写了也不画）。超出部分读不到。",
        },
        "walkAction": {
            "maxChars": WALK_ACTION_MAX,
            "note": "动作描述会拼进 Wan 的视频提示词模板，模板本身已占掉大半预算。",
        },
    }
