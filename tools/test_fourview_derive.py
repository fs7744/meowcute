"""四视图修复的真机验证：右视图 derive 复用正视图背景 + 保留 RGBA。

用项目里现成的正视图（矮人搬运工，中灰底）直接调 derive_view：
1. instruction 里必须带正视图的背景句（#808080）——侧/背/右与正视图同底色
2. 输出保留 RGBA（旧代码 convert("RGB") 会把透明区抹黑）
3. 出图尺寸正确、meta 带 background 字段

用法：.venv/Scripts/python.exe tools/test_fourview_derive.py
"""
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

BASE = ("data/projects/p-b165c3855c82/images/"
        "out-20261022-201652-1-090b.png")
OUT_DIR = Path(".verify/fourview")


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    base = Image.open(BASE).convert("RGBA")
    print(f"base: {base.size} {base.mode}")

    from backend.app.pipelines import threeview as tv
    from backend.app.config import load_settings

    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    print(f"provider imageModel: {cfg.get('imageModel')}")

    t0 = time.time()
    out, meta = asyncio.run(tv.derive_view(
        "side_r", base, background="gray",
        width=512, height=768, provider_cfg=cfg))
    dt = time.time() - t0

    fails: list[str] = []
    if "#808080" not in meta.get("instruction", ""):
        fails.append(f"instruction 缺正视图背景句: ...{meta.get('instruction', '')[-90:]}")
    if meta.get("background") != "gray":
        fails.append(f"meta.background={meta.get('background')}")
    if out.mode != "RGBA":
        fails.append(f"输出 mode={out.mode}（应保留 RGBA）")
    if out.size != (512, 768):
        fails.append(f"输出尺寸 {out.size}")

    p = OUT_DIR / "side_r-gray.png"
    out.save(p)
    print(f"saved {p}  mode={out.mode} size={out.size}  {dt:.1f}s")
    print(f"meta: view={meta.get('view')} steps={meta.get('steps')} "
          f"model={meta.get('model')} autoRouted={meta.get('autoRouted')}")
    print(f"instruction tail: {meta.get('instruction', '')[-100:]}")

    # 顺带验证 none / transparent 的背景拼接分支（纯文本，不占 GPU）
    i_none = tv._apply_background("把角色转成侧面。", "none")
    i_tr = tv._apply_background("把角色转成侧面。", "transparent")
    if "背景要求" in i_none:
        fails.append("none 背景不该拼背景句")
    if not i_tr.startswith("This is an RGBA image with transparency."):
        fails.append("transparent 缺双段式声明开头")
    if "The image has alpha channel and the background is transparent." not in i_tr:
        fails.append("transparent 缺双段式声明结尾")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS: 右视图 derive 复用正视图背景、保留 RGBA、尺寸正确")
    return 0


if __name__ == "__main__":
    sys.exit(main())
