# -*- coding: utf-8 -*-
"""test_txtvideo_api.py — 文本生视频 API 端到端测试（真实 GPU 跑一次）。

验证用户报的问题链「结果视频预览看不到」的后端半边 + MP4 默认输出：
1. POST /api/txtvideo（无参考图 = 纯文本，hy15）→ 任务落盘；
2. 默认输出 MP4：images[0].url 是 .mp4、内容是 ftyp 盒、MIME 是 video/mp4
   （结果卡 <video controls> 播的就是这个路由）；
3. meta.video.mode/engine/format/frames 齐备（结果卡徽章的数据源）；
4. interpolate=True 时帧数翻倍（后处理链路在端点里真的跑了）。

用法：.venv/Scripts/python.exe tools/test_txtvideo_api.py [--gif] [--wan]
（--gif 验 GIF 输出分支；--wan 切 Wan 引擎；默认验 MP4 新默认 + hy15）
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="meowcute-txv-")
os.environ["MEOWCUTE_DATA_DIR"] = _TMP

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402

PROMPT = ("像素风小猫少女在草地上奔跑，阳光明媚，镜头缓慢跟随，"
          "双腿持续交替迈步、身体随步伐上下颠动")

FMT = "gif" if "--gif" in sys.argv else "mp4"
ENGINE = "wan" if "--wan" in sys.argv else "hy15"


def main() -> int:
    # 必须 with：TestClient 不给 with 时每个请求独立事件循环，
    # create_task 的后台任务会被取消，永远走不到落盘。
    with TestClient(app) as client:
        r = client.post("/api/projects", json={"name": "文本视频API测试",
                                               "templateId": "txt_video"})
        pid = r.json()["project"]["id"]
        print(f"[项目] {pid}（模板 txt_video，输出 {FMT}，引擎 {ENGINE}）")

        r = client.post("/api/txtvideo", json={
            "projectId": pid,
            "prompt": PROMPT,
            "engine": ENGINE,
            "steps": 12, "numFrames": 13, "fps": 12,
            "width": 848, "height": 480, "maxSide": 848,
            "quality": "standard", "resolution": "480p",
            "interpolate": True,          # 顺带验后处理链路
            "format": FMT,
        })
        tid = r.json()["taskId"]
        print(f"[任务] {tid} 已提交（{ENGINE} 纯文本 + 插帧，输出 {FMT}）…")

        t0 = time.time()
        result = None
        while time.time() - t0 < 600:
            st = client.get(f"/api/tasks/{tid}").json()
            if "result" in st:
                result = st["result"]
                break
            time.sleep(3)
        if result is None:
            print("FAIL: 任务 600s 未完成")
            return 1
        if result.get("error"):
            print(f"FAIL: 任务报错 {result['error']}")
            return 1

        fails = []
        imgs = result.get("images") or []
        if not imgs:
            fails.append("结果没有 images")
        else:
            url = imgs[0].get("url") or ""
            print(f"[资产] {url} {imgs[0].get('width')}×{imgs[0].get('height')}")
            if FMT == "mp4" and not url.lower().endswith(".mp4"):
                fails.append(f"默认输出应为 MP4（新默认）: {url}")
            if FMT == "gif" and not url.lower().endswith(".gif"):
                fails.append(f"显式 gif 应输出 GIF: {url}")
            if not (imgs[0].get("assetId") or ""):
                fails.append("结果卡拿不到 assetId（save_video_stream 的 id 没映射）")
        v = result.get("video") or {}
        print(f"[video] mode={v.get('mode')} engine={v.get('engine')} "
              f"format={v.get('format')} frames={v.get('frames')} "
              f"post={v.get('post')} peak={v.get('vram', {}).get('peakGB')}GB")
        if v.get("mode") != "t2v":
            fails.append(f"mode 应为 t2v: {v.get('mode')}")
        if v.get("engine") != ENGINE:
            fails.append(f"engine 应为 {ENGINE}: {v.get('engine')}")
        if v.get("format") != FMT:
            fails.append(f"format 应为 {FMT}: {v.get('format')}")
        # 插帧后 frames 应报最终帧数（25），不是生成时的 13
        if v.get("frames") != 25:
            fails.append(f"插帧后 frames 应为 25: {v.get('frames')}")
        post = v.get("post") or {}
        if not post.get("interpolated"):
            fails.append(f"后处理记录缺 interpolated: {post}")
        if post.get("out") != 25:
            fails.append(f"插帧输出应为 25 帧: {post.get('out')}")

        # 项目里能按资产 id 读回视频（前端 <video src> / <img> 走的就是这个
        # 路由，文件名要带后缀——路由按后缀定 MIME）
        aid = imgs[0].get("assetId") if imgs else ""
        if aid:
            r2 = client.get(f"/api/projects/{pid}/images/{aid}.{FMT}")
            if r2.status_code != 200:
                fails.append(f"资产读不回: HTTP {r2.status_code}")
            elif FMT == "mp4":
                if r2.content[4:8] != b"ftyp":
                    fails.append("资产内容不是 MP4（ftyp 盒缺失）")
                elif "video/mp4" not in (r2.headers.get("content-type") or ""):
                    fails.append(f"MIME 不是 video/mp4: {r2.headers.get('content-type')}")
                else:
                    print(f"[读回] MP4 {len(r2.content) // 1024}KB，"
                          f"ftyp 正确，MIME {r2.headers.get('content-type')}")
            else:
                if not (r2.content[:6].startswith(b"GIF89a")
                        or r2.content[:6].startswith(b"GIF87a")):
                    fails.append("资产内容不是 GIF 魔数")
                else:
                    print(f"[读回] GIF {len(r2.content) // 1024}KB，魔数正确")

        for f in fails:
            print("FAIL:", f)
        print("API_E2E_" + ("FAIL" if fails else "PASS"))
        return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
