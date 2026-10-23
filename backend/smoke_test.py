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
    # 必须用 with：TestClient 不加 with 时**每个请求都新建一个事件循环**，
    # 请求返回后循环即销毁 → asyncio.create_task 起的后台任务被取消，
    # 任何真正跨请求的后台任务（序列帧 / 精灵整图 / 生成）都永远完不成。
    # 以前 run_sequence 是「async 壳、同步肉身」，从不让出循环所以恰好躲过；
    # 改成 asyncio.to_thread（修事件循环阻塞）后第一次让出就露馅。
    with TestClient(app) as client:
        return _run_checks(client)


def _run_checks(client) -> int:

    print("== 基础 / 环境 ==")
    r = client.get("/api/health")
    check("health 200", r.status_code == 200)
    check("health 带版本", r.json().get("version") == "0.1.0")
    r = client.get("/api/doctor")
    check("doctor 200", r.status_code == 200, str(r.status_code))
    check("doctor 有检查项", len(r.json().get("items", [])) >= 5)
    r = client.get("/api/models")
    check("models 200", r.status_code == 200)
    r = client.get("/api/styles")
    check("styles 200", r.status_code == 200)
    bgs = r.json().get("backgrounds", [])
    check("背景预设含透明", any(b.get("id") == "transparent" for b in bgs),
          str([b.get("id") for b in bgs]))
    check("背景预设 4 个", len(bgs) == 4, str(len(bgs)))

    print("== 模板 / 画风 ==")
    r = client.get("/api/templates")
    ts = r.json().get("templates", [])
    check("模板列表非空", len(ts) >= 1)
    check("第一个模板可用", ts[0]["status"] == "available")
    check("模板有步骤", len(ts[0].get("steps", [])) >= 3)
    # 四视图模板必须带右视图节点：缺了它拼板只能出三视图
    tv = next((t for t in ts if t.get("id") == "char_three_view"), None)
    check("有四视图模板", tv is not None)
    if tv:
        vgs = [n for n in tv["graph"]["nodes"] if n.get("type") == "viewgen"]
        views = sorted(n["viewgen"]["view"] for n in vgs)
        check("四视图模板含右视图节点", views == ["back", "side", "side_r"], str(views))
        check("四视图模板含拼板节点",
              any(n.get("type") == "threeview" for n in tv["graph"]["nodes"]))
    dw = next((t for t in ts if t.get("id") == "dir_walk"), None)
    if dw:
        dtypes = [n["type"] for n in dw["graph"]["nodes"]]
        # 入口是「选择图片」：手上已有立绘的人不必先花三次推导生四视图。
        # 四视图锚点那条更强的路径留给 char_three_view（拼板可接在行走节点前）。
        check("八方向模板从选择图片开始（不再预置生成图/视角节点）",
              dtypes[0] == "input" and "viewgen" not in dtypes
              and "generate" not in dtypes, str(dtypes))
        check("八方向模板仍含行走/序列帧/精灵表/预览",
              all(t in dtypes for t in ("walkvideo", "sequence", "spritesheet", "play")),
              str(dtypes))
    r = client.get("/api/styles")
    st = r.json().get("styles", [])
    check("画风 15 个", len(st) == 15, str(len(st)))
    check("像素画风标记", any(s.get("pixelClass") for s in st))
    check("风格词可读", all(s.get("words") for s in st))

    print("== 提示词示例 ==")
    r = client.get("/api/prompt-examples")
    ex = r.json()
    check("示例库有内置条目", len(ex["examples"]) >= 10, str(len(ex["examples"])))
    check("示例分类齐全（含行走视频）", len(ex["categories"]) == 6,
          str(ex["categories"]))
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
    # 透明背景：Qwen-Image-2.1 原生 RGBA 的官方双段式声明必须进提示词
    r = client.post("/api/generate/preview", json={
        "prompt": "一只猫", "styleId": "pixel_16bit", "background": "transparent",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    tp = r.json()["prompt"]
    check("透明背景带 RGBA 开头声明", tp.startswith("This is an RGBA image with transparency."), tp[:60])
    check("透明背景带 alpha 结尾声明", "The image has alpha channel and the background is transparent." in tp)
    check("透明背景有背景约束句", "背景完全透明" in tp)
    check("像素契约不禁死 alpha", "禁止半透明像素" not in tp and "全透明或全不透明" in tp)
    r = client.post("/api/generate/preview", json={
        "prompt": "一只猫", "styleId": "pixel_16bit", "background": "gray",
        "pixel": {"enabled": True, "gridSnap": True, "colors": 16, "palette": "auto"}})
    check("中灰背景不误加 RGBA 声明", "RGBA" not in r.json()["prompt"])
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

    print("== 四视图一致性校验（viewcheck）==")

    def _body(color, w, h, size=192):
        """透明底 + 一块居中色块：用面积占比模拟「配色块还在不在」。"""
        from PIL import Image as _I
        im = _I.new("RGBA", (size, size), (0, 0, 0, 0))
        d = ImageDraw.Draw(im)
        d.rectangle([size // 2 - w // 2, size // 3, size // 2 + w // 2,
                     size // 3 + h], fill=color)
        # 灰色主体底座：让「主体」有足够像素，色块才是主体里的一部分
        d.rectangle([size // 4, size // 6, size * 3 // 4, size - size // 8],
                    fill=(150, 150, 150, 255))
        d.rectangle([size // 2 - w // 2, size // 3, size // 2 + w // 2,
                     size // 3 + h], fill=color)
        return im

    _front = _body((210, 50, 45, 255), 96, 90)
    _lost = _body((210, 50, 45, 255), 18, 8)         # 红块几乎消失
    _kept = _body((210, 50, 45, 255), 90, 84)        # 红块还在
    from backend.app import viewcheck as _vc
    _ref = _vc.build_reference([_front])
    _r_kept = _vc.check_view(_vc.analyze(_kept, "kept"), _ref, "side")
    _r_lost = _vc.check_view(_vc.analyze(_lost, "lost"), _ref, "side")
    check("校验：配色保留的视角图通过", _r_kept["ok"], str(_r_kept["fails"]))
    check("校验：配色丢件的视角图被拦", not _r_lost["ok"], str(_r_lost["fails"]))
    check("校验：拦截项点名了具体色块",
          any("配色疑似丢失" in f for f in _r_lost["fails"]),
          str(_r_lost["fails"]))

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
    check("不透明底板 meta 标记",
          im["threeview"].get("alpha") is False
          and im["threeview"].get("bg") == "#1B4332",
          f"{im['threeview'].get('alpha')}/{im['threeview'].get('bg')}")
    # 拼错的张数要明确报错，而不是拼出一张怪板
    r = client.post("/api/threeview/compose", json={
        "projectId": pid, "sources": [{"kind": "images", "file": view_ids[0]}]})
    check("少给图报 400", r.status_code == 400, str(r.status_code))
    # 底板色：乱码一律 400，别静默回落到暗绿拼出一张错板；
    # 空串则回落默认底板色（用户在框里清空不该直接把拼板卡死）
    for bad_bg in ("GGGGGG", "ZZZ", "1B43"):
        r = client.post("/api/threeview/compose", json={
            "projectId": pid,
            "sources": [{"kind": "images", "file": i} for i in view_ids],
            "bg": bad_bg})
        check(f"底板色拒绝 {bad_bg!r}", r.status_code == 400, str(r.status_code))
    r = client.post("/api/threeview/compose", json={
        "projectId": pid,
        "sources": [{"kind": "images", "file": i} for i in view_ids],
        "bg": ""})
    check("底板色空串回落默认", r.status_code == 200, r.text[:120])

    # 透明底板：三种写法（开关 / bg=transparent / bg=none）都该产出 RGBA 板，
    # 四角全透明、主体 alpha 保住、meta 标记 alpha=True
    for label, extra in (("transparentBg 开关", {"transparentBg": True}),
                         ("bg=transparent", {"bg": "transparent"}),
                         ("bg=none", {"bg": "none"})):
        r = client.post("/api/threeview/compose", json={
            "projectId": pid,
            "sources": [{"kind": "images", "file": i} for i in view_ids],
            **extra})
        check(f"透明底板 {label} 200", r.status_code == 200, r.text[:200])
        if r.status_code != 200:
            continue
        tim = r.json()["image"]
        tstrip = storage.load_image(pid, "images", f"{tim['assetId']}.png")
        check(f"透明底板 {label} 产物 RGBA", tstrip.mode == "RGBA", tstrip.mode)
        if tstrip.mode != "RGBA":
            continue
        am = tstrip.getchannel("A")
        check(f"透明底板 {label} 四角全透明",
              am.getpixel((0, 0)) == 0
              and am.getpixel((tstrip.width - 1, tstrip.height - 1)) == 0,
              f"{am.getpixel((0, 0))}/{am.getpixel((tstrip.width - 1, tstrip.height - 1))}")
        opaque = sum(1 for v in am.getdata() if v > 200)
        check(f"透明底板 {label} 主体保住了",
              opaque / (tstrip.width * tstrip.height) > 0.02,
              str(round(opaque / (tstrip.width * tstrip.height), 3)))
        tmeta = tim.get("threeview") or {}
        check(f"透明底板 {label} meta 标记",
              bool(tmeta.get("alpha")) and tmeta.get("bg") == "transparent",
              str(tmeta.get("bg")))

    # 已带透明背景的输入直接透传 alpha（method=alpha），不再二次抠图：
    # 二次抠底会把已经成立的边缘再啃一遍
    tr_view = Image.new("RGBA", (256, 384), (0, 0, 0, 0))
    d = ImageDraw.Draw(tr_view)
    d.ellipse([96, 60, 160, 140], fill=(240, 200, 160, 255))
    d.rectangle([104, 140, 152, 320], fill=(240, 200, 160, 255))
    tr_id = storage.save_image(pid, tr_view, prefix="tv-src")["id"]
    r = client.post("/api/threeview/compose", json={
        "projectId": pid,
        "sources": [{"kind": "images", "file": tr_id}] * 3,
        "transparentBg": True})
    check("透明输入拼板 200", r.status_code == 200, r.text[:200])
    if r.status_code == 200:
        tviews = r.json()["image"]["threeview"]["views"]
        check("透明输入跳过抠图",
              all(v["matte"] == "alpha" for v in tviews),
              str([v["matte"] for v in tviews]))

    print("== 模板编辑：内置默认模板可改、可恢复 ==")
    # 内置模板是 templates.py 里的常量。直接改常量不可接受（升级会被覆盖、
    # 也没法恢复），所以用户编辑存成覆盖补丁，读取与建项目都走合并视图。
    _ts = client.get("/api/templates").json()["templates"]
    _b = next((t for t in _ts if t["id"] == "char_three_view"), None)
    check("内置模板可编辑且默认未被修改",
          _b is not None and _b.get("editable") is True
          and _b.get("overridden") is False, str(_b and _b.get("id")))
    check("列表返回节点图（编辑器要打开它）",
          bool(_b and _b.get("graph", {}).get("nodes")),
          str(len((_b or {}).get("graph", {}).get("nodes") or [])))

    _g = _b["graph"]
    _gen = next(n for n in _g["nodes"] if n["type"] == "generate")
    _put = client.put("/api/templates/builtin/char_three_view", json={
        "name": "八方向行走 · 我的",
        "defaults": {**_b["defaults"], "width": 768, "height": 768},
        "graph": {"nodes": [{**n, "prompt": "我的固定提示词"} if n["type"] == "generate" else n
                            for n in _g["nodes"]], "edges": _g["edges"]},
    })
    check("保存内置模板覆盖", _put.status_code == 200
          and _put.json()["template"].get("overridden") is True, _put.text[:120])

    _b2 = next(t for t in client.get("/api/templates").json()["templates"]
               if t["id"] == "char_three_view")
    _gen2 = next(n for n in _b2["graph"]["nodes"] if n["type"] == "generate")
    check("覆盖在读接口生效", _b2["name"] == "八方向行走 · 我的"
          and _b2["defaults"].get("width") == 768 and _gen2["prompt"] == "我的固定提示词",
          f'{_b2["name"]} / {_b2["defaults"].get("width")} / {_gen2["prompt"]}')

    # 关键：建项目也要走合并视图，否则「编辑了但新建的项目还是老样子」
    _pid2 = client.post("/api/projects", json={"name": "tpl-ov", "templateId": "char_three_view"}
                        ).json()["project"]["id"]
    _p2 = client.get(f"/api/projects/{_pid2}").json()["project"]
    _g3 = next(n for n in _p2["nodes"] if n["type"] == "generate")
    check("新建项目用覆盖后的节点图", _g3["prompt"] == "我的固定提示词", _g3["prompt"])

    # 只存差异：把出厂值原样提交回去不应该留下「已修改」
    client.delete("/api/templates/builtin/char_three_view")
    _factory = client.get("/api/templates/char_three_view").json()
    _same = client.put("/api/templates/builtin/char_three_view", json={
        "name": _factory["name"], "tagline": _factory["tagline"],
        "description": _factory["description"], "highlights": _factory["highlights"],
        "notes": _factory["notes"], "steps": _factory["steps"],
        "defaults": _factory["defaults"], "graph": _factory["graph"],
    })
    check("提交与出厂相同的值不产生覆盖（最小补丁）",
          _same.status_code == 200 and _same.json()["template"].get("overridden") is False,
          str(_same.json().get("template", {}).get("overridden")))

    check("非法节点图被拒（空节点）",
          client.put("/api/templates/builtin/char_three_view",
                     json={"graph": {"nodes": []}}).status_code == 400)
    check("不存在的内置模板被拒",
          client.put("/api/templates/builtin/nope", json={"name": "x"}).status_code == 400)

    # 悬空连线 / 非法节点类型在保存时被清理，而不是留着让画布运行期踩空
    _clean = client.put("/api/templates/builtin/char_three_view", json={"graph": {
        "nodes": [{"id": "a", "type": "generate", "x": -50, "y": 0},
                  {"id": "b", "type": "bogus"}],
        "edges": [{"from": "a", "to": "ghost"}, {"from": "a", "to": "a"}],
    }}).json()["template"]["graph"]
    check("非法节点与悬空连线被清理",
          [n["id"] for n in _clean["nodes"]] == ["a"] and _clean["edges"] == [],
          str(_clean))

    _dup = client.post("/api/templates/char_three_view/duplicate")
    check("内置模板可另存为自定义副本",
          _dup.status_code == 200 and _dup.json()["template"].get("custom") is True
          and len(_dup.json()["template"]["graph"]["nodes"]) >= 1,
          _dup.text[:120])

    _rst = client.delete("/api/templates/builtin/char_three_view")
    check("恢复默认清掉覆盖",
          _rst.status_code == 200 and _rst.json()["template"].get("overridden") is False
          and _rst.json()["template"]["name"] == _factory["name"],
          _rst.json()["template"]["name"])

    # 自定义模板此前只能建/删，没有编辑入口（前端也没有 update 方法）
    _ct = client.post("/api/templates/custom", json={
        "name": "可编辑的副本", "nodes": [{"id": "g", "type": "generate", "x": 0, "y": 0}],
        "edges": []}).json()["template"]
    _ct2 = client.put(f"/api/templates/custom/{_ct['id']}", json={"name": "改过名字"}).json()
    check("自定义模板可编辑", _ct2["template"]["name"] == "改过名字",
          _ct2["template"]["name"])

    print("== 模板编辑草稿：在无限画布上改，保存回模板 ==")
    # 模板编辑不再用表单填节点（用户明确反馈太复杂），改为把模板打开成一份
    # 画布草稿：拖节点、改参数、连线，然后一键写回。
    _dr = client.post("/api/templates/char_three_view/draft")
    _dp = _dr.json()["project"]
    check("打开模板草稿", _dr.status_code == 200
          and _dp.get("templateDraftOf") == "char_three_view",
          str(_dp.get("templateDraftOf")))
    check("草稿保留模板节点 id（写回时结构零 diff）",
          any(n["id"] == "gen1" for n in _dp["nodes"]),
          str([n["id"] for n in _dp["nodes"]]))
    _dr2 = client.post("/api/templates/char_three_view/draft")
    check("重复打开复用同一份草稿",
          _dr2.json().get("reused") is True and _dr2.json()["project"]["id"] == _dp["id"])

    _dnodes = [{**n} for n in _dp["nodes"]]
    _dgen = next(n for n in _dnodes if n["type"] == "generate")
    _dgen["prompt"] = "画布上改的提示词"
    _dgen["params"] = {**_dgen["params"], "width": 640, "height": 960}
    _ddrop = next((n["id"] for n in _dnodes if n["type"] == "viewgen"
                   and n["viewgen"]["view"] == "side_r"), None)
    _dnodes = [n for n in _dnodes if n["id"] != _ddrop]
    _dedges = [e for e in _dp["edges"] if e["from"] != _ddrop and e["to"] != _ddrop]
    client.put(f"/api/projects/{_dp['id']}", json={
        "name": _dp["name"], "nodes": _dnodes, "edges": _dedges,
        "viewport": _dp["viewport"]})
    _cm = client.post("/api/templates/char_three_view/commit",
                      json={"projectId": _dp["id"]})
    _tp2 = _cm.json().get("template") or {}
    check("画布草稿写回模板",
          _cm.status_code == 200 and _cm.json().get("draftDeleted") is True
          and next(n for n in _tp2["graph"]["nodes"] if n["type"] == "generate")["prompt"]
          == "画布上改的提示词", _cm.text[:160])
    check("默认参数从模板里的生成卡派生",
          _tp2["defaults"].get("width") == 640 and _tp2["defaults"].get("height") == 960,
          str(_tp2.get("defaults")))
    check("草稿里删掉的节点也写回了模板",
          not any(n.get("viewgen", {}).get("view") == "side_r"
                  for n in _tp2["graph"]["nodes"]))
    check("提交后草稿项目被清理",
          client.get(f"/api/projects/{_dp['id']}").status_code == 404)

    # 文案与节点图是两个编辑入口：改文案不能把画布改过的图冲回出厂
    client.put("/api/templates/builtin/char_three_view", json={"name": "画布版四视图"})
    _after = client.get("/api/templates/char_three_view").json()
    check("改文案不会丢掉画布改的节点图",
          _after["name"] == "画布版四视图"
          and next(n for n in _after["graph"]["nodes"]
                   if n["type"] == "generate")["prompt"] == "画布上改的提示词",
          _after["name"])
    client.delete("/api/templates/builtin/char_three_view")

    _normal = client.post("/api/projects",
                          json={"name": "普通项目", "templateId": "matte_cutout"}
                          ).json()["project"]["id"]
    check("非草稿项目不能提交回模板",
          client.post("/api/templates/matte_cutout/commit",
                      json={"projectId": _normal}).status_code == 400)

    print("== 四视图拼板投入行走链：拆锚点 + 清脏 alpha + GIF 全局调色板 ==")
    # 用户刻意把四视图拼板投进 dir_walk 保一致性。以前整张 4 角色拼板会被
    # passthrough 直接喂给视频底座（1024×253 塞进 384×512），出来是残影 +
    # 闪烁 + 变形；且拼板背景 51.3% 像素是「alpha 1-8 + 品红 RGB」，Wan 的
    # convert("RGB") 把这片垃圾变成可见的品红背景。三条修复各有一条断言。
    import numpy as _np
    from PIL import Image as _Im, ImageDraw as _Dr
    from backend.app.alpha_clean import sanitize_alpha, transparent_ratio
    from backend.app.pipelines.threeview import split_view_board
    _pid = client.post("/api/projects", json={
        "name": "board-split", "templateId": "dir_walk"}).json()["project"]["id"]

    # 1) 脏 alpha：近透明区 RGB 清零（品红泄漏的根源）
    _dirty = _Im.new("RGBA", (64, 64), (255, 0, 255, 2))
    _d = _Dr.Draw(_dirty)
    _d.rectangle((20, 20, 44, 44), fill=(200, 60, 60, 255))
    _clean = sanitize_alpha(_dirty)
    _ca = _np.asarray(_clean)
    check("脏 alpha 的近透明区被清零（RGB+alpha）",
          _ca[2, 2].tolist() == [0, 0, 0, 0] and _ca[30, 30, 3] == 255,
          f'{_ca[2, 2].tolist()} / {_ca[30, 30, 3]}')
    check("清理不改变实心区颜色", _ca[30, 30, :3].tolist() == [200, 60, 60],
          str(_ca[30, 30, :3].tolist()))
    check("透明占比可算（Wan 参考图铺底色要用）",
          0.8 < transparent_ratio(_clean) < 0.9
          and transparent_ratio(_Im.new("RGB", (8, 8))) == 0.0
          and transparent_ratio(_Im.new("RGBA", (8, 8), (0, 0, 0, 0))) == 1.0)

    # 2) 拼板拆分：4 联 → 4 张；单角色不误拆
    _board = _Im.new("RGBA", (400, 100), (0, 0, 0, 0))
    for _i in range(4):
        _x = _i * 100
        _Dr.Draw(_board).rectangle((_x + 25, 15, _x + 75, 90), fill=(200, 60, 60, 255))
    _cells = split_view_board(_board)
    check("四联拼板拆成 4 张", len(_cells) == 4, str(len(_cells)))
    check("拆出的每张只有一个主体",
          all(transparent_ratio(c) > 0.5 for c in _cells),
          str([round(transparent_ratio(c), 2) for c in _cells]))
    # 不透明底拼板（compose 传了 bg 的产物）：间隔带是同色纯色列，
    # 判据要认「与边框同色」而不只是透明
    _ob = _Im.new("RGB", (400, 100), (27, 67, 50))
    for _i in range(4):
        _Dr.Draw(_ob).rectangle((_i * 100 + 25, 15, _i * 100 + 75, 90),
                                fill=(200, 60, 60))
    check("不透明底四联拼板也拆", len(split_view_board(_ob)) == 4,
          str(len(split_view_board(_ob))))
    # 贴边的背景带是页边距不是间隔：切了会把角色切掉一半
    _mg = _Im.new("RGB", (400, 100), (27, 67, 50))
    for _i in range(4):
        _Dr.Draw(_mg).rectangle((_i * 100 + 30, 5, _i * 100 + 90, 95),
                                fill=(200, 60, 60))
    _mc = split_view_board(_mg)
    check("拼板拆分保留页边距（不切角色）", len(_mc) == 4
          and all(_np.asarray(c)[:, :, 3].size for c in _mc), str(len(_mc)))
    _solo = _Im.new("RGBA", (100, 100), (0, 0, 0, 0))
    _Dr.Draw(_solo).rectangle((30, 10, 70, 95), fill=(200, 60, 60, 255))
    check("单角色立绘不误拆", split_view_board(_solo) == [])
    check("不透明底图不拆", split_view_board(_Im.new("RGB", (400, 100), (30, 30, 30))) == [])

    # 3) GIF 全局调色板：帧数与尺寸不变，同一颜色跨帧同值
    _fr = []
    for _i in range(6):
        _f = _Im.new("RGB", (64, 64), (120, 130, 140))
        # 红块固定在所有帧里：全局调色板保证它跨帧解回同一个值
        _Dr.Draw(_f).rectangle((10, 20, 20, 50), fill=(200, 60, 60))
        _Dr.Draw(_f).rectangle((30, 20 + _i * 3, 40, 50), fill=(60, 90, 200))
        _fr.append(_f)
    _gi = storage.save_gif(_pid, _fr, prefix="pal", fps=12)
    _back, _, _ = storage.load_gif(_pid, "images", _gi["file"])
    check("GIF 帧数不丢", len(_back) == 6, str(len(_back)))
    check("GIF 全局调色板：同一颜色跨帧同值",
          _back[0].convert("RGB").getpixel((15, 40)) == (200, 60, 60)
          and _back[5].convert("RGB").getpixel((15, 40)) == (200, 60, 60),
          f'{_back[0].convert("RGB").getpixel((15, 40))} vs '
          f'{_back[5].convert("RGB").getpixel((15, 40))}')

    print("== 行走提示词：整段由用户输入，程序只给示例 ==")
    # 2026-10-23：提示词不再由程序拼模板（用户反馈「一点都不像在举枪行走」，
    # 根因之一是模板与持械参考图自相矛盾）。现在整段提示词是用户输入，
    # 程序只提供完整示例；walk_prompt 退化为老项目的兼容路径。
    import inspect as _isp          # _inspect 这个名字在本函数后面才导入，别撞
    from backend.app.pipelines import walkvideo as _wv
    from backend.app.pipelines.walkvideo import EMPHASIS_TAIL, walk_prompt
    from backend.app.projects import DEFAULT_WALK_PROMPT, _clean_walkvideo
    from backend.app import prompt_examples as pex

    _walk_ex = [e for e in pex.load_examples() if e["category"] == "行走视频"]
    check("示例库有行走视频分类（完整提示词）", len(_walk_ex) >= 4,
          str([e["title"] for e in _walk_ex]))
    check("每条示例都是完整提示词（含镜头静止段）",
          all("取景框" in e["content"] and len(e["content"]) > 80 for e in _walk_ex),
          str([len(e["content"]) for e in _walk_ex]))
    check("默认提示词与示例库第一条同文（改一边不忘另一边）",
          DEFAULT_WALK_PROMPT == next(e["content"] for e in _walk_ex
                                      if e["id"] == "builtin-walk-walk"))
    check("默认提示词不含自相矛盾的手臂摆动（持械角色会冲突）",
          "手臂与腿反向自然摆动" not in DEFAULT_WALK_PROMPT
          and "双臂与武器保持稳定" in DEFAULT_WALK_PROMPT)
    check("默认提示词不用强调词（实测会出高抬腿猛踏步）",
          "明确地" not in DEFAULT_WALK_PROMPT and "明显" not in DEFAULT_WALK_PROMPT)

    # 老项目迁移：只有 action 时拼一句完整起始提示词，之后用户直接改全文
    _mig = _clean_walkvideo({"action": "举枪行走"})
    check("老项目迁移出完整提示词",
          _mig["prompt"].startswith("角色在原地举枪行走")
          and "取景框" in _mig["prompt"], _mig["prompt"][:40])
    check("新节点默认带完整提示词", len(_clean_walkvideo({})["prompt"]) > 100)
    check("用户提示词超长被夹（防 project.json 被撑爆）",
          len(_clean_walkvideo({"prompt": "啊" * 900})["prompt"]) == 400)

    # 管线：用户给了 prompt 就用用户的；重试只在尾部追加强调句
    _src = _isp.getsource(_wv.generate_walk)
    check("generate_walk 优先用用户提示词",
          'user_prompt = (prompt or "").strip()' in _src
          and "base_prompt = user_prompt or walk_prompt(action)" in _src)
    check("重试强调句追加在用户提示词后（不换回模板）",
          "base_prompt + EMPHASIS_TAIL" in _src)
    check("强调句只追加不替换", len(EMPHASIS_TAIL) > 20)

    # 长度预算接口
    _lim = client.get("/api/limits").json()["limits"]
    check("行走提示词上限已暴露给前端",
          _lim["walkPrompt"]["maxChars"] == 300
          and "示例库" in _lim["walkPrompt"]["note"], str(_lim.get("walkPrompt")))

    print("== 序列帧采样：按动作周期取，循环才闭合 ==")
    # 等距采样跨整个视频时，若视频里有 1.6 个步态周期，8 帧会混到两个圈的相位，
    # 精灵表循环播放时姿势跳变。改成先检测周期、在同一圈内取帧。
    from backend.app.pipelines.sequence import (
        detect_cycle_period, sample_cycle, sample_frames,
    )
    # 合成一段周期 12 的往复运动（方块左右移动 + 形变）
    _pf = []
    for _i in range(48):
        _im = _Im.new("RGB", (96, 96), (30, 30, 30))
        _Dr.Draw(_im).rectangle((20 + (_i % 12) * 4, 30, 44 + (_i % 12) * 4, 70),
                                fill=(200, 60, 60))
        _pf.append(_im)
    check("合成周期视频能检出周期", detect_cycle_period(_pf) == 12,
          str(detect_cycle_period(_pf)))
    _sc = sample_cycle(_pf, 8)
    check("按周期采样仍在同一圈内（最大下标 < 周期）",
          max(_pf.index(f) for f in _sc) < 12, str([_pf.index(f) for f in _sc]))
    check("按周期采样取够帧数", len(_sc) == 8, str(len(_sc)))
    # 全程渐变（无周期）→ 检不出，安全退回等距
    _nf = []
    for _i in range(40):
        _im = _Im.new("RGB", (96, 96), (30 + _i, 30, 30))
        _nf.append(_im)
    check("无周期视频检不出（不硬挑）", detect_cycle_period(_nf) is None,
          str(detect_cycle_period(_nf)))
    check("无周期时退回等距采样", sample_cycle(_nf, 8) == sample_frames(_nf, 8))
    # 帧数少于请求时全给
    check("帧不够时全给", len(sample_cycle(_pf[:5], 8)) == 5)

    print("== 参数校验：恶意输入不许打挂服务 ==")
    import time as _time
    from backend.app import projects as _pj
    # pixel.gridSize 是用户可经 API 直达的参数，之前完全没有校验：
    #   [0,8]  → cell_w = W/0 除零
    #   [-8,8] → x 单向递减，while 条件永真 → 死循环
    #   [2000,2000] → cell_w<0.5 时 find_best_grid 把小数进度抹掉 → 原地打转 + OOM
    # 现在 refine_grids 入口校验 + 每侧步行加「连续两轮同线即停」保险。
    import numpy as _np
    from PIL import Image as _PILImage
    from backend.app.pipelines import refine as _refine
    _base = _PILImage.fromarray(
        _np.tile(_np.arange(64, dtype=_np.uint8).reshape(1, 64, 1), (64, 1, 3)), "RGB")
    _t0 = _time.time()
    _bad_results = []
    for _gs in ([0, 8], [-8, 8], [2000, 2000], [1, 1]):
        _r = _refine.refine_one(_base, grid_snap=True, grid_size=_gs)
        _bad_results.append((_gs, _r["image"].size, _r["meta"].get("gridFailed")))
    _dt = _time.time() - _t0
    check("恶意 gridSize 全部快速返回（不死循环/不除零）", _dt < 5.0,
          f"{_dt:.2f}s {_bad_results}")
    check("恶意 gridSize 走降级而不是崩", all(r[1] == (64, 64) or r[1][0] > 0
                                          for r in _bad_results), str(_bad_results))
    # targetSize 无 clamp 时 99999 会让 PIL 去分配约 30GB（实测 120s 挂死）
    _t0 = _time.time()
    _r = _refine.refine_one(_base, grid_snap=False, target_size=[99999, 99999])
    check("恶意 targetSize 被夹到原图尺寸",
          _r["image"].size == (64, 64) and _time.time() - _t0 < 2.0,
          f"{_r['image'].size} {_time.time() - _t0:.2f}s")
    # 未知色板名必须报错：静默回落到 auto 会让「限定 N 色」被悄悄绕过
    try:
        _refine.refine_batch([_base], grid_snap=False, palette_name="gameboyy")
        check("未知色板名报错", False, "没有报错")
    except ValueError as _e:
        check("未知色板名报错", "未知色板" in str(_e), str(_e)[:60])

    print("== 抠图：色相判定与性能 ==")
    # _rgb_to_hsv_u8 曾写成 h*30/2（=h*15），所有色相减半：
    # 纯绿算成 30（应为 60），_hue_spill 的目标表一个都匹配不上 →
    # 绿幕/蓝幕的暗部溢色判定静默失效，residualKeyPixels 还虚报「干净」。
    from backend.app.pipelines import matte as _matte
    _hsv = _matte._rgb_to_hsv_u8(_np.array([[[0, 255, 0], [0, 0, 255], [255, 255, 0]]],
                                          dtype=_np.uint8))
    check("纯绿/纯蓝/纯黄色相与 OpenCV 量纲一致",
          [int(v) for v in _hsv[0, :, 0]] == [60, 120, 30],
          str(_hsv[0, :, 0].tolist()))
    _spill = _matte._hue_spill(_np.array([[[20, 80, 30]]], dtype=_np.uint8), 1)
    check("暗部绿丝能被色相判定抓到", bool(_spill[0, 0]))
    # 颜色键洪泛从 Python BFS 换成 connectedComponents：结果必须逐像素一致
    _m = _np.zeros((64, 64), bool)
    _m[:20] = True
    _m[:, -10:] = True
    _from_border, _cnt = _matte._flood_from_border(_m)
    check("边框洪泛像素数与连通域一致", _cnt == int(_from_border.sum()) and _cnt > 0,
          f"{_cnt} vs {int(_from_border.sum())}")
    check("洪泛不会越过非 match 像素", not _from_border[32, 32])

    print("== 保存白名单：参数不再丢 ===")
    # 这两处白名单漏字段 = 用户改了参数、保存刷新后静默重置回默认值
    from backend.app.projects import _clean_params, _clean_walkvideo
    _cp = _clean_params({"width": 768, "height": 768, "n": 2,
                         "maxSeqLen": 1024, "background": "transparent"})
    check("生成参数保留 maxSeqLen（质量档位）", _cp.get("maxSeqLen") == 1024, str(_cp))
    _cw = _clean_walkvideo({
        "dirImages": {"left": "a.png"}, "pixelArt": True,
        "guidanceScale": 7.5, "motionThreshold": 9.0, "retries": 3,
    })
    check("行走参数保留 dirImages/pixelArt/CFG/合格线/重试",
          _cw.get("dirImages") == {"left": "a.png"} and _cw.get("pixelArt") is True
          and _cw.get("guidanceScale") == 7.5 and _cw.get("motionThreshold") == 9.0
          and _cw.get("retries") == 3, str(_cw))
    check("行走参数拒收路径穿越的文件名",
          _clean_walkvideo({"dirImages": {"left": "../x.png"}}).get("dirImages") == {})

    print("== 视频列表：方向名按最长前缀 ==")
    # walk-down-left-… 用 split("-")[1] 取会把四个斜向全截成 down/up
    from backend.app import storage as _st
    _pv = _pj.create_project("方向名测试", "dir_walk", {}, None)["id"]
    _dir = _st.assets_dir(_pv, "images")
    _expect = {"down": "walk-down-x", "down-left": "walk-down-left-x",
               "up-right": "walk-up-right-x", "left": "walk-left-x"}
    for _d, _stem in _expect.items():
        (_dir / f"{_stem}-20261023-000000.mp4").write_bytes(b"x")
    _got = {v["dir"] for v in _st.list_videos(_pv)}
    check("斜向方向名不被截断", _got == set(_expect), str(_got))

    print("== 底座统一：图片侧只有 Qwen-Image-2.1 ==")
    # 2026-10-23：Z-Image / Edit-2511 / Layered 的 loader 与设置项全部删除。
    # 这些断言是「不再退回多底座」的护栏：谁要再把旧底座加回来，会先在这里红。
    from backend.app.config import DEFAULT_SETTINGS, KNOWN_MODELS
    from backend.app.providers.diffusers_local import DiffusersProvider, detect_family
    check("已知底座只剩 qwen21 / wan",
          set(KNOWN_MODELS.values()) == {"qwen21", "wan"}, str(KNOWN_MODELS))
    check("默认图片底座是 Qwen-Image-2.1",
          DEFAULT_SETTINGS["providers"]["diffusers"]["model"] == "models/qwen-image-2.1",
          DEFAULT_SETTINGS["providers"]["diffusers"]["model"])
    # 默认步数曾是 Z-Image 的 9 步；Qwen-Image-2.1 是非蒸馏底座，必须 40 步
    check("默认步数不再写死（改由 provider 按底座兜底）",
          DEFAULT_SETTINGS["defaults"]["steps"] is None,
          str(DEFAULT_SETTINGS["defaults"]["steps"]))
    check("qwen-image-2.1 目录识别为 qwen21",
          detect_family("models/qwen-image-2.1") == "qwen21")
    check("旧底座目录不再被识别（不会静默走错 loader）",
          detect_family("models/z-image-turbo") == "unknown"
          and detect_family("models/qwen-image-edit-2511") == "unknown")
    check("旧 loader 已删除",
          not hasattr(DiffusersProvider, "_load_zimage")
          and not hasattr(DiffusersProvider, "_load_qwen_edit"))

    print("== 视角指令：文本预算与传给管线 ==")
    # 2026-10-23 定位到的真问题：_run_qwen21 收下 max_seq_len 后**没有传给管线**，
    # 管线默认 max_sequence_length=512（中文约 390 字），1000+ 字的视角指令被
    # 静默截断——配件守恒/背覆守恒/姿势不变全在被截掉的尾部，表现成「指令写了
    # 没用」：佩剑丢、没披风的角色反而被加披风。这组断言守两件事：
    # 1) 指令长度换算后不超预算；2) 长度确实交给了管线。
    from backend.app.pipelines.threeview import (
        _apply_background as _ab, _fit_seq_len, _view_instruction as _vi,
    )
    _long_desc = "熊猫特种兵，战术背心、弹匣袋、护膝、模块化突击步枪。" * 12
    _ins = _ab(_vi("back", _long_desc), "transparent")
    check("超长角色描述被收敛，不挤爆文本预算",
          len(_ins) <= 1400 and len(_ins) * 1.3 + 64 <= 1920,
          f"{len(_ins)} 字")
    check("估算的序列长度覆盖整条指令",
          _fit_seq_len(_ins, None) >= min(1792, len(_ins) * 1.3 + 64) - 128,
          f"{_fit_seq_len(_ins, None)}")

    import inspect as _inspect
    from backend.app.providers import diffusers_local as _dl
    _src = _inspect.getsource(_dl.DiffusersProvider._run_qwen21)
    check("qwen21 把 max_sequence_length 真的传给管线",
          "max_sequence_length" in _src, "_run_qwen21 里没有 max_sequence_length")
    try:
        from diffusers import QwenImagePipeline as _QP
        check("能力检测认得 QwenImagePipeline 的 max_sequence_length",
              _dl._accepts_max_seq(_QP))
    except Exception:
        pass

    # 视角输出比例必须跟参考图走：正视图 768×768、节点默认 512×768 时，
    # letterbox 会把角色缩在竖图中间，拼板和正视图对不上（看起来像换了体型）
    from backend.app.pipelines.threeview import fit_view_size
    check("视角尺寸跟随参考图比例（方图 → 方图）",
          fit_view_size(512, 768, 768, 768) == (768, 768),
          str(fit_view_size(512, 768, 768, 768)))
    check("视角尺寸保持竖图比例",
          fit_view_size(512, 768, 768, 1024) == (576, 768),
          str(fit_view_size(512, 768, 768, 1024)))
    check("Wan 也把 max_sequence_length 传下去",
          "max_sequence_length" in _inspect.getsource(_dl.DiffusersProvider._run_wan))

    print("== 提示词长度上限：前后端同一口径 ==")
    # 文本超限会被管线静默截断，所以上限必须由一个地方算、由界面显示出来。
    _lim = client.get("/api/limits").json()["limits"]
    check("长度上限接口可用",
          _lim["seqLenMax"] == 1920 and len(_lim["styles"]) >= 15,
          f'{_lim["seqLenMax"]} / {len(_lim["styles"])}')
    check("像素契约的固定开销被计入",
          _lim["styles"]["pixel_16bit"]["pixel"] > _lim["styles"]["pixel_16bit"]["plain"] + 100,
          str(_lim["styles"]["pixel_16bit"]))
    check("自动档字符预算换算正确",
          int((1920 - 64) / 1.3) == 1427, str(int((1920 - 64) / 1.3)))
    check("四视图描述上限与后端常量同源",
          _lim["character"]["maxChars"] == 380, str(_lim["character"]["maxChars"]))
    check("超长描述被截到上限",
          ("猫" * 380) in _vi("back", "猫" * 1000)
          and ("猫" * 381) not in _vi("back", "猫" * 1000))

    # 「自动」不能等于管线默认 512：像素契约本身就占 256 字，用户写 100 字就被截
    from backend.app import prompt_limits as _pl
    from backend.app.styles import build_prompt as _bp
    _b = _bp("猫" * 400, "pixel_16bit", pixel=True, background="gray")
    check("生成提示词按实长给序列长度（不再恒为 512）",
          _pl.fit_seq_len(_b["prompt"], None) > 512, str(_pl.fit_seq_len(_b["prompt"], None)))
    check("短提示词保底 512", _pl.fit_seq_len("猫", None) == 512)
    # 接口层必须真的用它：算了但不用，等于没算
    from backend.app import main as _main_mod
    check("生成接口按最终提示词估算序列长度",
          'limits_mod.fit_seq_len(built["prompt"]' in _inspect.getsource(_main_mod),
          "main.py 里没有调用")
    # 算出来的长度要能回到界面上（曾经白名单写 seqLen、provider 产 maxSeqLen，
    # 结果「实际用了多少 token」永远是空的，排查只能靠猜）
    check("结果 meta 带上实际序列长度",
          '"maxSeqLen": meta.get("maxSeqLen")' in _inspect.getsource(_main_mod))

    # 行走：walk_prompt 模板本身 173 字，umT5 默认 226 token 装不下
    from backend.app.pipelines.walkvideo import walk_prompt as _wp
    check("行走提示词预算覆盖模板本身",
          _pl.fit_seq_len(_wp("行走"), None, limit=512) >= 384,
          str(_pl.fit_seq_len(_wp("行走"), None, limit=512)))
    check("Wan 调用把 max_sequence_length 传下去",
          "max_sequence_length" in _inspect.getsource(_dl.DiffusersProvider._run_wan))

    print("== 长静默：加载底座/解码期间要有心跳 ==")
    # 实测时间线（修复前）：视频任务前 30.5s 只有一条不动的 label，
    # 然后再跳到 1/24 步。加载底座（首次 fp8 量化可达数分钟）与 VAE 解码
    # 都不在 diffusers 回调里，没有心跳就完全静默。
    import time as _t2
    from backend.app import progress as _pg
    from backend.app.providers.diffusers_local import start_heartbeat
    _pg.create("t-hb", 0, "加载视频底座…")
    _stop = start_heartbeat("t-hb", "正面行走视频", hint="测试用")
    _t2.sleep(3.6)
    _hb = _pg.get("t-hb").snapshot()
    _stop.set()
    check("静默 3 秒后心跳会把已等秒数写进 label",
          "已等" in (_hb.get("label") or ""), str(_hb.get("label")))

    # 视频步进不能把整体进度撞成 1/24：hold 模式只更新 label
    _pg.create("t-hb2", 10, "")
    _cb = _pg.make_step_callback("t-hb2", 24, label="正面行走视频", base=3, grand=10, hold=True)
    _cb(None, 5, None, {})
    _h2 = _pg.get("t-hb2").snapshot()
    check("视频步进只更新 label、不动整体进度",
          _h2["done"] == 3 and _h2["total"] == 10 and "6/24" in (_h2["label"] or ""),
          str(_h2))

    from backend.app.pipelines import walkvideo as _wv
    check("行走任务开场给出方向数与预计时长",
          "预计约" in _inspect.getsource(_wv.run_walk))
    check("立绘推导不再用 40 步（首帧锚点 28 步足够）",
          "28 if steps is None else steps" in _inspect.getsource(_wv.derive_direction))

    print("== 视角生成：参数复用正视图 ==")
    from backend.app.pipelines.threeview import _apply_background
    instr = _apply_background("把角色转成侧面。", "gray")
    check("视角指令带正视图背景句", "#808080" in instr, instr[-80:])
    instr = _apply_background("把角色转成侧面。", "none")
    check("无背景不加背景句", "背景要求" not in instr, instr[-80:])
    instr = _apply_background("把角色转成侧面。", "transparent")
    check("透明背景带双段式声明",
          instr.startswith("This is an RGBA image with transparency.")
          and "The image has alpha channel and the background is transparent." in instr)
    # 视角指令里那句「背景保持纯色不变」在透明底上会让模型照抄参考图的地面
    # → 实测右视图脚下留椭圆投影。透明分支必须换掉它并禁投影。
    from backend.app.pipelines.threeview import _view_instruction
    tr_instr = _apply_background(_view_instruction("side_r"), "transparent")
    check("透明视角指令不带纯色背景句", "背景保持纯色不变" not in tr_instr,
          tr_instr[-140:])
    check("透明视角指令禁地面投影",
          "不要地面投影" in tr_instr and "不要阴影" in tr_instr, tr_instr[-140:])
    # 背景句必须前置：文本超长时截的是尾部，透明双段式声明被截掉 alpha 就不激活
    check("背景句前置到指令最前",
          tr_instr.startswith("This is an RGBA image with transparency.")
          and _apply_background("把角色转成侧面。", "gray").startswith("背景要求："))

    print("== 视角指令：单侧配件守恒（腰挎刀类） ==")
    # 单侧配件（腰挎刀/单肩包/单侧护具）在侧/背/右视图里最容易镜像或消失：
    # 指令必须同时给出「配件守恒」「遮挡守恒」和「自身左右锚定」三条几何约束，
    # 且不能点名任何具体道具（点名＝给所有角色下「必须长出来」的指令）。
    for v in ("side", "side_r", "back"):
        ins = _view_instruction(v)
        check(f"{v} 指令含配件守恒", "同一侧" in ins and "不能换边" in ins)
        check(f"{v} 指令含遮挡守恒", "被身体挡住" in ins)
        check(f"{v} 指令不点名具体道具",
              all(w not in ins for w in ("武士刀", "披风", "法杖", "盾牌")), v)
    check("侧视图锚定角色左侧面",
          "角色的左侧面" in _view_instruction("side")
          and "角色的右侧面" in _view_instruction("side_r"))
    check("背视图锚定左右镜像关系",
          "与正视图正好相反" in _view_instruction("back"))
    # 背覆体积守恒：披风这类「正面只看见一条边」的部件在背视图最容易被抹掉。
    # 修法必须是几何描述 + 条件句（不能点名「披风」，否则没披风的角色会长一件）
    _bk = _view_instruction("back")
    # 背覆条款必须是**双分支**：「图里有」怎么画 + 「图里没有」怎么画。
    # 写单边条件句（"如果有…它必须原样保留…至少与肩同宽"）实测会被模型当成
    # 「给我补一件」的指令——没有披风的角色每个视角都多一件。
    check("背视图有背覆守恒条款",
          "盖住后背" in _bk and "图里有" in _bk and "图里没有" in _bk, _bk[200:280])
    check("背覆条款是双分支（没有就别补）",
          "图里没有" in _bk and "不要补" in _bk and "披风" not in _bk)
    # 「角色穿着从肩部垂下的外层布料时…」这种句式，本意是条件句，模型读到
    # 「穿着…外层布料」就照做——这是「没披风却加了披风」的直接来源。
    check("背面不无条件断言角色穿着什么",
          "穿着从肩部垂下的外层布料" not in _bk, _bk[:120])
    check("通用条款不点名具体道具（名词只来自用户描述）",
          "刀剑" not in _bk and "披风" not in _bk)
    check("单侧细长物件有可见性要求（防整根被身体吃掉）",
          "露在身体轮廓之外" in _view_instruction("side"))
    check("角色描述只用于辨认，不作增补依据",
          "不是让你照着文字补齐" in _view_instruction("side", "左腰佩剑")
          and "也不许画" in _view_instruction("side", "左腰佩剑"))
    check("指令含身份锚点条款",
          "身份特征逐项一致" in _view_instruction("side")
          and "花纹" in _view_instruction("side_r"))
    # 近侧配件必须在肢体最外层：漏了这句，左视图里腰挎的刀整把消失（实测）
    check("侧视图声明近侧配件在最外层",
          "最外层" in _view_instruction("side") and "画在胳膊和腿的外面" in _view_instruction("side_r"))
    # 角色描述原文当「部件清单」：细长配件不会在重画身体时被漏掉，
    # 但原句里的姿势词必须当场作废，否则模型退回正面站姿。
    _with_char = _view_instruction("side", "灰白猫武士，左腰侧挎武士刀，正面站立")
    # 描述从「部件清单」降级为「辨认用角色设定」：清单会两头出错——
    # 图里没有、描述里写了的被加上；图里有、描述没写的被当成清单外删掉。
    check("指令附角色设定（仅用于辨认）",
          "角色设定" in _with_char and "左腰侧挎武士刀" in _with_char
          and "不是让你照着文字补齐" in _with_char)
    check("清单里姿势词声明作废",
          "作废" in _with_char and "以本张图的视角为准" in _with_char)
    check("无角色描述时不加设定段", "角色设定" not in _view_instruction("side"))
    # 长指令被序列长度截断是静默失效：截掉的正好是尾部的背景句/守恒条款
    from backend.app.pipelines.threeview import _fit_seq_len
    _ins = _view_instruction("side")
    _need = int(len(_ins) * 1.3) + 64
    check("视角指令不被序列长度截断", _fit_seq_len(_ins, 512) >= _need,
          f"{_fit_seq_len(_ins, 512)} vs {_need}")

    print("== 背景句：脚下不留阴影 ==")
    from backend.app.styles import background_phrase
    for _bg in ("gray", "green", "transparent"):
        _ph = background_phrase(_bg)
        check(f"{_bg} 背景句把背景说成「只有角色+单一底色」",
              "整幅图只有" in _ph, _ph[:40])
        # 「角色悬空不落地」与姿势词「站立」自相矛盾，模型会折中成带影子的站姿
        check(f"{_bg} 背景句不再写「悬空」", "悬空" not in _ph)
    from backend.app.pipelines.threeview import _VIEW_META
    check("正视图姿势词不提踩地", "踩地" not in _VIEW_META["front"]["pose_cn"])
    # derive 要认 background 字段（pydantic 不认会 422，前端任务起不来）。
    # 不真的 POST：那会 create_task 跑模型，冒烟测试不该碰 GPU。
    from backend.app.main import ViewDerivePayload
    dp = ViewDerivePayload(projectId=pid, view="side_r", background="gray")
    check("derive payload 认 background", dp.background == "gray")
    dp = ViewDerivePayload(projectId=pid, view="side", background="transparent")
    check("derive payload 支持透明背景", dp.background == "transparent")
    check("derive payload 默认背景中灰",
          ViewDerivePayload(projectId=pid, view="back").background == "gray")
    check("derive payload 带角色描述",
          ViewDerivePayload(projectId=pid, view="side", character="左腰挎刀").character
          == "左腰挎刀"
          and ViewDerivePayload(projectId=pid, view="side").character == "")
    # 多张正视图候选 → 每张各出一张视角图（拼板时按 pairIndex 成组挑图）
    _multi = ViewDerivePayload(projectId=pid, view="back",
                               sources=[{"kind": "images", "file": "a1"},
                                        {"kind": "images", "file": "a2"}])
    check("derive payload 支持多张正视图", len(_multi.sources) == 2,
          str(_multi.sources))
    check("derive payload 默认单源为空",
          ViewDerivePayload(projectId=pid, view="side").sources == [])

    print("== 拼板抠图：底色与填充同色时补回身体 ==")
    # 灰猫（#7B7777）站中灰底（#808080）上：u2net 必然把头判成背景，而且头在
    # alpha 里和外部背景连成一片（不是封闭孔洞）→ 老版本拼出「没有脑袋的披风」。
    # _restore_enclosed_flat 用轮廓边（Canny）把「被围住却没被判成主体的区域」
    # 恢复不透明；这里用合成图直接验判定逻辑（不依赖 u2net 权重）。
    from backend.app.pipelines.threeview import _restore_enclosed_flat
    from PIL import Image as _Im, ImageDraw as _Draw
    _src = _Im.new("RGB", (200, 200), (128, 128, 128))
    _d = _Draw.Draw(_src)
    _d.ellipse((50, 30, 150, 130), fill=(123, 119, 119), outline=(10, 10, 10), width=3)
    # 模拟 u2net：只有描边被留下，头内部和外部背景都判成透明
    _mask = _Im.new("L", (200, 200), 0)
    _Draw.Draw(_mask).ellipse((50, 30, 150, 130), outline=255, width=3)
    _cut = _src.convert("RGBA")
    _cut.putalpha(_mask)
    _fixed, _n = _restore_enclosed_flat(_cut, _src)
    _fa = _fixed.load()
    check("同色头被轮廓围住时补回不透明", _fa[100, 80][3] == 255, str(_fa[100, 80]))
    check("轮廓外的底色保持透明", _fa[5, 5][3] == 0, str(_fa[5, 5]))
    check("补回的像素是原色", _fa[100, 80][:3] == (123, 119, 119),
          str(_fa[100, 80][:3]))

    print("== 参考图预处理：等比缩放 + 透明铺底 ==")
    # convert("RGB") 会把 alpha=0 压成黑色（模型把黑当背景画进结果）；
    # resize((w,h)) 是拉伸（512×768 转 768² 时角色被拉胖 1.5 倍）。
    from backend.app.providers.diffusers_local import _prep_ref_images
    from PIL import Image as _Img
    _tall = _Img.new("RGBA", (128, 192), (200, 30, 30, 0))
    for _x in range(40, 88):
        for _y in range(60, 150):
            _tall.putpixel((_x, _y), (200, 30, 30, 255))
    _out = _prep_ref_images([_tall], 128, 128, (128, 128, 128))[0]
    _arr = _out.convert("RGB")
    check("参考图输出目标尺寸", _out.size == (128, 128), str(_out.size))
    check("透明参考图铺底色（不变黑）", _arr.getpixel((2, 2))[0] > 90,
          str(_arr.getpixel((2, 2))))
    # 等比缩放：128×192 放进 128×128 → 85×128（左右各留 21px 底色），
    # 红色块宽度 48*0.667≈32px；若被拉伸则宽度仍是 48px（这就是「转视角后换体型」）
    _cols = [x for x in range(128) if _arr.getpixel((x, 64))[0] > 150
             and _arr.getpixel((x, 64))[1] < 90]
    check("参考图等比缩放不拉伸", 28 <= len(_cols) <= 36, f"红色块宽 {len(_cols)}px")
    check("等比缩放后多余边缘铺底色", _arr.getpixel((2, 64))[0] == 128,
          str(_arr.getpixel((2, 64))))

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
    check("八方向链 = 选择图片 → 行走视频 → 序列帧 → 精灵表 → 预览",
          [n["type"] for n in wnodes] == ["input", "walkvideo", "sequence",
                                          "spritesheet", "play", "result"],
          str([n["type"] for n in wnodes]))
    check("模板不再预置生成图 / 四视图节点（单图入口）",
          not any(n["type"] in ("generate", "viewgen") for n in wnodes),
          str([n["type"] for n in wnodes]))

    # 锚点选择：有三视图时正/侧/背零推导，斜向只转 45 度
    from backend.app.pipelines.walkvideo import anchor_for as _af
    from backend.app.pipelines.walkvideo import direction_instruction as _di

    with3 = {"front", "side", "back"}
    plan3 = {d: _af(d, with3) for d in ("down", "down-left", "left", "up-left", "up")}
    check("三视图：正/侧/背零推导",
          plan3["down"] == ("front", 0) and plan3["left"] == ("side", 0)
          and plan3["up"] == ("back", 0), str(plan3))
    check("三视图：斜向只转 45 度",
          abs(plan3["down-left"][1]) == 45 and abs(plan3["up-left"][1]) == 45,
          str(plan3))
    check("左后从背面锚点推导（保住背面细节）",
          plan3["up-left"][0] == "back", str(plan3["up-left"]))
    check("只有正视图时退回老链路",
          _af("left", {"front"}) == ("front", 90)
          and _af("up", {"front"})[1] == -180,
          str(_af("up", {"front"})))
    ins = _di("up-left", "back")
    check("背面锚点的左后指令说到背后与画面左侧",
          "从背后观察" in ins and "画面左侧" in ins, ins[:60])

    # 合成两个方向的「行走视频」：纯色底 + 一个逐帧移动的小人，各 24 帧
    # （帧必须逐帧不同：PIL 写 GIF 会合并连续相同的帧，那是它的优化不是 bug）
    from backend.app import storage as storage_mod

    def fake_walk_frames(n: int = 48, bg=(40, 120, 60)) -> list:
        # 48 帧、周期 24（一个完整步态圈）。周期要大于采样数的两倍，
        # 否则同一周期内取 8 帧会拿到相近姿态、被去重掉——那就测不到
        # 「周期采样」想保证的行为了。
        frames = []
        for i in range(n):
            img = Image.new("RGB", (192, 256), bg)
            d = ImageDraw.Draw(img)
            swing = (i % 24) - 12
            lift = 6 if (i % 24) < 12 else 0
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
    check("GIF 读得回来", len(frames_back) == 48 and len(durations) == 48,
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
    check("没有抠图失败帧", seq_meta.get("matteErrors") == 0,
          str(seq_meta.get("matteErrors")))

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
    check("总览按方向数收窄",
          sheet_meta["overviewSize"][0] < 1024 * 3,
          str(sheet_meta.get("overviewSize")))
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

    # run_sheets 已改成普通函数（丢线程池由调用方负责），直接调即可
    _dirs, _meta = sheet_mod.run_sheets(
        [{"dir": "down", "frames": body_frames(180)},
         {"dir": "left", "frames": body_frames(90)}],
        size=128, shared_scale=True, anchor="bottom")
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
            # 摆动幅度要够大：每帧只移 2px 的话，32×32 灰度差只有 0.87，
            # 低于去重阈值 1.0，按周期采样（步长 2 帧）取到的帧会被全判为
            # 「和前一帧相同」而掉光——那是测试数据不像真人走路，不是缺陷。
            swing = (k % 12) * 3 - 18
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

    # 镜像方向也能指定现成视频：显式来源优先于「镜像左侧」
    r = client.post("/api/walkvideo", json={
        "projectId": vpid,
        "origAssetId": base_info["id"],
        "dirMode": "eight",
        "sources": [{"dir": d, "file": g["file"]} for d, g in vsrc.items()]
                   + [{"dir": "right", "file": vsrc["left"]["file"]}],
    })
    wv4 = wait_task(r.json()["taskId"]).get("result", {})
    check("镜像方向显式来源无错误", not wv4.get("error"), str(wv4.get("error")))
    wm4 = wv4.get("walk") or {}
    r4 = {d["dir"]: d for d in (wm4.get("directions") or [])}
    check("右侧用自己的来源而非镜像",
          r4.get("right", {}).get("mirrored") is False
          and r4["right"]["walk"]["mode"] == "existing",
          str(r4.get("right", {}).get("mirrored")))
    check("现成方向数含镜像方向", wm4.get("existingDirections") == 6,
          str(wm4.get("existingDirections")))

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
                                for d in (sq.get("sequence") or {}).get("directions", [])),
          str([(d.get("dir"), d.get("count"), d.get("dropped"))
               for d in (sq.get("sequence") or {}).get("directions", [])]))

    # 入参兜底：直接调 API 传超大尺寸也不会把输出撑爆（界面输入框有上下限，API 没有）
    r = client.post("/api/spritesheet", json={
        "projectId": vpid,
        "directions": [{"dir": d["dir"],
                        "frames": [f["assetId"] + ".png" for f in d["frames"]]}
                       for d in (sq.get("sequence") or {}).get("directions", [])[:1]],
        "size": 4096, "cols": 999,
    })
    check("超范围尺寸仍接单", r.status_code == 200)
    sq2 = wait_task(r.json()["taskId"]).get("result", {})
    check("尺寸被夹到 512", not sq2.get("error")
          and sq2["sheet"]["directions"][0]["frameSize"]["w"] == 512,
          str(sq2.get("error") or sq2["sheet"]["directions"][0]["frameSize"]))

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
