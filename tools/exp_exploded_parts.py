# -*- coding: utf-8 -*-
"""实验：edit-2511 爆炸图路线生成骨骼动画部件。

前情（.verify/layered/）：Qwen-Image-Layered 拆的是「合成层」（背景/阴影/
填充/描边/角色/暗色变体），不服从「头/躯干/四肢」的部件语义——此路不通。

本实验换思路：不从整图上抠部件，而是让 edit-2511 直接生成一张「爆炸图」——
6 个部件在图里空间上就分开摆放。分离是生成出来的，不是算出来的：
连通域即可切块，彻底绕开「扣部件算法」的接缝/鬼影问题。

步骤：
  1. 参考图 .verify/rigwalk2/front-char.png（正面 A-pose 像素角色）
  2. QwenImageEditPlusPipeline（fp8 + cpu offload，同 exp-a 实测配置）
  3. 指令：拆成 头/躯干(含披风)/左臂/右臂/左腿/右腿 爆炸图，白底不重叠
  4. 连通域切块（binary_fill_holes 处理眼洞等内部孔洞）→ part-*.png
  5. 调试图：各部件 bbox 框在原图上

产出：.verify/exploded/exploded.png + part-*.png + parts-debug.png
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("MEOWCUTE_DATA_DIR", str(ROOT / "data"))

OUT = ROOT / ".verify" / "exploded"
OUT.mkdir(parents=True, exist_ok=True)

MODEL = str(ROOT / "models" / "qwen-image-edit-2511")
REF = ROOT / ".verify" / "rigwalk2" / "front-char.png"

PROMPT = (
    "把参考图中的像素游戏角色画成爆炸拆解图（exploded view）："
    "共 6 个部件——头部、躯干（连着披风）、左臂、右臂、左腿、右腿。"
    "每个部件完整独立，分散摆放在白色背景上，部件之间留明显空隙、互不接触。"
    "严格保持原有像素风格、配色和三头身比例不变。"
)
NEG = "重叠的部件，连接在一起的身体，模糊，背景装饰，多余的文字"

# 宫崎骏画风专用：部件提示词换成「长发/裙装/复杂衣褶」，且不强调比例约束
PROMPT_GHIBLI = (
    "把参考图中的宫崎骏动画风格角色画成爆炸拆解图（exploded view）："
    "共 6 个部件——头部（含飘逸长发）、躯干（连着衣服上身）、左臂、右臂、左腿、右腿。"
    "每个部件完整独立，分散摆放在白色背景上，部件之间留明显空隙、互不接触，"
    "保持原有手绘动画风格、配色和服装细节不变。"
)
NEG_GHIBLI = "重叠的部件，连接在一起的身体，模糊，背景装饰，多余的文字，衣物残缺"

# 强化版：显式网格布局 + 六件缺一不可 + 强负向约束。
# 失败经验（seed42/13）：edit 模型会「重新想象」而非精确复制，复杂角色的对称肢体
# 经常丢一件、且 seed 方差极大。强化提示词把布局钉死成网格，让角色定位变成
# 纯位置判断题；丢件再由「镜像补件」兜底。
PROMPT_GHIBLI_ENHANCED = (
    "把参考图里的宫崎骏动画风格少女画成一张爆炸拆解图（exploded view），"
    "用于角色部件动画。请严格按下面的网格摆放 6 个互不接触、各自完整的部件，"
    "背景纯白、没有任何装饰、部件之间留出明显空隙：\n"
    "【第一行·最上方中央】头部（含长发，头发必须完全收拢在头部轮廓内，不能碰到衣服）\n"
    "【第二行·左右两侧】左臂在画面左、右臂在画面右（每侧只画一条完整手臂）\n"
    "【第三行·正中】躯干（连着上衣/连衣裙上身，是最大的一块）\n"
    "【第四行·左右两侧】左腿在画面左、右腿在画面右（每侧只画一条完整腿）\n"
    "要求：六件必须全部出现、左右肢体都要有、缺一不可；严格保持参考图的手绘动画风格、"
    "人物长相、配色与服装细节；正面平视，不要背面、侧面、姿势歪斜、肢体粘连，"
    "不要解剖图风格。"
)
NEG_GHIBLI_ENHANCED = (
    "解剖图，骨头，骨骼，X光，多余的手臂，多余的腿，缺少肢体，只有一条手臂，"
    "只有一条腿，左右不对称，肢体粘连，头发接触衣服，背面，侧面，姿势歪斜，"
    "模糊，背景有装饰，方格纸，网格线，多余的文字，衣物残缺"
)

# v3：v2 的失败点是头部没和躯干分离（头连在身体上，切出的"头"其实是袖管）。
# v3 用更强的空间语言钉死头部必须是孤岛 + 负向里禁"头身一体"。
PROMPT_GHIBLI_V3 = (
    "把参考图里的宫崎骏动画风格少女画成一张爆炸拆解图（exploded view），"
    "用于角色部件动画。请严格按下面的网格摆放 6 个互不接触、各自完整的部件，"
    "背景纯白、没有任何装饰、部件之间留出明显空隙：\n"
    "【第一行·最上方中央】头部（含长发）——头部必须是一块独立漂浮的孤岛，"
    "下巴和脖子以下完全悬空，头部与下方躯干之间留出一块清晰的纯白横向空隙，"
    "皮肤和头发都绝不能与躯干相连\n"
    "【第二行·左右两侧】左臂在画面左、右臂在画面右（每侧一条完整手臂，带手）\n"
    "【第三行·正中】躯干（连衣裙，无头无臂，肩膀处断开）\n"
    "【第四行·左右两侧】左腿在画面左、右腿在画面右（每侧一条完整腿，含鞋）\n"
    "要求：六件必须全部出现、左右肢体都要有、缺一不可；严格保持参考图的手绘动画风格、"
    "人物长相、配色与服装细节；正面平视，不要背面、侧面、姿势歪斜、肢体粘连，"
    "不要解剖图风格。"
)
NEG_GHIBLI_V3 = NEG_GHIBLI_ENHANCED + "，头部连接躯干，脖子相连，头和身体连成一体"


def split_parts(rgb_img, min_area=400):
    """非白像素 → 连通域 → 部件列表（按面积降序）。"""
    import numpy as np
    from PIL import Image
    from scipy import ndimage

    a = np.asarray(rgb_img.convert("RGB")).astype(np.int16)
    # 掩码 = 暗色描边 或 高饱和色块。教训：v1 用「非白 sum>90」，浅色填充
    # （骨白/米色，离白太近）会被漏掉只剩细描边；方格纸/网格背景也扛不住。
    # 浅色填充不进掩码没关系——部件由描边闭合，fill_holes 会把内部填回来。
    luma = a.mean(axis=2)
    chroma = a.max(axis=2) - a.min(axis=2)
    mask = (luma < 190) | (chroma > 40)
    mask = ndimage.binary_closing(mask, structure=np.ones((3, 3)))
    labels, n = ndimage.label(mask)
    parts = []
    for i in range(1, n + 1):
        m = labels == i
        if int(m.sum()) < min_area:
            continue
        m = ndimage.binary_fill_holes(m)
        # 面积必须按 fill_holes 之后的「可见面积」算！教训（v3）：米白连衣裙
        # 填充色不在掩码内，若按填充前算，裙子面积只剩描边（4584），
        # assign_roles 的「最大块=躯干」会把它漏掉、把腿当躯干，槽位连锁错配。
        area = int(m.sum())
        ys, xs = np.where(m)
        x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
        rgb = np.asarray(rgb_img.convert("RGB"))
        crop = np.where(m[..., None], rgb, 0)[y0:y1 + 1, x0:x1 + 1].astype(np.uint8)
        # alpha 直接用掩码：掩码内不透明（保留白填充！），掩码外透明。
        # 教训：不能按「白色→透明」的颜色阈值抠底——角色身体就是白的，会把
        # 白填充全抠穿只剩描边（第一版渲染全身镂空就是这么来的）。
        part = Image.fromarray(
            np.dstack([crop, (m[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8)]),
            "RGBA",
        )
        parts.append({"img": part, "bbox": (int(x0), int(y0), int(x1), int(y1)),
                      "area": area})
    parts.sort(key=lambda p: -p["area"])
    return parts


ROLES = ["head", "torso", "arm_l", "arm_r", "leg_l", "leg_r"]


def _centroid(b):
    return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)


def assign_roles(parts):
    """按「网格 y 排名」把散落部件映射到 6 个角色槽位。

    网格约定（强化提示词已钉死）：第一行=头，第二行=双臂，第三行=躯干，
    第四行=双腿。因此按质心 y 排：最顶=头；剩余中面积最大=躯干；再剩余里
    最下面两件=腿、其中面积最大两件=臂（模型偶尔多画一条，取大弃小）；
    左右由相对躯干中线的 x 决定。缺件留 None，交给镜像补件。

    教训（v2）：模型没把头和躯干分离时，最顶的部件其实是侧置的袖管，
    原逻辑会把袖管当头。现加两道防线：
      1) 头带校验：头的质心必须落在整图顶部 30% 内，且不是全场最大块；
      2) 头身一体回退：无合格头候选时，从最大块顶部切 25% 作"头"，
         其余作"躯干"。
    """
    roles = {r: None for r in ROLES}
    if not parts:
        return roles
    ys = [v for p in parts for v in (p["bbox"][1], p["bbox"][3])]
    head_zone = min(ys) + (max(ys) - min(ys)) * 0.30
    biggest = max(parts, key=lambda p: p["area"])

    head = None
    for p in sorted(parts, key=lambda p: _centroid(p["bbox"])[1])[:3]:
        if _centroid(p["bbox"])[1] <= head_zone and p is not biggest:
            head = p
            break

    if head is None:
        # 头身一体（v2 失败模式）：从最大块顶部切 25% 当"头"，其余当"躯干"。
        blob = biggest
        img = blob["img"]
        w, h = img.size
        cut = int(h * 0.25)
        x0, y0, x1, y1 = blob["bbox"]
        print(f"[w] 未检出独立头部（疑似头身一体），从最大块顶部切 25% "
              f"({cut}px) 作为头部", flush=True)
        head = {"img": img.crop((0, 0, w, cut)),
                "bbox": (x0, y0, x1, y0 + cut),
                "area": blob["area"] // 4}
        roles["torso"] = {"img": img.crop((0, cut, w, h)),
                          "bbox": (x0, y0 + cut, x1, y1),
                          "area": blob["area"] - blob["area"] // 4}
        pool = [p for p in parts if p is not blob]
    else:
        pool = [p for p in parts if p is not head]
    roles["head"] = head

    if roles["torso"] is None:
        if not pool:
            return roles
        torso = max(pool, key=lambda p: p["area"])
        roles["torso"] = torso
        rest = [p for p in pool if p is not torso]
    else:
        rest = pool
    if not rest:
        return roles
    mid = _centroid(roles["torso"]["bbox"])[0]
    by_y = sorted(rest, key=lambda p: _centroid(p["bbox"])[1])
    legs = by_y[-2:]
    leg_ids = {id(p) for p in legs}
    remaining = [p for p in rest if id(p) not in leg_ids]
    arms = sorted(remaining, key=lambda p: -p["area"])[:2]

    def place(cands, left_key, right_key):
        for p in cands:
            key = left_key if _centroid(p["bbox"])[0] < mid else right_key
            if roles[key] is None:
                roles[key] = p
            else:  # 同槽冲突：塞进另一个空槽
                other = left_key if roles[left_key] is None else right_key
                roles[other] = p

    place(arms, "arm_l", "arm_r")
    place(legs, "leg_l", "leg_r")
    return roles


def mirror_img(img):
    return img.transpose(Image.FLIP_LEFT_RIGHT)


def complete_with_mirror(roles):
    """对称肢体缺一件时，用对侧镜像补齐。返回 {role: 是否镜像生成}。"""
    mirrored = {r: False for r in ROLES}
    if roles["arm_l"] is None and roles["arm_r"] is not None:
        roles["arm_l"] = {"img": mirror_img(roles["arm_r"]["img"]),
                           "bbox": roles["arm_r"]["bbox"], "area": roles["arm_r"]["area"]}
        mirrored["arm_l"] = True
    if roles["arm_r"] is None and roles["arm_l"] is not None:
        roles["arm_r"] = {"img": mirror_img(roles["arm_l"]["img"]),
                           "bbox": roles["arm_l"]["bbox"], "area": roles["arm_l"]["area"]}
        mirrored["arm_r"] = True
    if roles["leg_l"] is None and roles["leg_r"] is not None:
        roles["leg_l"] = {"img": mirror_img(roles["leg_r"]["img"]),
                           "bbox": roles["leg_r"]["bbox"], "area": roles["leg_r"]["area"]}
        mirrored["leg_l"] = True
    if roles["leg_r"] is None and roles["leg_l"] is not None:
        roles["leg_r"] = {"img": mirror_img(roles["leg_l"]["img"]),
                           "bbox": roles["leg_l"]["bbox"], "area": roles["leg_l"]["area"]}
        mirrored["leg_r"] = True
    return mirrored


def main():
    from PIL import Image

    ghibli = "--ghibli" in sys.argv
    resplit = "--resplit" in sys.argv
    # 允许 --ref <path> 覆盖参考图
    if "--ref" in sys.argv:
        idx = sys.argv.index("--ref")
        REF_OVERRIDE = sys.argv[idx + 1]
    else:
        REF_OVERRIDE = None
    # 允许 --outdir <path> 覆盖输出目录
    if "--outdir" in sys.argv:
        idx = sys.argv.index("--outdir")
        out_dir = Path(sys.argv[idx + 1])
    else:
        out_dir = OUT
    out_dir.mkdir(parents=True, exist_ok=True)

    enhance = "--enhance" in sys.argv
    PROMPT_USED = PROMPT_GHIBLI if ghibli else PROMPT
    NEG_USED = NEG_GHIBLI if ghibli else NEG
    if ghibli and enhance and "--v3" in sys.argv:
        PROMPT_USED = PROMPT_GHIBLI_V3
        NEG_USED = NEG_GHIBLI_V3
    elif ghibli and enhance:
        PROMPT_USED = PROMPT_GHIBLI_ENHANCED
        NEG_USED = NEG_GHIBLI_ENHANCED

    if resplit:
        img = Image.open(out_dir / "exploded.png")
        print(f"[i] 复用 {out_dir / 'exploded.png'} {img.size}", flush=True)
    else:
        import torch
        from diffusers import QwenImageEditPlusPipeline
        from backend.app.providers.diffusers_local import (
            DiffusersProvider, _enable_offload, _release_vram)

        t0 = time.time()
        prov = DiffusersProvider({})
        transformer = prov._load_qwen_transformer_fp8(MODEL)
        print(f"[t] fp8 transformer 加载 {time.time() - t0:.0f}s", flush=True)

        pipe = QwenImageEditPlusPipeline.from_pretrained(
            MODEL, torch_dtype=torch.bfloat16, transformer=transformer)
        _enable_offload(pipe, "qwen_edit")
        pipe.set_progress_bar_config(disable=True)
        print(f"[t] 管线就绪 {time.time() - t0:.0f}s", flush=True)

        ref_path = Path(REF_OVERRIDE) if REF_OVERRIDE else REF
        ref = Image.open(ref_path).convert("RGB")
        ref.save(out_dir / "ref.png")

        gen = torch.Generator("cpu").manual_seed(
            int(sys.argv[sys.argv.index("--seed") + 1]) if "--seed" in sys.argv else 42)
        try:
            torch.cuda.reset_peak_memory_stats()
        except Exception:
            pass

        t1 = time.time()
        out = pipe(
            image=ref,
            prompt=PROMPT_USED,
            negative_prompt=NEG_USED,
            num_inference_steps=40,
            true_cfg_scale=4.0,
            width=768, height=768,
            generator=gen,
        )
        print(f"[t] 推理 {time.time() - t1:.0f}s", flush=True)

        img = out.images[0]
        img.save(out_dir / "exploded.png")
        print(f"[i] exploded.png {img.size} mode={img.mode}", flush=True)

        try:
            torch.cuda.synchronize()
            print(f"[vram] peak={torch.cuda.max_memory_allocated() / 2**30:.1f}GB", flush=True)
        except Exception:
            pass
        _release_vram()

    parts = split_parts(img)
    print(f"[i] 切出 {len(parts)} 个部件", flush=True)
    from PIL import ImageDraw

    mirror = "--mirror" in sys.argv and ghibli
    if mirror:
        roles = assign_roles(parts)
        mirrored = complete_with_mirror(roles)
        # 角色化输出：每个槽位一张 PNG + 标注是否镜像补件
        role_names = {"head": "头", "torso": "躯干", "arm_l": "左臂",
                      "arm_r": "右臂", "leg_l": "左腿", "leg_r": "右腿"}
        present = {r: roles[r] is not None for r in ROLES}
        print(f"[i] 角色定位：{ {role_names[r]: present[r] for r in ROLES} }", flush=True)
        print(f"[i] 镜像补件：{ [role_names[r] for r in ROLES if mirrored[r]] }", flush=True)
        # parts.json 供 rigwalk_ghibli.py 读取
        import json
        meta = {}
        for r in ROLES:
            if roles[r] is None:
                continue
            fp = out_dir / f"{r}.png"
            roles[r]["img"].save(fp)
            meta[r] = {"file": fp.name, "bbox": list(roles[r]["bbox"]),
                       "area": roles[r]["area"], "mirrored": mirrored[r]}
        (out_dir / "parts.json").write_text(
            json.dumps({"roles": meta, "ref": str(ref_path) if not resplit else None},
                       ensure_ascii=False, indent=2), encoding="utf-8")

        # 角色条带图（带标签，清晰展示补件结果）
        cells = []
        for r in ROLES:
            if roles[r] is None:
                continue
            im = roles[r]["img"]
            tag = f"{role_names[r]}{'★镜像' if mirrored[r] else ''}"
            cells.append((im, tag))
        pad = 12
        cell_w = max(im.width for im, _ in cells) + pad * 2
        cell_h = max(im.height for im, _ in cells) + pad * 2 + 18
        strip = Image.new("RGBA", (cell_w, cell_h * len(cells)), (0, 0, 0, 0))
        dr = ImageDraw.Draw(strip)
        for i, (im, tag) in enumerate(cells):
            y = i * cell_h
            strip.alpha_composite(im, (pad, y + pad))
            dr.text((pad, y + pad + im.height + 4), tag, fill=(255, 80, 80, 255))
        strip.convert("RGB").save(out_dir / "roles-strip.png")

        # 原图角色标注调试（在原爆炸图上框出角色名）
        dbg = img.convert("RGB").copy()
        dr = ImageDraw.Draw(dbg)
        for r in ROLES:
            if roles[r] is None:
                continue
            x0, y0, x1, y1 = roles[r]["bbox"]
            dr.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
            dr.text((x0 + 4, y0 + 4), f"{role_names[r]}{'★' if mirrored[r] else ''}",
                    fill=(255, 0, 0))
        dbg.save(out_dir / "parts-debug.png")
        print(f"DONE 输出目录 {out_dir}（角色化 + 镜像补件）", flush=True)
        return

    dbg = img.convert("RGB").copy()
    dr = ImageDraw.Draw(dbg)
    for i, p in enumerate(parts):
        p["img"].save(out_dir / f"part-{i}.png")
        x0, y0, x1, y1 = p["bbox"]
        dr.rectangle([x0, y0, x1, y1], outline=(255, 0, 0), width=3)
        dr.text((x0 + 4, y0 + 4), f"#{i} a={p['area']}", fill=(255, 0, 0))
        print(f"    part-{i}.png bbox={p['bbox']} area={p['area']}", flush=True)
    dbg.save(out_dir / "parts-debug.png")
    print(f"DONE 输出目录 {out_dir}", flush=True)


if __name__ == "__main__":
    main()
