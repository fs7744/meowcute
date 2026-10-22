"""透明四视图真机验证（选项 B）：拼板输出 RGBA 底板是否真的成立。

要验证三件事，缺一不可：
1. 侧/背/右走编辑底座时，transparent 背景的双段式 RGBA 声明能不能激活 alpha
   （此前只在文生图上实测过，编辑接力路径未验证——这是本方案最大的未知）；
2. 拼板 transparentBg=True 时产出 RGBA 板：底板全透明、主体 alpha 保住；
3. 已带 alpha 的输入在拼板里被识别为 alpha 直通（method=alpha），不再二次抠图。

用法：.venv/Scripts/python.exe tools/test_transparent_fourview.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

# 正视图素材：Z-Image 生成的正面 A-pose 像素小人（洋红纯色底，768×768）
# 项目 data/projects 会被清空，所以基准图放在 .verify 里，跑测试不依赖项目数据
BASE = ".verify/rigwalk2/front-char.png"
OUT_DIR = Path(".verify/fourview")


def alpha_stats(im: Image.Image) -> dict:
    a = np.asarray(im.convert("RGBA"))[:, :, 3]
    return {
        "transparent": round(float((a < 16).mean()), 3),
        "opaque": round(float((a > 200).mean()), 3),
        "corner": int(a[0, 0]),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = Image.open(BASE).convert("RGBA")
    print(f"base: {base.size} {base.mode} alpha={alpha_stats(base)}")

    from backend.app.pipelines import threeview as tv
    from backend.app.config import load_settings

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    print(f"provider imageModel: {cfg.get('imageModel')}")

    fails: list[str] = []

    # ---- 1. 透明背景的右视图（编辑底座接力） ----
    t0 = time.time()
    side_r, meta = asyncio.run(tv.derive_view(
        "side_r", base, background="transparent",
        width=512, height=768, provider_cfg=cfg))
    dt = time.time() - t0
    st = alpha_stats(side_r)
    p1 = OUT_DIR / "side_r-transparent.png"
    side_r.save(p1)
    print(f"derive side_r: {side_r.size} mode={side_r.mode} {dt:.1f}s "
          f"alpha={st} -> {p1}")
    print(f"instruction tail: {meta.get('instruction', '')[-110:]}")

    if side_r.mode != "RGBA":
        fails.append(f"右视图 mode={side_r.mode}（应 RGBA）")
    if st["transparent"] < 0.10:
        fails.append(f"右视图真正的透明像素只占 {st['transparent']:.1%}，"
                     f"编辑接力没激活 alpha（这是方案 B 的关键风险）")
    if st["opaque"] < 0.05:
        fails.append(f"右视图主体几乎全丢：不透明像素 {st['opaque']:.1%}")

    # ---- 2. 拼板输出 RGBA 底板 ----
    board, bmeta = tv.compose_view_board(
        [base, side_r], bg=None, gap=12, baseline=0.93)
    bs = alpha_stats(board)
    p2 = OUT_DIR / "board-alpha.png"
    board.save(p2)
    print(f"board: {board.size} mode={board.mode} alpha={bs} -> {p2}")
    print(f"board meta: bg={bmeta.get('bg')} alpha={bmeta.get('alpha')} "
          f"views={[v['matte'] for v in bmeta['views']]}")

    if board.mode != "RGBA":
        fails.append(f"拼板 mode={board.mode}（应 RGBA）")
    if bs["corner"] != 0:
        fails.append(f"拼板左上角 alpha={bs['corner']}（应 0）")
    if not bmeta.get("alpha") or bmeta.get("bg") != "transparent":
        fails.append(f"meta 未标记透明底板：{bmeta.get('bg')}/{bmeta.get('alpha')}")
    if bs["opaque"] < 0.05:
        fails.append(f"拼板主体几乎全丢：不透明像素 {bs['opaque']:.1%}")
    if bs["transparent"] < 0.30:
        fails.append(f"拼板透明区只占 {bs['transparent']:.1%}，不像透明底板")

    # ---- 3. 已带 alpha 的输入走直通，不二次抠图 ----
    mattes = [v["matte"] for v in bmeta["views"]]
    if mattes[1] != "alpha":
        fails.append(f"带 alpha 的右视图没走直通（matte={mattes[1]}）")

    # 叠到棋盘格上导出预览，肉眼能直接看边缘有没有被啃 / 有没有残留底色
    checker = Image.new("RGB", board.size, (60, 60, 60))
    d = ImageDraw.Draw(checker)
    for y in range(0, board.height, 24):
        for x in range(0, board.width, 24):
            if (x // 24 + y // 24) % 2:
                d.rectangle([x, y, x + 23, y + 23], fill=(90, 90, 90))
    checker.paste(board, (0, 0), board)
    p3 = OUT_DIR / "board-alpha-checker.png"
    checker.save(p3)
    print(f"checker preview -> {p3}")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: 编辑接力能出真透明右视图；拼板输出 RGBA 透明板；"
          "带 alpha 的输入直通不二次抠图")
    return 0


if __name__ == "__main__":
    sys.exit(main())
