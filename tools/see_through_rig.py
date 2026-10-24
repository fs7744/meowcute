# -*- coding: utf-8 -*-
"""see-through 分层 PSD → 骨骼槽位部件（head/torso/arm_l/arm_r/leg_l/leg_r/weapon）。

为什么要这一层：see-through 拆的是 **Live2D drawable 语义**（头发/脸/上衣/裤袜/手套/
道具），不是骨骼部件。实测它的层名非常有规律，映射关系是确定的：

  head   ← back hair + front hair + face + neck + ears/l/r + eyelash + eyewhite +
           irides + eyebrow + mouth + nose + headwear（按 PSD 顺序合成）
  torso  ← topwear
  arm_l  ← handwear-l / handwear-r（实测这两层是**整条手臂含手**，不是手套）
  leg_*  ← legwear（两层腿常合并成一层，需按连通域/中线劈开）+ footwear 并回腿
  weapon ← objects（实测道具会单独成层，爆炸图路线会直接丢掉它）

关键细节：导出的 PNG 是**各自裁边**的，位置信息只在 PSD 里（layer.left/top）。
所以必须读 PSD 合成，不能直接把 PNG 叠起来。

用法（必须用 see-through 的 venv，它装了 psd_tools）：
  D:/code/gen/others/see-through-main/.venv/Scripts/python.exe tools/see_through_rig.py \
      --psd <...>/output/panda/layerdiff_output/cell0.psd --out .verify/panda/st-parts
产出：<out>/{head,torso,arm_l,arm_r,leg_l,leg_r,weapon}.png + parts.json
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from psd_tools import PSDImage

HEAD_NAMES = {
    "back hair", "front hair", "hair", "face", "neck", "nose", "mouth",
    "headwear", "helmet",
    "ears-l", "ears-r", "eyelash-l", "eyelash-r", "eyewhite-l", "eyewhite-r",
    "irides-l", "irides-r", "eyebrow-l", "eyebrow-r",
}
TORSO_NAMES = {"topwear", "bottomwear", "innerwear", "outerwear"}
ARM_NAMES = {"handwear-l", "handwear-r", "armwear-l", "armwear-r"}
LEG_NAMES = {"legwear", "footwear", "shoes", "socks"}
WEAPON_NAMES = {"objects", "weapon", "accessory", "accessories"}


def load_layers(psd_path: Path):
    """返回 [(name, rgba_image, (left, top))]，顺序 = PSD 自下而上。"""
    psd = PSDImage.open(str(psd_path))
    out = []
    for layer in psd:
        im = layer.topil()
        if im is None:
            continue
        out.append((layer.name.strip().lower(), im.convert("RGBA"), (layer.left, layer.top)))
    return out, psd.size


def composite(canvas_size, items) -> Image.Image:
    """把若干 (img, offset) 按顺序叠到同尺寸画布上（保持原坐标）。"""
    c = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    for im, (x, y) in items:
        c.alpha_composite(im, (x, y))
    return c


def crop_alpha(img: Image.Image) -> Image.Image:
    b = img.split()[-1].getbbox()
    return img.crop(b) if b else img


def split_lr(img: Image.Image):
    """把「两条腿画在一层」的图劈成左右两半（连通域优先，失败按中线切）。"""
    a = np.array(img.split()[-1]) > 40
    try:
        from scipy import ndimage

        lab, n = ndimage.label(ndimage.binary_opening(a, np.ones((3, 3))))
        sizes = sorted(((int((lab == i).sum()), i) for i in range(1, n + 1)), reverse=True)
        big = [i for sz, i in sizes if sz > 0.06 * a.sum()]
        if len(big) >= 2:
            halves = []
            for i in big[:2]:
                m = lab == i
                ys, xs = np.nonzero(m)
                box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
                sub = img.crop(box)
                mask = Image.fromarray((m[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
                                        * 255).astype(np.uint8))
                sub.putalpha(Image.composite(sub.getchannel("A"), Image.new("L", sub.size, 0), mask))
                halves.append((sub, (xs.min(), ys.min())))
            halves.sort(key=lambda t: t[1][0])
            return halves
    except Exception:
        pass
    # 回退：按 alpha 重心竖切
    ys, xs = np.nonzero(a)
    if len(xs) == 0:
        return []
    cx = int(np.median(xs))
    left = img.crop((0, 0, cx, img.height))
    right = img.crop((cx, 0, img.width, img.height))
    return [(left, (0, 0)), (right, (cx, 0))]


def build_from_psd(psd_path: Path, out: Path, quiet: bool = False) -> dict:
    """PSD -> 6 个骨骼槽位 + weapon.png，返回 roles meta。供 CLI 与管线复用。"""
    out.mkdir(parents=True, exist_ok=True)
    _log = (lambda *a, **k: None) if quiet else print
    layers, size = load_layers(psd_path)
    names = [n for n, _, _ in layers]
    _log(f"[i] 画布 {size}，{len(layers)} 层：{names}")

    head = [(im, off) for n, im, off in layers if n in HEAD_NAMES]
    torso = [(im, off) for n, im, off in layers if n in TORSO_NAMES]
    arms = [(n, im, off) for n, im, off in layers if n in ARM_NAMES]
    legs = [(n, im, off) for n, im, off in layers if n in LEG_NAMES]
    weapon = [(im, off) for n, im, off in layers if n in WEAPON_NAMES]
    used = HEAD_NAMES | TORSO_NAMES | ARM_NAMES | LEG_NAMES | WEAPON_NAMES
    rest = [n for n in names if n not in used]
    if rest:
        _log(f"[warn] 未归类的层（已忽略）：{rest}")

    if not torso:
        raise SystemExit("没找到躯干层（topwear/bottomwear）")
    if not head:
        raise SystemExit("没找到头部相关的层")
    if len(arms) < 2:
        raise SystemExit(f"手臂层不足（只找到 {[n for n, _, _ in arms]}）")

    # 手臂：按画面 x 位置定左右（rigwalk 的 arm_l 在屏幕左侧）
    arms_sorted = sorted(arms, key=lambda t: t[2][0])
    arm_l = crop_alpha(composite(size, [(arms_sorted[0][1], arms_sorted[0][2])]))
    arm_r = crop_alpha(composite(size, [(arms_sorted[1][1], arms_sorted[1][2])]))

    # 腿：先把 legwear+footwear 合成一整块，再劈成左右
    leg_full = crop_alpha(composite(size, [(im, off) for _, im, off in legs]))
    halves = split_lr(leg_full)
    if len(halves) < 2:
        raise SystemExit("腿层无法劈成左右两块")
    leg_l = crop_alpha(halves[0][0])
    leg_r = crop_alpha(halves[1][0])

    parts = {
        "head": crop_alpha(composite(size, head)),
        "torso": crop_alpha(composite(size, torso)),
        "arm_l": arm_l, "arm_r": arm_r,
        "leg_l": leg_l, "leg_r": leg_r,
    }
    meta = {}
    for role, im in parts.items():
        fp = out / f"{role}.png"
        im.save(fp)
        meta[role] = {"file": fp.name, "bbox": list(im.split()[-1].getbbox() or (0, 0, im.width, im.height)),
                      "area": int(np.array(im.split()[-1]).sum() // 255), "mirrored": False}
    # 武器单独留一份（不进骨骼，但用户要能看到它没丢）
    if weapon:
        w = crop_alpha(composite(size, weapon))
        w.save(out / "weapon.png")
        _log(f"[i] 武器/道具层 -> weapon.png {w.size}")

    (out / "parts.json").write_text(
        json.dumps({"roles": meta, "src": str(psd_path), "source": "see-through"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    for r, im in parts.items():
        _log(f"  {r:7s} {im.size}")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--psd", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    build_from_psd(Path(args.psd), Path(args.out))
    print(f"DONE {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
