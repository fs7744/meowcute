"""四视图输出的一致性校验（纯 CPU、毫秒级、不占显存）。

为什么不用「主色直方图相似度」——两条实测反例（data/projects 下的真实产物）：

1. 同一角色、同一视角、纯抽卡的四张正视图，量化色集合的 Jaccard 就已经是
   0.33~1.00，换一个项目整体掉到 0.17~0.36。抗锯齿边缘每抽一次都不一样，
   把色集合撑大又随机填充。绝对阈值不可能同时适配两个项目。
2. 主色簇 EMD 稳一些，但**背视图天然比侧视图远**（实测 36~70 对 20~35）：
   背面看不到脸和胸前配色，分布形状必然变。拿「与正视图的距离」当判据，
   等于系统性惩罚背视图。

有效的判据是**色相存在性**，因为色相是视角无关的：同样的披风从正面转到
背面，面积变大（实测红占比 5.6%→10.4%），色相不变；而「披风整块丢了」是
断崖——同一批 6 张背视图里，红占比 20.5% 那一张披风还在（缩成围脖），
其余 5 张只有 5.1%~6.2%（披风没了）。所以该判的是「这个色还在不在」，
不是「分布像不像」。

三层判据：
- 强（可触发自动重试）：有色主色丢失。豁免「重心落在上半身的色」——脸和
  胸前纹样在背视图本来就没有，不豁免就会把正确的背视图判死。
- 弱（只提示）：无彩色丢失、新增配色、几何异常。
- 几何：剪影高比 / 主体面积比按视角分档。实测这批全部在区间内，所以它只
  拦灾难（换了体型、被裁掉半身、背景没抠干净），不参与细微漂移判断。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Sequence

import cv2
import numpy as np
from PIL import Image

HUE_BINS = 12          # 12 × 15° 的固定色相桶（跨图可比，不像 k-means 每次漂）
GRAY_BINS = 4          # 无彩色按明度 4 档
SAT_MIN = 80           # 饱和度够才算「有色」
DARK_MAX = 40          # s<SAM_MIN 且 v<DARK_MAX 视为描边，不参与统计
MIN_ALPHA = 8
BG_TOL = 26

@dataclass
class Thresholds:
    """判据阈值。全部按「占主体像素的比例」定，不用归一化到有色像素的比例。

    这个口径是踩出来的：若把色相占比归一化到「有色像素」，那么当角色大面积
    配色丢失时，剩余的有色像素（腰带、眼睛、金属件）会重新分配 100%，红色
    在归一化后仍能占到 50%——把真正的丢件洗白。改成相对主体像素后，灰猫的
    红披风丢失表现为 14%→0.1%，是断崖。

    实测标定（data/projects 两个项目、24 张真实产物）：
    - 红披风丢失：正 14.0% → 背 0.1%（ratio 0.007）
    - 红披风缩成围脖但仍在：正 14.0% → 背 8%（通过）
    - 步枪在背视图被身体挡住：正 9.0% → 背 2.7%（通过，不误拦）
    """
    ref_min: float = 0.030      # 正视图占主体 ≥3% 才算「识别色」
    lost_max: float = 0.015     # 视角图掉到 <1.5% 且不足半数 → 判丢件
    lost_ratio: float = 0.50    # 配合 lost_max：两条件同时满足才判丢件
    warn_ratio: float = 0.40    # 掉到不足 40% 但还有量 → 只提示
    top_zone: float = 0.35      # 色块重心在此以上 → 背视图豁免（脸/胸前）
    new_share: float = 0.12     # 新增配色阈值（占有色像素）
    new_ref_max: float = 0.02   # 且正视图几乎不存在


TH = Thresholds()

# 主体面积比 / 剪影高比的合理区间（按视角分档，取实测值两端外扩）
GEOM = {
    "side": {"area": (0.45, 1.00), "h": (0.86, 1.14)},
    "side_r": {"area": (0.45, 1.00), "h": (0.86, 1.14)},
    "back": {"area": (0.70, 1.25), "h": (0.86, 1.14)},
}


def _mask(img: Image.Image) -> tuple:
    """主体掩码。返回 (mask, 来源)。透明底优先用 alpha。"""
    arr = np.asarray(img.convert("RGBA"))
    a = arr[:, :, 3]
    if a.min() < MIN_ALPHA and float((a <= MIN_ALPHA).mean()) > 0.02:
        return a > MIN_ALPHA, "alpha"
    rgb = arr[:, :, :3].astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    qb = border // 16 * 16
    colors, counts = np.unique(qb, axis=0, return_counts=True)
    bg = colors[counts.argmax()] + 8
    return np.abs(rgb - bg).max(axis=2) > BG_TOL, "bgcolor"


def analyze(img: Image.Image, name: str = "") -> Dict[str, Any]:
    """单张图的一致性画像。"""
    img = img.convert("RGBA")
    mask, how = _mask(img)
    n = int(mask.sum())
    out: Dict[str, Any] = {"name": name, "mask": how, "pixels": n,
                           "trusted": True, "hue": np.zeros(HUE_BINS),
                           "gray": np.zeros(GRAY_BINS),
                           "hue_rgb": np.zeros((HUE_BINS, 3)),
                           "hue_y": np.full(HUE_BINS, -1.0)}
    if n < 500:
        out["trusted"] = False
        return out
    frac = n / float(mask.size)
    out["area"] = frac
    ys, xs = np.nonzero(mask)
    out["box_h"] = int(ys.max() - ys.min() + 1)
    out["box_w"] = int(xs.max() - xs.min() + 1)
    out["y0"], out["y1"] = int(ys.min()), int(ys.max())
    # 底色抠取失败（把大半个画布当主体）→ 颜色判据不可信，只留几何
    if how == "bgcolor" and frac > 0.72:
        out["trusted"] = False
        return out

    rgb = np.asarray(img.convert("RGB"))
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    h, s, v = hsv[:, :, 0].astype(np.int32), hsv[:, :, 1].astype(np.int32), \
        hsv[:, :, 2].astype(np.int32)
    edge = (s < SAT_MIN) & (v < DARK_MAX)
    body = mask & ~edge
    nb = int(body.sum())
    if nb < 400:
        out["trusted"] = False
        return out

    chroma = body & (s >= SAT_MIN)
    nc = int(chroma.sum())
    if nc:
        bins = np.clip(h[chroma] * HUE_BINS // 180, 0, HUE_BINS - 1)
        share = np.bincount(bins, minlength=HUE_BINS) / float(nc)
        out["hue"] = share
        cpx = rgb[chroma].astype(np.float32)
        # 每桶的平均 RGB：报告里直接给色号，比「粉红色」这种桶名直观
        for b in range(HUE_BINS):
            sel = bins == b
            if sel.any():
                out["hue_rgb"][b] = cpx[sel].mean(axis=0)
        # 每桶的垂直重心（0=主体顶部 1=底部），用于判定「上半身专属色」
        span = max(1, out["y1"] - out["y0"])
        yy = (np.nonzero(chroma)[0] - out["y0"]) / span
        for b in range(HUE_BINS):
            sel = bins == b
            if sel.sum() >= max(20, 0.004 * nc):
                out["hue_y"][b] = float(yy[sel].mean())
    achroma = body & (s < SAT_MIN)
    na = int(achroma.sum())
    if na:
        vb = np.clip(v[achroma] * GRAY_BINS // 256, 0, GRAY_BINS - 1)
        out["gray"] = np.bincount(vb, minlength=GRAY_BINS) / float(na)
    out["chroma_share"] = nc / float(nb)
    return out


def _hue_name(b: int) -> str:
    # 桶 11（330°~360°）归「红」：实测深红 #77141B 的色相是 355.8°，落在红
    # 的负向延伸上，叫「粉红」会让报告读起来莫名其妙。
    names = ["红", "橙", "橙黄", "黄", "黄绿", "绿",
             "青绿", "青", "蓝", "蓝紫", "紫", "红"]
    return names[b % HUE_BINS]


def _swatch(ref: Dict[str, Any], b: int) -> str:
    """给用户看的色块标签：色相名 + 实际色号（比裸桶名好认）。"""
    rgb = ref.get("hue_rgb")
    if rgb is None or not np.any(rgb[b]):
        return _hue_name(b) + "色"
    v = rgb[b]
    return f"{_hue_name(b)}色 #{int(v[0]):02X}{int(v[1]):02X}{int(v[2]):02X}"


def build_reference(fronts: Sequence[Image.Image]) -> Dict[str, Any]:
    """正视图（可多张）→ 参考画像。多张取逐桶中位数，抗单张异常。"""
    profs = [analyze(f, f"front{i}") for i, f in enumerate(fronts)]
    profs = [p for p in profs if p.get("trusted", True) and p["pixels"] >= 500]
    if not profs:
        return {"trusted": False, "reason": "正视图不可分析"}
    hue = np.median(np.stack([p["hue"] for p in profs]), axis=0)
    gray = np.median(np.stack([p["gray"] for p in profs]), axis=0)
    hue_y = np.median(np.stack([p["hue_y"] for p in profs]), axis=0)
    hue_rgb = np.median(np.stack([p["hue_rgb"] for p in profs]), axis=0)
    return {
        "trusted": True,
        "count": len(profs),
        "hue": hue, "gray": gray, "hue_y": hue_y, "hue_rgb": hue_rgb,
        "area": float(np.median([p["area"] for p in profs])),
        "box_h": float(np.median([p["box_h"] for p in profs])),
        "box_w": float(np.median([p["box_w"] for p in profs])),
        "chroma_share": float(np.median([p.get("chroma_share", 0) for p in profs])),
    }


def check_view(prof: Dict[str, Any], ref: Dict[str, Any], view: str) -> Dict[str, Any]:
    """单张视角图 vs 正视图参考。返回 {ok, retry, fails, warns, metrics}。"""
    fails: List[str] = []
    warns: List[str] = []
    metrics: Dict[str, Any] = {"view": view,
                               "area": round(prof.get("area", 0), 3),
                               "box_h": prof.get("box_h")}
    if not prof.get("trusted", True):
        warns.append("底色抠取不可靠，仅做几何检查")
    if not ref.get("trusted", True):
        warns.append(ref.get("reason", "参考不可用"))
        return {"ok": True, "retry": False, "fails": fails,
                "warns": warns, "metrics": metrics}

    # --- 几何 ---
    gr = GEOM.get(view, GEOM["side"])
    h_ratio = prof["box_h"] / max(1.0, ref["box_h"])
    a_ratio = prof["area"] / max(1e-6, ref["area"])
    metrics.update({"hRatio": round(h_ratio, 3), "areaRatio": round(a_ratio, 3)})
    if not (gr["h"][0] <= h_ratio <= gr["h"][1]):
        fails.append(f"剪影高度异常：{h_ratio:.2f}× 正视图（合理 {gr['h'][0]}~{gr['h'][1]}）")
    if not (gr["area"][0] <= a_ratio <= gr["area"][1]):
        fails.append(f"主体面积异常：{a_ratio:.2f}× 正视图（合理 {gr['area'][0]}~{gr['area'][1]}）")

    # --- 颜色存在性 ---
    if prof.get("trusted", True):
        cs_ref = float(ref.get("chroma_share") or 1.0)
        cs_now = float(prof.get("chroma_share") or 1.0)
        lost: List[str] = []
        for b in range(HUE_BINS):
            r = float(ref["hue"][b]) * cs_ref
            if r < TH.ref_min:
                continue
            now = float(prof["hue"][b]) * cs_now
            ratio = now / max(1e-6, r)
            tag = (f"{_swatch(ref, b)} 配色疑似丢失"
                   f"（正视图占主体 {r * 100:.0f}% → 本张 {now * 100:.1f}%）")
            metrics.setdefault("color", []).append(
                {"hue": int(b), "ref": round(r, 4), "now": round(now, 4)})
            if now >= TH.lost_max or ratio >= TH.lost_ratio:
                if ratio < TH.warn_ratio:
                    warns.append(tag + "；仍有余量，多为遮挡")
                continue
            cy = float(ref["hue_y"][b])
            if 0.0 <= cy < TH.top_zone and view == "back":
                warns.append(tag + "；该色集中在正视图上半身，背视图可能本来就看不到")
                continue
            lost.append(tag)
        fails.extend(lost)

        for b in range(HUE_BINS):
            now = float(prof["hue"][b])
            r = float(ref["hue"][b])
            if now >= TH.new_share and r < TH.new_ref_max and cs_now > 0.15:
                warns.append(f"新增{_swatch(ref, b)} 配色"
                             f"（正视图几乎没有 → 本张占主体 {now * cs_now * 100:.0f}%）")

        for b in range(GRAY_BINS):
            r = float(ref["gray"][b])
            if r < 0.25:
                continue
            keep = float(prof["gray"][b]) / max(1e-6, r)
            if keep < 0.3:
                warns.append(f"无彩色明度档丢失（正视图 {r * 100:.0f}% → "
                             f"本张 {prof['gray'][b] * 100:.0f}%）")

    return {"ok": not fails, "retry": bool(fails), "fails": fails,
            "warns": warns, "metrics": metrics}


def summarize(verdicts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """一批视角图的汇总，给拼板前的 UI 提示用。"""
    bad = [v for v in verdicts if not v["ok"]]
    return {"total": len(verdicts), "failed": len(bad),
            "ok": not bad,
            "reasons": [r for v in bad for r in v["fails"]][:6]}
