"""去除局部：擦掉图里的一块（水印 / 招牌文字 / 电线 / 路人），用周围内容补上。

和「局部重绘」共用蒙版与贴回逻辑，区别在**意图**：重绘是「这块要变成什么」，
去除是「这块的东西要消失」。所以这里没有必须填的提示词，四条路都可以走：

1. **扩散修复（telea）** —— OpenCV 的 Telea 快速行进法：从洞边界向内扩散像素。
   水印、划痕、细线、纯色或渐变背景上几乎无痕，毫秒级，不跑模型。
   背景有噪点/纹理时会显得「太干净」（涂抹感），这是它的天性，不是 bug。
2. **纹理平移（offset）** —— 先在选区周围找**最相似的一次平移**（自相似性），
   再把平移来的像素搬进洞里。砖墙、木纹、金属板、条纹这类重复纹理用它，
   保住纹理而不是抹平。
3. **镜像（mirror）** —— 以最近的包围盒边为镜面反射周围内容。左右/上下对称、
   大色块、单侧延展的场景最自然。
4. **AI 补全（ai）** —— 周围没有可借的内容（去掉人物、大物体、复杂结构）时才需要，
   走 Qwen-Image-2.1 重画，几十秒。

三条非 AI 路线都只在**有效蒙版内**写入像素：`expand` 是刻意的——水印边缘的抗锯齿
像素比实际笔画宽 1~3px，只按用户涂的形状抠会留下一圈残影。

为什么不用 numpy 硬写一套 PatchMatch：那是「找张量场 + 多轮传播」的迭代算法，
纯 Python 在 512² 上要几十秒，而它的收益（大洞的纹理连续性）只有 AI 那条路能稳定
超过。这里用「找单一最优平移」代替：对重复纹理效果接近，向量化后 10ms 级。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .. import progress as progress_mod
from . import inpaint as inpaint_mod
from .palette import extract_palette  # noqa: F401  (保留给后续色板实验)

METHODS = ("telea", "offset", "mirror", "ai")
# 每种方式的「什么时候用」，随接口返回给界面显示——方法选择本身就是产品知识，
# 不该只写在代码注释里。
METHOD_INFO = {
    "telea": {
        "label": "扩散修复",
        "hint": "水印 / 划痕 / 细线 / 纯色渐变底。毫秒级，不跑模型",
        "needsModel": False,
    },
    "offset": {
        "label": "纹理平移",
        "hint": "背景有重复纹理（砖、木纹、金属板、条纹）时更自然",
        "needsModel": False,
    },
    "mirror": {
        "label": "镜像",
        "hint": "左右 / 上下对称、大色块、单侧延展的场景",
        "needsModel": False,
    },
    "ai": {
        "label": "AI 补全",
        "hint": "周围没有可借的内容（去掉人物 / 大物体 / 复杂结构）才需要，几十秒",
        "needsModel": True,
    },
}

# AI 方式的默认指令。
#
# 注意这不是「让模型去擦掉东西」——实测那样做会失败：Qwen-Image-2.1 没有蒙版输入，
# 它只看到「一条刺眼的白条」，就被诱导去重画整块，出来是一块比周围更暗的方块加
# 一圈亮边（2026-10-25 真机对比图）。所以 AI 这条路改成**两段式**：
#   先用扩散修复把洞补平（模型看到的已经是没有水印的图）→ 再让模型把这一块「画自然」。
# 模型不需要理解「标记区域在哪」，它只要把一块略微发糊的补丁收成和周围一致的质感。
# 这也解释了为什么这段条款写的是「延续/一致」而不是「去掉」。
DEFAULT_CONSTRAINT = (
    "标记区域刚被补平，还带着一点模糊和补丁感：请把它画得和周围完全连成一体——"
    "延续周围的纹理走向、噪点颗粒、明暗与颜色渐变，让补过的痕迹看不出来。"
    "不要留下残影或半透明痕迹，不要新增任何物体、文字或装饰，"
    "也不要改变标记区域以外的画面。"
)
DEFAULT_NEGATIVE = (
    "残留轮廓, 半透明水印, 文字, 鬼影, 重复的物体, 模糊块, 明显色块拼接感, 接缝"
)


def build_prompt(instruction: str = "", constraint: str = "") -> Dict[str, Any]:
    """组装送给模型的整段提示词（复用重绘那套三段式，默认条款换成「去除」）。

    `defaultConstraint` 要改成去除场景的那段：重绘接口返回的是「只重画标记区域」，
    界面上「恢复默认条款」会显示成错的文字。
    """
    info = inpaint_mod.build_prompt(instruction, constraint or DEFAULT_CONSTRAINT, 0)
    info["defaultConstraint"] = DEFAULT_CONSTRAINT
    return info


# --------------------------------------------------------------------------- #
# 非 AI 填充：三种手法共用一个入口
# --------------------------------------------------------------------------- #
def _shift_or(m: np.ndarray, dy: int, dx: int) -> np.ndarray:
    """把布尔图按 (dy,dx) 平移后并回自身（越界部分视为 False）。"""
    out = m.copy()
    h, w = m.shape
    ys0, ys1 = max(0, dy), min(h, h + dy)
    xs0, xs1 = max(0, dx), min(w, w + dx)
    if ys1 <= ys0 or xs1 <= xs0:
        return out
    out[ys0:ys1, xs0:xs1] |= m[ys0 - dy:ys1 - dy, xs0 - dx:xs1 - dx]
    return out


def _dilate(m: np.ndarray, r: int) -> np.ndarray:
    """方形核膨胀（半径 r），用「平方倍增」代替 PIL 的 MaxFilter。

    为什么要自己写：`MaxFilter(2r+1)` 是逐像素的暴力核，r=46 时在 512×384 上要
    17 亿次比较（实测 ~1 秒），而这里是整张「找环 + 外扩」里唯一的大开销。
    倍增法每轮把半径翻倍，log2(r) 轮就够，每轮只有 4 次数组并运算。
    """
    r = max(0, int(r))
    if r <= 0:
        return m

    def square(x: np.ndarray, s: int) -> np.ndarray:
        """半径 s 的实心方阵膨胀：先横向再纵向（方核可分离）。"""
        y = _shift_or(_shift_or(x, 0, s), 0, -s)      # 左右各 s
        return _shift_or(_shift_or(y, s, 0), -s, 0)   # 上下各 s

    # 逐步累积：1,2,4,… 每次都在**已膨胀**的图上再叠，半径才是连续覆盖的
    # （按二进制位各做一次是错的：只做 ±32 得到的是 3×3 个点阵，中间全是洞）
    out, done, step = m, 0, 1
    while done + step <= r:
        out = square(out, step)
        done += step
        step *= 2
    if done < r:
        out = square(out, r - done)
    return out


def _hole_mask(mask: Image.Image, expand: int) -> np.ndarray:
    """用户蒙版 + 外扩 → 布尔洞（True = 要填）。"""
    m = np.asarray(inpaint_mod.load_mask(mask)) > 127
    return _dilate(m, max(0, int(expand)))


def _neighbour_mean(a: np.ndarray, hole: np.ndarray, iters: int) -> np.ndarray:
    """雅可比迭代：洞内像素反复取四邻域均值（调和插值）。

    只在洞内写，洞外一个像素都不动——所以调用方可以直接把结果当作成品。
    """
    out = a.astype(np.float32).copy()
    if not hole.any() or iters <= 0:
        return out
    h, w = hole.shape
    for _ in range(max(1, int(iters))):
        p = np.pad(out, ((1, 1), (1, 1), (0, 0)), mode="edge")
        nb = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
        out[hole] = nb[hole]
    del h, w
    return out


def fill_diffuse(rgb: np.ndarray, hole: np.ndarray, iters: int = 60) -> np.ndarray:
    """多级调和扩散（无 cv2 时的兜底，也用于补上其它手法的死角）。

    直接在原分辨率上迭代，收敛慢到看不出效果（200px 的洞要上千轮）；所以把图
    逐级缩小（1/8 → 1/4 → 1/2 → 1），在粗分辨率上先把洞填平，再逐级细化——
    低频一步到位，高频靠最后几轮打磨。实测 512² 图上的水印条 < 0.2s。
    """
    H, W = hole.shape
    cur = rgb.astype(np.float32)
    for f in (8, 4, 2):
        if f > 1 and min(H, W) // f < 8:
            continue
        h2, w2 = max(8, H // f), max(8, W // f)
        small = np.asarray(Image.fromarray(rgb.astype(np.uint8)).resize(
            (w2, h2), Image.BILINEAR), dtype=np.float32)
        hsmall = np.asarray(Image.fromarray((hole.astype(np.uint8) * 255)).resize(
            (w2, h2), Image.BILINEAR)) > 127
        filled = _neighbour_mean(small, hsmall, iters)
        cur = np.asarray(Image.fromarray(np.clip(filled, 0, 255).astype(np.uint8))
                         .resize((W, H), Image.BILINEAR), dtype=np.float32)
    out = rgb.astype(np.float32).copy()
    out[hole] = cur[hole]
    out = _neighbour_mean(out, hole, iters)
    return np.clip(out, 0, 255).astype(np.uint8)


def fill_telea(rgb: np.ndarray, hole: np.ndarray, radius: int = 5
               ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """扩散修复：优先 OpenCV 的 Telea（快且质量稳定），没有 cv2 就用多级调和扩散。"""
    if hole.any():
        try:
            import cv2
            m = (hole.astype(np.uint8)) * 255
            out = cv2.inpaint(rgb, m, max(1, int(radius)), cv2.INPAINT_TELEA)
            return out, {"engine": "cv2-telea"}
        except Exception:
            pass
    return fill_diffuse(rgb, hole), {"engine": "harmonic"}


def _ring(hole: np.ndarray, band: int) -> np.ndarray:
    """洞外围一圈已知像素（用来找自相似平移 & 判断纹理是否成立）。"""
    return _dilate(hole, max(1, int(band))) & ~hole


def best_offset(rgb: np.ndarray, hole: np.ndarray, ring: np.ndarray,
                search: int = 64, step: int = 4, sample_cap: int = 2000
                ) -> Optional[Tuple[float, int, int]]:
    """在选区周围找「最相似的一次平移」：(score, dx, dy)。

    三条约束，都是实测踩出来的：

    1. **比较的两端都必须是干净像素**。环上像素平移后可能落回洞里——洞里有水印，
       拿干净像素去比水印，恰好把「我们最想要的那个平移」判成最差。
    2. **平移必须够大**，至少能把洞的 60% 挪开。否则最优解永远是 ±4px 的邻居：
       平滑背景上邻域像素几乎相同，分数趋近 0，搬进来的其实就是紧贴洞边那一圈，
       洞深处越搬越糊（实测与真值差 96/255，比扩散修复差 20 倍）。
    3. **先粗后细**。搜索窗要覆盖「洞的尺寸 + 余量」，水印条那种 280px 宽的洞
       搜索窗要 300px，4px 步长 = 2.2 万个候选 × 4000 个采样点，实测 2.3 秒。
       改成粗扫（步长 = 窗/16）再在最优附近细扫，候选降到约 500 个，30ms 出结果。

    分数上加了「离得越远扣分越多」的轻微惩罚：重复纹理里有很多等价平移，
    选最近的那个更容易和周围光照、角度对得上。
    """
    ys, xs = np.nonzero(ring)
    if len(ys) < 40:
        return None
    # 采样上限：再多只是线性变慢，最优平移不会因此改变。
    # 每个候选都是一轮 numpy 小数组运算（~30µs 固定开销），所以候选数比采样数
    # 更影响耗时：粗扫 600 点、细扫 1200 点，够用且不会把单次调用拖到秒级。
    n_sample = int(sample_cap)
    sel = np.arange(0, len(ys), max(1, len(ys) // n_sample))
    ys, xs = ys[sel], xs[sel]
    H, W = hole.shape
    hy, hx = np.nonzero(hole)
    hole_h = int(hy.max() - hy.min()) + 1 if len(hy) else 1
    hole_w = int(hx.max() - hx.min()) + 1 if len(hx) else 1
    min_dy, min_dx = 0.6 * hole_h, 0.6 * hole_w
    # 只取亮度做匹配：纹理的「结构」比颜色更能代表可搬性，且快 3 倍
    lum = rgb.astype(np.float32).mean(axis=2)
    base = lum[ys, xs]
    # 常量预取到局部：内层循环每轮都要用
    hf = hole.ravel()
    H_, W_ = H, W

    def scan(step_: int, cx: int = 0, cy: int = 0, radius: Optional[int] = None
             ) -> Optional[Tuple[float, int, int]]:
        best: Optional[Tuple[float, int, int]] = None
        span = range(-search, search + 1, step_) if radius is None else \
            range(-radius, radius + 1, step_)
        for oy in span:
            for ox in span:
                dx, dy = cx + ox, cy + oy
                if abs(dx) < min_dx and abs(dy) < min_dy:
                    continue
                sy, sx = ys + dy, xs + dx
                ok = (sy >= 0) & (sy < H_) & (sx >= 0) & (sx < W_)
                if ok.any():
                    idx = np.nonzero(ok)[0]
                    # 源像素必须干净（第 1 条）；numpy 不裁边，越界必须先筛掉
                    clean = ~hf[sy[idx] * W_ + sx[idx]]
                    ok = np.zeros_like(ok)
                    ok[idx[clean]] = True
                if ok.sum() < len(ys) * 0.5:
                    continue
                d = base[ok] - lum[sy[ok], sx[ok]]
                score = float((d * d).mean()) + 0.15 * (abs(dx) + abs(dy))
                if best is None or score < best[0]:
                    best = (score, int(dx), int(dy))
        return best

    # 粗扫步长随搜索窗放大：窗越宽越不该用细步长去铺满（那是平方级开销）
    coarse_step = max(step, search // 16)
    coarse = scan(coarse_step)
    if coarse is None:
        return None
    fine = scan(max(1, coarse_step // 4), cx=coarse[1], cy=coarse[2],
                radius=max(coarse_step * 2, 12))
    return fine if (fine and fine[0] <= coarse[0]) else coarse


def fill_offset(rgb: np.ndarray, hole: np.ndarray, *,
                search: int = 0, passes: int = 8
                ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """纹理平移：把「洞 + 最优平移」位置的像素搬进洞里，搬不到的用扩散补。"""
    if not search:
        # 搜索窗至少能覆盖「洞的尺寸 + 一圈余量」：窗比洞小就等于禁止大平移，
        # 而大平移正是这个方法唯一有价值的解
        ys, xs = np.nonzero(hole)
        ext = max(int(ys.max() - ys.min()) + 1, int(xs.max() - xs.min()) + 1) if len(ys) else 32
        # 上限 256：再大的洞就算找得到平移，搬来的内容也跟周围对不上，
        # 该由用户改用「AI 补全」。候选数不设上限会让这一步变成数秒级。
        search = int(min(max(48, ext + 24), 256))
    ring = _ring(hole, max(6, search // 6))
    found = best_offset(rgb, hole, ring, search=search, sample_cap=800)
    meta: Dict[str, Any] = {"engine": "offset"}
    if found is None:
        # 找不到可信平移时退回扩散修复（比纯调和扩散的观感好），并留痕
        out, eng = fill_telea(rgb, hole)
        meta["offset"] = None
        meta["engine"] = f"fallback-{eng.get('engine')}"
        meta["reason"] = "选区里没有可参照的重复纹理"
        return out, meta

    score, dx, dy = found
    H, W = hole.shape
    filled = rgb.copy()
    todo = hole.copy()
    for k in range(1, max(1, int(passes)) + 1):
        ys, xs = np.nonzero(todo)
        if not len(ys):
            break
        sy, sx = ys + k * dy, xs + k * dx
        ok = (sy >= 0) & (sy < H) & (sx >= 0) & (sx < W)
        take = np.zeros(len(ys), dtype=bool)
        if ok.any():
            # 源像素必须本身是干净的：否则会把刚填进去的内容再搬一遍（越搬越糊）
            clean = ~hole[sy[ok], sx[ok]]
            idx = np.nonzero(ok)[0][clean]
            take[idx] = True
            filled[ys[take], xs[take]] = rgb[sy[take], sx[take]]
            todo[ys[take], xs[take]] = False
    left = int(todo.sum())
    if left:
        filled = fill_diffuse(filled, todo, iters=40)
    meta.update({"offset": [dx, dy], "score": round(score, 2),
                 "diffuseFilled": left})
    return filled, meta


def fill_mirror(rgb: np.ndarray, hole: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
    """镜像：以最近的包围盒边为镜面，把外侧内容翻进来。"""
    ys, xs = np.nonzero(hole)
    if not len(ys):
        return rgb.copy(), {"engine": "mirror"}
    y0, y1, x0, x1 = int(ys.min()), int(ys.max()), int(xs.min()), int(xs.max())
    H, W = hole.shape
    dl, dr = xs - x0, x1 - xs
    dt, db = ys - y0, y1 - ys
    near_left = (dl <= dr) & (dl <= dt) & (dl <= db)
    near_right = (~near_left) & (dr <= dt) & (dr <= db)
    near_top = (~near_left) & (~near_right) & (dt <= db)

    sy = ys.copy()
    sx = xs.copy()
    sx[near_left] = np.maximum(0, 2 * x0 - xs[near_left] - 1)
    sx[near_right] = np.minimum(W - 1, 2 * x1 - xs[near_right] + 1)
    sy[near_top] = np.maximum(0, 2 * y0 - ys[near_top] - 1)
    bottom = ~(near_left | near_right | near_top)
    sy[bottom] = np.minimum(H - 1, 2 * y1 - ys[bottom] + 1)

    # 反射点要落在洞外，且翻转后仍然在图内；其余交给扩散
    ok = ~hole[sy, sx]
    out = rgb.copy()
    out[ys[ok], xs[ok]] = rgb[sy[ok], sx[ok]]
    left = int((~ok).sum())
    if left:
        todo = np.zeros_like(hole)
        todo[ys[~ok], xs[~ok]] = True
        out = fill_diffuse(out, todo, iters=40)
    return out, {"engine": "mirror", "diffuseFilled": left}


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
async def run_erase(
    src: Image.Image,
    mask: Image.Image,
    *,
    method: str = "telea",
    expand: int = 6,
    feather_px: int = 2,
    hard: bool = False,
    match_color: bool = True,
    match_strength: float = 0.6,
    palette: str = "none",
    palette_colors: int = 48,
    instruction: str = "",
    constraint: str = "",
    negative: str = "",
    ai_mode: str = "crop",
    context: float = 0.35,
    steps: Optional[int] = None,
    seed: Optional[int] = None,
    max_seq_len: Optional[int] = None,
    provider_cfg: Optional[Dict[str, Any]] = None,
    task_id: Optional[str] = None,
    generator: Any = None,
) -> Tuple[Image.Image, Dict[str, Any]]:
    """擦掉蒙版里的内容。

    `expand` 是**有效蒙版**的外扩量：水印边缘的抗锯齿比笔画宽 1~3px，
    只按用户涂的形状抠会留一圈残影，所以填充和贴回都用外扩后的蒙版。
    因此「选区外像素不变」这个承诺的范围是「外扩后的蒙版之外」。
    """
    t0 = time.time()
    src = src.convert("RGBA")
    m = inpaint_mod.load_mask(mask)
    if m.size != src.size:
        raise ValueError(
            f"蒙版尺寸 {m.size[0]}×{m.size[1]} 与源图 {src.width}×{src.height} "
            f"不一致：换图后要重新涂抹")
    if inpaint_mod.mask_bbox(m) is None:
        raise ValueError("蒙版是空的：先用笔刷涂出要去掉的东西")

    method = method if method in METHODS else "telea"
    expand = max(0, min(int(expand), 64))
    eff = Image.fromarray((_dilate(np.asarray(m) > 127, expand) * 255).astype(np.uint8), "L")
    hole = np.asarray(eff) > 127
    if not hole.any():
        raise ValueError("蒙版是空的：先用笔刷涂出要去掉的东西")

    progress_mod.report(task_id, 0, 2, "准备…")
    meta: Dict[str, Any] = {
        "method": method,
        "methodLabel": METHOD_INFO[method]["label"],
        "expand": expand,
        "feather": int(feather_px),
        "hard": bool(hard),
        "matchColor": bool(match_color) and not hard,
        "matchStrength": match_strength,
        "palette": palette,
        "holePixels": int(hole.sum()),
        "coverage": round(float(hole.mean()), 4),
        "ai": None,
        "warnings": [],
    }

    if method == "ai":
        prompt = build_prompt(instruction, constraint)
        # 第一段：确定性补平。这一份只作为模型的输入，不进最终结果。
        rgb = np.asarray(src.convert("RGB"), dtype=np.uint8)
        flat, fm = fill_telea(rgb, hole)
        base = np.asarray(src.convert("RGBA")).copy()
        base[..., :3] = flat
        pre_img = Image.fromarray(base, "RGBA")
        meta["prefill"] = {"engine": fm.get("engine")}
        progress_mod.report(task_id, 1, 2, "AI 收尾中…")
        # 第二段：让模型把这块收成与周围一致的质感（蒙版贴回由 run_inpaint 负责）
        out, ai_meta = await inpaint_mod.run_inpaint(
            pre_img, eff,
            instruction=instruction,
            constraint=constraint or DEFAULT_CONSTRAINT,
            negative=negative,
            mode=ai_mode, context=context,
            feather_px=feather_px, expand=0,
            hard=hard, match_color=match_color,
            match_strength=match_strength,
            palette=palette, palette_colors=palette_colors,
            steps=steps, seed=seed, max_seq_len=max_seq_len,
            provider_cfg=provider_cfg, task_id=task_id,
            generate=generator,
        )
        meta["ai"] = {k: ai_meta.get(k) for k in
                      ("mode", "targetSize", "cropSize", "steps", "model",
                       "seqLen", "prompt") if k in ai_meta}
        meta["prompt"] = ai_meta.get("prompt")
        meta["constraint"] = ai_meta.get("constraint")
        meta["constraintCustom"] = ai_meta.get("constraintCustom")
        meta["negative"] = ai_meta.get("negative")
        meta["seconds"] = round(time.time() - t0, 1)
        for w in (ai_meta.get("warnings") or []):
            if w not in meta["warnings"]:
                meta["warnings"].append(w)
        progress_mod.report(task_id, 2, 2, "完成")
        return out, meta

    # ---- 非 AI 三条路线 ----
    rgb = np.asarray(src.convert("RGB"), dtype=np.uint8)
    progress_mod.check_cancel(task_id)
    if method == "offset":
        filled, fm = fill_offset(rgb, hole)
    elif method == "mirror":
        filled, fm = fill_mirror(rgb, hole)
    else:
        filled, fm = fill_telea(rgb, hole)
    meta.update({k: v for k, v in fm.items() if k != "engine"})
    meta["engine"] = fm.get("engine")

    progress_mod.report(task_id, 1, 2, "贴回…")
    gen = Image.fromarray(filled, "RGB").convert("RGBA")
    # 贴回仍用同一套「羽化向内 + 接缝带校正」：填充结果与原图的接缝在这里收掉
    out = inpaint_mod.composite(
        src, gen, eff, feather_px=feather_px, hard=hard,
        match_color=match_color and not hard, match_strength=match_strength)

    if palette == "source":
        region = Image.fromarray((hole.astype(np.uint8) * 255), "L")
        out2, pal, snapped = inpaint_mod.snap_palette(
            out, src, region, colors=palette_colors, tol=30)
        out = out2
        meta["paletteColors"] = len(pal)
        meta["paletteSnapped"] = snapped
    else:
        meta["paletteColors"] = 0
        meta["paletteSnapped"] = 0.0

    # ---- 质量提示：非 AI 方式有明确的失效边界，得说清楚 ----
    cov = meta["coverage"]
    # 15% 是个经验阈值：水印条、字幕、招牌这类常见目标都在 3~10%，
    # 一条覆盖半幅图的大洞才是「该换 AI」的信号。
    if cov > 0.15:
        meta["warnings"].append(
            f"选区占画面 {cov * 100:.0f}%，非 AI 方式在这种面积上容易看出涂抹感；"
            "想要「填出合理的东西」请改用「AI 补全」")
    if method == "offset" and meta.get("offset") is None:
        meta["warnings"].append("没找到明显的重复纹理，已退回扩散修复")
    elif method == "offset" and (meta.get("score") or 0) > 900:
        meta["warnings"].append(
            "平移匹配分数偏高（纹理不够重复），结果可能不如「扩散修复」稳定")
    if method == "mirror" and (meta.get("diffuseFilled") or 0) > (cov * src.width * src.height * 0.3):
        meta["warnings"].append("大部分像素是反射不到的，已用扩散补足：边界结构可能变软")

    meta["seconds"] = round(time.time() - t0, 2)
    progress_mod.report(task_id, 2, 2, "完成")
    return out, meta


def methods_info() -> List[Dict[str, Any]]:
    """给界面用的方式清单（含「什么时候用」的说明）。"""
    return [{"id": k, **v} for k, v in METHOD_INFO.items()]
