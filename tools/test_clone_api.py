"""动作复刻链端到端验证：真 uvicorn + 真文件 + 真模型。

跑的是「新建项目（模板 sprite_pose_clone）→ 拆解精灵图 → 动作复刻 → 精灵图 →
存盘往返」整条链，用来确认接口契约、任务制进度、白名单存盘都对得上。

用法（在项目根目录执行）：
    .venv/Scripts/python.exe tools/test_clone_api.py [steps]
    .venv/Scripts/python.exe tools/test_clone_api.py 0 <精灵图.png> <四视图板.png>

不给路径时会自动从 data/projects 里找一张 gridsheet-*.png 和一张 tv-*.png。
steps 传 0 = 用后端默认（40 步）。
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
STEPS = int(sys.argv[1]) if len(sys.argv) > 1 else 0     # 0 = 后端默认 40 步


def _pick(pattern: str, arg_index: int) -> str:
    if len(sys.argv) > arg_index:
        return sys.argv[arg_index]
    hits = sorted(glob.glob(pattern), key=lambda p: -os.path.getmtime(p))
    if not hits:
        raise SystemExit(f"找不到 {pattern}：请把图片路径作为参数传进来")
    return hits[0]


SHEET = _pick("data/projects/*/images/gridsheet*.png", 2)
BOARD = _pick("data/projects/*/images/tv-*.png", 3)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def http(method, url, body=None, timeout=900):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode() or "{}")


def wait(base, tid, timeout=1200):
    t0 = time.time()
    last = ""
    while time.time() - t0 < timeout:
        j = http("GET", f"{base}/api/tasks/{tid}")
        pr = j.get("progress") or {}
        last = pr.get("label") or last
        if pr.get("doneAt") is not None:
            return j
        time.sleep(0.4)
    raise TimeoutError(last)


tmp = Path(tempfile.mkdtemp(prefix="pf-clone-"))
port = free_port()
base = f"http://127.0.0.1:{port}"
env = {**os.environ, "MEOWCUTE_DATA_DIR": str(tmp)}
log = open(tmp / "uv.log", "wb")
proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "backend.app.main:app",
     "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
    cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT)
try:
    for _ in range(160):
        try:
            http("GET", f"{base}/api/health")
            break
        except Exception:
            time.sleep(0.5)
    print("[0] 服务已起", flush=True)

    r = http("POST", f"{base}/api/projects",
             {"name": "clone-e2e", "templateId": "sprite_pose_clone"})
    proj = r["project"]
    pid = proj["id"]
    print("[1] 建项目", pid,
          [(n["id"][:3], n["type"]) for n in proj["nodes"]], flush=True)

    refs = tmp / "projects" / pid / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SHEET, refs / "spritesrc.png")
    shutil.copyfile(BOARD, refs / "charboard.png")

    t = http("POST", f"{base}/api/spritesplit",
             {"projectId": pid, "file": "spritesrc.png", "kind": "refs"})
    j = wait(base, t["taskId"])
    if j.get("error"):
        raise SystemExit("spritesplit 失败: " + j["error"])
    sp = j["result"]["split"]
    m = sp["meta"]
    print(f"[2] spritesplit: {len(sp['frames'])} 帧  {m['cols']}x{m['rows']} "
          f"cell={m['cellW']}x{m['cellH']} method={m['gridMethod']} "
          f"{m['seconds']}s", flush=True)

    files = [f["file"] for f in sp["frames"][:2]]
    t = http("POST", f"{base}/api/poseclone",
             {"projectId": pid, "mode": "per-frame", "files": files,
              "refs": ["charboard.png"], "steps": STEPS,
              "background": "transparent"})
    j = wait(base, t["taskId"])
    if j.get("error"):
        raise SystemExit("poseclone 失败: " + j["error"])
    cl = j["result"]["clone"]
    cm = cl["meta"]
    print(f"[3] poseclone: {len(cl['frames'])} 帧  iouMean={cm['iouMean']} "
          f"iouMin={cm['iouMin']} retried={cm.get('retried')} "
          f"autoMatted={cm.get('autoMatted')} refPack={cm.get('refPack')} "
          f"{cm['seconds']}s", flush=True)
    if cm.get("warnings"):
        print("    warnings:", cm["warnings"], flush=True)

    t = http("POST", f"{base}/api/spritegrid",
             {"projectId": pid,
              "files": [f["file"] for f in cl["frames"]],
              "cell": m["cellW"], "pivot": "shared"})
    print(f"[4] spritegrid: {t['sheet']['width']}x{t['sheet']['height']} "
          f"count={t['meta']['count']} pivotMode={t['meta'].get('pivotMode')}",
          flush=True)

    nodes = proj["nodes"]
    for n in nodes:
        if n["type"] == "poseclone":
            n["poseclone"].update({"steps": 30, "anchor": "center"})
            n["frames"] = cl["frames"]     # 让白名单有机会保留每帧的 iou
        if n["type"] == "spritesplit":
            n["spritesplit"].update({"picks": files})
            n["frames"] = sp["frames"]
    http("PUT", f"{base}/api/projects/{pid}",
         {"nodes": nodes, "edges": proj["edges"]})
    back = http("GET", f"{base}/api/projects/{pid}")["project"]
    pc = next(n for n in back["nodes"] if n["type"] == "poseclone")["poseclone"]
    spn = next(n for n in back["nodes"] if n["type"] == "spritesplit")["spritesplit"]
    print(f"[5] 存盘往返: steps={pc['steps']} anchor={pc['anchor']} "
          f"picks={spn['picks']}", flush=True)

    # 复刻帧的每帧指标是否也被白名单保住了（刷新后不该消失）
    cf = next(n for n in back["nodes"] if n["type"] == "poseclone").get("frames") or []
    print(f"[6] 复刻帧指标存盘: {len(cf)} 帧, iou 字段保留="
          f"{[round(f.get('iou') or 0, 3) for f in cf]}", flush=True)
    print("[OK] 全链路通过", flush=True)
finally:
    proc.terminate()
    try:
        proc.wait(15)
    except Exception:
        proc.kill()
