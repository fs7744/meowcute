"""端到端验证：走真实 HTTP 路由用 HunyuanVideo-1.5 生成一段行走视频。

验证链路：payload(engine=hy15) → walkvideo.run_walk → generate_walk →
DiffusersProvider.generate_video → providers/hy15.py → 帧 + GIF 存盘。

用法：python tools/hy15_e2e.py [--frames 21] [--steps 12] [--dirs single]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CELL = ROOT / ".verify" / "hy15" / "cell0.png"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", type=int, default=21)
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--dirMode", default="single")
    ap.add_argument("--timeout", type=int, default=900)
    args = ap.parse_args()

    from fastapi.testclient import TestClient

    from backend.app.main import app

    with TestClient(app) as client:
        # 1) 建项目
        r = client.post("/api/projects", json={"name": "hy15-e2e", "templateId": "dir_walk"})
        pid = (r.json().get("project") or {}).get("id")
        print("project:", pid, r.status_code)

        # 2) 存一张参考图（走真实的存储层）
        from backend.app import storage
        from PIL import Image

        info = storage.save_image(pid, Image.open(CELL).convert("RGBA"), kind="images", prefix="e2e-src")
        print("source:", info["id"], info["width"], info["height"])

        # 3) 提交行走任务（引擎 hy15）
        body = {
            "projectId": pid,
            "sourceKind": "images",
            "sourceFile": info["id"],
            "dirMode": args.dirMode,
            "engine": "hy15",
            "numFrames": args.frames,
            "steps": args.steps,
            "fps": 12,
            "prompt": ("The soldier keeps stepping on the same spot with a clear alternating "
                       "stride: the rear leg swings forward and plants, then the other leg lifts "
                       "and swings forward. Static camera, plain light-gray background, identical "
                       "outfit and weapon."),
            "motionThreshold": 3.0,   # 单次即可，别触发重试（重试要再跑一轮）
            "retries": 0,
        }
        r = client.post("/api/walkvideo", json=body)
        tid = r.json().get("taskId")
        print("task:", tid, r.status_code)

        t0 = time.time()
        last = ""
        result = None
        while time.time() - t0 < args.timeout:
            st = client.get(f"/api/tasks/{tid}").json()
            pg = st.get("progress") or {}          # /api/tasks 返回 {progress, result}
            label = f"{pg.get('done')}/{pg.get('total')} {pg.get('label') or ''}"
            if label != last:
                print(f"  [{time.time()-t0:6.1f}s] {label}")
                last = label
            if pg.get("doneAt"):
                result = st.get("result")
                if pg.get("error"):
                    print("任务失败:", pg.get("error"))
                    return 1
                print(f"任务完成 {time.time()-t0:.1f}s")
                break
            time.sleep(2)
        else:
            print("超时")
            return 1

        # 结果直接读任务 result.walk（节点 meta 由前端写回，这里不依赖前端）
        meta = (result or {}).get("walk") or {}
        dirs = meta.get("directions") or []
        print("engine:", meta.get("engine"), "| seconds:", meta.get("seconds"))
        for d in dirs:
            print(f"  dir={d['dir']} frames={d['count']} fps={d.get('fps')} gif={bool(d.get('gif'))}")
        print("OK" if dirs else "NO_OUTPUT")
        return 0 if dirs else 1


if __name__ == "__main__":
    raise SystemExit(main())
