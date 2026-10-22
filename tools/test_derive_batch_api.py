"""API 端到端：一次提交多张正视图 → 视角批量推导（真机，小尺寸）。

只验**接线**，不验画质：512² / 20 步，两张各十几秒。要看生成质量看
tools/test_view_batch.py（768² / 40 步）。

    .venv/Scripts/python.exe tools/test_derive_batch_api.py
"""
import asyncio
import pathlib
import shutil
import sys
import time

sys.path.insert(0, ".")

from fastapi.testclient import TestClient

from backend.app.config import PROJECTS_DIR
from backend.app.main import app

FRONTS = [
    pathlib.Path(".verify/view-consistency/front-gray-0.png"),
    pathlib.Path(".verify/view-consistency/front-gray-1.png"),
]


def main() -> int:
    with TestClient(app) as client:
        # 用四视图模板建项目：它本来就带 viewgen / threeview 节点，接线最接近真实
        r = client.post("/api/projects",
                        json={"name": "batch-derive-api-test",
                              "templateId": "char_three_view"})
        r.raise_for_status()
        pid = r.json()["project"]["id"]
        # 直接把两张正视图塞进 images/，当作「生成图抽了 2 张」的产物
        names = []
        for i, src in enumerate(FRONTS):
            aid = f"fa{i}"
            shutil.copy(src, PROJECTS_DIR / pid / "images" / f"{aid}.png")
            names.append(aid)
        print("project", pid, "sources", names, flush=True)

        r = client.post("/api/threeview/derive", json={
            "projectId": pid,
            "view": "back",
            "sources": [{"kind": "images", "file": n} for n in names],
            "width": 512, "height": 512,
            "steps": 20,
            "seed": 7,
            "background": "gray",
            "character": "灰白英短猫武士，红色短披风，左腰侧挎武士刀，三头身比例",
        })
        r.raise_for_status()
        tid = r.json()["taskId"]
        t0 = time.time()
        while True:
            time.sleep(2)
            st = client.get(f"/api/tasks/{tid}").json()
            pr = st.get("progress") or {}
            res = st.get("result")
            if res:
                break
            if time.time() - t0 > 600:
                print("超时")
                return 1
            print("  ", pr.get("done"), "/", pr.get("total"), pr.get("label"),
                  flush=True)
        if res.get("error"):
            print("ERROR", res["error"])
            return 1
        imgs = res.get("images") or []
        print(f"\n返回 {len(imgs)} 张（期望 {len(names)}）耗时 {res.get('seconds')}s")
        for im in imgs:
            print("  pairIndex", im.get("pairIndex"), "src", im.get("srcAssetId"),
                  im.get("width"), "x", im.get("height"), im.get("assetId"))
        metas = res.get("meta", {}).get("batch") or []
        print("每张的指令长度", [len(m.get("instruction", "")) for m in metas])
        ok = (len(imgs) == len(names)
              and [im.get("pairIndex") for im in imgs] == [0, 1]
              and [im.get("srcAssetId") for im in imgs] == names)
        print("接线正确" if ok else "接线不对")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
