"""精灵图 → Godot 4 · 2D 序列动画工程包（纯文本生成，不跑模型）。

产物是一个**自包含文件夹 + 一个 zip**（放进 Godot 工程就能播）：

    <stem>.png      图集（atlas 模式；就是那张精灵图）
    frames/*.png    逐帧文件（files 模式，从图集切出来）
    <stem>.tres     SpriteFrames 动画资源
    <stem>.tscn     AnimatedSprite2D 场景（autoplay / 最近邻 / 原点已设好）
    README.txt      三步导入说明（中文，含 res:// 路径与常见坑）

为什么不是「导出 JSON、让用户在引擎里自己拼」：Godot 的资源引用是 res://
**绝对路径**，SpriteFrames 又必须是资源文件（`load_steps` + `ext_resource` +
`AtlasTexture.region` 这套结构）。这些全是确定性文本，手写和让用户手写没有
差别——直接生成，用户拖进工程双击就能播。索引 JSON 那条路留给 Unity/Phaser
（`spritegrid.build_index` 已经出了 Phaser 段）。

两个刻意的设计取舍：

1. **路径必须整体一致**。`.tres` 里写的是 `res://<folder>/<stem>.png`，所以
   导出物必须整体落在 `<folder>` 这个相对目录下。zip 的条目名带 `<folder>/`
   前缀，解到工程根目录即成立——这是唯一能让「路径正确」这件事由结构保证、
   而不是靠用户记的办法。写完会自检一遍（每个 res:// 路径都能在包内找到）。
2. **不生成 `.import`**。那是 Godot 编辑器导入时自己写的（还带本机 uid），
   手写只会冲突。首次打开工程时 Godot 会自动生成。

像素风的两处默认值（都在节点上可改）：`texture_filter = 1`（最近邻，不用改
项目设置）与「原点对齐脚底」（AnimatedSprite2D 的 offset.y = -cell/2），
后者决定 `set_position()` 到地面线时角色是站着还是半埋进地里。
"""
from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from PIL import Image

from .spritegrid import frame_rects, sheet_size

# 动画名是 Godot 里的字符串（可以中文）；文件名必须是 ASCII 才能跨平台安全
_STEM_MAX = 40
_FOLDER_SEGS = 4
_FILTER_VALUE = {"nearest": 1, "linear": 2}     # CanvasItem.TextureFilter


# --------------------------------------------------------------------------- #
# 命名与转义
# --------------------------------------------------------------------------- #
def safe_stem(s: Any, default: str = "anim") -> str:
    """文件名主干：只留 [a-z0-9_-]。

    中文动画名（「行走」）不会被音译——那需要词典，且译出来用户也认不出。
    统一退回 default，动画名本身仍然是中文，只有文件名变成 ASCII。
    """
    raw = str(s or "").strip().lower()
    out = re.sub(r"[^a-z0-9_-]+", "", raw)
    out = out.strip("-_")[:_STEM_MAX]
    return out or default


def safe_folder(f: Any, default: str = "sprites") -> str:
    """res:// 下的相对目录：接受 `res://` 前缀，逐段清洗，最多 4 层。

    用户从 Godot 里复制路径时通常带着 `res://`，直接拒掉太苛刻；带前导 /
    或 `..` 的一律清洗掉——导出物是往磁盘上写的，路径必须可控。
    """
    raw = str(f or "").strip().replace("\\", "/")
    if raw.lower().startswith("res://"):
        raw = raw[6:]
    segs: List[str] = []
    for seg in raw.split("/"):
        if seg in ("", ".", ".."):
            continue
        clean = re.sub(r"[^a-z0-9_-]+", "", seg.lower()).strip("-_")
        if clean:
            segs.append(clean[:_STEM_MAX])
    segs = segs[:_FOLDER_SEGS]
    return "/".join(segs) or default


def _q(s: Any) -> str:
    """Godot 文本资源里的字符串转义（只处理双引号与反斜杠）。"""
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def _node_name(s: Any, default: str = "Anim") -> str:
    """场景节点名：Godot 不允许 . : @ / " % 这几个字符。"""
    raw = str(s or "").strip() or default
    out = re.sub(r'[.:@/"%]', "_", raw)[:40]
    return out or default


