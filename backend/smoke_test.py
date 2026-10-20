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

    print("== 删除项目 ==")
    r = client.delete(f"/api/projects/{pid}")
    check("删除项目", r.status_code == 200)
    r = client.get(f"/api/projects/{pid}")
    check("删除后 404", r.status_code == 404)

    print(f"\n结果：{PASS} 通过 / {FAIL} 失败")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
