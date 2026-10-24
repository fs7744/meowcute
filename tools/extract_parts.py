# -*- coding: utf-8 -*-
"""一键拆件：角色立绘 → 6 个骨骼部件 + 武器（混合路线）。

为什么要混合（实测结论，见 .workbuddy/memory/2026-10-24.md）：
  - 爆炸图（Qwen-Image-2.1）头部拆得准，但**会丢手持道具**；
  - see-through 躯干/手臂/腿/道具都拆得好（道具单独成 objects 层），但**半写实角色的
    头部会崩**（back hair 变成覆盖全身的半透明鬼影）；
  - 两者拼起来（爆炸图的头 + see-through 的身体）面积稳定性从 ±5.4% 提到 ±2.05%。

流程：
  1. see-through（独立 venv，本机 1280 分辨率约 2m50s）→ 分层 PSD
  2. PSD → 6 槽位（见 tools/see_through_rig.py）
  3. 爆炸图（Qwen-Image-2.1，约 45s）→ 只取 head，按躯干宽度缩放
  4. 合成 parts.json，输出给 tools/rigwalk_ghibli.py 渲染

用法：
  python tools/extract_parts.py --ref <立绘> --out .verify/xxx/parts
    --no-head        只用 see-through（动漫角色足够，省 45s）
    --no-see-through 只用爆炸图（无 GPU 环境或只想快速试）
    --force          已有结果也重跑
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

ST_DIR = Path(os.environ.get("MEOWCUTE_SEETHROUGH_DIR",
                             r"D:/code/gen/others/see-through-main"))
ST_PY = ST_DIR / ".venv" / "Scripts" / "python.exe"
ST_SCRIPT = ST_DIR / "download_and_run.py"
PROJ_PY = Path(sys.executable)


def _run(cmd, cwd=None, timeout=1800):
    print("[run]", " ".join(str(c) for c in cmd[:3]), "...", flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                          capture_output=True, text=True, timeout=timeout)


def step_see_through(ref: Path, work: Path, resolution: int) -> Path:
    """跑 see-through，返回 PSD 路径。"""
    if not ST_PY.exists() or not ST_SCRIPT.exists():
        raise SystemExit(f"see-through 不可用：{ST_DIR}（可用 --no-see-through）")
    out = work / "see-through"
    out.mkdir(parents=True, exist_ok=True)
    psd_cached = out / "layerdiff_output" / f"{ref.stem}.psd"
    if psd_cached.exists():
        print(f"[see-through] 复用已有 PSD {psd_cached}", flush=True)
        return psd_cached
    t0 = time.time()
    r = _run([ST_PY, ST_SCRIPT, "--ref", str(ref), "--out", str(out),
              "--resolution", str(resolution)], timeout=1800)
    psd = out / "layerdiff_output" / f"{ref.stem}.psd"
    if r.returncode != 0 or not psd.exists():
        tail = (r.stdout or "")[-800:] + (r.stderr or "")[-800:]
        raise SystemExit(f"see-through 失败（{time.time()-t0:.0f}s）：{tail}")
    print(f"[see-through] {time.time()-t0:.0f}s -> {psd}", flush=True)
    return psd


def step_exploded_head(ref: Path, work: Path, seed: int) -> Path | None:
    """跑爆炸图，只取 head.png。失败返回 None（退回 see-through 的头）。"""
    out = work / "exploded"
    out.mkdir(parents=True, exist_ok=True)
    if (out / "head.png").exists():
        print(f"[exploded-head] 复用已有 {out / 'head.png'}", flush=True)
        return out / "head.png"
    t0 = time.time()
    r = _run([PROJ_PY, ROOT / "tools" / "exploded_parts21.py",
              "--ref", str(ref), "--out", str(out), "--seed", str(seed)], timeout=1200)
    head = out / "head.png"
    if not head.exists():
        tail = (r.stdout or "")[-600:] + (r.stderr or "")[-600:]
        print(f"[warn] 爆炸图头部不可用，退回 see-through 的头：{tail}", flush=True)
        return None
    print(f"[exploded-head] {time.time()-t0:.0f}s -> {head}", flush=True)
    return head


def compose_hybrid(st_parts: Path, head_src: Path | None, out: Path) -> dict:
    """把 see-through 的 5 个槽位 + 爆炸图的头合成最终 parts.json。"""
    out.mkdir(parents=True, exist_ok=True)
    roles = ["torso", "arm_l", "arm_r", "leg_l", "leg_r"]
    for r in roles:
        src = st_parts / f"{r}.png"
        if not src.exists():
            raise SystemExit(f"see-through 缺少部件：{src}")
        shutil.copy(src, out / f"{r}.png")
    for extra in ("weapon.png",):
        if (st_parts / extra).exists():
            shutil.copy(st_parts / extra, out / extra)

    torso = Image.open(st_parts / "torso.png").convert("RGBA")
    if head_src and head_src.exists():
        head = Image.open(head_src).convert("RGBA")
        k = (torso.width * 0.85) / head.width          # 头宽按躯干宽对齐（不同底图尺度不同）
        head = head.resize((max(1, int(head.width * k)), max(1, int(head.height * k))),
                           Image.LANCZOS)
    else:
        head = Image.open(st_parts / "head.png").convert("RGBA")
    head.save(out / "head.png")

    # 武器相对躯干的偏移：武器是单独层的，渲染时按这个偏移贴回去，
    # 否则正面看就是「双臂抱胸」（原姿势在持枪，枪被拆走了）
    weapon_off = None
    if (st_parts / "weapon.png").exists():
        import json as _json

        _m = _json.loads((st_parts / "parts.json").read_text(encoding="utf-8"))["roles"]
        try:
            from psd_tools import PSDImage  # noqa: F401  # 仅为确认依赖存在
        except Exception:
            pass
        # st_parts 里的 bbox 是裁边后的，偏移得从 _work 的 PSD 拿
        weapon_off = None

    st_meta = json.loads((st_parts / "parts.json").read_text(encoding="utf-8")).get("roles") or {}
    meta = {}
    for r in ["head", *roles]:
        im = Image.open(out / f"{r}.png").convert("RGBA")
        alpha = im.split()[-1]
        meta[r] = {"file": f"{r}.png",
                   "bbox": list(alpha.getbbox() or (0, 0, im.width, im.height)),
                   # 原点要带下去：渲染时武器按「相对躯干原点」贴回手上
                   "origin": list((st_meta.get(r) or {}).get("origin") or [0, 0]),
                   "area": int(sum(1 for p in alpha.getdata() if p > 40)),
                   "mirrored": False}
    if (out / "weapon.png").exists() and (st_meta.get("weapon") or {}).get("origin"):
        meta["weapon"] = dict(st_meta["weapon"])
    (out / "parts.json").write_text(
        json.dumps({"roles": meta, "source": "hybrid" if head_src else "see-through"},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--resolution", type=int, default=1280)
    ap.add_argument("--seed", type=int, default=7, help="爆炸图抽卡种子")
    ap.add_argument("--no-head", action="store_true", help="不做爆炸图头部（纯 see-through）")
    ap.add_argument("--no-see-through", action="store_true", help="只用爆炸图")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    # 必须绝对化：see-through 子进程会 chdir 到它自己的目录，相对路径会失效
    ref = Path(args.ref).resolve()
    out = Path(args.out).resolve()
    work = out / "_work"
    if (out / "parts.json").exists() and not args.force:
        print(f"[skip] 已有结果 {out/'parts.json'}（--force 重跑）")
        return 0

    t0 = time.time()
    if args.no_see_through:
        r = _run([PROJ_PY, ROOT / "tools" / "exploded_parts21.py",
                  "--ref", str(ref), "--out", str(out), "--seed", str(args.seed)], timeout=1200)
        if r.returncode != 0:
            print((r.stdout or "")[-800:], (r.stderr or "")[-800:])
            return 1
        print(f"DONE（纯爆炸图）{time.time()-t0:.0f}s -> {out}")
        return 0

    psd = step_see_through(ref, work, args.resolution)
    st_parts = work / "st-parts"
    from see_through_rig import build_from_psd  # noqa: PLC0415

    build_from_psd(psd, st_parts)

    head = None if args.no_head else step_exploded_head(ref, work, args.seed)
    meta = compose_hybrid(st_parts, head, out)

    src = "爆炸图头 + see-through身" if head else "see-through"
    print(f"[i] 来源：{src}", flush=True)
    for r, m in meta.items():
        print(f"  {r:7s} bbox={m['bbox']} area={m['area']}", flush=True)
    print(f"DONE {time.time()-t0:.0f}s -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
