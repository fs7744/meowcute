"""用 data/projects 下已有的真实产物回归 viewcheck 的判据（不占 GPU）。

判据是否有效，只看两件事：
  1. 正确的那批（实测肉眼确认没问题的视角图）不能被拦 —— 误报会毁掉整个流程；
  2. 肉眼确认坏的必须被拦 —— 漏报则这个机制白做。

用法：python tools/check_view_consistency.py [目录...]
      不带参数则跑 data/projects 下所有项目。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from backend.app import viewcheck  # noqa: E402

PREFIX = {"vs-": "side", "vb-": "back", "vr-": "side_r"}


def run(d: Path) -> None:
    files = sorted(d.glob("*.png"))
    fronts = [Image.open(p) for p in files
              if p.name.startswith(("out-", "im-"))]
    if not fronts:
        print(f"\n== {d.parent.name}: 无正视图，跳过")
        return
    ref = viewcheck.build_reference(fronts)
    print(f"\n== {d.parent.name}  正视图 {len(fronts)} 张"
          f"{'（抠取不可信，仅几何）' if not ref.get('trusted', True) else ''}")
    for pfx, view in PREFIX.items():
        for p in [x for x in files if x.name.startswith(pfx)]:
            img = Image.open(p)
            prof = viewcheck.analyze(img, p.name)
            v = viewcheck.check_view(prof, ref, view)
            flag = "通过" if v["ok"] else "拦截"
            print(f"   [{flag}] {view:<6} {p.name:<32}"
                  f"高比 {v['metrics']['hRatio']:>5.2f}"
                  f"  面积比 {v['metrics']['areaRatio']:>5.2f}"
                  f"  色相 {''.join('.' if x < 0.03 else str(min(9, int(x * 10))) for x in prof['hue'])}")
            for f in v["fails"]:
                print(f"          × {f}")
            for w in v["warns"]:
                print(f"          · {w}")


def main(argv) -> int:
    root = Path(__file__).resolve().parents[1]
    dirs = [Path(a) for a in argv[1:]] or sorted(
        p for p in (root / "data" / "projects").glob("*/images") if p.is_dir())
    for d in dirs:
        if d.is_dir():
            run(d)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
