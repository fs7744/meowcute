"""端到端：拆件 → 骨骼行走（真实 HTTP 路由）。

用法：python tools/parts_e2e.py [--ref .verify/hy15/cell0.png] [--with-head]
说明：see-through 首次要 ~3 分钟；PAND 缓存放在 data/projects/<pid>/parts-work/ 下可复用。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default=str(ROOT / ".verify" / "hy15" / "cell0.png"))
    ap.add_argument("--with-head", action="store_true")
    ap.add_argument("--timeout", type=int, default=1800)
    args = ap.parse_args()

    from fastapi.testclient import TestClient

    from backend.app import storage
    from backend.app.main import app

    with TestClient(app) as client:
        pid = client.post("/api/projects", json={"name": "parts-e2e",
                                                "templateId": "dir_walk"}).json()["project"]["id"]
        from PIL import Image

        info = storage.save_image(pid, Image.open(args.ref).convert("RGBA"),
                                  kind="images", prefix="parts-src")
        print(f"project={pid} src={info['id']}")

        def wait(tid: str) -> dict:
            t0 = time.time()
            last = ""
            while time.time() - t0 < args.timeout:
                j = client.get(f"/api/tasks/{tid}").json()
                pg = j.get("progress") or {}
                label = f"{pg.get('done')}/{pg.get('total')} {pg.get('label') or ''}"
                if label != last:
                    print(f"  [{time.time()-t0:6.1f}s] {label}")
                    last = label
                if pg.get("doneAt"):
                    if pg.get("error"):
                        print("失败:", pg["error"][:400])
                        return {}
                    return j
                time.sleep(1)
            print("超时")
            return {}

        print("== 拆件 ==")
        j = wait(client.post("/api/parts", json={
            "projectId": pid, "sourceKind": "images", "sourceFile": info["id"],
            "resolution": 1280, "withHead": args.with_head, "seed": 7,
        }).json()["taskId"])
        if not j:
            return 1
        parts = (j.get("result") or {}).get("parts") or {}
        print(f"  来源={parts.get('source')} 用时={parts.get('meta',{}).get('seconds')}s")
        for role, p in (parts.get("parts") or {}).items():
            print(f"    {role:7s} {p['width']}x{p['height']} {p['assetId']}")
        print(f"    武器: {bool(parts.get('weapon'))}")

        print("== 骨骼行走 ==")
        j2 = wait(client.post("/api/rigwalk", json={
            "projectId": pid, "frames": 8, "swing": 13, "hold": False, "fps": 8,
        }).json()["taskId"])
        if not j2:
            return 1
        rig = (j2.get("result") or {}).get("rig") or {}
        print(f"  帧数={len((j2.get('result') or {}).get('frames') or [])} "
              f"步幅={rig.get('stridePx')} 面积={rig.get('areaMean')}±{rig.get('areaStdPct')}% "
              f"渲染={rig.get('renderSecs')}s 总={rig.get('seconds')}s")
        print(f"  GIF: {(rig.get('gif') or {}).get('url')}")
        print("OK")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
