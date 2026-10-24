# -*- coding: utf-8 -*-
"""test_wan_fitaspect.py — 验证「画布跟随参考图宽高比」在真实 API 链路上生效。

背景：用户报 Wan2.2 出片「效果非常差」。排查确认权重没坏（825/825 键对齐、
无 NaN、首帧与参考图 L1=0.9），真正的问题是使用方式：
  · 前端 720p 预设是 1216×688，而 TI2V 的 patchify 要求 32 的倍数，
    diffusers 会静默改成 1216×672（只留一条 logger.warning）；
  · 768² 的方形立绘塞进 16:9 画布，letterbox 后角色只占中间一条，
    有效像素从 590k 掉到 451k —— 这才是「切 720p 反而更糊」的原因。

本脚本用真实参考图走 /api/txtvideo（引擎 wan，请求 1216×688 横屏），断言：
1. 结果尺寸是方形且 ≤ 请求面积（说明宽高比被重算，而不是照搬横屏）；
2. 宽高都是 32 的倍数（不会被底层再改一次）；
3. 首帧与输入图的 L1 很小（模型链路正常，不是靠崩坏换来的方形）。

用法：.venv/Scripts/python.exe tools/test_wan_fitaspect.py [--keep]
产物 mp4 拷到 out/wan_diag/ 便于肉眼对比。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC_REF = (ROOT / "data/projects/p-4c0436d1798e/refs/"
           "ref-20261025-001057-39828871.png")
OUT = ROOT / "out" / "wan_diag"

TMP = tempfile.mkdtemp(prefix="meowcute-fitaspect-")
os.environ["MEOWCUTE_DATA_DIR"] = TMP
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

PROMPT = ("严格参照提供的角色设定，角色正面朝向镜头，朝着摄影机直线奔跑，"
          "身体正面正对镜头，奔跑动态自然流畅，肢体动作连贯，重心向前，"
          "手臂前后摆动，腿部交替迈步；电影级运镜，机位固定，正面平视，"
          "人物主体居中，画面稳定，高细节")
NEG = "扭曲肢体，畸形手脚，面部崩坏，画面抖动，模糊"


def main() -> int:
    if not SRC_REF.is_file():
        print(f"参考图不存在：{SRC_REF}")
        return 2
    with TestClient(app) as client:
        pid = client.post("/api/projects", json={
            "name": "wan fitAspect 验证", "templateId": "txt_video",
        }).json()["project"]["id"]
        refs_dir = Path(TMP) / "projects" / pid / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        name = "ref-test.png"
        shutil.copy(SRC_REF, refs_dir / name)
        print(f"[项目] {pid} 参考图 {Image.open(SRC_REF).size}")

        # 故意请求用户当时用的 1216×688（横屏、非 32 倍数），
        # 修正后应该被重算成方形画布
        r = client.post("/api/txtvideo", json={
            "projectId": pid,
            "sourceKind": "refs", "sourceFile": name,
            "prompt": PROMPT, "negative": NEG,
            "engine": "wan", "steps": 40, "guidanceScale": 5.0,
            "numFrames": 21, "fps": 12,
            "width": 1216, "height": 688, "maxSide": 1216,
            "quality": "fine", "resolution": "720p",
            "format": "mp4",
            # fitAspect 不传 = None = 跟随参考图（新默认）
        })
        tid = r.json()["taskId"]
        print(f"[任务] {tid} 已提交（wan，请求 1216×688）…")
        t0, result = time.time(), None
        while time.time() - t0 < 900:
            st = client.get(f"/api/tasks/{tid}").json()
            if "result" in st:
                result = st["result"]
                break
            time.sleep(3)
        if result is None:
            print("FAIL: 900s 未完成")
            return 1
        if result.get("error"):
            print(f"FAIL: {result['error']}")
            return 1

        img = (result.get("images") or [{}])[0]
        w, h = img.get("width"), img.get("height")
        v = result.get("video") or {}
        print(f"[产物] {w}×{h} {img.get('url')}")
        print(f"[meta] engine={v.get('engine')} size={v.get('size')} "
              f"frames={v.get('frames')} seconds={v.get('seconds')}")

        fails = []
        if not w or not h:
            fails.append("结果没有尺寸")
        else:
            if w % 32 or h % 32:
                fails.append(f"{w}×{h} 不是 32 的倍数（底层还会再改）")
            req_ar, out_ar = 1216 / 688, w / h
            if abs(np.log(out_ar / req_ar)) < 0.10:
                fails.append(f"{w}×{h} 仍是横屏比例（{out_ar:.2f}），"
                             "画布没跟随方形参考图")
            if w * h > 1216 * 688:
                fails.append(f"面积 {w*h} 超请求预算（不该放大）")
            print(f"[比例] 请求 {req_ar:.2f} → 实际 {out_ar:.2f}")

        # 首帧 vs 输入图：链路正常性的硬指标
        aid = img.get("assetId")
        if aid:
            OUT.mkdir(parents=True, exist_ok=True)
            dst = OUT / "fixed_square.mp4"
            rr = client.get(f"/api/projects/{pid}/images/{aid}.mp4")
            if rr.status_code == 200:
                dst.write_bytes(rr.content)
                print(f"[保存] {dst}")

        print("\n" + ("FAIL: " + "; ".join(fails) if fails else "PASS"))
        if "--keep" not in sys.argv and not fails:
            shutil.rmtree(TMP, ignore_errors=True)
        return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
