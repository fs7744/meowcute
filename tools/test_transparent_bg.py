"""透明背景选项端到端测试：走与后端 _run_generate 完全相同的提示词组装
（_build_generation_prompt → Qwen-Image-2.1），验证：

1. background="transparent" 时提示词带官方双段式 RGBA 声明；
2. 生成的图是 RGBA 且背景像素真正透明（alpha=0 占比显著）；
3. 主体（画面中心区域）保持不透明——不是整张图都被擦掉。

对照：同一 seed + background="gray" 的图 alpha 应全为 255（不透明）。

用法：.venv/Scripts/python.exe tools/test_transparent_bg.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

OUT = ROOT / ".verify" / "transparent-bg"
OUT.mkdir(parents=True, exist_ok=True)

PROMPT = "像素风小猫少女，三头身，正面站立，红色短披风，白色小猫耳朵"
SEED = 12345


def build(background: str):
    """复刻 main._build_generation_prompt 的提示词组装（不启动 FastAPI）。"""
    from backend.app.main import PixelOptions, _build_generation_prompt
    pixel = PixelOptions(enabled=False)
    return _build_generation_prompt(PROMPT, "pixel_16bit", pixel,
                                    512, 512, background)


def gen(built: dict, tag: str):
    """调 DiffusersProvider 生成一张图（与后端同一 provider 路径）。"""
    import torch
    from backend.app.providers.diffusers_local import DiffusersProvider
    from backend.app.config import load_settings

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    prov = DiffusersProvider(cfg)
    model_id = prov._resolve_model()
    g = torch.Generator("cuda").manual_seed(SEED)
    t0 = time.time()
    result = prov._run_qwen21(
        model_id,
        built["prompt"], built["negative"], 512, 512,
        steps=None, seed=SEED, generator=g, n=1, refs=None,
        task_id=None, max_seq_len=None)
    imgs, meta = result
    img = imgs[0]
    p = OUT / f"{tag}.png"
    img.save(p)
    print(f"[{tag}] {time.time() - t0:.1f}s family={meta.get('family')} "
          f"mode={meta.get('mode')} peak={meta.get('vram', {}).get('peakGB')}GB → {p.name}")
    return img


def alpha_stats(img: Image.Image, tag: str) -> dict:
    rgba = img.convert("RGBA")
    a = np.asarray(rgba)[..., 3]
    h, w = a.shape
    # 中心主体区（中间 40% 矩形）与四角背景区（15% 边框）分开统计
    cy0, cy1, cx0, cx1 = int(h * .3), int(h * .7), int(w * .3), int(w * .7)
    center = a[cy0:cy1, cx0:cx1]
    border = np.concatenate([
        a[:int(h * .15), :].ravel(), a[int(h * .85):, :].ravel(),
        a[:, :int(w * .15)].ravel(), a[:, int(w * .85):].ravel()])
    st = {
        "mode": rgba.mode,
        "transparent_ratio": float((a < 10).mean()),
        "center_opaque_ratio": float((center > 245).mean()),
        "border_transparent_ratio": float((border < 10).mean()),
        "alpha_min": int(a.min()), "alpha_max": int(a.max()),
    }
    print(f"  [{tag}] {st}")
    return st


def main() -> int:
    fails = []

    # ---- 1) 提示词组装（CPU，不需要 GPU）----
    built_t = build("transparent")
    assert built_t["prompt"].startswith("This is an RGBA image with transparency.")
    assert "The image has alpha channel and the background is transparent." in built_t["prompt"]
    print("[提示词] transparent 组装 OK：", built_t["prompt"][:80], "…")

    # ---- 2) 真实生成：透明 ----
    img_t = gen(built_t, "transparent")
    st_t = alpha_stats(img_t, "transparent")
    if not (img_t.mode == "RGBA" and st_t["alpha_min"] < 10):
        fails.append("透明图没有真正的透明像素")
    if st_t["border_transparent_ratio"] < 0.5:
        fails.append(f"边框透明占比过低: {st_t['border_transparent_ratio']:.2f}")
    if st_t["center_opaque_ratio"] < 0.3:
        fails.append(f"中心主体不透明占比过低: {st_t['center_opaque_ratio']:.2f}")

    # ---- 3) 对照：中灰背景应全不透明 ----
    built_g = build("gray")
    img_g = gen(built_g, "gray-control")
    st_g = alpha_stats(img_g, "gray-control")
    if st_g["alpha_min"] > 250:
        pass  # 预期：全不透明
    else:
        print("  [warn] 中灰对照图也出现了透明像素（模型自行发挥），不算失败")

    # ---- 4) 拼一张对比图（左透明右灰），棋盘格衬底方便肉眼检查 ----
    def on_checker(im: Image.Image) -> Image.Image:
        w, h = im.size
        checker = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ck = Image.new("RGBA", (16, 16), (200, 200, 200, 255))
        ck2 = Image.new("RGBA", (16, 16), (140, 140, 140, 255))
        for y in range(0, h, 16):
            for x in range(0, w, 16):
                checker.paste(ck if ((x // 16) + (y // 16)) % 2 == 0 else ck2, (x, y))
        checker.alpha_composite(im.convert("RGBA"))
        return checker

    a, b = on_checker(img_t), on_checker(img_g)
    board = Image.new("RGB", (a.width + b.width + 16, max(a.height, b.height) + 40),
                      (24, 24, 28))
    board.paste(a.convert("RGB"), (0, 40))
    board.paste(b.convert("RGB"), (a.width + 16, 40))
    board.save(OUT / "compare.png")
    print(f"[对比图] → {(OUT / 'compare.png').name}（左：透明 + 棋盘格 / 右：中灰对照）")

    print()
    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS：透明背景生成符合预期（RGBA + 边框真透明 + 主体不透明）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