# --------------------------------------------------------------------------- #
# .tres / .tscn 文本
# --------------------------------------------------------------------------- #
def build_sprite_frames(
    *,
    folder: str,
    stem: str,
    count: int,
    anim_name: str,
    fps: float,
    loop: bool,
    mode: str = "atlas",
    rects: Optional[Sequence[Dict[str, int]]] = None,
) -> str:
    """生成 Godot 4 的 SpriteFrames 资源文本（format=3）。

    `load_steps` = ext_resource + sub_resource + 1（主资源本身）。写错不会
    报错（Godot 读的时候自己重算），但既然知道就该给对。
    """
    n = max(0, int(count))
    name = _q(anim_name or "default")
    speed = round(max(0.1, float(fps)), 3)
    lines: List[str] = []
    body: List[str] = []

    if mode == "files":
        # 每帧一个独立 PNG：不依赖图集坐标，代价是文件多、draw call 多
        lines.append(f'[gd_resource type="SpriteFrames" load_steps={n + 1} format=3]')
        lines.append("")
        for i in range(n):
            lines.append('[ext_resource type="Texture2D" '
                         'path="res://{}/frames/frame_{:03d}.png" id="1_{:03d}"]'
                         .format(folder, i, i))
            body.append('{{\n"duration": 1.0,\n"texture": ExtResource("1_{:03d}")\n}}'
                        .format(i))
    else:
        lines.append(f'[gd_resource type="SpriteFrames" load_steps={n + 2} format=3]')
        lines.append("")
        lines.append('[ext_resource type="Texture2D" '
                     'path="res://{}/{}.png" id="1_sheet"]'.format(folder, stem))
        lines.append("")
        rs = list(rects or [])
        for i in range(n):
            r = rs[i] if i < len(rs) else {"x": 0, "y": 0, "w": 1, "h": 1}
            lines.append(f'[sub_resource type="AtlasTexture" id="AtlasTexture_{i:03d}"]')
            lines.append('atlas = ExtResource("1_sheet")')
            lines.append("region = Rect2({}, {}, {}, {})".format(
                int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"])))
            lines.append("")
            body.append('{{\n"duration": 1.0,\n"texture": SubResource("AtlasTexture_{:03d}")\n}}'
                        .format(i))

    lines.append("[resource]")
    lines.append("animations = [{")
    lines.append('"frames": [')
    lines.append(", ".join(body))
    lines.append("],")
    lines.append(f'"loop": {str(bool(loop)).lower()},')
    lines.append(f'"name": &"{name}",')
    lines.append(f'"speed": {speed}')
    lines.append("}]")
    lines.append("")
    return "\n".join(lines)


def build_scene(
    *,
    folder: str,
    stem: str,
    anim_name: str,
    node_name: str,
    cell: int,
    origin: str = "bottom",
    texture_filter: str = "nearest",
) -> str:
    """生成 AnimatedSprite2D 场景文本（.tscn, format=3）。

    origin 决定 offset：bottom = 画面底部（脚底）落在节点原点。像素角色的
    地面线就是脚底，做成居中会让 `set_position(地面)` 埋进去半截。
    """
    filt = _FILTER_VALUE.get(texture_filter, 1)
    h = max(1, int(cell))
    if origin == "bottom":
        offset = f"Vector2(0, {-h / 2:g})"
    elif origin == "top":
        offset = f"Vector2(0, {h / 2:g})"
    else:
        offset = "Vector2(0, 0)"
    lines = [
        "[gd_scene load_steps=2 format=3]",
        "",
        '[ext_resource type="SpriteFrames" path="res://{}/{}.tres" id="1_frames"]'
        .format(folder, stem),
        "",
        '[node name="{}" type="AnimatedSprite2D"]'.format(_q(node_name)),
        f"texture_filter = {filt}",
        'sprite_frames = ExtResource("1_frames")',
        'animation = &"{}"'.format(_q(anim_name or "default")),
        'autoplay = "{}"'.format(_q(anim_name or "default")),
        "centered = true",
        f"offset = {offset}",
        "",
    ]
    return "\n".join(lines)


def build_readme(
    *,
    folder: str,
    stem: str,
    anim_name: str,
    count: int,
    fps: float,
    loop: bool,
    cell: int,
    cols: int,
    rows: int,
    sheet: Tuple[int, int],
    mode: str,
    origin: str,
    origin_shift: int,
) -> str:
    origin_text = {
        "bottom": f"脚底（画面底边）在节点原点 · offset.y = {-origin_shift}",
        "top": f"顶部在节点原点 · offset.y = {origin_shift}",
        "center": "中心在节点原点 · offset = (0, 0)",
    }.get(origin, "中心在节点原点")
    frames_note = (
        "· 逐帧 PNG：frames/frame_000.png 起，共 {} 个；SpriteFrames 直接引用它们"
        .format(count)
        if mode == "files" else
        "· 图集：{}.png（{}×{}），SpriteFrames 用 AtlasTexture.region 切格".format(
            stem, sheet[0], sheet[1])
    )
    sample = 'res://{}/{}.tscn'.format(folder, stem)
    return """Pixelforge 导出 · Godot 4 · 2D 序列动画
生成时间：{when}

动画名：{anim}      帧数：{count}    帧率：{fps} fps    循环：{loop}
单元格：{cell}×{cell}    网格：{cols} 列 × {rows} 行
{frames_note}

一、放进 Godot 工程（路径必须一致）
  把本文件夹里的「{top}」整个拷到 Godot 工程根目录（和 project.godot 同级），
  让资源路径恰好是：
      res://{folder}/{stem}.png
      res://{folder}/{stem}.tres
      res://{folder}/{stem}.tscn
  .tres / .tscn 里写死的就是这些路径 —— 放到别的目录，Godot 会报「资源缺失」
  （不是坏了，是路径对不上）。拷完回到 Godot，等导入转圈结束。

二、使用（三种，任选）
  A. 最简单 —— 代码实例化整场景：
         var anim = preload("{sample}").instantiate()
         add_child(anim)
         anim.play("{anim}")
     autoplay 已经设好，其实 add_child 之后就会自己播。

  B. 拖进已有场景：把 {stem}.tscn 从文件系统面板拖到场景树里，当子节点用。

  C. 只要动画资源：把 {stem}.tres 拖到任意 AnimatedSprite2D 节点的
     Sprite Frames 属性上；或把自己的 SpriteFrames 换成它。

三、已经替你设好的东西
  · 节点类型 AnimatedSprite2D，autoplay = "{anim}"：挂上去就播。
  · texture_filter = 1（最近邻）：像素素材不会被糊成一片，不用改项目设置。
  · 原点：{origin_text}
    这样 set_position() 到地面线就站得住。（想改：节点检查器里调 Offset）
  · 循环 {loop_txt}，帧率 {fps} fps（在 SpriteFrames 面板里可改）。

四、想换动画名 / 帧率 / 图集还是逐帧
  回 Pixelforge 的「Godot 导出」节点改参数重导，会重新生成同一文件夹
  （同名覆盖，不产生一堆历史包）。

五、常见问题
  · 播放空白/黑块：多是路径不对（见第一节），看 Godot 输出面板的具体缺哪个文件。
  · 边缘有黑边毛刺：图和图之间发生了插值 → 确认 texture_filter 仍是 1；
    如果用了自定义材质，把材质的 filter 也设成最近邻。
  · 角色位置偏上/偏下：offset 是按「单元格边长 {cell}」算的；如果你在
    SpriteFrames 里换了尺寸，记得同步改 Offset。
""".format(
        when=time.strftime("%Y-%m-%d %H:%M"),
        anim=anim_name or "default", count=count, fps=fps,
        loop="是" if loop else "否", loop_txt="loop = true" if loop else "loop = false",
        cell=cell, cols=cols, rows=rows, frames_note=frames_note,
        top=folder.split("/")[-1], folder=folder, stem=stem, sample=sample,
        origin_text=origin_text,
    )


# --------------------------------------------------------------------------- #
# 自检
# --------------------------------------------------------------------------- #
def verify_bundle(root: Path, folder: str, *, expect_frames: int,
                  mode: str) -> Dict[str, Any]:
    """交付前自检：`res://` 里引用的每个文件都在包内存在，且帧数对得上。

    这条检查针对的是一类真实事故：`.tres` 里的路径和实际落盘的目录名差了
    一层（或大小写不同），Godot 打开时一片空白，用户以为是导出坏了。
    包内自洽 = 解到工程根目录就一定能用。
    """
    texts = sorted(root.glob("*.tres")) + sorted(root.glob("*.tscn"))
    missing: List[str] = []
    refs = 0
    for f in texts:
        try:
            body = f.read_text(encoding="utf-8")
        except Exception as e:
            missing.append(f"{f.name}: 读不出来（{e}）")
            continue
        for m in re.finditer(r'path="res://([^"]+)"', body):
            refs += 1
            rel = m.group(1)
            pre = folder + "/"
            target = root / (rel[len(pre):] if rel.startswith(pre) else rel)
            if not target.is_file():
                missing.append(f"{f.name} → res://{rel}")
    tres = next((f for f in texts if f.suffix == ".tres"), None)
    count_ok = False
    frames_seen = 0
    if tres is not None:
        body = tres.read_text(encoding="utf-8")
        frames_seen = (body.count('sub_resource type="AtlasTexture"')
                       if mode != "files" else body.count("[ext_resource"))
        count_ok = frames_seen == expect_frames
    return {
        "pathsMatch": not missing,
        "missing": missing[:8],
        "refsChecked": refs,
        "framesInResource": frames_seen,
        "framesMatch": count_ok,
    }


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def run_export(
    project_root: Path,
    *,
    sheet_path: Path,
    url_base: str,
    cell: int = 128,
    cols: int = 0,
    count: int = 0,
    padding: int = 0,
    margin: int = 0,
    fps: float = 12.0,
    anim_name: str = "default",
    stem: str = "walk",
    loop: bool = True,
    mode: str = "atlas",
    folder: str = "sprites",
    origin: str = "bottom",
    texture_filter: str = "nearest",
) -> Dict[str, Any]:
    """精灵图 + 拼板参数 → Godot 4 工程包（文件夹 + zip）。

    只读精灵图，不改它；产出落在 <项目>/exports/<stem>/ 与 exports/<stem>.zip。
    同名重导会整目录重建（顺带清掉上一次多出来的逐帧文件）。
    """
    if not sheet_path.is_file():
        raise ValueError("找不到精灵图文件：先跑一遍「精灵图」节点")
    cell = max(8, min(int(cell), 512))
    padding = max(0, min(int(padding), 32))
    margin = max(0, min(int(margin), 64))
    count = max(0, int(count))
    if count < 1:
        raise ValueError("精灵图里没有帧：先在「拆解帧」里勾选几帧")
    cols = max(1, min(int(cols) if cols else count, count))
    rows = (count + cols - 1) // cols
    fps = max(0.1, min(float(fps), 60.0))
    mode = "files" if str(mode) == "files" else "atlas"
    origin = origin if origin in ("bottom", "center", "top") else "bottom"
    stem = safe_stem(stem, "anim")
    folder = safe_folder(folder, "sprites")

    # 参数 ↔ 图集必须自洽：列数/单元格对不上时切出来的 region 全是错的，
    # 与其导出一个「能播但错位」的包，不如在这里拦下来
    want_w, want_h = sheet_size(count, cell=cell, cols=cols,
                                padding=padding, margin=margin)
    try:
        with Image.open(sheet_path) as im:
            im.load()
            got = im.size
            rgba = im.convert("RGBA")
    except Exception as e:
        raise ValueError(f"精灵图读不出来：{e}")
    if got != (want_w, want_h):
        raise ValueError(
            f"精灵图尺寸 {got[0]}×{got[1]} 与参数不符（应为 {want_w}×{want_h}）："
            "画布上的列数 / 单元格变过，重跑一次「精灵图」节点再导出")

    out_root = project_root / "exports"
    out_dir = out_root / stem
    if out_dir.is_dir():
        shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    rects = frame_rects(count, cell=cell, cols=cols, padding=padding, margin=margin)
    files: List[Dict[str, Any]] = []

    if mode == "files":
        fdir = out_dir / "frames"
        fdir.mkdir(parents=True, exist_ok=True)
        for i, r in enumerate(rects):
            crop = rgba.crop((r["x"], r["y"], r["x"] + r["w"], r["y"] + r["h"]))
            p = fdir / f"frame_{i:03d}.png"
            crop.save(p, "PNG", optimize=True)
        png_res = f"res://{folder}/frames/frame_000.png"
    else:
        dst = out_dir / f"{stem}.png"
        shutil.copyfile(sheet_path, dst)
        files.append({"name": dst.name, "bytes": dst.stat().st_size,
                      "url": f"{url_base}/{stem}/{dst.name}"})
        png_res = f"res://{folder}/{stem}.png"

    tres_text = build_sprite_frames(folder=folder, stem=stem, count=count,
                                    anim_name=anim_name, fps=fps, loop=loop,
                                    mode=mode, rects=rects)
    tpath = out_dir / f"{stem}.tres"
    tpath.write_text(tres_text, encoding="utf-8")
    files.append({"name": tpath.name, "bytes": tpath.stat().st_size,
                  "url": f"{url_base}/{stem}/{tpath.name}"})

    spath = out_dir / f"{stem}.tscn"
    spath.write_text(build_scene(folder=folder, stem=stem, anim_name=anim_name,
                                 node_name=_node_name(stem), cell=cell,
                                 origin=origin, texture_filter=texture_filter),
                     encoding="utf-8")
    files.append({"name": spath.name, "bytes": spath.stat().st_size,
                  "url": f"{url_base}/{stem}/{spath.name}"})

    rpath = out_dir / "README.txt"
    rpath.write_text(build_readme(
        folder=folder, stem=stem, anim_name=anim_name, count=count, fps=fps,
        loop=loop, cell=cell, cols=cols, rows=rows, sheet=(want_w, want_h),
        mode=mode, origin=origin, origin_shift=cell // 2), encoding="utf-8")
    files.append({"name": rpath.name, "bytes": rpath.stat().st_size,
                  "url": f"{url_base}/{stem}/{rpath.name}"})

    checks = verify_bundle(out_dir, folder, expect_frames=count, mode=mode)

    # zip：条目带 <folder>/ 前缀，解到工程根目录后 res:// 路径恰好成立
    zip_path = out_root / f"{stem}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(out_dir.rglob("*")):
            if f.is_file():
                rel = f.relative_to(out_dir).as_posix()
                z.writestr(f"{folder}/{rel}", f.read_bytes())

    warnings: List[str] = []
    if not checks["pathsMatch"]:
        warnings.append("包内路径自检未通过：" + "；".join(checks["missing"]))
    if not checks["framesMatch"]:
        warnings.append(f"帧数与资源不符（{checks['framesInResource']} / {count}）")
    if count > 64:
        warnings.append(f"{count} 帧会让图集偏大；超过 64 帧建议开「逐帧文件」模式")

    return {
        "ok": True,
        "stem": stem,
        "folder": folder,
        "mode": mode,
        "files": files,
        "zip": {"name": zip_path.name, "bytes": zip_path.stat().st_size,
                "url": f"{url_base}/{zip_path.name}"},
        "resPaths": {
            "png": png_res,
            "tres": f"res://{folder}/{stem}.tres",
            "scene": f"res://{folder}/{stem}.tscn",
        },
        "meta": {
            "animName": anim_name or "default",
            "count": count,
            "fps": fps,
            "loop": bool(loop),
            "cell": cell,
            "cols": cols,
            "rows": rows,
            "padding": padding,
            "margin": margin,
            "sheetSize": [want_w, want_h],
            "mode": mode,
            "origin": origin,
            "textureFilter": texture_filter,
            "originOffsetY": -(cell // 2) if origin == "bottom"
            else (cell // 2 if origin == "top" else 0),
            "checks": checks,
            "warnings": warnings,
            "seconds": round(time.time(), 3),
        },
    }
