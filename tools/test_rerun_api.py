"""端到端验证「补跑 / 重跑指定帧」：真 uvicorn + 真模型。

验的是三件事：
  1. only=[k] + keepExisting=True 时，只跑第 k 帧，结果带 partial 标记
  2. 之前跑出来的帧文件**不会被清掉**（这是「不必整批重来」的前提）
  3. 文件名按源帧下标稳定，重跑第 k 帧不会顶掉别的帧

实测（2026-10-27）：only=[0] 出 cloned-...-1-*.png；only=[2] 出
cloned-...-3-*.png，且第一张仍在磁盘上。

用法：.venv/Scripts/python.exe tools/test_rerun_api.py [steps]
"""
import glob
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(".").resolve()
PID = "p-dbdb6b3dbc19"          # 有 12 帧 120×120 的那个项目（就是全失败的那批）
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 20


def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close(); return p


def http(method, url, body=None, timeout=900):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait(base, tid, timeout=900):
    t0 = time.time(); last = ""
    while time.time() - t0 < timeout:
        j = http("GET", f"{base}/api/tasks/{tid}")
        pr = j.get("progress") or {}
        last = pr.get("label") or last
        if pr.get("doneAt") is not None:
            return j
        time.sleep(0.5)
    raise TimeoutError(last)


tmp = Path(tempfile.mkdtemp(prefix="pf-rerun-"))
port = free_port(); base = f"http://127.0.0.1:{port}"
env = {**os.environ, "MEOWCUTE_DATA_DIR": str(tmp)}
log = open(tmp / "uv.log", "wb")
proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.app.main:app",
                         "--host", "127.0.0.1", "--port", str(port),
                         "--log-level", "warning"],
                        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(200):
        try:
            http("GET", f"{base}/api/health"); break
        except Exception:
            time.sleep(0.5)

    proj = http("POST", f"{base}/api/projects",
                {"name": "rerun-e2e", "templateId": "sprite_pose_clone"})["project"]
    pid = proj["id"]
    print("[0] 建项目", pid, flush=True)

    src = Path("data") / "projects" / PID
    dst = tmp / "projects" / pid
    for kind in ("images", "refs"):
        (dst / kind).mkdir(parents=True, exist_ok=True)
    pj = json.loads((src / "project.json").read_text(encoding="utf-8"))
    frames = [n for n in pj["nodes"]
              if n.get("type") == "spritesplit" and n.get("frames")][0]["frames"]
    files = []
    for f in frames[:4]:
        shutil.copyfile(src / "images" / f["file"], dst / "images" / f["file"])
        files.append(f["file"])
    refs = []
    for n in pj["nodes"]:
        if n.get("type") == "input":
            for r in (n.get("refs") or []):
                p = src / "refs" / r.get("file")
                if p.exists():
                    shutil.copyfile(p, dst / "refs" / r.get("file"))
                    refs.append(r.get("file"))
    print(f"[1] 拷入 {len(files)} 帧 + {len(refs)} 张参考图", flush=True)

    def run(only, keep, tag):
        body = {"projectId": pid, "mode": "per-frame", "files": files,
                "refs": refs, "steps": STEPS, "background": "transparent",
                "only": only, "keepExisting": keep}
        t = http("POST", f"{base}/api/poseclone", body)
        j = wait(base, t["taskId"])
        res = j.get("result") or {}
        if j.get("progress", {}).get("error") or res.get("error"):
            raise SystemExit(f"{tag} 失败: "
                             f"{j.get('progress', {}).get('error') or res.get('error')}")
        cl = res.get("clone") or {}
        if not cl:
            raise SystemExit(f"{tag} 没有 clone 结果：{list(res)}")
        got = [(f["srcIndex"], f["file"]) for f in cl["frames"]]
        print(f"[{tag}] partial={cl.get('partial')} 产出 {len(got)} 帧 "
              f"srcIndex={[g[0] for g in got]}", flush=True)
        for g in got:
            print(f"       srcIndex={g[0]} file={g[1]}", flush=True)
        return cl

    print("--- 第一次：只跑第 1 帧（下标 0）---", flush=True)
    c1 = run([0], True, "only=0")
    f1 = c1["frames"][0]["file"]

    print("--- 第二次：只跑第 3 帧（下标 2），第 1 帧必须还在 ---", flush=True)
    c2 = run([2], True, "only=2")
    f2 = c2["frames"][0]["file"]

    left = [p.name for p in (dst / "images").glob("cloned-*.png")]
    print("[3] 磁盘上的 cloned-* ：", sorted(left), flush=True)
    ok_keep = f1 in left
    ok_new = f2 in left and f2 != f1
    print(f"[4] 第一次的帧还在 = {ok_keep}；新帧没覆盖它 = {ok_new}", flush=True)
    print("[OK] 全链路通过" if (ok_keep and ok_new and c2.get("partial")) else "[FAIL]",
          flush=True)
finally:
    proc.terminate()
    try:
        proc.wait(15)
    except Exception:
        proc.kill()
