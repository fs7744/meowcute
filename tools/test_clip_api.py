"""端到端验证「视频转素材」链：真实 uvicorn 服务 + 真实 mp4。

为什么不用 TestClient：新版 starlette 的 TestClient **每个请求建一个新的事件
循环**。任务制接口里 `asyncio.create_task(...)` 起的后台协程挂在那个循环上，
请求一结束循环就没了 —— 表现为「进度停在中途、任务永不完成」。真实 uvicorn
只有一个常驻循环，没有这个问题。所以：**秒级任务可以用 TestClient 测，长任务
（抠图、视频生成）必须起真服务**。

本脚本自己拉起 uvicorn（临时数据目录，不污染 data/），跑完自动关掉。

用法：
    .venv/Scripts/python.exe tools/test_clip_api.py [视频路径]
不给路径时自动找项目里最近的一段 mp4。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out" / "clip_test"
sys.path.insert(0, str(ROOT))


def http(method: str, url: str, body: dict | None = None, raw: bool = False):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=60) as r:
        buf = r.read()
        return buf if raw else json.loads(buf or b"{}")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def newest_mp4() -> Path | None:
    cands = sorted(ROOT.glob("data/projects/*/images/*.mp4"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0] if cands else None


def wait(base: str, tid: str, timeout: float = 600.0) -> dict:
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        j = http("GET", f"{base}/api/tasks/{tid}")
        p = j.get("progress") or {}
        label = p.get("label") or ""
        if label != last:
            done, total = p.get("done"), p.get("total")
            print(f"    [{time.time() - t0:5.1f}s] {label}"
                  + (f"  ({done}/{total})" if total else ""), flush=True)
            last = label
        if p.get("doneAt") is not None:
            return j
        time.sleep(0.25)
    raise TimeoutError(f"任务超时：{tid}（最后进度：{last}）")


def main() -> int:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else newest_mp4()
    if not src or not src.is_file():
        print("找不到可用的 mp4：请显式传路径")
        return 2
    print(f"源视频：{src}  ({src.stat().st_size / 1024:.0f} KB)")
    OUT.mkdir(parents=True, exist_ok=True)

    tmp = Path(tempfile.mkdtemp(prefix="pixelforge-clip-"))
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = {**os.environ, "MEOWCUTE_DATA_DIR": str(tmp)}
    log = open(tmp / "uvicorn.log", "wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        for _ in range(120):
            try:
                http("GET", f"{base}/api/health")
                break
            except Exception:
                time.sleep(0.5)
        else:
            print("服务没起来：", (tmp / "uvicorn.log").read_text(encoding="utf-8",
                                                          errors="replace")[-800:])
            return 1

        r = http("POST", f"{base}/api/projects",
                 {"name": "clip-e2e", "templateId": "clip_to_sprite"})
        pid = r["project"]["id"]
        print(f"项目：{pid}")

        # 把源视频拷进项目（模拟上传后的状态；走同一套资产目录）
        assets = tmp / "projects" / pid / "images"
        assets.mkdir(parents=True, exist_ok=True)
        dst = assets / f"vidsrc-{src.stem[-8:]}{src.suffix}"
        shutil.copyfile(src, dst)
        print(f"已导入：{dst.name}  ({dst.stat().st_size / 1024:.0f} KB)")

        # ---- 拆帧（真抠图）----
        print("\n[1/2] 拆帧 + 抠图")
        t0 = time.time()
        r = http("POST", f"{base}/api/clipsplit", {
            "projectId": pid, "file": dst.name,
            "fpsCap": 12, "maxFrames": 60, "matte": True,
            "tolerance": 40, "maxSide": 512,
        })
        j = wait(base, r["taskId"])
        res = j.get("result") or {}
        if res.get("error"):
            print("  拆帧失败：", res["error"])
            return 1
        clip = res["clip"]
        frames, meta = clip["frames"], clip["meta"]
        s = clip["source"]
        print(f"  源：{s['width']}×{s['height']} {s['fps']}fps {s['durationSec']}s "
              f"共 {meta['sourceFrames']} 帧")
        print(f"  拆出 {len(frames)} 帧（步长 {meta['step']}，有效 {meta['effFps']}fps，"
              f"抽稀={'是' if meta['thinned'] else '否'}）"
              f" 抠图失败 {meta['matteErrors']} 帧")
        print(f"  帧间差异 中位 {meta['diffMedian']}% / 最大 {meta['diffMax']}%")
        print(f"  耗时 {time.time() - t0:.1f}s")

        # 落一份帧缩略图横排，便于肉眼检查抠图质量
        from PIL import Image
        try:
            thumbs = []
            for f in frames[:10]:
                p = assets / f["thumbFile"]
                thumbs.append(Image.open(p).convert("RGBA"))
            if thumbs:
                w = sum(t.width for t in thumbs) + 4 * (len(thumbs) - 1)
                h = max(t.height for t in thumbs)
                strip = Image.new("RGBA", (w, h), (26, 28, 34, 255))
                x = 0
                for t in thumbs:
                    strip.alpha_composite(t, (x, 0))
                    x += t.width + 4
                strip.save(OUT / "frames_strip.png")
                print(f"  帧缩略图 → {OUT / 'frames_strip.png'}")
        except Exception as e:
            print("  （缩略图汇总跳过：", e, "）")

        # 模拟「用户挑帧」：近似度过滤压掉重复姿势
        from backend.app.pipelines.clipframes import diff_percent
        picked, last_hash = [], ""
        SIM = max(1.0, meta["diffMedian"] * 0.8)
        for f in frames:
            if not picked:
                picked.append(f)
                last_hash = f["hash"]
                continue
            if diff_percent(last_hash, f["hash"]) < SIM:
                continue
            picked.append(f)
            last_hash = f["hash"]
        print(f"  近似度过滤（阈值 {SIM:.1f}%）：{len(frames)} → {len(picked)} 帧")

        # ---- 精灵图 ----
        print("\n[2/2] 拼精灵图")
        for tag, sel in (("all", frames), ("filtered", picked)):
            t0 = time.time()
            g = http("POST", f"{base}/api/spritegrid", {
                "projectId": pid,
                "files": [f["file"] for f in sel],
                "cell": 128, "cols": 0, "padding": 2, "margin": 4,
                "anchor": "bottom", "sharedScale": True, "fps": meta["effFps"],
            })
            m = g["meta"]
            print(f"  [{tag}] {m['count']} 帧 → {m['cols']}×{m['rows']} 格 "
                  f"{m['sheetSize'][0]}×{m['sheetSize'][1]}"
                  f"  自检={'通过' if m['checks'].get('cellsMatchFrames') else '异常'}"
                  f"  缩放 {m.get('scaleRange')}"
                  f"  耗时 {(time.time() - t0) * 1000:.0f}ms")
            for w in (m.get("warnings") or [])[:4]:
                print(f"        ⚠ {w}")
            name = g["sheet"]["file"]
            Image.open(assets / name).save(OUT / f"spritesheet_{tag}.png")
            idx = g["indexFile"]
            (OUT / f"index_{tag}.json").write_text(
                (assets / idx).read_text(encoding="utf-8"), encoding="utf-8")
            print(f"        → {OUT / f'spritesheet_{tag}.png'}")
        print(f"\n产物目录：{OUT}")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()
        log.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
