# -*- coding: utf-8 -*-
"""在 see-through 自己的 venv 里跑：静态动漫立绘 → 分层 PSD（2.5D 可操作模型）。

为什么必须用它的 venv：see-through 依赖 torch 2.8+cu128 / SDXL-LayerDiff /
Marigold，与主项目 venv（torch 2.6）不兼容，同进程 import 会直接炸。所以只能
子进程调用——后端只依赖「一个能跑的 CLI + 约定的目录结构」
（见 backend/app/pipelines/layer25d.py），模型换代 / 换路径都不用动后端代码。

用法（必须用 see-through 的 venv）：
  <see-through>/.venv/Scripts/python.exe tools/see_through_export.py \
      --ref <立绘.png> --out <输出目录> --resolution 1024 --steps 30 --seed 42 [--lrsplit]

产出（都在 --out 下）：
  model.psd          分层 PSD：Live2D 语义层（face / topwear / handwear-l …），
                     每层都已补全被遮挡区域（See-Through 的核心能力），
                     层序 = 深度顺序（最靠前的画在最上层）
  model.psd.json     每层的 xyxy / part_id / depth_median（深度中位数）
  model_depth.psd    深度版 PSD（灰度）
  model_lrsplit.psd  左右拆分版（--lrsplit：眼/耳/手饰/头发拆左右）
  layers/<tag>.png   每层的透明 PNG（裁边版，位置见 manifest）
  manifest.json      汇总清单：层列表 + 深度 + 绘制顺序 + 画布尺寸

进度：每个阶段打印一行 `[stage] <文案>`，后端按这个转发给前端。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

# see-through 仓库根目录：默认用机器上那份；可用环境变量改（后端同款约定）
ST_DIR = Path(os.environ.get(
    "MEOWCUTE_SEETHROUGH_DIR", r"D:/code/gen/others/see-through-main"))

# 左右拆分的目标层（与 see-through 自带编排一致）
SPLIT_TAGS = "topwear,bottomwear,legwear,handwear,hair,headwear"

# 层名 → 文件名：PSD 层名带空格（"front hair"），做文件名要安全化
_SAFE = re.compile(r"[^\w\-一-鿿]+")


def stage(msg: str) -> None:
    print(f"[stage] {msg}", flush=True)


def safe_name(name: str) -> str:
    s = _SAFE.sub("_", (name or "").strip()).strip("_")
    return s or "layer"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True, help="输入立绘")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--resolution", type=int, default=1024)
    ap.add_argument("--steps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--lrsplit", action="store_true")
    a = ap.parse_args()

    ref = Path(a.ref).resolve()
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if not ref.is_file():
        print(f"[err] 输入图不存在：{ref}", flush=True)
        return 2

    if not ST_DIR.is_dir():
        print(f"[err] 找不到 see-through：{ST_DIR}", flush=True)
        return 3
    models = ST_DIR / "models"
    layer_model = models / "layerdiff3d"
    depth_model = models / "marigold"
    for m in (layer_model, depth_model):
        if not m.is_dir():
            print(f"[err] 缺少模型权重：{m}（先跑一次 see-through 的下载流程）",
                  flush=True)
            return 4

    # inference_utils 用相对路径 / workspace 定位资源，必须切到它的根目录
    os.chdir(ST_DIR)
    sys.path.insert(0, str(ST_DIR))

    t0 = time.time()
    stage("加载 LayerDiff（分层生成）…")
    from utils.inference_utils import (apply_layerdiff, apply_marigold,
                                       further_extr, seg_wlr_psd)

    stage(f"图层分解中（{a.resolution}px / {a.steps} 步）…")
    apply_layerdiff(str(ref), str(layer_model),
                     num_inference_steps=a.steps, seed=a.seed,
                     save_dir=str(out), resolution=a.resolution)

    stage("深度估计中（Marigold）…")
    apply_marigold(str(ref), str(depth_model),
                    seed=a.seed, save_dir=str(out), resolution=768)

    stage("后处理 + 导出 PSD…")
    saved = out / ref.stem
    further_extr(str(saved), rotate=False, save_to_psd=True, tblr_split=True)
    psd = out / f"{ref.stem}.psd"
    if not psd.is_file():
        print(f"[err] PSD 没生成：{psd}", flush=True)
        return 5

    stage("整理图层清单…")
    # see-through 的 NumpyEncoder 白名单里没有 numpy 2.x 的 intc（np.intc 是
    # 独立类型，不再是 np.int32 的别名），写 lrsplit 的 .psd.json 时会抛
    # "Object of type intc is not JSON serializable" —— PSD 本身已经写完，
    # 但异常把流程打断，lrsplit PSD 拿不到。给 default 打个补丁兜住。
    try:
        import numpy as _np

        def _safe_default(self, obj):
            if isinstance(obj, _np.bool_):
                return bool(obj)
            if isinstance(obj, _np.integer):
                return int(obj)
            if isinstance(obj, _np.floating):
                return float(obj)
            if isinstance(obj, _np.ndarray):
                return obj.tolist()
            return json.JSONEncoder.default(self, obj)

        # **必须按模块实例打补丁**：see-through 内部 import 的是 `utils.io_utils`
        # （common 目录被塞进 sys.path 后的另一个模块对象），跟
        # `common.utils.io_utils` 不是同一个 —— 之前只 patch 后者，补丁根本没
        # 生效（左右拆分照样报 intc 序列化失败）。这里把所有同名模块都覆盖。
        _patched = 0
        for _name, _mod in list(sys.modules.items()):
            if not _name.endswith("io_utils"):
                continue
            _enc = getattr(_mod, "NumpyEncoder", None)
            if _enc is None or getattr(_enc, "_pf_patched", False):
                continue
            _enc.default = _safe_default
            _enc._pf_patched = True
            _patched += 1
        if not _patched:
            print("[warn] 没找到 io_utils 模块，numpy 补丁没打上", flush=True)
        else:
            print(f"[patch] numpy 序列化补丁已打上（{_patched} 个模块）", flush=True)
    except Exception as e:  # noqa: BLE001 —— 补丁失败不该影响主产物
        print(f"[warn] numpy 序列化补丁没打上（{e}）", flush=True)

    from psd_tools import PSDImage

    meta = {}
    meta_path = Path(str(psd) + ".json")
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    parts = meta.get("parts") or {}

    # PSD 里的层序 = 自下而上；深度中位数小的（靠前）画在最上层
    psd_img = PSDImage.open(str(psd))
    layers_dir = out / "layers"
    layers_dir.mkdir(parents=True, exist_ok=True)

    layers = []
    for layer in psd_img:
        name = (layer.name or "").strip()
        im = layer.topil()
        if im is None or not name:
            continue
        im = im.convert("RGBA")
        # 裁到内容 bbox（PSD 里 position 是画布坐标，manifest 里记下来）
        bb = im.getbbox()
        if bb:
            ox, oy = layer.left + bb[0], layer.top + bb[1]
            im = im.crop(bb)
        else:
            ox, oy = layer.left, layer.top
        fname = f"{safe_name(name)}.png"
        im.save(layers_dir / fname)
        info = parts.get(name) or {}
        depth = info.get("depth_median")
        layers.append({
            "name": name,
            "file": fname,
            "width": im.width,
            "height": im.height,
            # 画布坐标（裁边后的左上角）——预览器按它把各层叠回原位
            "x": int(ox),
            "y": int(oy),
            "box": [int(ox), int(oy), int(ox + im.width), int(oy + im.height)],
            "depth": depth,
            "partId": info.get("part_id"),
        })

    # 深度小 = 靠前（最上层）
    def _d(l):
        v = l.get("depth")
        return 1.0 if v is None else float(v)

    layers.sort(key=_d)
    for i, l in enumerate(layers):
        l["order"] = i          # 0 = 最前/最上层

    # 左右拆分（在 numpy 补丁之后跑，否则写 .psd.json 会炸）
    lrsplit_psd = None
    if a.lrsplit:
        stage("左右拆分（眼/耳/手饰/头发）…")
        lrsplit_psd = out / f"{ref.stem}_lrsplit.psd"
        try:
            seg_wlr_psd(str(psd), SPLIT_TAGS, savep=str(lrsplit_psd))
        except Exception as e:  # noqa: BLE001 —— 拆分失败不影响主产物
            print(f"[warn] 左右拆分失败（{e}），跳过", flush=True)
            lrsplit_psd = None

    # 合成预览图：see-through 把所有层叠起来的那张（下游「图片结果」卡用）
    recon = out / ref.stem / "reconstruction.png"
    if not recon.is_file():
        recon = out / ref.stem / "optimized" / "reconstruction.png"
    if recon.is_file():
        shutil.copy2(recon, out / "model_preview.png")

    # 目标 PSD 改成固定名字，前端下载链接才好写
    final_psd = out / "model.psd"
    if psd != final_psd:
        psd.replace(final_psd)
    final_meta = out / "model.psd.json"
    if meta_path.is_file():
        meta_path.replace(final_meta)
    depth_psd = out / "model_depth.psd"
    raw_depth = out / f"{ref.stem}_depth.psd"
    if raw_depth.is_file() and raw_depth != depth_psd:
        raw_depth.replace(depth_psd)
    final_split = None
    if lrsplit_psd and lrsplit_psd.is_file():
        final_split = out / "model_lrsplit.psd"
        if lrsplit_psd != final_split:
            lrsplit_psd.replace(final_split)

    # 清掉重命名后残留的中间文件（seg_wlr_psd 自己写的 <stem>_lrsplit*.psd/json）
    for leftover in out.glob(f"{ref.stem}_lrsplit*"):
        if leftover.name in ("model_lrsplit.psd", "model_lrsplit_depth.psd"):
            continue
        try:
            leftover.unlink()
        except OSError:
            pass

    frame = meta.get("frame_size") or [psd_img.width, psd_img.height]
    manifest = {
        "psd": "model.psd",
        "psdJson": "model.psd.json",
        "depthPsd": "model_depth.psd" if depth_psd.is_file() else None,
        "lrsplitPsd": "model_lrsplit.psd" if final_split else None,
        "frameSize": [int(frame[0]), int(frame[1])],
        "resolution": a.resolution,
        "steps": a.steps,
        "seed": a.seed,
        "seconds": round(time.time() - t0, 1),
        "layers": layers,
    }
    (out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    stage(f"完成：{len(layers)} 层 / {manifest['seconds']}s")
    print("=== DONE ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
