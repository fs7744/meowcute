"""真机验证：单侧配件（腰挎武士刀）在侧/背/右视图里不丢、不镜像。

默认用一张干净的正视图（中灰底、左侧腰挎刀、无接地阴影）推导三个视角，
再拼一张四视图板。中间产物落 .verify/view-consistency/。

用法：
  .venv/Scripts/python.exe tools/test_view_consistency.py            # 视角 + 拼板
  .venv/Scripts/python.exe tools/test_view_consistency.py --front    # 附：正视图阴影体检
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

OUT = Path(".verify/view-consistency")
BASE = OUT / "front-gray-0.png"
CHARACTER = ("灰白英短猫武士，红色短披风，左腰侧挎武士刀，皮质护腕，圆耳竖立，"
             "琥珀色眼睛，三头身比例，正面站立，表情专注")


def foot_shadow(im: Image.Image) -> dict:
    """脚下阴影体检：底部 14% 带内，明显暗于背景的像素数量。

    纯色底才有意义（透明底的「背景色」是无效 RGB）；只做参考，看人眼。
    """
    a = np.asarray(im.convert("RGBA")).astype(np.int16)
    h, _, _ = a.shape
    corners = np.concatenate([a[:6, :6, :3].reshape(-1, 3), a[:6, -6:, :3].reshape(-1, 3)])
    bg_lum = float(np.median(corners, axis=0).sum())
    band = a[int(h * 0.86):, :, :3]
    dark = band.sum(axis=2) < bg_lum * 0.92
    return {"bg": [int(v) for v in np.median(corners, axis=0)],
            "darkPxBottom": int(dark.sum())}


async def derive_all(cfg: dict, bg: str) -> None:
    from backend.app.pipelines import threeview as tv

    base = Image.open(BASE).convert("RGBA")
    print(f"[base] {BASE.name} {base.size} {foot_shadow(base)}")
    views = {"front": base}
    for view in ("side", "side_r", "back"):
        t = time.time()
        img, meta = await tv.derive_view(
            view, base, background=bg, character=CHARACTER,
            width=base.width, height=base.height, provider_cfg=cfg)
        p = OUT / f"final-{view}-{bg}.png"
        img.save(p)
        views[view] = img
        print(f"[{view}] {time.time() - t:.0f}s -> {p.name}  "
              f"instr={len(meta.get('instruction', ''))}字  {foot_shadow(img)}")

    if all(v in views for v in ("front", "side", "back", "side_r")):
        board, bmeta = tv.compose_view_board(
            [views[v] for v in tv.VIEW_ORDER], bg=None)
        bp = OUT / f"final-board-{bg}.png"
        board.save(bp)
        print(f"[board] {bp.name} {board.size} alpha={bmeta['alpha']} "
              f"matte={[v['matte'] for v in bmeta['views']]}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--front", action="store_true", help="顺带生成正视图做阴影体检")
    ap.add_argument("--bg", default="gray")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)

    from backend.app.config import load_settings

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    print(f"model={cfg.get('model')}")
    asyncio.run(derive_all(cfg, args.bg))

    if args.front:
        from backend.app.main import PixelOptions, _build_generation_prompt
        from backend.app.providers.diffusers_local import DiffusersProvider

        provider = DiffusersProvider(cfg)
        for i, bg in enumerate(("gray", "transparent")):
            built = _build_generation_prompt(
                CHARACTER, "pixel_16bit", PixelOptions(enabled=False), 768, 768, bg)
            res = asyncio.run(provider.generate(
                prompt=built["prompt"], width=768, height=768, seed=2000 + i, n=1,
                negative=built["negative"] or None))
            im = res.images[0].convert("RGBA")
            p = OUT / f"front-check-{bg}.png"
            im.save(p)
            print(f"[front-{bg}] -> {p.name} {foot_shadow(im)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
