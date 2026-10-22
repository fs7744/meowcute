"""透明背景选项的 API 端到端测试：完全按前端载荷格式走
POST /api/generate（params.background="transparent"）→ 轮询任务 →
读取落盘资产验证 alpha。覆盖 payload 校验 / 任务流 / 落盘 / meta 透传。

用法：.venv/Scripts/python.exe tools/test_transparent_api.py
"""
from __future__ import annotations

import io
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_TMP = tempfile.mkdtemp(prefix="meowcute-tbg-")
import os  # noqa: E402
os.environ["MEOWCUTE_DATA_DIR"] = _TMP

import numpy as np  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

from backend.app.main import app  # noqa: E402

PROMPT = "像素风小猫少女，三头身，正面站立，红色短披风"


def main() -> int:
    # 必须用 with：TestClient 不给 with 时每个请求都新建独立事件循环，
    # 请求结束循环即销毁，asyncio.create_task 的后台任务会被取消，
    # 任务永远走不到落盘（生产里 uvicorn 是常驻单循环，无此问题）。
    with TestClient(app) as client:
        return _run(client)


def _run(client: TestClient) -> int:
    fails = []

    r = client.post("/api/projects", json={"name": "透明背景API测试",
                                           "templateId": "pixel_sprite_txt2img"})
    pid = r.json()["project"]["id"]
    print(f"[项目] {pid}")

    # 前端 GenNode 的载荷形状（frontend/src/api.ts walkVideo/generate 同款 params）
    payload = {
        "projectId": pid,
        "prompt": PROMPT,
        "styleId": "pixel_16bit",
        "refs": [],
        "params": {
            "width": 512, "height": 512, "n": 1, "steps": 40, "seed": 777,
            "background": "transparent", "maxSeqLen": None,
        },
        "pixel": {"enabled": False, "gridSnap": True, "colors": 16,
                  "palette": "auto", "gridSize": None, "targetSize": None,
                  "transparentBg": False},
    }
    r = client.post("/api/generate", json=payload)
    tid = r.json()["taskId"]
    print(f"[任务] {tid} 已提交，轮询中…")

    t0 = time.time()
    result = None
    while time.time() - t0 < 600:
        st = client.get(f"/api/tasks/{tid}").json()
        if "result" in st:
            result = st["result"]
            break
        snap = st.get("progress") or {}
        print(f"  …{time.time() - t0:5.0f}s {snap.get('stage') or snap.get('label') or ''}"
              f"{snap.get('step', '')}/{snap.get('total', '')}", flush=True)
        time.sleep(3)
    if result is None:
        print("FAIL: 任务 600s 未完成")
        return 1
    if result.get("error"):
        print(f"FAIL: 任务报错 {result['error']}")
        return 1

    meta = result.get("meta") or {}
    print(f"[meta] family={meta.get('family')} mode={meta.get('mode')} "
          f"bgWarning={meta.get('bgWarning')!r}")
    if meta.get("family") != "qwen21":
        fails.append(f"底座不是 qwen21: {meta.get('family')}")
    if meta.get("bgWarning"):
        fails.append(f"误报底座不支持: {meta['bgWarning']}")

    # 读落盘资产验证 alpha
    img_info = result["images"][0]
    fname = img_info["origAssetId"] if "origAssetId" in img_info else img_info["assetId"]
    p = Path(_TMP) / "projects" / pid / "images" / f"{fname}.png"
    if not p.exists():
        # storage 命名可能不同：找目录里最新的一张
        cands = sorted((Path(_TMP) / "projects" / pid / "images").glob("*.png"),
                       key=os.path.getmtime)
        p = cands[-1] if cands else None
    if p is None or not p.exists():
        print(f"FAIL: 找不到落盘资产（期望 {fname}.png）")
        return 1
    im = Image.open(p)
    a = np.asarray(im.convert("RGBA"))[..., 3]
    h, w = a.shape
    border = np.concatenate([
        a[:int(h * .15), :].ravel(), a[int(h * .85):, :].ravel(),
        a[:, :int(w * .15)].ravel(), a[:, int(w * .85):].ravel()])
    center = a[int(h * .3):int(h * .7), int(w * .3):int(w * .7)]
    st = {"file": p.name, "mode": im.mode,
          "border_transparent": float((border < 10).mean()),
          "center_opaque": float((center > 245).mean()),
          "alpha_min": int(a.min())}
    print(f"[资产] {st}")
    if im.mode != "RGBA":
        fails.append(f"资产不是 RGBA: {im.mode}")
    if st["alpha_min"] >= 10:
        fails.append("资产没有透明像素")
    if st["border_transparent"] < 0.5:
        fails.append(f"边框透明占比过低: {st['border_transparent']:.2f}")
    if st["center_opaque"] < 0.3:
        fails.append(f"中心主体不透明占比过低: {st['center_opaque']:.2f}")

    if fails:
        print("FAIL:")
        for f in fails:
            print("  -", f)
        return 1
    print("PASS：API 端到端透明背景符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
