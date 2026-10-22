# -*- coding: utf-8 -*-
"""st2db_parts.py — see-through 语义部件 → DragonBones rig 六大件。

数据源：see-through 原始 ref.psd（21 层语义分解）。

实测教训（2026-10-22）：
  - ref_lrsplit.psd 的 legwear 切分是坏的（legwear-l 空、legwear-r 只一条），
    所以双腿从原始 legwear 层自己切：两条腿在层里连成一团（无 x 间隙），
    按整团 x 中线竖切（裙摆会盖住上端，切口不可见）。
  - 各层有全画布 alpha 10~24 的 inpainting 噪声，直接 getbbox 裁不动；
    先形态学开运算去噪点、只保留 >500px 的连通域，再算内容 bbox。

  head  = front hair + back hair + face + 五官 + neck
  torso = topwear + bottomwear
  arm_l/arm_r = handwear-*（按内容 x 中心排序：小=画面左）
  leg_l/leg_r = legwear 层去噪后整团竖切两半（按 x 排序）
输出：.verify/ghibli/st-parts/<role>.png（供 build_rig.py <目录> 消费）
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image
from psd_tools import PSDImage
from scipy import ndimage

ST = Path(r"D:/code/gen/others/see-through-main/output/ghibli/layerdiff_output/ref.psd")
OUT = Path(r"D:/code/pixelforge/.verify/ghibli/st-parts")

HEAD_PARTS = [
    "front hair", "back hair", "face",
    "eyewhite-l", "eyewhite-r", "irides-l", "irides-r",
    "eyelash-l", "eyelash-r", "eyebrow-l", "eyebrow-r",
    "ears-l", "ears-r", "nose", "mouth", "neck",
]
TORSO_PARTS = ["topwear", "bottomwear"]
MIN_AREA = 500  # 连通域最小面积（滤 inpainting 噪点）


def load_layers(psd_path: Path):
    psd = PSDImage.open(psd_path)
    layers = {}
    for layer in psd:
        im = layer.topil()
        if im is None:
            continue
        im = im.convert("RGBA")
        if im.size != psd.size:
            full = Image.new("RGBA", psd.size, (0, 0, 0, 0))
            full.alpha_composite(im, (layer.left, layer.top))
            im = full
        layers[layer.name] = im
    return layers, psd.size


def clean_mask(im: Image.Image) -> np.ndarray:
    """alpha 掩码：去噪（开运算）+ 只保留大连通域。"""
    a = np.asarray(im.getchannel("A"))
    mask = a > 16
    mask = ndimage.binary_opening(mask, structure=np.ones((3, 3)))
    lab, n = ndimage.label(mask)
    if n == 0:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    keep_ids = [i + 1 for i, s in enumerate(sizes) if s >= MIN_AREA]
    if not keep_ids:
        return mask
    return np.isin(lab, keep_ids)


def mask_bbox(mask: np.ndarray):
    ys, xs = np.where(mask)
    if not len(xs):
        return None
    return int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1


def trim(im: Image.Image) -> Image.Image:
    bbox = mask_bbox(clean_mask(im))
    return im.crop(bbox) if bbox else im


def composite(layers, names, size):
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    for n in names:
        canvas.alpha_composite(layers[n])
    return canvas


def split_blob_vertical(im: Image.Image, mask: np.ndarray):
    """把一整团（两条腿）按 x 中线竖切两半；若团内有 x 间隙则沿间隙切。"""
    x0, y0, x1, y1 = mask_bbox(mask)
    blob = mask[y0:y1, x0:x1]
    colsum = blob.sum(axis=0)
    nz = np.where(colsum > 0)[0]
    # 团内找最宽的空列带（两腿之间的缝）
    gaps = []
    if len(nz):
        prev = nz[0]
        for x in nz[1:]:
            if x - prev > 3:
                gaps.append((prev, x))
            prev = x
    if gaps:
        g = max(gaps, key=lambda g: g[1] - g[0])
        cut = x0 + (g[0] + g[1]) // 2
    else:
        cut = x0 + (x0 + x1) // 2 if False else (x0 + x1) // 2
    rgb = np.asarray(im.convert("RGBA"))
    out = []
    for cols, m in ((slice(0, cut), mask[:, :cut]),
                    (slice(cut, None), mask[:, cut:])):
        bbox = mask_bbox(m)
        if bbox is None:
            continue
        bx0, by0, bx1, by1 = bbox
        sub_rgb = rgb[:, cols]
        crop = np.where(m[..., None], sub_rgb, 0)[by0:by1, bx0:bx1].astype(np.uint8)
        out.append(Image.fromarray(crop, "RGBA"))
    return out


def main():
    layers, size = load_layers(ST)
    print(f"[psd] {len(layers)} 层, 画布 {size}")
    need = HEAD_PARTS + TORSO_PARTS + ["handwear-l", "handwear-r", "legwear"]
    missing = [p for p in need if p not in layers]
    if missing:
        raise SystemExit(f"PSD 缺层：{missing}")
    OUT.mkdir(parents=True, exist_ok=True)

    head = trim(composite(layers, HEAD_PARTS, size))
    torso = trim(composite(layers, TORSO_PARTS, size))
    head.save(OUT / "head.png")
    torso.save(OUT / "torso.png")
    print(f"[head] {head.size}  [torso] {torso.size}")

    # 手臂：按内容 x 中心排序
    arms = []
    for n in ("handwear-l", "handwear-r"):
        im = trim(layers[n])
        bbox = mask_bbox(clean_mask(layers[n]))
        cx = (bbox[0] + bbox[2]) / 2 if bbox else 0
        arms.append((cx, im, n))
    arms.sort(key=lambda t: t[0])
    for side, (_, im, n) in zip(("l", "r"), arms):
        im.save(OUT / f"arm_{side}.png")
        print(f"[arm_{side}] {im.size} (来自 {n})")

    legs = split_blob_vertical(layers["legwear"], clean_mask(layers["legwear"]))
    legs = [im for im in legs if im.getchannel("A").getbbox()]
    if len(legs) != 2:
        raise SystemExit(f"腿部分切出 {len(legs)} 条（期望 2）")
    legs.sort(key=lambda im: mask_bbox(clean_mask(im))[0])
    for side, im in zip(("l", "r"), legs):
        im.save(OUT / f"leg_{side}.png")
        print(f"[leg_{side}] {im.size}")
    print(f"[done] → {OUT}")


if __name__ == "__main__":
    main()
