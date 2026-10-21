"""冒烟测试：不依赖 GPU / 模型也能跑的核心链路。

覆盖：环境 / 模板 / 画风 / 示例库 CRUD / 设置 / 项目 CRUD / 提示词预览 /
像素精修（合成图）/ 资产读取。所有数据写在临时目录，不污染 data/。

用法：python backend/smoke_test.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from pathlib import Path

# 必须在 import app.* 之前设数据目录
_TMP = tempfile.mkdtemp(prefix="meowcute-test-")
os.environ["MEOWCUTE_DATA_DIR"] = _TMP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

from backend.app.main import app  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok  {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def make_fake_pixel_art(size: int = 512, block: int = 8) -> Image.Image:
    """合成一张「AI 式像素图」：8×8 块 + 轻微边缘抖动，验证网格对齐。"""
    img = Image.new("RGB", (size, size), (250, 250, 246))
    d = ImageDraw.Draw(img)
    cols = [(40, 40, 60), (220, 70, 90), (60, 140, 220), (240, 190, 60), (80, 180, 120)]
    # 画一个简单角色剪影：头 + 身体 + 手臂，全部按 block 对齐
    blocks = []
    for y in range(6, 58):
        for x in range(18, 46):
            cx, cy = x - 32, y - 32
            if cx * cx + (cy + 10) * (cy + 10) < 120:      # 头
                blocks.append((x, y, 0))
            elif abs(cx) < 12 and 0 < cy < 22:              # 身体
                blocks.append((x, y, 1))
            elif 12 <= abs(cx) < 18 and -2 < cy < 14:       # 手臂
                blocks.append((x, y, 4))
    for x, y, c in blocks:
        d.rectangle([x * block, y * block, (x + 1) * block - 1, (y + 1) * block - 1],
                    fill=cols[c])
    # 加一点抗锯齿噪声（模拟 AI 出图的毛边）
    px = img.load()
    for y in range(0, size, 3):
        for x in range(0, size, 3):
            r, g, b = px[x, y]
            if r < 240 or g < 240:
                px[x, y] = (min(255, r + 6), min(255, g + 4), min(255, b + 2))
    return img


def main() -> int:
    client = TestClient(app)

    print("== 基础 / 环境 ==")
    r = client.get("/api/health")
    check("health 200", r.status_code == 200)
    check("health 带版本", r.json().get("version") == "0.1.0")
    r = client.get("/api/doctor")
    check("doctor 200", r.status_code == 200, str(r.status_code))
    check("doctor 有检查项", len(r.json().get("items", [])) >= 5)
    r = client.get("/api/models")
    check("models 200", r.status_code == 200)

    print("== 模板 / 画风 ==")
    r = client.get("/api/templates")
    ts = r.json().get("templates", [])
    check("模板列表非空", len(ts) >= 1)
    check("第一个模板可用", ts[0]["status"] == "available")
    check("模板有步骤", len(ts[0].get("steps", [])) >= 3)
    r = client.get("/api/styles")
    st = r.json().get("styles", [])
    check("画风 15 个", len(st) == 15, str(len(st)))
    check("像素画风标记", any(s.get("pixelClass") for s in st))
    check("风格词可读", all(s.get("words") for s in st))

    print("== 提示词示例 ==")
    r = client.get("/api/prompt-examples")
    ex = r.json()
    check("示例库有内置条目", len(ex["examples"]) >= 10, str(len(ex["examples"])))
    check("示例分类齐全", len(ex["categories"]) == 5)
    r = client.post("/api/prompt-examples", json={
        "category": "角色描述", "title": "测试条目",
        "content": "一只测试用的像素小猫", "note": "测试备注"})
    eid = r.json()["example"]["id"]
    check("新增示例", r.status_code == 200 and eid)
    r = client.post("/api/prompt-examples", json={"id": eid, "content": "改过的内容"})
    check("编辑示例", any(e["id"] == eid and e["content"] == "改过的内容"
                       for e in r.json()["example"].values() if isinstance(e, dict))
          or r.json()["example"]["content"] == "改过的内容")
    r = client.delete(f"/api/prompt-examples/{eid}")
    check("删除示例", r.status_code == 200)

    print("== 设置 ==")
    r = client.get("/api/settings")
    s = r.json()["settings"]
    check("设置含 diffusers", "diffusers" in s.get("providers", {}))
    check("设置含像素默认", "pixel" in s)
    r = client.put("/api/settings", json={"defaults": {"steps": 9, "width": 512, "height": 512}})
    check("保存设置", r.status_code == 200)

    print("== 项目 ==")
    r = client.post("/api/projects", json={"name": "测试项目", "templateId": "pixel_sprite_txt2img"})
    pid = r.json()["project"]["id"]
    check("新建项目", r.status_code == 200 and pid)
    proj = r.json()["project"]
    check("项目自带生成卡", any(n["type"] == "generate" for n in proj["nodes"]))
    check("生成模板自带扣背景节点",
          any(n["type"] == "matte" and n["matte"].get("enabled") for n in proj["nodes"]))

    r = client.get(f"/api/projects/{pid}")
    check("读取项目", r.status_code == 200)
    r = client.get("/api/projects")
    check("项目列表含新项目", any(p["id"] == pid for p in r.json()["projects"]))

    # 画布保存：加入一张结果卡
    nodes = proj["nodes"] + [{
        "id": "res-test", "type": "result", "x": 700, "y": 120, "genId": proj["nodes"][0]["id"],
        "images": [], "prompt": "p", "negative": "n", "meta": {"seed": 1}}]
    r = client.put(f"/api/projects/{pid}", json={
        "nodes": nodes, "edges": [{"from": proj["nodes"][0]["id"], "to": "res-test"}],
        "viewport": {"x": -40, "y": 20, "z": 0.8}})
    check("保存画布", r.status_code == 200, str(r.status_code))
    saved = r.json()["project"]
    check("节点数 +1", len(saved["nodes"]) == len(proj["nodes"]) + 1)
    check("边保存", len(saved["edges"]) == 1)
    check("视口保存", abs(saved["viewport"]["z"] - 0.8) < 1e-6)

    r = client.patch(f"/api/projects/{pid}/name", json={"name": "改名后的项目"})
    check("重命名", r.json()["project"]["name"] == "改名后的项目")

    print("== 参考图上传 ==")
    buf = io.BytesIO()
    make_fake_pixel_art(256, 8).save(buf, "PNG")
    buf.seek(0)
    r = client.post(f"/api/projects/{pid}/refs",
                    files={"file": ("ref.png", buf.getvalue(), "image/png")})
    check("上传参考图", r.status_code == 200, str(r.status_code))
    ref_name = r.json()["ref"]["file"]
    ref_obj = r.json()["ref"]
    r = client.get(f"/api/projects/{pid}/refs/{ref_name}")
    check("读取参考图", r.status_code == 200 and r.content[:4] == b"\x89PNG")

    print("== 提示词预览 ==")
    r = client.post("/api/generate/preview", json={
        "prompt": "灰白英短猫武士，红披风",
        "styleId": "pixel_16bit",
        "width": 512, "height": 512,
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16,
                  "palette": "auto", "gridSize": None, "targetSize": None}})
    pv = r.json()
    check("预览 200", r.status_code == 200)
    check("风格词在最前", pv["prompt"].startswith(pv["styleWords"]), pv["prompt"][:40])
    check("含像素契约", "像素网格" in pv["prompt"])
    check("负面词含抗锯齿", "抗锯齿" in pv["negative"])
    check("分段说明 4 段", len(pv["breakdown"]) == 4)
    # 背景选项：绿幕句要真的进提示词
    r = client.post("/api/generate/preview", json={
        "prompt": "一只猫", "styleId": "pixel_16bit", "background": "green",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    check("绿幕背景进提示词", "#00FF00" in r.json()["prompt"])
    r = client.post("/api/generate/preview", json={
        "prompt": "一只猫", "styleId": "pixel_16bit", "background": "gray",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    check("中灰背景进提示词", "#808080" in r.json()["prompt"])
    r = client.post("/api/generate/preview", json={
        "prompt": "一只猫", "styleId": "pixel_16bit", "background": "none",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    check("无背景不写背景句", "#00FF00" not in r.json()["prompt"] and "#808080" not in r.json()["prompt"])
    # 幂等：提示词里已含风格词不应重复
    r2 = client.post("/api/generate/preview", json={
        "prompt": pv["prompt"], "styleId": "pixel_16bit",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    check("提示词幂等", r2.json()["prompt"].count(pv["styleWords"].split("，")[0]) == 1)

    print("== 像素精修（合成图） ==")
    from backend.app.pipelines import refine as refine_mod
    fake = make_fake_pixel_art(512, 8)
    rb = refine_mod.refine_batch([fake], grid_snap=True, colors=16, palette_name="auto")
    item = rb["items"][0]
    meta = item["meta"]
    check("检测到网格", meta.get("grid") is not None, str(meta.get("grid")))
    check("网格接近 64", abs(meta["grid"][0] - 64) <= 6, str(meta.get("grid")))
    check("输出为原生像素尺寸", item["image"].width <= 96, str(item["image"].size))
    check("色板外颜色为 0", meta.get("offPalette") == 0, str(meta.get("offPalette")))
    check("颜色数受控", meta.get("colors", 99) <= 20, str(meta.get("colors")))
    # 手工指定网格
    rb2 = refine_mod.refine_batch([fake], grid_snap=True, grid_size=[32, 32], colors=8,
                                  palette_name="auto")
    check("指定 32 网格生效", rb2["items"][0]["meta"]["grid"] == [32, 32])
    # 固定色板
    rb3 = refine_mod.refine_batch([fake], grid_snap=True, colors=16, palette_name="gameboy")
    check("GameBoy 4 色", rb3["items"][0]["meta"]["colors"] <= 4,
          str(rb3["items"][0]["meta"]["colors"]))

    print("== 重新精修接口 ==")
    orig = Image.new("RGBA", (512, 512), (255, 255, 255, 255))
    d = ImageDraw.Draw(orig)
    for i in range(0, 512, 64):
        d.rectangle([i, 128, i + 31, 383], fill=(220, 60, 80, 255))
    from backend.app import storage
    info = storage.save_image(pid, orig, "images")
    r = client.post("/api/pixel/refine", json={
        "projectId": pid, "origAssetId": info["id"],
        "pixel": {"enabled": True, "gridSnap": True, "colors": 8, "palette": "auto"}})
    check("重精修 200", r.status_code == 200, str(r.status_code))
    check("重精修出图", r.json()["image"]["width"] > 0)
    px_id = r.json()["image"]["assetId"]

    print("== 扣除背景接口 ==")
    # 纯色灰底 + 中间一块主体：抠完四角应透明、中心应保留
    mat_src = Image.new("RGBA", (256, 256), (128, 128, 128, 255))
    dm = ImageDraw.Draw(mat_src)
    dm.ellipse([70, 40, 186, 200], fill=(230, 140, 60, 255))
    minfo = storage.save_image(pid, mat_src, "images")
    r = client.get("/api/matte/status")
    check("抠图引擎状态 200", r.status_code == 200 and bool(r.json().get("primary")))
    r = client.post("/api/matte", json={
        "projectId": pid, "origAssetId": minfo["id"],
        "matte": {"enabled": True, "method": "auto", "hard": True}})
    check("扣背景 200", r.status_code == 200, str(r.status_code))
    body = r.json()["image"]
    check("扣背景出图", body["width"] > 0 and bool(body.get("matte", {}).get("method")))
    cut = storage.load_image(pid, "images", f"{body['assetId']}.png")
    check("四角变透明", cut is not None and cut.getpixel((2, 2))[3] == 0,
          str(None if cut is None else cut.getpixel((2, 2))))
    check("主体保留", cut is not None and cut.getpixel((128, 120))[3] == 255,
          str(None if cut is None else cut.getpixel((128, 120))))

    # 边缘偏移：内缩一定比外扩留的像素少（啃边 / 留边时的可预测旋钮）
    def _alpha_count(im):
        if im is None:
            return -1
        return sum(1 for y in range(im.height) for x in range(im.width)
                   if im.getpixel((x, y))[3] > 8)

    def _bottom_row(im):
        if im is None:
            return -1
        for y in range(im.height - 1, -1, -1):
            for x in range(im.width):
                if im.getpixel((x, y))[3] > 8:
                    return y
        return -1

    def _matte(extra):
        r = client.post("/api/matte", json={
            "projectId": pid, "origAssetId": minfo["id"],
            "matte": {"enabled": True, "method": "auto", "hard": True, **extra}})
        assert r.status_code == 200, r.text
        return r.json()["image"]

    base = storage.load_image(pid, "images", f"{body['assetId']}.png")
    shrunk = storage.load_image(pid, "images", f"{_matte({'edge': -2})['assetId']}.png")
    grown = storage.load_image(pid, "images", f"{_matte({'edge': 2})['assetId']}.png")
    check("边缘偏移方向正确", _alpha_count(shrunk) < _alpha_count(base) < _alpha_count(grown),
          f"{_alpha_count(shrunk)}/{_alpha_count(base)}/{_alpha_count(grown)}")
    check("边缘偏移记进 meta", body["matte"].get("edgeOffset") == 0)
    # 底部裁切：切过的结果最下面一行必须更靠上（去接地投影）
    trimmed = storage.load_image(pid, "images", f"{_matte({'bottomTrim': 0.1})['assetId']}.png")
    check("底部裁切上移底边", 0 <= _bottom_row(trimmed) < _bottom_row(base),
          f"{_bottom_row(trimmed)} vs {_bottom_row(base)}")
    check("底部裁切记进 meta", trimmed is not None)

    print("== 三视图拼板接口 ==")
    # 三张不同底色的「视角图」：抠掉各自底色后应拼成一张 3 格宽的板
    def _view_img(bg, subject):
        img = Image.new("RGB", (256, 384), bg)
        d = ImageDraw.Draw(img)
        d.ellipse([96, 60, 160, 140], fill=subject)          # 头
        d.rectangle([104, 140, 152, 320], fill=subject)      # 身体
        return img

    view_ids = []
    for bg, sub in (((30, 90, 60), (240, 200, 160)), ((30, 90, 60), (240, 200, 160)),
                    ((30, 90, 60), (240, 200, 160))):
        v = storage.save_image(pid, _view_img(bg, sub), prefix="tv-src")
        view_ids.append(v["id"])
    r = client.post("/api/threeview/compose", json={
        "projectId": pid,
        "sources": [{"kind": "images", "file": i} for i in view_ids],
        "bg": "1B4332", "gap": 12, "baseline": 0.93})
    check("拼板 200", r.status_code == 200, r.text[:200])
    im = r.json()["image"]
    strip = storage.load_image(pid, "images", f"{im['assetId']}.png")
    check("拼板是三格宽", strip.width == 256 * 3 + 12 * 2, str(strip.size))
    check("拼板高度不变", strip.height == 384, str(strip.size))
    check("拼板带 meta", im.get("threeview", {}).get("baseline") is not None)
    # 拼错的张数要明确报错，而不是拼出一张怪板
    r = client.post("/api/threeview/compose", json={
        "projectId": pid, "sources": [{"kind": "images", "file": view_ids[0]}]})
    check("少给图报 400", r.status_code == 400, str(r.status_code))

    print("== 项目文件夹（输入与产出都落在项目目录） ==")
    from backend.app.config import PROJECTS_DIR
    pdir = PROJECTS_DIR / pid
    check("项目目录存在", pdir.is_dir(), str(pdir))
    check("目录带说明书", (pdir / "README.txt").is_file())
    readme = (pdir / "README.txt").read_text(encoding="utf-8")
    check("说明书讲清 images/refs", "images/" in readme and "refs/" in readme)
    check("参考图落 refs/", (pdir / "refs" / ref_name).is_file())
    check("参考图名可读", ref_name.startswith("ref-"), ref_name)
    check("原图名可读", info["id"].startswith("out-"), info["id"])
    check("精修名可读", px_id.startswith("px-"), px_id)

    r = client.get(f"/api/projects/{pid}/files")
    fl = r.json()
    check("清单 200", r.status_code == 200, str(r.status_code))
    check("清单根目录对", fl["root"].replace("\\", "/").endswith(pid), fl["root"])
    check("清单含产出图", fl["counts"].get("images", 0) >= 2, str(fl["counts"]))
    check("清单含输入图", fl["counts"].get("refs", 0) == 1, str(fl["counts"]))
    paths = {f["path"] for f in fl["files"]}
    check("清单列出输入", f"refs/{ref_name}" in paths)
    check("清单列出画布记录", "project.json" in paths)
    check("清单列出说明书", "README.txt" in paths)

    r = client.post(f"/api/projects/{pid}/reveal")
    check("打开文件夹接口", r.status_code == 200 and "root" in r.json())

    print("== 关掉再打开继续处理 ==")
    cur = client.get(f"/api/projects/{pid}").json()["project"]
    gnode = next(n for n in cur["nodes"] if n["type"] == "generate")
    gnode["prompt"] = "续做测试：橘猫剑士，正面站姿"
    gnode["refs"] = [ref_obj]
    gnode["params"] = {**gnode["params"], "background": "green", "n": 3}
    gnode["pixel"] = {**gnode["pixel"], "colors": 8, "palette": "gameboy"}
    r = client.put(f"/api/projects/{pid}", json={
        "nodes": cur["nodes"], "edges": cur["edges"],
        "viewport": {"x": -120, "y": 44, "z": 1.6}})
    check("续做保存 200", r.status_code == 200, str(r.status_code))

    again = client.get(f"/api/projects/{pid}").json()["project"]
    g2 = next(n for n in again["nodes"] if n["type"] == "generate")
    check("重开后提示词在", g2["prompt"] == "续做测试：橘猫剑士，正面站姿")
    check("重开后参考图引用在", len(g2["refs"]) == 1 and g2["refs"][0]["file"] == ref_name)
    check("重开后背景参数在", g2["params"].get("background") == "green")
    check("重开后张数在", g2["params"].get("n") == 3)
    check("重开后像素参数在", g2["pixel"].get("colors") == 8 and g2["pixel"].get("palette") == "gameboy")
    check("重开后画布位置在", abs(again["viewport"]["z"] - 1.6) < 1e-6)
    check("重开后结果图在", any(n["type"] == "result" for n in again["nodes"]))

    print("== 八方向行走链（合成视频帧走 序列帧 → 精灵整图） ==")
    # 不用 GPU：直接合成「行走视频帧」存成 GIF（模拟行走视频节点的产物），
    # 再走后两个端点，验证拆帧 / 抠图 / 拼板 / 落盘的整条链路。
    import time as _time

    def wait_task(tid: str, timeout: float = 90.0):
        t0 = _time.time()
        while _time.time() - t0 < timeout:
            j = client.get(f"/api/tasks/{tid}").json()
            if j.get("progress", {}).get("doneAt") is not None:
                return j
            _time.sleep(0.15)
        raise TimeoutError(f"任务超时：{tid}")

    r = client.post("/api/projects", json={"name": "八方向链路", "templateId": "dir_walk"})
    wpid = r.json()["project"]["id"]
    check("八方向模板建项目", r.status_code == 200 and wpid)
    wnodes = r.json()["project"]["nodes"]
    check("模板链五个节点齐",
          [n["type"] for n in wnodes] == ["generate", "walkvideo", "sequence",
                                          "spritesheet", "play", "result"],
          str([n["type"] for n in wnodes]))

    # 合成两个方向的「行走视频」：纯色底 + 一个逐帧移动的小人，各 24 帧
    # （帧必须逐帧不同：PIL 写 GIF 会合并连续相同的帧，那是它的优化不是 bug）
    from backend.app import storage as storage_mod

    def fake_walk_frames(n: int = 24, bg=(40, 120, 60)) -> list:
        frames = []
        for i in range(n):
            img = Image.new("RGB", (192, 256), bg)
            d = ImageDraw.Draw(img)
            swing = (i % 12) - 6
            lift = 6 if (i % 6) < 3 else 0
            d.rectangle([80 + swing, 90, 112 + swing, 150], fill=(240, 200, 160))   # 身体
            d.ellipse([86 + swing, 60, 106 + swing, 90], fill=(240, 200, 160))      # 头
            d.rectangle([84 + swing, 150, 94 + swing, 230 - lift], fill=(60, 60, 90))   # 左腿
            d.rectangle([98 + swing, 150, 108 + swing, 230], fill=(60, 60, 90))     # 右腿
            frames.append(img)
        return frames

    gifs = {}
    for d in ("down", "left"):
        info = storage_mod.save_gif(wpid, fake_walk_frames(), prefix=f"walk-{d}", fps=12)
        gifs[d] = info
    check("合成行走视频 GIF 落盘", all(g["file"].endswith(".gif") for g in gifs.values()))
    frames_back, durations, gmeta = storage_mod.load_gif(wpid, "images", gifs["down"]["file"])
    check("GIF 读得回来", len(frames_back) == 24 and len(durations) == 24,
          f"{len(frames_back)} 帧 / {len(durations)} 时长")

    r = client.post("/api/sequence", json={
        "projectId": wpid,
        "directions": [{"dir": d, "gif": g["file"]} for d, g in gifs.items()],
        "framesPerDir": 8, "fps": 8, "matte": True, "tolerance": 40,
    })
    check("序列帧接口接单", r.status_code == 200 and r.json().get("taskId"))
    seq = wait_task(r.json()["taskId"]).get("result", {})
    seq_meta = seq.get("sequence") or {}
    check("序列帧无错误", not seq.get("error"), str(seq.get("error")))
    check("序列帧两个方向", len(seq_meta.get("directions", [])) == 2,
          str(seq_meta.get("directions")))
    check("每方向 8 帧", all(d.get("count") == 8 for d in seq_meta.get("directions", [])),
          str([d.get("count") for d in seq_meta.get("directions", [])]))
    check("序列帧带拼条图", len(seq.get("images", [])) == 2)

    r = client.post("/api/spritesheet", json={
        "projectId": wpid,
        "directions": [{"dir": d["dir"],
                        "frames": [f"{f['assetId']}.png" for f in d["frames"]]}
                       for d in seq_meta.get("directions", [])],
        "size": 128, "cols": 0, "sharedScale": True, "anchor": "bottom",
    })
    check("精灵整图接口接单", r.status_code == 200 and r.json().get("taskId"))
    sheet = wait_task(r.json()["taskId"]).get("result", {})
    sheet_meta = sheet.get("sheet") or {}
    check("精灵整图无错误", not sheet.get("error"), str(sheet.get("error")))
    check("两个方向各一张表", len(sheet_meta.get("directions", [])) == 2)
    check("有八方向总览", bool(sheet_meta.get("overview", {}).get("url")))
    check("单元格 128", all(d.get("frameSize") == {"w": 128, "h": 128}
                          for d in sheet_meta.get("directions", [])),
          str([d.get("frameSize") for d in sheet_meta.get("directions", [])]))
    check("逐格自检通过", sheet_meta.get("checksOk") is True, str(sheet_meta.get("warnings")))
    check("共享缩放在 meta 里", sheet_meta.get("sharedScale") is True)
    check("给出全方向统一缩放系数", (sheet_meta.get("globalScale") or 0) > 0,
          str(sheet_meta.get("globalScale")))

    # 全方向统一缩放：两个方向角色体型差一倍，缩放系数必须一样，
    # 否则播放器里一转身角色就变大变小
    import asyncio as _asyncio
    from backend.app.pipelines import spritesheet as sheet_mod

    def body_frames(h: int):
        out = []
        for i in range(3):
            img = Image.new("RGBA", (h * 2, h * 2), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            w = h // 2
            d.rectangle([h - w // 2, h, h + w // 2, h + h], fill=(240, 200, 160, 255))
            out.append(img)
        return out

    _dirs, _meta = _asyncio.run(sheet_mod.run_sheets(
        [{"dir": "down", "frames": body_frames(180)},
         {"dir": "left", "frames": body_frames(90)}],
        size=128, shared_scale=True, anchor="bottom"))
    _scales = [d["normalize"]["scaleRange"][0] for d in _dirs]
    check("体型不同的方向缩放系数一致", abs(_scales[0] - _scales[1]) < 1e-6,
          f"{_scales}")
    check("统一缩放等于两方向上界", abs(_scales[0] - _meta["globalScale"]) < 1e-2,
          f"{_scales[0]} vs {_meta['globalScale']}")
    # 总览与两张表都能通过资产端点读出来
    ov_url = sheet_meta["overview"]["url"]
    r = client.get(ov_url)
    check("总览图可读取", r.status_code == 200 and len(r.content) > 1000)
    gif_url = gifs["down"]["url"]
    r = client.get(gif_url)
    check("GIF 资产按 image/gif 提供", r.status_code == 200
          and r.headers.get("content-type", "").startswith("image/gif"),
          r.headers.get("content-type", ""))

    print("== 行走视频 · 现成视频来源（全部指定 → 不加载任何模型） ==")
    r = client.post("/api/projects", json={"name": "八方向现成视频", "templateId": "dir_walk"})
    vpid = r.json()["project"]["id"]
    check("现成视频项目建好", r.status_code == 200 and vpid)

    # 基准正视图（walkvideo 起点会读它，即使全部走现成视频）
    base = Image.new("RGB", (192, 256), (70, 90, 130))
    base_info = storage_mod.save_image(vpid, base, prefix="out")
    # 五个真实方向各合成一段 24 帧 GIF（尺寸 192×256，和节点参数不同，
    # 用来验证 normalize_source_frames 的缩放 + 居中裁切）
    vsrc = {}
    for i, d in enumerate(("down", "down-left", "left", "up-left", "up")):
        frames = []
        for k in range(24):
            img = Image.new("RGB", (192, 256), (40 + i * 30, 120, 60))
            dr = ImageDraw.Draw(img)
            swing = (k % 12) - 6
            dr.rectangle([80 + swing, 90, 112 + swing, 150], fill=(240, 200, 160))
            dr.ellipse([86 + swing, 60, 106 + swing, 90], fill=(240, 200, 160))
            frames.append(img)
        vsrc[d] = storage_mod.save_gif(vpid, frames, prefix=f"walk-{d}", fps=12)

    r = client.get(f"/api/projects/{vpid}/videos")
    vlist = r.json().get("videos", [])
    check("视频列表含 5 个生成视频", len(vlist) == 5, str(len(vlist)))
    check("列表标出方向", all(v.get("dir") and v.get("origin") == "generated" for v in vlist))

    r = client.post("/api/walkvideo", json={
        "projectId": vpid,
        "origAssetId": base_info["id"],
        "dirMode": "eight",
        "sources": [{"dir": d, "file": g["file"]} for d, g in vsrc.items()],
        "width": 384, "height": 512,
    })
    check("现成视频 walkvideo 接单", r.status_code == 200 and r.json().get("taskId"))
    wv = wait_task(r.json()["taskId"]).get("result", {})
    check("现成视频 walkvideo 无错误", not wv.get("error"), str(wv.get("error")))
    wmeta = wv.get("walk") or {}
    wdirs = wmeta.get("directions", [])
    check("八个方向齐（5 现成 + 3 镜像）", len(wdirs) == 8, str(len(wdirs)))
    check("现成方向数=5", wmeta.get("existingDirections") == 5, str(wmeta.get("existingDirections")))
    check("镜像方向数=3", wmeta.get("mirroredDirections") == 3)
    by_dir = {d["dir"]: d for d in wdirs}
    check("现成方向帧数来自源文件", by_dir["down"]["count"] == 24,
          str(by_dir["down"]["count"]))
    check("现成方向标记 mode=existing",
          by_dir["down"]["walk"]["mode"] == "existing"
          and by_dir["down"]["walk"]["model"] == "现成视频",
          str(by_dir["down"]["walk"]))
    check("镜像方向帧数同样来自源文件",
          by_dir["right"]["count"] == 24 and by_dir["right"]["mirrored"] is True
          and by_dir["right"]["mirroredFrom"] == "left",
          f"{by_dir['right']['count']}/{by_dir['right']['mirroredFrom']}")
    check("来源视频规整到节点尺寸", by_dir["down"]["gif"]["width"] == 384
          and by_dir["down"]["gif"]["height"] == 512,
          f"{by_dir['down']['gif']['width']}x{by_dir['down']['gif']['height']}")

    # 单方向 + 现成视频：与 dirMode 组合的最快路径
    r = client.post("/api/walkvideo", json={
        "projectId": vpid,
        "origAssetId": base_info["id"],
        "dirMode": "single",
        "sources": [{"dir": "down", "file": vsrc["down"]["file"]}],
    })
    check("单方向现成视频接单", r.status_code == 200)
    wv2 = wait_task(r.json()["taskId"]).get("result", {})
    check("单方向现成视频无错误", not wv2.get("error"), str(wv2.get("error")))
    wm2 = wv2.get("walk") or {}
    check("单方向只有正面", len(wm2.get("directions", [])) == 1
          and wm2["directions"][0]["dir"] == "down")

    # 来源文件不存在 → 明确报错（不能静默回退到 Wan）
    r = client.post("/api/walkvideo", json={
        "projectId": vpid,
        "origAssetId": base_info["id"],
        "dirMode": "single",
        "sources": [{"dir": "down", "file": "walk-down-不存在.gif"}],
    })
    check("坏来源接单后报错", r.status_code == 200)
    wv3 = wait_task(r.json()["taskId"]).get("result", {})
    check("坏来源任务失败且点名文件",
          bool(wv3.get("error")) and "不存在" in str(wv3.get("error")),
          str(wv3.get("error")))

    # 上传接口：合成一个小 GIF 传上去，应落盘为 vidsrc-*
    import io as _io
    up_frames = [Image.new("RGB", (96, 128), (200, 60, 60)) for _ in range(6)]
    buf = _io.BytesIO()
    up_frames[0].save(buf, "GIF", save_all=True, append_images=up_frames[1:],
                      duration=100, loop=0)
    r = client.post(f"/api/projects/{vpid}/videos/upload",
                    files={"file": ("my-walk.gif", buf.getvalue(), "image/gif")})
    check("视频上传成功", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    up = (r.json().get("video") or {})
    check("上传文件带 url", bool(up.get("url")))
    check("上传文件是 vidsrc 前缀", up.get("file", "").startswith("vidsrc-"))
    r = client.get(up.get("url", "/x"))
    check("上传视频可读取", r.status_code == 200 and len(r.content) > 100)
    r = client.post(f"/api/projects/{vpid}/videos/upload",
                    files={"file": ("bad.txt", b"not a video", "text/plain")})
    check("拒绝非视频上传", r.status_code == 400)

    # 现成视频跑完的产物可以直接进序列帧（下游接口不变）
    r = client.post("/api/sequence", json={
        "projectId": vpid,
        "directions": [{"dir": d["dir"], "gif": d["gif"]["file"]} for d in wdirs[:2]],
        "framesPerDir": 6, "fps": 8, "matte": False,
    })
    check("现成视频产物进序列帧", r.status_code == 200)
    sq = wait_task(r.json()["taskId"]).get("result", {})
    check("序列帧无错误", not sq.get("error"), str(sq.get("error")))
    check("两个方向各 6 帧", all(d.get("count") == 6
                                for d in (sq.get("sequence") or {}).get("directions", [])))

    client.delete(f"/api/projects/{vpid}")

    print("== 删除项目 ==")
    r = client.delete(f"/api/projects/{pid}")
    check("删除项目", r.status_code == 200)
    r = client.get(f"/api/projects/{pid}")
    check("删除后 404", r.status_code == 404)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
