"""喵喵叫的可爱助手 · 后端服务。

端点分组：
- /api/health, /api/doctor, /api/models         环境与模型
- /api/templates, /api/styles, /api/prompt-examples   模板 / 画风 / 示例库
- /api/settings                                  设置
- /api/projects...                               项目（无限画布的持久化）
- /api/generate/preview                          提示词预览（纯字符串，无 GPU）
- /api/generate                                  生成（任务 + SSE 进度）
- /api/tasks/{id}/stream|/cancel                 进度与取消
- /api/pixel/refine                              对已有原图重新精修
- /api/matte                                     扣除背景（u2net → GrabCut → 颜色键）
- /api/threeview/derive                          四视图：由正视图接力生成侧/背/右视图（任务制）
- /api/threeview/compose                         四视图：正/侧/背/右拼成横向板（3 张按老三视图）
- /api/walkvideo                                 八方向行走：方向立绘 + Wan 行走视频（任务制）
- /api/txtvideo                                  文本生视频：提示词 + 可选参考图 → 视频（任务制）
- /api/sequence                                  八方向行走：视频 → 抽帧去重抠图的序列帧（任务制）
- /api/spritesheet                               八方向行走：序列帧 → 每方向精灵表 + 总览（任务制）
- /api/clipsplit                                 视频转素材：视频/GIF → 拆帧 + 指纹 + 抠图（任务制）
- /api/spritegrid                                视频转素材：选中帧 → 网格精灵图 + 索引 JSON（同步）
- /api/godot2d                                   精灵图 → Godot 4 工程包（tres/tscn/zip，同步）
- /api/inpaint                                   局部重绘：涂抹蒙版 + 提示词 → 只重画这一块（任务制）
- /api/inpaint/instruction                       局部重绘：提示词拼装预览（我写的 / 程序加的 / 参考图声明）
- /api/projects/{pid}/mask                       上传涂抹蒙版（白色=重绘区）
- /api/erase                                     去除局部：涂抹擦除水印/杂物（扩散/纹理/镜像/AI 四选一）
- /api/erase/instruction                         去除局部：AI 方式的提示词拼装预览
- /api/erase/methods                             去除局部：四种方式的适用场景说明
- /api/layer25d                                  2.5D 分层模型：静态立绘 → 可操作的分层 PSD（任务制）
- /api/layer25d/status                           2.5D 分层模型：see-through 运行环境体检
- /api/projects/{pid}/export/{path}               导出包文件下载（tres / tscn / zip / 逐帧 PNG）

生成是「任务制」：POST /api/generate 立即返回 taskId，前端订阅 SSE 看进度，
完成事件里带结果（同时留档 10 分钟供 /api/tasks/{id} 兜底查询）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image
from pydantic import BaseModel, Field

from . import doctor as doctor_mod
from . import progress as progress_mod
from . import projects as projects_mod
from . import prompt_examples as examples_mod
from . import prompt_limits as limits_mod
from . import storage
from . import alpha_clean
from . import custom_templates as ctpl
from . import template_overrides as tov
from . import viewcheck
from .config import DATA_DIR, ROOT, load_settings, save_settings, scan_models
from .pipelines import matte as matte_mod
from .pipelines import refine as refine_mod
from .pipelines import sequence as sequence_mod
from .pipelines import clipframes as clipframes_mod
from .pipelines import spritegrid as spritegrid_mod
from .pipelines import godot2d as godot2d_mod
from .pipelines import inpaint as inpaint_mod
from .pipelines import erase as erase_mod
from .pipelines import parts as parts_mod
from .pipelines import layer25d as layer25d_mod
from .pipelines import rigwalk as rigwalk_mod
from .pipelines import spritesheet as spritesheet_mod
from .pipelines import threeview as threeview_mod
from .pipelines import mp4encode as mp4encode_mod
from .pipelines import videofx as videofx_mod
from .pipelines import walkvideo as walkvideo_mod
from .providers.diffusers_local import DiffusersProvider
from .styles import (
    BACKGROUND_PRESETS, background_phrase, build_prompt, detect_material,
    get_style, list_styles, pixel_negative_extra, resolve_pixel_negative,
)
from .templates import get_template, list_templates

APP_VERSION = "0.1.0"
MAX_REFS = 4
MAX_IMAGE_SIDE = 1024

app = FastAPI(title="喵喵叫的可爱助手", version=APP_VERSION)

# 开发期：Vite dev server 跨端口访问后端
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _on_startup() -> None:
    # 启动即准备好数据目录与示例库，避免首个请求踩初始化路径
    examples_mod.load_examples()
    asyncio.create_task(_idle_unload_loop())
    _install_disconnect_noise_filter()


def _is_client_disconnect_noise(context: Dict[str, Any]) -> bool:
    """这条「loop 异常」是不是「对端已经断了」的已知噪声？

    典型现场（Windows 上刷屏的那段）：
        Exception in callback _ProactorBasePipeTransport._call_connection_lost()
        ... self._sock.shutdown(socket.SHUT_RDWR)
        ConnectionResetError: [WinError 10054] 远程主机强迫关闭了一个现有的连接。

    成因：浏览器关标签/刷新、EventSource.close()、fetch 被 abort 之后，连接已经
    RST 了，但 Proactor 的 _call_connection_lost 仍会对那个 socket 调 shutdown()。
    这是 CPython 在 Windows 上的已知噪声——**连接本来就不在了，没有可修的动作**。

    判据收得很窄（只认 ConnectionResetError + 连接丢失/关闭上下文），避免把真正的
    服务端异常一起吞掉。
    """
    exc = context.get("exception")
    if not isinstance(exc, ConnectionResetError):
        return False
    msg = str(context.get("message") or "").lower()
    return ("_call_connection_lost" in msg or "connection_lost" in msg
            or "shutdown" in msg)


def _install_disconnect_noise_filter() -> None:
    """装一个 loop 异常过滤器：只吞「客户端断连」噪声，其余照旧交给原处理器。

    为什么需要：这段 traceback 会把终端刷得很吓人，让人以为后台崩了；而它既不影响
    正在跑的任务（任务在独立的 asyncio.create_task 里，与 SSE 连接无耦合，前端断开
    也不会取消它——结果照样落盘，下次拉 /api/tasks/{id} 还能拿到），也不影响下一次
    请求。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    prev = loop.get_exception_handler()

    def _handler(loop_: asyncio.AbstractEventLoop, context: Dict[str, Any]) -> None:
        if _is_client_disconnect_noise(context):
            return
        if prev is not None:
            prev(loop_, context)          # 保留原有行为（uvicorn 的日志、其它异常）
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)



# 空闲多久后自动卸载驻留底座（秒）。显存是独占资源：跑完一张图后如果一直
# 抱着 20GB，浏览器 / 游戏 / 别的 AI 工具就分不到显存了。跑任务时不会触发
# （有未完成任务就跳过），代价只是下次生成要多一次加载。
IDLE_UNLOAD_SECONDS = float(os.environ.get("MEOWCUTE_IDLE_UNLOAD", "600"))


async def _idle_unload_loop() -> None:
    """每 60 秒看一次：没有任务在跑且底座空闲超过阈值，就卸掉归还显存。"""
    while True:
        await asyncio.sleep(60)
        try:
            if not DiffusersProvider.is_loaded():
                continue
            if DiffusersProvider.idle_seconds() < IDLE_UNLOAD_SECONDS:
                continue
            # 有任务没结束就别动：中途卸掉模型会直接把任务打挂
            if progress_mod.has_running():
                continue
            before = DiffusersProvider.vram_gb().get("reserved", 0)
            DiffusersProvider.unload()
            after = DiffusersProvider.vram_gb().get("reserved", 0)
            print(f"[meowcute] 空闲 {IDLE_UNLOAD_SECONDS:.0f}s，已卸载驻留底座"
                  f"（显存 {before}GB → {after}GB）", flush=True)
        except asyncio.CancelledError:
            return
        except Exception as e:
            print(f"[meowcute] 空闲卸载检查失败：{e}", flush=True)


# --------------------------------------------------------------------------- #
# 基础
# --------------------------------------------------------------------------- #


@app.get("/api/health")
async def health() -> Dict[str, Any]:
    models = scan_models()
    return {
        "ok": True,
        "app": "meowcute",
        "version": APP_VERSION,
        "dataDir": str(DATA_DIR),
        "modelCount": len(models),
        "families": sorted({m["family"] for m in models}),
    }


@app.get("/api/doctor")
async def doctor() -> Dict[str, Any]:
    return doctor_mod.run_doctor()


@app.get("/api/models")
async def models() -> Dict[str, Any]:
    return {"models": scan_models()}


# --------------------------------------------------------------------------- #
# 模板 / 画风 / 示例库
# --------------------------------------------------------------------------- #


@app.get("/api/templates")
async def templates() -> Dict[str, Any]:
    # 内置模板一律经过 apply：用户对默认模板的编辑是本地覆盖补丁，
    # 直接返回 TEMPLATES 常量会让「编辑了但不生效」再次上演。
    builtin = [tov.apply(t) for t in list_templates()]
    custom = [{**ctpl._public(t), "editable": True} for t in ctpl.list_templates()]
    return {"templates": builtin + custom}


@app.get("/api/templates/{tid}")
async def template_detail(tid: str) -> Dict[str, Any]:
    t = get_template(tid)
    if t:
        return tov.apply(t)
    ct = ctpl.get_template(tid)
    if not ct:
        raise HTTPException(404, "模板不存在")
    return {**ctpl._public(ct), "editable": True}


@app.post("/api/templates/{tid}/draft")
async def open_template_draft(tid: str) -> Dict[str, Any]:
    """把模板打开成一份画布草稿：模板编辑走无限画布，而不是表单填节点。"""
    raw = get_template(tid)
    if raw is not None:
        t = tov.apply(raw)
    else:
        ct = ctpl.get_template(tid)
        if not ct:
            raise HTTPException(404, "模板不存在")
        t = {**ctpl._public(ct), "editable": True}
    if t.get("status") != "available":
        raise HTTPException(400, "该模板尚未开放，先把节点链跑通再编辑")
    # 同一个模板只留一份草稿：反复点「编辑」不该在项目列表里堆出一排草稿
    exist = projects_mod.find_draft(tid)
    if exist is not None:
        return {"ok": True, "project": exist, "template": t, "reused": True}
    project = projects_mod.create_project(
        f"{t.get('name') or '模板'} · 模板编辑", tid, t.get("defaults") or {},
        t.get("graph") or {"nodes": [], "edges": []},
        draft_of=tid, keep_ids=True)
    return {"ok": True, "project": project, "template": t, "reused": False}


class CommitTemplatePayload(BaseModel):
    projectId: str
    keepDraft: bool = False


@app.post("/api/templates/{tid}/commit")
async def commit_template_draft(tid: str, payload: CommitTemplatePayload) -> Dict[str, Any]:
    """把画布草稿写回模板。

    运行时数据（参考图、结果图、选图记录）由 _clean_graph 统一清掉——模板里
    指向别的项目的 assetId 没有任何意义；`_updatedAt` 之类的项目字段也不会
    进模板。校验「草稿确实属于这个模板」是安全边界：否则前端可以拿任意
    projectId 覆盖任意模板。
    """
    proj = projects_mod.load_project(payload.projectId)
    if proj is None:
        raise HTTPException(404, "编辑草稿不存在（可能已被删除）")
    if str(proj.get("templateDraftOf") or "") != tid:
        raise HTTPException(400, "这个项目不是该模板的编辑草稿")

    # normalize=False：坐标已经在模板坐标系里，平移只会制造假 diff
    graph = ctpl._clean_graph(
        {"nodes": proj.get("nodes") or [], "edges": proj.get("edges") or []},
        normalize=False)
    if not graph["nodes"]:
        raise HTTPException(400, "草稿里没有可用节点")

    raw = get_template(tid)
    if raw is not None:
        tpl = tov.apply(raw)
    else:
        ct = ctpl.get_template(tid)
        if not ct:
            raise HTTPException(404, "模板不存在")
        tpl = ctpl._public(ct)

    # 默认参数从模板里的第一张生成卡派生：用户在画布上已经把尺寸/画风调好了，
    # 再让他单独维护一套 defaults 只会分叉（这正是上一版编辑器被嫌复杂的原因）
    defaults = dict(tpl.get("defaults") or {})
    gen = next((n for n in graph["nodes"] if n.get("type") == "generate"), None)
    if gen:
        if gen.get("styleId"):
            defaults["styleId"] = gen["styleId"]
        gp = gen.get("params") or {}
        for k in ("width", "height", "n", "background", "maxSeqLen"):
            if k in gp:
                defaults[k] = gp[k]
        if isinstance(gen.get("pixel"), dict):
            defaults["pixel"] = {**(defaults.get("pixel") or {}), **gen["pixel"]}

    if raw is not None:
        t = tov.set_override(tid, {"graph": graph, "defaults": defaults})
    else:
        t = ctpl.update(tid, {"graph": graph, "defaults": defaults})
        if t is None:
            raise HTTPException(404, "模板不存在")
        t = {**t, "editable": True}

    if not payload.keepDraft:
        projects_mod.delete_project(payload.projectId)
    return {"ok": True, "template": t, "draftDeleted": not payload.keepDraft}


class BuiltinTemplatePayload(BaseModel):
    """内置模板的编辑补丁：只提交想改的字段，其余保持出厂值。"""

    name: Optional[str] = None
    tagline: Optional[str] = None
    description: Optional[str] = None
    highlights: Optional[List[str]] = None
    notes: Optional[List[str]] = None
    steps: Optional[List[Dict[str, Any]]] = None
    inputs: Optional[List[Dict[str, Any]]] = None
    outputs: Optional[List[Dict[str, Any]]] = None
    defaults: Optional[Dict[str, Any]] = None
    graph: Optional[Dict[str, Any]] = None


@app.put("/api/templates/builtin/{tid}")
async def update_builtin_template(tid: str, payload: BuiltinTemplatePayload) -> Dict[str, Any]:
    """编辑内置默认模板（存为本地覆盖补丁，不改代码里的出厂定义）。"""
    try:
        t = tov.set_override(tid, payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "template": t}


@app.delete("/api/templates/builtin/{tid}")
async def reset_builtin_template(tid: str) -> Dict[str, Any]:
    """恢复出厂默认：删掉覆盖补丁。"""
    try:
        t = tov.reset_override(tid)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "template": t}


@app.post("/api/templates/{tid}/duplicate")
async def duplicate_template(tid: str) -> Dict[str, Any]:
    """把任何模板（含内置）另存为自定义副本：想大改又不想动默认时用。"""
    src = get_template(tid)
    if src is not None:
        eff = tov.apply(src)
        name = f"{eff.get('name') or '模板'}（副本）"
        graph = eff.get("graph") or {"nodes": [], "edges": []}
        defaults = eff.get("defaults") or {}
    else:
        ct = ctpl.get_template(tid)
        if not ct:
            raise HTTPException(404, "模板不存在")
        pub = ctpl._public(ct)
        name = f"{pub.get('name') or '模板'}（副本）"
        graph = pub.get("graph") or {"nodes": [], "edges": []}
        defaults = pub.get("defaults") or {}
    try:
        t = ctpl.create({"name": name, "tagline": "自定义工作流",
                         "description": "", "graph": graph, "defaults": defaults})
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "template": t}


class CustomTemplatePayload(BaseModel):
    name: str = "未命名模板"
    tagline: str = ""
    description: str = ""
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    graph: Optional[Dict[str, Any]] = None
    defaults: Optional[Dict[str, Any]] = None


@app.post("/api/templates/custom")
async def create_custom_template(payload: CustomTemplatePayload) -> Dict[str, Any]:
    """把画布节点图存成自定义模板。"""
    try:
        t = ctpl.create(payload.model_dump(exclude_none=True))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "template": t}


@app.put("/api/templates/custom/{tid}")
async def update_custom_template(tid: str, payload: CustomTemplatePayload) -> Dict[str, Any]:
    t = ctpl.update(tid, payload.model_dump(exclude_none=True))
    if not t:
        raise HTTPException(404, "模板不存在")
    return {"ok": True, "template": t}


@app.delete("/api/templates/custom/{tid}")
async def delete_custom_template(tid: str) -> Dict[str, Any]:
    if not ctpl.delete(tid):
        raise HTTPException(404, "模板不存在")
    return {"ok": True}


@app.get("/api/limits")
async def prompt_limits() -> Dict[str, Any]:
    """提示词长度上限（按画风 / 质量档位换算），前端用来做计数器与硬限制。

    单一事实来源放在后端：上限取决于「画风词 + 像素契约占多少 token」，
    前端自己拍一个数字，迟早和真实截断点对不上。
    """
    return {"limits": limits_mod.snapshot()}


@app.get("/api/styles")
async def styles() -> Dict[str, Any]:
    return {"styles": list_styles(), "backgrounds": BACKGROUND_PRESETS}


@app.get("/api/prompt-examples")
async def prompt_examples() -> Dict[str, Any]:
    return {"categories": examples_mod.CATEGORIES,
            "examples": examples_mod.load_examples()}


class ExamplePayload(BaseModel):
    id: Optional[str] = None
    category: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    note: Optional[str] = None


@app.post("/api/prompt-examples")
async def upsert_prompt_example(payload: ExamplePayload) -> Dict[str, Any]:
    item = examples_mod.upsert_example(payload.model_dump(exclude_none=True))
    return {"ok": True, "example": item}


@app.delete("/api/prompt-examples/{eid}")
async def delete_prompt_example(eid: str) -> Dict[str, Any]:
    ok = examples_mod.delete_example(eid)
    if not ok:
        raise HTTPException(404, "示例不存在")
    return {"ok": True}


@app.post("/api/prompt-examples/reset")
async def reset_prompt_examples() -> Dict[str, Any]:
    return {"ok": True, "examples": examples_mod.reset_examples()}


# --------------------------------------------------------------------------- #
# 设置
# --------------------------------------------------------------------------- #


@app.get("/api/settings")
async def get_settings() -> Dict[str, Any]:
    s = load_settings()
    # 不下发 apiKey 明文（本地也无所谓，但接口保持干净）
    out = json.loads(json.dumps(s))
    for cfg in (out.get("providers") or {}).values():
        if isinstance(cfg, dict) and cfg.get("apiKey"):
            cfg["apiKey"] = "***"
    return {"settings": out, "models": scan_models()}


@app.put("/api/settings")
async def put_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    # apiKey 回传 "***" 表示未修改
    providers = payload.get("providers") or {}
    if isinstance(providers, dict):
        cur = load_settings()
        for key, cfg in providers.items():
            if isinstance(cfg, dict) and cfg.get("apiKey") == "***":
                cfg.pop("apiKey", None)
                if isinstance(cur.get("providers", {}).get(key), dict):
                    cfg["apiKey"] = cur["providers"][key].get("apiKey", "")
    merged = save_settings(payload)
    return {"ok": True, "settings": merged}


@app.post("/api/settings/test")
async def test_provider() -> Dict[str, Any]:
    s = load_settings()
    cfg = (s.get("providers") or {}).get("diffusers") or {}
    p = DiffusersProvider(cfg)
    try:
        res = await p.test()
        return res
    except Exception as e:
        return {"ok": False, "message": str(e)[:400]}


@app.post("/api/settings/unload")
async def unload_model() -> Dict[str, Any]:
    DiffusersProvider.unload()
    return {"ok": True, "message": "模型已卸载，显存已释放"}


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #


class CreateProjectPayload(BaseModel):
    name: str = Field(default="未命名项目")
    templateId: str


@app.get("/api/projects")
async def list_projects() -> Dict[str, Any]:
    return {"projects": projects_mod.list_projects()}


@app.post("/api/projects")
async def create_project(payload: CreateProjectPayload) -> Dict[str, Any]:
    raw = get_template(payload.templateId)
    t = tov.apply(raw) if raw is not None else None
    graph = t.get("graph") if t else None
    if not t:
        ct = ctpl.get_template(payload.templateId)
        if not ct:
            raise HTTPException(404, "模板不存在")
        t = ctpl._public(ct)
        graph = t.get("graph")
    if t.get("status") != "available":
        raise HTTPException(400, "该模板尚未开放")
    project = projects_mod.create_project(payload.name, payload.templateId,
                                          t.get("defaults") or {}, graph)
    return {"ok": True, "project": project}


@app.get("/api/projects/{pid}")
async def get_project(pid: str) -> Dict[str, Any]:
    p = projects_mod.load_project(pid)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return {"project": p}


@app.put("/api/projects/{pid}")
async def put_project(pid: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    try:
        p = projects_mod.save_project(pid, payload)
    except FileNotFoundError:
        raise HTTPException(404, "项目不存在")
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "project": p}


@app.delete("/api/projects/{pid}")
async def delete_project(pid: str) -> Dict[str, Any]:
    ok = projects_mod.delete_project(pid)
    if not ok:
        raise HTTPException(404, "项目不存在")
    return {"ok": True}


class RenamePayload(BaseModel):
    name: str


@app.patch("/api/projects/{pid}/name")
async def rename_project(pid: str, payload: RenamePayload) -> Dict[str, Any]:
    p = projects_mod.rename_project(pid, payload.name)
    if p is None:
        raise HTTPException(404, "项目不存在")
    return {"ok": True, "project": p}


@app.get("/api/projects/{pid}/files")
async def project_files(pid: str) -> Dict[str, Any]:
    """项目文件夹清单：输入（refs/）与产出（images/）都在这里。"""
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    return storage.project_files(pid)


@app.post("/api/projects/{pid}/reveal")
async def reveal_project_folder(pid: str) -> Dict[str, Any]:
    """在系统文件管理器里打开项目文件夹。"""
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    info = storage.project_files(pid)
    ok = await asyncio.to_thread(storage.reveal_in_os, pid)
    if not ok:
        return {"ok": False, "root": info["root"], "message": "无法打开文件管理器，路径已复制到界面"}
    return {"ok": True, "root": info["root"], "message": "已在文件管理器中打开"}


@app.get("/api/projects/{pid}/export/{path:path}")
async def project_export_file(pid: str, path: str):
    """导出包里的任意文件（.tres / .tscn / .zip / README / 逐帧 PNG）。

    单独一条路由而不是复用下面的 /{kind}/{filename}：那条按扩展名白名单只放
    png / json / 视频，Godot 的资源文件（tres / tscn）和 zip 都不在里面。
    必须声明在通用路由**之前**——`export/walk.tres` 也能匹配 `{kind}/{filename}`，
    先声明的先命中。
    """
    p = storage.export_path(pid, path)
    if p is None:
        raise HTTPException(404, "导出文件不存在")
    low = p.name.lower()
    if low.endswith(".zip"):
        media, as_attachment = "application/zip", True
    elif low.endswith(".txt"):
        media, as_attachment = "text/plain; charset=utf-8", True
    elif low.endswith(".psd"):
        # 分层 PSD 是二进制资源，浏览器要下载而不是当文本显示
        media, as_attachment = "application/octet-stream", True
    elif low.endswith(".png"):
        media, as_attachment = "image/png", False
    else:                       # .tres / .tscn：让浏览器直接显示文本
        media, as_attachment = "text/plain; charset=utf-8", False
    return FileResponse(str(p), media_type=media,
                        filename=p.name if as_attachment else None,
                        headers={"Cache-Control": "no-cache"})


@app.get("/api/projects/{pid}/{kind}/{filename}")
async def project_asset(pid: str, kind: str, filename: str):
    if kind not in storage.ALLOWED_KINDS:
        raise HTTPException(404, "资产类型不存在")
    path = storage.asset_path(pid, kind, filename)
    if path is None:
        raise HTTPException(404, "资产不存在")
    low = filename.lower()
    if low.endswith(".gif"):
        media = "image/gif"
    elif low.endswith(".json"):
        media = "application/json"
    elif low.endswith(storage.VIDEO_EXTS):
        media = storage.VIDEO_MIME.get(Path(low).suffix, "application/octet-stream")
    else:
        media = "image/png"
    return FileResponse(str(path), media_type=media,
                        headers={"Cache-Control": "public, max-age=31536000"})


@app.get("/api/projects/{pid}/videos")
async def list_project_videos(pid: str) -> Dict[str, Any]:
    """项目内可用视频列表：历史生成的 walk-*.gif + 上传的 vidsrc-*。"""
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    return {"videos": storage.list_videos(pid)}


@app.post("/api/projects/{pid}/videos/upload")
async def upload_project_video(pid: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """上传一段现成视频（mp4/mov/webm/gif…），存进项目供行走视频节点选用。

    分块落盘（storage.save_video_stream），100MB 的视频不会整包进内存。
    """
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in storage.VIDEO_EXTS:
        raise HTTPException(400, "只支持 " + " / ".join(storage.VIDEO_EXTS) + " 格式")
    try:
        info = storage.save_video_stream(pid, file.file, suffix)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "video": info}


@app.post("/api/projects/{pid}/refs")
async def upload_ref(pid: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    raw = await file.read()
    if len(raw) > 24 * 1024 * 1024:
        raise HTTPException(400, "参考图不能超过 24MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(400, "无法识别的图片格式")
    img = img.convert("RGBA")
    w, h = img.size
    if max(w, h) > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / max(w, h)
        img = img.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.LANCZOS)
    info = storage.save_image(pid, img, kind="refs", prefix="ref")
    return {"ok": True, "ref": {**info, "name": (file.filename or "参考图")[:80]}}


# --------------------------------------------------------------------------- #
# 提示词预览
# --------------------------------------------------------------------------- #


class PixelOptions(BaseModel):
    enabled: bool = True
    gridSnap: bool = True
    colors: int = 16
    palette: str = "auto"
    gridSize: Optional[List[int]] = None
    targetSize: Optional[List[int]] = None
    # 精修后把纯色背景置为透明（四角估计背景色）
    transparentBg: bool = False


class PreviewPayload(BaseModel):
    prompt: str = ""
    styleId: Optional[str] = None
    width: int = 512
    height: int = 512
    background: str = "gray"
    pixel: Optional[PixelOptions] = None
    # 生成卡上覆盖的负面词（空 = 用「全局默认 + 像素契约」拼出来的那套）
    negative: str = ""


def _contract_grid(pixel: PixelOptions, style_id: Optional[str],
                   width: int, height: int) -> List[int]:
    """像素契约里的网格尺寸：目标尺寸 > 画风固定网格 > 画风建议块大小 > 固定 64。

    固定网格（pixelize.grid）优先于「画布宽 / block」：模型原生 1024 出图时，
    block=4 会算出 256 网格，远超像素画需要的精度，反而让像素块细碎、
    描边跨格。各画风直接声明目标素材网格更稳。
    """
    if pixel.targetSize and len(pixel.targetSize) == 2:
        return [max(8, int(pixel.targetSize[0])), max(8, int(pixel.targetSize[1]))]
    style = get_style(style_id)
    grid_px = 0
    block = 0
    if style and isinstance(style.get("pixelize"), dict):
        grid_px = int(style["pixelize"].get("grid") or 0)
        block = int(style["pixelize"].get("block") or 0)
    if grid_px > 0:
        g = max(8, grid_px)
        if width == height:
            return [g, g]
        # 非方形画布：短边定网格、长边按比例。直接给方网格会把 512×768
        # 说成「64×64 网格」，模型只能拉伸构图，比例必崩。
        if width < height:
            return [g, max(8, int(round(g * height / width)))]
        return [max(8, int(round(g * width / height))), g]
    if block <= 0:
        return [64, 64]
    return [max(8, width // block), max(8, height // block)]


def _build_generation_prompt(prompt: str, style_id: Optional[str],
                             pixel: PixelOptions, width: int, height: int,
                             background: str = "gray",
                             edit: bool = False,
                             negative_override: str = "") -> Dict[str, Any]:
    style = get_style(style_id)
    pixel_class = bool(style and style.get("pixelClass"))
    # 像素契约只对像素类画风生效：gridSnap 是像素精修的子选项，不能用来
    # 绕过画风把写实/卡通图也按像素规格约束。
    use_contract = pixel.enabled and pixel_class
    grid = _contract_grid(pixel, style_id, width, height)
    material = detect_material(prompt)
    built = build_prompt(prompt, style_id, pixel=use_contract, grid=grid,
                         colors=pixel.colors,
                         background=background_phrase(background),
                         material=material, edit=edit)
    settings = load_settings()
    base_neg = (settings.get("providers", {}).get("diffusers", {}) or {}).get("negativePrompt", "")
    # 负面词三层来源，全部摊开给界面：
    #   base     = 全局设置里的默认负面词
    #   contract = 开了像素契约时自动追加的「像素画最怕的瑕疵」
    #   custom   = 用户在生成卡上整段覆盖（非空则前两层都不生效）
    # 以前 custom 这一层不存在，负面词只能改全局设置，节点上看不到也改不了——
    # 换个非像素画风时用户没有地方把「抗锯齿/渐变」那套拿掉。
    custom_neg = (negative_override or "").strip()
    contract_neg = pixel_negative_extra(use_contract, material)
    if custom_neg:
        built["negative"] = custom_neg
    else:
        built["negative"] = resolve_pixel_negative(base_neg, use_contract, material)
    built["negativeParts"] = {
        "base": ("" if custom_neg else str(base_neg or "").strip("，, ")),
        "contract": ("" if custom_neg else contract_neg),
        "custom": custom_neg,
        "overridden": bool(custom_neg),
    }
    # 透明背景：Qwen-Image-2.1 原生 RGBA 的官方双段式声明。缺了这段，
    # VAE 第 4 通道（alpha）不会激活，输出恒为不透明（实测）。
    if background == "transparent":
        built["prompt"] = (
            "This is an RGBA image with transparency. "
            + built["prompt"]
            + " The image has alpha channel and the background is transparent."
        )
    built["useContract"] = use_contract
    built["grid"] = grid
    built["background"] = background
    built["material"] = material
    return built


@app.post("/api/generate/preview")
async def preview_prompt(payload: PreviewPayload) -> Dict[str, Any]:
    pixel = payload.pixel or PixelOptions()
    built = _build_generation_prompt(payload.prompt, payload.styleId, pixel,
                                     payload.width, payload.height, payload.background,
                                     negative_override=payload.negative)
    return built


class NegativePayload(BaseModel):
    """只要负面词：画风 + 像素选项 + 主体描述（材质检测用）+ 已覆盖的值。"""

    prompt: str = ""
    styleId: Optional[str] = None
    pixel: Optional[PixelOptions] = None
    negative: str = ""


@app.post("/api/generate/negative")
async def preview_negative(payload: NegativePayload) -> Dict[str, Any]:
    """当前生效的负面词正文 —— **自动态也要能看见**。

    生成卡以前只写一句「当前用全局设置里的默认负面词」，用户看不到具体是哪
    几个词，想改还得先点「改为自己写」把内容预填出来（等于改之前不能看）。
    这里把最终会送进模型的那串负面词、以及它是哪几段拼起来的，直接摊给界面。

    复用 `_build_generation_prompt` 而不是在这里重算：负面词是
    「全局默认 + 像素契约（含材质检测）+ 用户覆盖」三层决定的，抄一遍必然漂。
    宽高与背景不参与负面词计算，传常量即可。
    """
    pixel = payload.pixel or PixelOptions()
    built = _build_generation_prompt(
        payload.prompt, payload.styleId, pixel, 512, 512, "gray",
        negative_override=payload.negative)
    parts = built["negativeParts"]
    return {
        "negative": built["negative"],
        "parts": parts,
        "useContract": built["useContract"],
        "material": built["material"],
        # 给界面直接显示「N 项」用，省得前端自己按逗号切（中文逗号也要认）
        "counts": {
            "base": _negative_count(parts.get("base")),
            "contract": _negative_count(parts.get("contract")),
            "custom": _negative_count(parts.get("custom")),
        },
    }


def _negative_count(text: str) -> int:
    return len([p for p in (text or "").replace("，", ",").split(",") if p.strip()])


# --------------------------------------------------------------------------- #
# 生成（任务制）
# --------------------------------------------------------------------------- #


class ParamsPayload(BaseModel):
    width: int = 512
    height: int = 512
    n: int = 1
    steps: Optional[int] = None
    seed: Optional[int] = None
    background: str = "gray"
    # 文本序列长度：界面用「快速/标准/高质量」映射到 512/768/1024，
    # 为空时由 provider 按提示词长度自动估算（不写死）
    maxSeqLen: Optional[int] = None
    # 负面词覆盖（空 = 全局默认 + 像素契约自动拼）。放在节点参数里而不是
    # 全局设置里：换个画风/换个角色往往就要换负面词，全局一处改不了这么细。
    negative: str = ""


class GeneratePayload(BaseModel):
    projectId: str
    genNodeId: Optional[str] = None
    prompt: str = ""
    styleId: Optional[str] = None
    refs: List[str] = Field(default_factory=list)   # 参考图文件名（refs/ 下）
    params: ParamsPayload = Field(default_factory=ParamsPayload)
    pixel: PixelOptions = Field(default_factory=PixelOptions)

task_results: Dict[str, Dict[str, Any]] = {}


def _b64_to_image(data: str) -> Optional[Image.Image]:
    """data:image/png;base64,xxx → PIL。"""
    try:
        if "," in data:
            data = data.split(",", 1)[1]
        raw = base64.b64decode(data)
        img = Image.open(io.BytesIO(raw))
        img.load()
        return img.convert("RGBA")
    except Exception:
        return None


async def _run_generate(task_id: str, payload: GeneratePayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        provider = DiffusersProvider(provider_cfg)

        params = payload.params
        width = max(128, min(int(params.width), 1536)) // 8 * 8
        height = max(128, min(int(params.height), 1536)) // 8 * 8
        n = max(1, min(int(params.n), 4))
        pixel = payload.pixel

        progress_mod.report(task_id, 0, 0, "读取参考图…")
        refs: List[Image.Image] = []
        for name in payload.refs[:MAX_REFS]:
            img = storage.load_image(payload.projectId, "refs", name)
            if img is not None:
                refs.append(img)

        # 有参考图 = 指令式编辑：主指令必须明确「保持角色外观不变」。
        # 之前只有 UI 这么承诺，后端其实没拼这句，模型会自由发挥导致角色漂移。
        prompt_text = payload.prompt
        if refs:
            base = (prompt_text or "").strip()
            prompt_text = ("以参考图中的角色为准，保持其外观、配色、体型比例不变"
                           + ("，只做以下调整：" + base if base else "。"))

        built = _build_generation_prompt(prompt_text, payload.styleId, pixel,
                                         width, height, params.background,
                                         edit=bool(refs),
                                         negative_override=params.negative)

        seed = params.seed
        if seed is None:
            seed = int(time.time() * 1000) % (2**31 - 1)

        progress_mod.report(task_id, 0, 0, "加载模型…" if not refs else "载入编辑底座…")
        result = await provider.generate(
            prompt=built["prompt"],
            width=width, height=height,
            steps=params.steps, seed=seed, n=n,
            refs=refs or None,
            task_id=task_id,
            # 「自动」= 按最终提示词（含风格词 / 像素契约）的实际长度估算。
            # 之前 None 直接落到管线默认 512 token（约 344 字），而像素契约本身
            # 就占 256 字，用户写 100 字就被截——界面上却显示「自动（推荐）」。
            max_seq_len=limits_mod.fit_seq_len(built["prompt"], params.maxSeqLen),
            # 负面词必须真的传下去：以前只把 built["negative"] 塞进 meta 给
            # 界面看，provider 读的是设置里的默认值，像素模式的负面清单
            # （抗锯齿/渐变/半透明…）从未生效。
            negative=built["negative"] or None,
        )

        images = list(result.images)
        meta = dict(result.meta or {})

        # 像素精修只对「像素类画风」生效。写实/卡通/水彩这类画风如果也走
        # 网格对齐 + 调色板量化，渐变和细节会被直接压成色块——这是毁图，不是精修。
        style_def = get_style(payload.styleId)
        pixel_on = bool(pixel.enabled and style_def and style_def.get("pixelClass"))
        meta["pixelOn"] = pixel_on

        # 透明背景只有 Qwen-Image-2.1 能原生输出（VAE 第 4 通道）；
        # 走到这里说明底座不是它（设置里被改成了别的路径），必须明说。
        if params.background == "transparent" and meta.get("family") != "qwen21":
            meta["bgWarning"] = (
                f"当前底座（{meta.get('family') or '未知'}）不支持原生透明输出，"
                "本张图为不透明背景；请切换 models/qwen-image-2.1 后再选「透明」。")

        # ---- 像素精修 ----
        refined_meta: List[Dict[str, Any]] = []
        refined_images: List[Image.Image] = []
        if pixel_on:
            progress_mod.report(task_id, 0, 0, "像素精修：网格对齐 + 调色板量化…")
            rb = await asyncio.to_thread(
                refine_mod.refine_batch,
                images,
                grid_snap=bool(pixel.gridSnap),
                grid_size=pixel.gridSize,
                target_size=pixel.targetSize,
                sample_method="median",
                colors=int(pixel.colors),
                palette_name=pixel.palette or "auto",
                transparent_bg=bool(pixel.transparentBg),
            )
            refined_images = [it["image"] for it in rb["items"]]
            refined_meta = [it["meta"] for it in rb["items"]]
            meta["refinePalette"] = rb.get("paletteName")
            meta["refinePaletteSize"] = len(rb.get("palette") or [])

        progress_mod.report(task_id, 0, 0, "落盘…")
        items: List[Dict[str, Any]] = []
        for i, img in enumerate(images):
            orig_info = storage.save_image(payload.projectId, img.convert("RGBA"),
                                           kind="images", prefix="out", seq=i + 1)
            item: Dict[str, Any] = {
                "origAssetId": orig_info["id"],
                "origUrl": orig_info["url"],
                "origSize": [img.width, img.height],
            }
            if pixel_on and i < len(refined_images):
                ref_info = storage.save_image(payload.projectId,
                                              refined_images[i].convert("RGBA"),
                                              kind="images", prefix="px", seq=i + 1)
                item.update({
                    "assetId": ref_info["id"],
                    "url": ref_info["url"],
                    "width": refined_images[i].width,
                    "height": refined_images[i].height,
                    "refine": refined_meta[i],
                })
            else:
                item.update({
                    "assetId": orig_info["id"],
                    "url": orig_info["url"],
                    "width": img.width,
                    "height": img.height,
                })
            items.append(item)

        elapsed = round(time.time() - t0, 1)
        task_results[task_id] = {
            "taskId": task_id,
            "images": items,
            "prompt": built["prompt"],
            "negative": built["negative"],
            "breakdown": built["breakdown"],
            "meta": {
                "model": meta.get("model"),
                "family": meta.get("family"),
                "mode": meta.get("mode"),
                "steps": meta.get("steps"),
                # provider 产出的键是 maxSeqLen（这条白名单以前写的是 seqLen，
                # 而 provider 从来没产过那个键 —— 界面上「实际用了多少 token」
                # 一直是空的，连排查都只能靠猜）
                "maxSeqLen": meta.get("maxSeqLen"),
                "seqLen": meta.get("maxSeqLen") or meta.get("seqLen"),
                "seed": seed,
                "seconds": elapsed,
                "vramWarning": meta.get("vramWarning"),
                "bgWarning": meta.get("bgWarning"),
                # 峰值显存（GB）：显存是这条工具链最容易被抱怨的资源，报出来
                "vram": meta.get("vram"),
                "refCount": len(refs),
                "pixelEnabled": bool(pixel_on),
                "palette": meta.get("refinePalette"),
            },
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/generate")
async def generate(payload: GeneratePayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_generate(task_id, payload))
    return {"taskId": task_id}


def _gc_tasks() -> None:
    """回收已完成一小时的任务快照与结果。

    两条缓存都是「一次任务一条」，跑一天下来（尤其反复重跑八方向链）
    会一直涨：任务快照带着标签字符串，task_results 带着整份产出清单。
    挂在任务查询上顺手清，不需要后台线程。
    """
    if len(task_results) < 32 and progress_mod.count() < 64:
        return
    progress_mod.cleanup()
    live = set(progress_mod.ids())
    for tid in [t for t in task_results if t not in live]:
        task_results.pop(tid, None)


@app.get("/api/tasks/{task_id}")
async def task_state(task_id: str) -> Dict[str, Any]:
    _gc_tasks()
    snap = progress_mod.get(task_id)
    if snap is None:
        raise HTTPException(404, "任务不存在")
    out: Dict[str, Any] = {"progress": snap.snapshot()}
    res = task_results.get(task_id)
    if res is not None:
        out["result"] = res
    return out


@app.get("/api/tasks/{task_id}/stream")
async def task_stream(task_id: str):
    """SSE：progress 事件流；done 事件里直接带结果（省一次往返）。"""
    if progress_mod.get(task_id) is None:
        raise HTTPException(404, "任务不存在")

    async def gen():
        q = progress_mod.subscribe(task_id)
        try:
            # 先推一次当前快照，避免前端错过早期进度。
            # **已完成的任务必须在这里就发 done 并结束**：毫秒级的任务（去除局部、
            # 抠图）经常在 SSE 建连之前就 finish 了，那时队列里不会再有新事件，
            # 前端只能收到一个「label=完成 但没有 done」的 progress —— 于是
            # 节点一直卡在「处理中…」，按钮永久禁用（2026-10-25 实测踩到）。
            snap = progress_mod.get(task_id)
            if snap and snap.done_at:
                res = task_results.get(task_id)
                payload = {"type": "done", **snap.snapshot()}
                if res is not None:
                    payload["result"] = res
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            if snap:
                yield f"data: {json.dumps({'type': 'progress', **snap.snapshot()}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    # 心跳：长推理（尤其模型加载）期间保持连接不被中间层掐断
                    yield ": ping\n\n"
                    continue
                if item.get("doneAt"):
                    res = task_results.get(task_id)
                    payload = {"type": "done", **item}
                    if res is not None:
                        payload["result"] = res
                    yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps({'type': 'progress', **item}, ensure_ascii=False)}\n\n"
        finally:
            progress_mod.unsubscribe(task_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/tasks/{task_id}/cancel")
async def task_cancel(task_id: str) -> Dict[str, Any]:
    ok = progress_mod.cancel(task_id)
    if not ok:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "message": "已请求取消，推理将在当前步结束后终止"}


# --------------------------------------------------------------------------- #
# 重新精修（对已有原图）
# --------------------------------------------------------------------------- #


class RefinePayload(BaseModel):
    projectId: str
    origAssetId: str = ""
    # 精修源：images/ 下的生成产物，或 refs/ 下的输入图片（「选择图片」节点）
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    pixel: PixelOptions = Field(default_factory=PixelOptions)


@app.post("/api/pixel/refine")
async def refine_existing(payload: RefinePayload) -> Dict[str, Any]:
    if payload.sourceKind == "refs" and payload.sourceFile:
        img = storage.load_image(payload.projectId, "refs", payload.sourceFile)
    else:
        img = storage.load_image(payload.projectId, "images", f"{payload.origAssetId}.png")
    if img is None:
        raise HTTPException(404, "原图不存在")
    p = payload.pixel
    rb = await asyncio.to_thread(
        refine_mod.refine_batch, [img],
        grid_snap=bool(p.gridSnap), grid_size=p.gridSize,
        target_size=p.targetSize, sample_method="median",
        colors=int(p.colors), palette_name=p.palette or "auto",
        transparent_bg=bool(p.transparentBg))
    out = rb["items"][0]
    info = storage.save_image(payload.projectId, out["image"].convert("RGBA"),
                              kind="images", prefix="px")
    return {"ok": True, "image": {
        "assetId": info["id"], "url": info["url"],
        "width": out["image"].width, "height": out["image"].height,
        "refine": out["meta"],
    }, "palette": rb.get("paletteName")}


# --------------------------------------------------------------------------- #
# 扣除背景
# --------------------------------------------------------------------------- #


class MatteOptions(BaseModel):
    """扣背景参数。enabled=False 时节点直接透传上游图片（开关在画布节点上）。"""
    enabled: bool = True
    # auto：u2net 语义分割 → GrabCut → 颜色键，逐级退化
    method: str = "auto"
    tolerance: int = 80
    # 硬边 alpha（像素素材） vs 羽化（照片 / 半透明素材）
    hard: bool = True
    feather: int = 0
    despill: bool = True
    # 抠完按 alpha 包围盒裁到主体，便于直接当素材用
    trim: bool = False
    # 边界偏移：负=内缩（去掉一圈底色残影），正=外扩（补回被啃掉的轮廓）
    edge: int = 0
    # 从主体底部切掉的比例（0-0.3）：AI 画的接地投影只能这样确定性地去掉
    bottomTrim: float = 0.0


class MattePayload(BaseModel):
    projectId: str
    origAssetId: str = ""
    # 源图位置：images/ 下的产出（生成、精修、上一次抠图），或 refs/ 下的输入图
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    matte: MatteOptions = Field(default_factory=MatteOptions)


@app.get("/api/matte/status")
async def matte_status() -> Dict[str, Any]:
    """抠图引擎体检：哪条路径能用、现在会走哪条（u2net 权重缺失时提示）。"""
    return await asyncio.to_thread(matte_mod.engine_status)


@app.post("/api/matte")
async def matte_existing(payload: MattePayload) -> Dict[str, Any]:
    if payload.sourceKind == "refs" and payload.sourceFile:
        img = storage.load_image(payload.projectId, "refs", payload.sourceFile)
    else:
        img = storage.load_image(payload.projectId, "images", f"{payload.origAssetId}.png")
    if img is None:
        raise HTTPException(404, "原图不存在")

    m = payload.matte
    out, meta = await asyncio.to_thread(
        matte_mod.remove_background, img,
        tolerance=int(m.tolerance), feather=int(m.feather),
        hard=bool(m.hard), despill=bool(m.despill),
        edge=int(m.edge), bottom_trim=float(m.bottomTrim),
        method=m.method if m.method in ("auto", "u2net", "grabcut", "key") else "auto")
    if m.trim:
        out, trim_info = matte_mod.trim_to_subject(out)
        meta["trim"] = trim_info
    info = storage.save_image(payload.projectId, out.convert("RGBA"),
                              kind="images", prefix="bg")
    return {"ok": True, "image": {
        "assetId": info["id"], "url": info["url"],
        "width": out.width, "height": out.height,
        "matte": meta,
    }}


# --------------------------------------------------------------------------- #
# 角色四视图
# --------------------------------------------------------------------------- #


class ThreeViewSource(BaseModel):
    """一路图片来源：拼板的一路输入，或视角推导的一个正视图候选。"""
    kind: str = "images"                    # images | refs
    file: str = ""                          # images 下是 assetId，refs 下是文件名


class ViewDerivePayload(BaseModel):
    """由正视图接力生成侧/背视图。参考图就是上游那张正视图。"""
    projectId: str
    # 源图位置：images/ 下的产出（生成卡），或 refs/ 下上传的正视图
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    origAssetId: str = ""
    # 多张正视图（生成卡 n>1 时的全部候选）：**按顺序每张各出一张视角图**，
    # 返回结果用 pairIndex 与源图一一对齐——抽卡抽到多张时，每张都有自己
    # 一套侧/背/右，拼板时才能逐张挑「同一组」而不串角色。
    sources: List[ThreeViewSource] = Field(default_factory=list)
    view: str = "side"                      # side | back | side_r
    styleId: Optional[str] = None
    steps: Optional[int] = None
    seed: Optional[int] = None
    width: int = 512
    height: int = 768
    maxSeqLen: Optional[int] = None
    # 正视图的背景（gray/green/none/transparent）：侧/背/右复用同一背景，
    # 拼板时各张底色才一致，抠底不会把角色咬出异色边
    background: str = "gray"
    # 正视图那句角色描述原文：附在视角指令里当「部件清单」，防止腰挎的刀、
    # 背上的弓这类细长配件在重画身体时被漏掉（前端从上游生成卡带下来）
    character: str = ""
    # 用户在视角卡上改过的指令正文（空 = 用程序里那套默认条款）。
    # 以前这套指令整段硬编码在 threeview._view_instruction 里，界面既看不到
    # 也改不了；现在默认值仍由程序给，但正文可以整段覆盖。
    instruction: str = ""
    # 同理：视角生成的负面词也一直是程序常量（VIEW_NEGATIVE），
    # 界面看不到也改不了，这里一并放开
    negative: str = ""
    # 一致性校验未通过时是否换种子重试一次。默认开：实测同一批 6 张背视图里
    # 只有 1 张保住了披风，重试一次的期望收益明显高于一次 40 步的成本。
    retryOnFail: bool = True


class ViewInstructionPayload(BaseModel):
    """视角提示词预览请求。字段与 derive 对齐，预览到的才是真实会送进模型的。"""
    view: str = "side"
    character: str = ""
    background: str = "gray"
    instruction: str = ""          # 已保存的自定义正文（有则按它算）
    negative: str = ""             # 已保存的自定义负面词（有则按它算）


class ComposePayload(BaseModel):
    projectId: str
    sources: List[ThreeViewSource] = Field(default_factory=list)
    bg: str = "1B4332"                      # 底板色（暗绿）
    # True = 输出 RGBA 透明底板（不贴底色，各视图 alpha 原样带出）
    transparentBg: bool = False
    gap: int = 12
    baseline: float = 0.93                  # 主体底部对齐到画布高度的比例
    cell: Optional[List[int]] = None        # 单格尺寸，空则取三张最大值


def _load_source(project_id: str, src: ThreeViewSource) -> Optional[Image.Image]:
    """加载上游资产图（参考图或生成图）。

    **容错是必需的**：前端各节点历史上把 refs 里的 id 当 images 传（sourceKind
    固定 'images'），按 kind 直查就会「文件明明在、却报参考图不存在」。所以两个
    目录都试、文件名带不带 .png 都试，命中即返回。
    """
    name = str(src.file or "").strip()
    if not name:
        return None
    order = ("refs", "images") if src.kind == "refs" else ("images", "refs")
    for kind in order:
        for cand in (name, f"{name}.png"):
            im = storage.load_image(project_id, kind, cand)
            if im is not None:
                return im
    return None


async def _run_view_derive(task_id: str, payload: ViewDerivePayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        if payload.view not in ("side", "back", "side_r"):
            raise RuntimeError(f"未知视角：{payload.view}")

        # 单源老路径（sourceKind/origAssetId）与多源新路径合并成一张清单：
        # 顺序 = 正视图候选顺序，返回的每张视角图都带 pairIndex 指回源图
        srcs = list(payload.sources or [])
        if not srcs:
            srcs = [ThreeViewSource(kind=payload.sourceKind,
                                    file=payload.origAssetId or payload.sourceFile or "")]
        if len(srcs) > 8:
            raise RuntimeError(f"一次最多推导 8 张，收到 {len(srcs)} 张")
        srcs = [s for s in srcs if (s.file or "").strip()]
        if not srcs:
            raise RuntimeError("没有可用的正视图：请先跑上游的「生成图」或「选择图片」节点")

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        images: List[Dict[str, Any]] = []
        metas: List[Dict[str, Any]] = []
        failed: List[str] = []
        for i, src in enumerate(srcs):
            progress_mod.check_cancel(task_id)
            base = _load_source(payload.projectId, src)
            if base is None:
                if len(srcs) == 1:
                    raise RuntimeError(
                        "正视图不存在：请先跑上游的「生成图」或「选择图片」节点")
                failed.append(f"{i + 1}: 源图不存在")
                continue
            # 一致性校验：参考色板取这张正视图本身（多源时每张各管各的）。
            # 判据是「主色块还在不在」（色相存在性），不是「直方图像不像」：
            # 背视图看不到脸和胸前配色，分布形状必然变，按相似度判会整批误拦。
            # 标定与反例见 backend/app/viewcheck.py 顶部注释。
            ref_prof = viewcheck.build_reference([base])
            attempt = 0
            while True:
                out, meta = await threeview_mod.derive_view(
                    payload.view, base,
                    style_id=payload.styleId,
                    steps=payload.steps,
                    # 重试必须换种子，否则同一 seed 出来的还是同一张
                    seed=(None if payload.seed is None
                          else int(payload.seed) + 997 * attempt),
                    width=payload.width, height=payload.height,
                    max_seq_len=payload.maxSeqLen,
                    background=payload.background,
                    character=payload.character,
                    instruction=payload.instruction,
                    negative=payload.negative,
                    provider_cfg=provider_cfg, task_id=task_id,
                    batch_index=i, batch_total=len(srcs))
                # 校验绝不该挡住出图：算不动就放行（宁可漏判，不可误杀）
                try:
                    verdict = viewcheck.check_view(
                        viewcheck.analyze(out, f"{src.file}#{attempt}"),
                        ref_prof, payload.view)
                except Exception as e:
                    verdict = {"ok": True, "retry": False, "fails": [],
                               "warns": [f"一致性校验跳过：{e}"[:200]],
                               "metrics": {}}
                if verdict["ok"] or not payload.retryOnFail or attempt >= 1:
                    break
                attempt += 1
                progress_mod.report(
                    task_id, i, len(srcs),
                    f"第 {i + 1} 张未通过一致性校验"
                    f"（{verdict['fails'][0]}），换种子重试")
            verdict["attempt"] = attempt
            info = storage.save_image(payload.projectId, out.convert("RGBA"),
                                      kind="images",
                                      # 文件名前缀区分视角：side→vs / back→vb /
                                      # side_r→vr（原来取 view[0]，side 和 side_r
                                      # 都是 vs，翻项目目录时分不清）
                                      prefix={"side": "vs", "back": "vb",
                                              "side_r": "vr"}[payload.view])
            images.append({
                "assetId": info["id"], "url": info["url"],
                "width": out.width, "height": out.height,
                # 与源图的对应关系：拼板按组挑图全靠它（第 k 张正视图 ↔ 第 k 张视角图）
                "pairIndex": i,
                "srcAssetId": src.file,
            })
            metas.append(dict(meta, pairIndex=i, srcAssetId=src.file,
                              check=verdict))

        if not images:
            raise RuntimeError("所有正视图都未能生成：" + "；".join(failed[:4]))
        progress_mod.report(task_id, len(images), len(srcs), "完成")
        task_results[task_id] = {
            "taskId": task_id,
            "image": images[0],
            "images": images,
            "view": payload.view,
            # 单张时给扁平 meta（老布局读 meta.mode/steps/seed），
            # 多张时给 batch 列表，每张一份
            "meta": metas[0] if len(metas) == 1 else {"batch": metas},
            "seconds": round(time.time() - t0, 1),
            **({"failed": failed} if failed else {}),
        }
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/threeview/instruction")
async def threeview_instruction(payload: ViewInstructionPayload) -> Dict[str, Any]:
    """视角提示词的拼装结果：给视角卡展示，也用于编辑时预填。

    返回三段：
    - body  = 指令正文（界面上可编辑的那部分，默认就是程序拼的 11 段）；
    - full  = 真正送进模型的整段（正文 + 背景声明），预览要显示这个；
    - parts = 默认条款的逐段拆解，让用户知道程序加了什么、加在哪。
    自定义正文非空时 body/full 都按它算，parts 仍返回默认值供对照。
    """
    if payload.view not in ("side", "back", "side_r"):
        raise HTTPException(400, f"未知视角：{payload.view}")
    custom = (payload.instruction or "").strip()
    parts = threeview_mod.view_instruction_parts(payload.view, payload.character)
    default_body = "".join(t for _, _, t in parts)
    body = custom or default_body
    full = threeview_mod.apply_background(body, payload.background)
    neg_custom = (payload.negative or "").strip()
    # 与 derive_view 同一套回落：背景选「无」时不传负面词（禁「地面」和自由背景矛盾）
    neg = (None if payload.background in ("", "none")
           else (neg_custom or threeview_mod.VIEW_NEGATIVE))
    return {
        "view": payload.view,
        "body": body,
        "full": full,
        "defaultBody": default_body,
        "custom": bool(custom),
        "background": payload.background,
        "parts": [{"key": k, "label": lb, "text": t} for k, lb, t in parts if t],
        "negative": neg,
        "negativeCustom": bool(neg_custom),
        "negativeMaxChars": limits_mod.chars_for_tokens(256),
        "chars": len(full),
        "maxChars": limits_mod.chars_for_tokens(limits_mod.SEQ_LEN_MAX),
    }


@app.post("/api/threeview/derive")
async def threeview_derive(payload: ViewDerivePayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_view_derive(task_id, payload))
    return {"taskId": task_id}


@app.post("/api/threeview/compose")
async def threeview_compose(payload: ComposePayload) -> Dict[str, Any]:
    if len(payload.sources) not in (3, 4):
        raise HTTPException(
            400, f"视图拼板需要 3 张（正/侧/背）或 4 张（正/侧/背/右），"
                 f"收到 {len(payload.sources)} 张")
    imgs: List[Image.Image] = []
    for i, src in enumerate(payload.sources):
        img = _load_source(payload.projectId, src)
        if img is None:
            raise HTTPException(
                404, f"第 {i + 1} 张图不存在：{src.file or '（未指定）'}")
        imgs.append(img)

    # 底板色：显式 transparentBg、或把 bg 写成 transparent/none，都算透明底板；
    # 其余必须是 6 位十六进制——乱码一律 400，别静默回落到暗绿拼出一张错板。
    # 空串仍回落默认暗绿（用户在色号框里清空不该把拼板卡死）。
    raw_bg = (payload.bg or "").strip().lstrip("#")
    transparent = bool(payload.transparentBg) or raw_bg.lower() in ("transparent", "none")
    bg: Optional[tuple] = None
    if not transparent:
        if not raw_bg:
            bg = threeview_mod.DEFAULT_BG
        elif len(raw_bg) == 6 and all(c in "0123456789abcdefABCDEF" for c in raw_bg):
            bg = tuple(int(raw_bg[j:j + 2], 16) for j in (0, 2, 4))
        else:
            raise HTTPException(
                400, "底板色格式应为 6 位十六进制（如 1B4332）；"
                     "要透明底板请打开「透明底板」开关")

    cell = None
    if payload.cell and len(payload.cell) == 2:
        cell = (int(payload.cell[0]), int(payload.cell[1]))

    # 拼板前的一致性复核：以第 1 张（正视图）为参考，逐张看主色块还在不在。
    # 真正的止损点在 derive（那里拦能换种子重跑），这里兜的是「用户手工逐格
    # 挑图挑错了」——拼板是确定性操作，错图拼上去看不出来，只会显得角色变了。
    try:
        ref_prof = viewcheck.build_reference([imgs[0]])
        checks: List[Dict[str, Any]] = []
        for idx, img in enumerate(imgs[1:], start=1):
            view = threeview_mod.VIEW_ORDER[idx]
            v = viewcheck.check_view(viewcheck.analyze(img), ref_prof, view)
            checks.append({"index": idx, "view": view, "ok": v["ok"],
                           "fails": v["fails"], "warns": v["warns"][:2]})
    except Exception as e:                       # 校验绝不该挡住拼板
        checks = [{"index": -1, "view": "", "ok": True,
                   "fails": [], "warns": [f"一致性复核跳过：{e}"[:200]]}]

    strip, meta = await asyncio.to_thread(
        threeview_mod.compose_three_view, imgs,
        cell=cell, bg=bg, gap=max(0, int(payload.gap)),
        baseline=min(0.99, max(0.5, float(payload.baseline))))
    # 透明底板存 RGBA，其余存原样 RGB：给不透明板凭空补一条 alpha=255 的通道
    # 只会让文件变大，还容易在下游被误判成「带透明」
    info = storage.save_image(payload.projectId, strip,
                              kind="images", prefix="tv")
    return {"ok": True, "image": {
        "assetId": info["id"], "url": info["url"],
        "width": strip.width, "height": strip.height,
        "threeview": dict(meta, checks=checks),
    }}


# --------------------------------------------------------------------------- #
# 八方向行走：行走视频 / 序列帧 / 精灵整图
# --------------------------------------------------------------------------- #
class WalkVideoSource(BaseModel):
    """行走视频节点的一路现成视频来源：方向 → 项目内视频文件。"""
    dir: str
    file: str = ""                              # images/ 下的文件名


class WalkViewAsset(BaseModel):
    """四视图锚点：角色（正/侧/背/右）→ 项目内图片文件。"""
    role: str                                   # front | side | back
    file: str = ""


class WalkDirImage(BaseModel):
    """用户为某个方向直接指定的立绘图（跳过推导，但仍会跑 Wan）。"""
    dir: str
    file: str = ""


def _output_fps(fps: int, interpolate: bool) -> int:
    """成片帧率：插帧是「同样时长换更顺的动作」，帧数翻倍时 fps 必须跟着翻。

    历史 bug：插帧后帧数 13→25 而 fps 留在 12，同一段动作被拉成两倍时长
    （变成慢动作）。封顶 60（浏览器与 H.264 的常见上限）。
    """
    f = int(fps or 0)
    if not interpolate:
        return f
    return min(60, f * 2)


def _clamp(v: Any, lo: int, hi: int, default: int) -> int:
    """接口入参兜底：界面上的输入框有上下限，直接调 API 的没有。

    不加这一层的话，`numFrames=100000` 或 `size=4096` 会一路传到推理里，
    轻则跑几小时，重则 OOM 把服务端进程打死。
    """
    try:
        return max(lo, min(int(v), hi))
    except Exception:
        return default


class WalkVideoPayload(BaseModel):
    """行走视频节点：正视图 → 八方向立绘 + Wan 行走视频。"""
    projectId: str
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    origAssetId: str = ""
    dirMode: str = "eight"                     # eight | four | single
    action: str = "行走"
    # 整段动作提示词：用户完整输入，程序不拼接。为空时退回按 action 拼的
    # 模板（老项目 / 接口直调的兼容路径）。
    prompt: Optional[str] = None
    # 负面词：空 = 用 walkvideo.DEFAULT_WALK_NEGATIVE（以前写死在管线里，
    # 卡片上看不到也改不了）
    negative: str = ""
    styleId: Optional[str] = None
    steps: Optional[int] = None
    seed: Optional[int] = None
    width: int = 384
    height: int = 512
    numFrames: int = 21                        # 默认引擎 hy15 帧数（4k+1；上限 257，越长越慢）
    fps: int = 24
    maxSide: int = 512
    maxSeqLen: Optional[int] = None
    # 现成视频来源：这些方向跳过立绘推导 + Wan，直接用文件里的帧
    sources: List[WalkVideoSource] = Field(default_factory=list)
    # 四视图锚点：有了它们，正/侧/背/右四个方向零推导，斜向只转 45 度
    viewAssets: List[WalkViewAsset] = Field(default_factory=list)
    # 用户为每个方向指定的立绘图
    dirImages: List[WalkDirImage] = Field(default_factory=list)
    # 像素保真：像素素材用最近邻缩放，别被视频底座糊掉
    pixelArt: bool = False
    # 行走动作控制（轻量闭环，不上更大模型也能逼出「真正在走」）：
    # guidanceScale=None 用默认 5.0；motionThreshold 是动作幅度合格线
    # （motion_stats 的剪影 1-IoU，真实行走约 5~20，定格 <3）；retries 是
    # 不达标时自动加压重跑的次数。
    guidanceScale: Optional[float] = None
    motionThreshold: float = 6.0
    retries: int = 2
    # 视频引擎：hy15（HunyuanVideo-1.5，默认优先）/ wan（Wan2.2 TI2V-5B）。
    # None 时按可用性自动选：hy15 权重就绪就用 hy15。
    engine: Optional[str] = None
    # hy15 分窗阈值：0=关闭（整段生成，长片会 OOM）；>0 且 numFrames 更大时链式
    # 分窗。默认 41 —— 段数越少角色漂移越小（121 帧 6 段→3 段：instability
    # 30.8%→23.4%、颜色漂移 76.8→57.3、耗时 122.7s→104.3s），而 41 帧/段峰值
    # 18.76GB 仍在 24G 内；依据见 backend/app/providers/hy15.py 的 DEFAULT_WINDOW。
    window: int = walkvideo_mod.DEFAULT_HY15_WINDOW


@app.get("/api/walkvideo/defaults")
async def walkvideo_defaults() -> Dict[str, Any]:
    """行走节点里由程序提供的两段文本，回显给界面。

    负面词和「动作不达标时追加的强调句」以前都硬编码在管线里：用户既看不到
    程序给视频底座喂了什么，也没法把这些措辞改成自己的。默认值仍留在程序里
    （改坏了会退化），这里只负责让它可见、可覆盖。
    """
    return {
        "negative": walkvideo_mod.DEFAULT_WALK_NEGATIVE,
        "emphasisTail": walkvideo_mod.EMPHASIS_TAIL.strip(),
        "prompt": projects_mod.DEFAULT_WALK_PROMPT,
        # 视频引擎：hy15 是默认优先（一致性/细节更好），wan 保留（像素风更保真）
        "engine": "hy15" if _hy15_available() else "wan",
        "engines": [
            {"id": "hy15", "label": "HunyuanVideo-1.5",
             "available": _hy15_available(),
             "note": "默认。不闪烁不变形，默认 21 帧；可加长（61 帧起每步 2.4s→47s，越长越慢）"},
            {"id": "wan", "label": "Wan2.2 TI2V-5B",
             "available": True,
             "note": "像素风更保真，长片可能闪烁/变形"},
        ],
        # 各引擎的帧数上限（4k+1）：hy15 单步耗时随帧数≈线性、上限只受显存限制
        # （~81 帧以上整段顶破 24G，超了用 window 分窗），故放开到 257 由用户决定
        "frameHints": {"hy15": {"default": 21, "max": 257, "align": 4},
                       "wan": {"default": 81, "max": 193, "align": 4}},
    }


def _resolve_video_engine(engine, cfg):
    """视频引擎解析：与管线内部 resolve 一致，用于按引擎设帧数上下限。"""
    from .providers.diffusers_local import resolve_video_engine

    try:
        return resolve_video_engine(engine, cfg or {})
    except Exception:
        return "hy15"


def _hy15_available() -> bool:
    """HunyuanVideo-1.5 权重是否就绪（决定默认引擎与下拉项可用性）。"""
    try:
        from .providers import hy15 as _h

        return _h.available()
    except Exception:
        return False


async def _run_walk_video(task_id: str, payload: WalkVideoPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        src = ThreeViewSource(kind=payload.sourceKind,
                              file=payload.origAssetId or payload.sourceFile or "")
        base = _load_source(payload.projectId, src)
        if base is None:
            raise RuntimeError("参考图不存在：请先跑上游的「生成图」或「选择图片」节点")
        # 脏 alpha 清理：透明底图的背景区 alpha 常是 1-8、RGB 是垃圾值
        # （实测拼板 51.3% 像素是「alpha 1-8 + 品红」），不清则下游丢掉 alpha
        # 时就变成可见的品红/黑底。
        base = alpha_clean.sanitize_alpha(base.convert("RGBA"))
        # 投入的参考图本身是四视图拼板时拆成锚点。用户是**刻意**这么干的：
        # 一张拼板同时提供四个朝向，保证八方向角色一致。不拆的后果实测过——
        # 「正面」方向 passthrough 把整张 4 角色拼板喂给视频底座，出来的是
        # 残影 + 闪烁 + 变形（1024×253 被塞进 384×512，Wan 无从下手）。
        board_cells = threeview_mod.split_view_board(base)

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        _weng = _resolve_video_engine(payload.engine, provider_cfg)

        # 现成视频来源：先把帧读出来（在跑任何模型之前，读不出来立刻报错，
        # 而不是等 Wan 加载完 17GB 显存才说文件坏了）
        allowed = set(walkvideo_mod.directions_of(payload.dirMode))
        src_frames: Dict[str, Dict[str, Any]] = {}
        for s in payload.sources:
            if not s.file or s.dir not in allowed:
                # 换了方向模式后残留的来源不必解码（8 方向 → 仅正面时省掉 4 次解码）
                continue
            frames, fps_src, vmeta = storage.load_video_frames(
                payload.projectId, payload.sourceKind, s.file)
            if not frames:
                raise RuntimeError(
                    f"现成视频读不出来：{s.file}"
                    f"（{vmeta.get('error') or '文件不存在或格式不支持'}）")
            src_frames[s.dir] = {"frames": frames, "fps": fps_src,
                                 "file": s.file}

        w_w = _clamp(payload.width, 128, 1024, 384)
        w_h = _clamp(payload.height, 128, 1024, 512)

        # 四视图锚点：每个方向从离它最近的锚点推导（一致性 + 少推导）
        view_anchors: Dict[str, Any] = {}
        for va in payload.viewAssets:
            if va.role not in ("front", "side", "back") or not va.file:
                continue
            img = storage.load_image(payload.projectId, payload.sourceKind, va.file)
            if img is not None:
                view_anchors[va.role] = img.convert("RGB")
        # 拼板拆分补锚点：格子顺序就是 VIEW_ORDER（front/side/back/side_r），
        # 与 compose_view_board 的排列一致。用 setdefault——上游 compose 节点
        # 显式给的锚点优先，拼板只补缺的角色。
        board_split = 0
        if board_cells:
            for role, cell in zip(threeview_mod.VIEW_ORDER, board_cells):
                view_anchors.setdefault(role, cell.convert("RGB"))
            board_split = len(board_cells)
            progress_mod.report(
                task_id, 0, 1,
                f"参考图是 {board_split} 联拼板，已按四视图锚点处理"
                "（正/侧/背/右零推导，斜向只转 45 度）")
        # 用户直接指定的方向立绘：规整到节点尺寸，跳过推导
        dir_images: Dict[str, Any] = {}
        for di in payload.dirImages:
            if not di.file:
                continue
            img = storage.load_image(payload.projectId, payload.sourceKind, di.file)
            if img is None:
                raise RuntimeError(f"指定立绘读不出来：{di.file}")
            frames = walkvideo_mod.normalize_source_frames(
                [img.convert("RGBA")], width=w_w, height=w_h)
            if frames:
                dir_images[di.dir] = frames[0].convert("RGB")

        directions, meta = await walkvideo_mod.run_walk(
            base,
            dir_mode=payload.dirMode, action=payload.action,
            prompt=(payload.prompt or "").strip() or None,
            steps=(_clamp(payload.steps, 4, 60, 30) if payload.steps else None),
            seed=payload.seed,
            width=w_w, height=w_h,
            num_frames=_clamp(payload.numFrames, 13 if _weng == "hy15" else 5,
                              257 if _weng == "hy15" else 193, 21),
            fps=_clamp(payload.fps, 8, 30, 24),
            max_side=_clamp(payload.maxSide, 256, 1024, 512),
            max_seq_len=(_clamp(payload.maxSeqLen, 256, 1536, 512)
                         if payload.maxSeqLen else None),
            provider_cfg=provider_cfg, task_id=task_id,
            sources=src_frames or None,
            view_anchors=view_anchors or None,
            dir_images=dir_images or None,
            pixel_art=payload.pixelArt,
            guidance_scale=(None if payload.guidanceScale is None
                            else _clamp(payload.guidanceScale, 1.0, 9.0, 5.0)),
            motion_threshold=_clamp(payload.motionThreshold, 1.0, 30.0, 6.0),
            retries=_clamp(payload.retries, 0, 5, 2),
            window=_clamp(payload.window, 0, 257, walkvideo_mod.DEFAULT_HY15_WINDOW),
            negative=(payload.negative or "").strip(),
            engine=payload.engine)

        # 显存账：峰值取各方向最大值（每段视频都单独清零过峰值统计），
        # 收尾值是卸载后的——这两个数能直接回答「这条链到底吃多少显存」
        peaks = [float((d.get("walk") or {}).get("vram", {}).get("peakGB") or 0)
                 for d in directions]
        if board_split:
            meta["boardSplit"] = board_split
        if any(peaks):
            meta["vramPeakGB"] = round(max(peaks), 2)
        after = DiffusersProvider.vram_gb().get("reserved")
        if after is not None:
            meta["vramAfterGB"] = after

        # 每方向存一个动画 GIF（视频中间产物）+ 首帧缩略图由前端 <img> 直出
        out_dirs = []
        images = []
        for d in directions:
            gif = storage.save_gif(payload.projectId, d["frames"],
                                   prefix=f"walk-{d['dir']}", fps=d.get("fps") or payload.fps)
            images.append({"assetId": gif["id"], "url": gif["url"],
                           "width": gif["width"], "height": gif["height"],
                           "walkDir": d["dir"]})
            # 立绘图单独存一份：节点上要能预览、用户要能看清哪一步走形了
            view_info = None
            if d.get("view") is not None:
                vi = storage.save_image(payload.projectId, d["view"].convert("RGBA"),
                                        kind="images", prefix=f"wv-{d['dir']}")
                view_info = {"assetId": vi["id"], "url": vi["url"],
                             "width": vi["width"], "height": vi["height"]}
            out_dirs.append({
                "dir": d["dir"], "label": d["label"], "gif": gif,
                "view": view_info,
                "count": len(d["frames"]), "fps": d.get("fps"),
                "seed": d.get("seed"), "mirrored": d.get("mirrored", False),
                "mirroredFrom": d.get("mirroredFrom"),
                "derive": d.get("derive"), "walk": d.get("walk"),
            })

        task_results[task_id] = {
            "taskId": task_id,
            "images": images,
            "walk": {**meta, "engine": (meta.get("engine") or payload.engine or "auto"),
                     "directions": out_dirs,
                     "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


# ---------------------------------------------------------------- 文本生视频
class TxtVideoPayload(BaseModel):
    """文本视频节点：提示词（+可选参考图）→ 视频。

    和行走视频节点的本质区别：参考图**可选**。行走视频必须有一张角色图
    推导八方向；文本视频没有图也能跑——两个引擎都支持纯文本（hy15 的
    T2V 管线 HunyuanVideo15Pipeline；Wan2.2 TI2V-5B 文/图统一权重）。
    """
    projectId: str
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    origAssetId: str = ""
    # 整段画面/动作提示词：用户完整输入，程序不拼接（与行走节点同一约定）
    prompt: str = ""
    negative: str = ""
    steps: Optional[int] = None
    seed: Optional[int] = None
    width: int = 832
    height: int = 480
    numFrames: int = 21                        # 默认引擎 hy15 帧数（4k+1；上限 257，越长越慢）
    fps: int = 24
    maxSide: int = 832
    maxSeqLen: Optional[int] = None
    pixelArt: bool = False
    guidanceScale: Optional[float] = None
    # 出片后处理（纯 CPU 确定性，见 pipelines/videofx.py）：
    # 插帧 = 光流双向变形 2×（小中幅运动效果好，硬边/大幅运动有伪影，
    # 非 AI 插帧——RIFE 类权重当前网络拉不到）；放大 = 2×（像素图最近邻
    # 整数放大，其余 Lanczos+USM，非 AI 超分）。
    interpolate: bool = False
    upscale: bool = False
    # 输出格式：mp4（默认，H.264 + yuv420p + faststart，浏览器 <video> 原生
    # 播）/ gif（256 色、<img> 播，精灵管线中间产物语境）。MP4 才是「最终
    # 成片」场景的格式——GIF 无帧间压缩，同规格大数倍且有 256 色色带。
    format: str = "mp4"
    # 视频引擎：None=自动。两个引擎都支持纯文本（hy15 有 T2V 管线，Wan 文/图
    # 统一权重）；自动模式默认 hy15，权重不全时回退 wan。
    engine: Optional[str] = None
    # 画布是否跟随参考图宽高比（None=跟随）。方形/竖图立绘塞进 16:9 画布时
    # 角色只占中间一小条，有效像素被砍掉一半——这是「同一张图切 720p 反而更糊」
    # 的直接原因。显式 false 表示用户就是要这个画幅（风景/全景素材）。
    fitAspect: Optional[bool] = None


async def _run_txt_video(task_id: str, payload: TxtVideoPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        prompt = (payload.prompt or "").strip()
        if not prompt:
            raise RuntimeError("请先写提示词：描述画面内容与动作")

        # 参考图可选：连了上游就加载（脏 alpha 清理同行走视频），没连就是 None
        ref = None
        src_file = payload.origAssetId or payload.sourceFile or ""
        if src_file:
            src = ThreeViewSource(kind=payload.sourceKind, file=src_file)
            ref = _load_source(payload.projectId, src)
            if ref is None:
                raise RuntimeError(
                    f"参考图加载失败：{src_file}。文件可能在项目里被删了——"
                    "重新在「选择图片」节点上传一张，或断开这条连线走纯文本。")
            ref = alpha_clean.sanitize_alpha(ref.convert("RGBA"))

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        # 引擎：用户显式选择优先；否则自动——两个引擎现在都支持纯文本
        # （hy15 2026-10-24 起有 T2V 管线；Wan 一直文/图统一），自动模式
        # 默认 hy15（一致性和细节更好），权重不全时 resolve 回退 wan。
        engine = payload.engine
        _teng = _resolve_video_engine(payload.engine, provider_cfg)

        result = await DiffusersProvider(provider_cfg).generate_video(
            prompt,
            ref,
            width=_clamp(payload.width, 128, 1280, 848),
            height=_clamp(payload.height, 128, 1280, 480),
            steps=(_clamp(payload.steps, 4, 60, 30) if payload.steps else None),
            seed=payload.seed,
            num_frames=_clamp(payload.numFrames, 13 if _teng == "hy15" else 5,
                              257 if _teng == "hy15" else 193, 21),
            fps=_clamp(payload.fps, 8, 30, 24),
            max_side=_clamp(payload.maxSide, 256, 1280, 832),
            max_seq_len=(_clamp(payload.maxSeqLen, 256, 1536, 512)
                         if payload.maxSeqLen else None),
            task_id=task_id,
            pixel_art=payload.pixelArt,
            # 留空 = 用全局设置里的默认负面词（与界面文案、图片生成口径一致）。
            # provider 只对 None 回落 negativePrompt；把空串传下去，一路到模型
            # 就是「零负面词」，视频底座很容易出模糊 / 水印 / 文字残影。
            # 想彻底不要负面词：去全局设置里把默认清空。
            negative=(payload.negative or "").strip() or None,
            guidance_scale=(None if payload.guidanceScale is None
                            else _clamp(payload.guidanceScale, 1.0, 9.0, 5.0)),
            # 进度条文案按节点语义给：provider 的默认值会把带参考图的文本视频
            # 叫成「行走视频」（它按有无 ref 猜），这里显式指定。
            progress={"label": "文生视频"},
            engine=engine,
            # 跟随参考图宽高比（默认开）：方形立绘用方形画布，角色才吃满像素。
            fit_aspect=True if payload.fitAspect is None else bool(payload.fitAspect),
        )
        # 出片后处理：插帧/放大都是纯 CPU 确定性操作（光流 + 缩放），
        # 丢线程池别堵事件循环；两关都关时零开销。
        frames = list(result.images)
        fx: Dict[str, Any] = {}
        if payload.interpolate or payload.upscale:
            frames, fx = await asyncio.to_thread(
                videofx_mod.postprocess, frames,
                interpolate=bool(payload.interpolate), upscale=bool(payload.upscale),
                pixel_art=bool(payload.pixelArt))
        fmt = (payload.format or "mp4").lower()
        if fmt not in ("mp4", "gif"):
            raise RuntimeError(f"不支持的输出格式：{fmt}（可选 mp4 / gif）")
        # 插帧不改时长：帧数翻倍，fps 同步翻倍（否则动作变半速慢放）
        fps_out = _output_fps(_clamp(payload.fps, 8, 30, 12), bool(payload.interpolate))
        if fmt == "mp4":
            # H.264 MP4：imageio-ffmpeg 静态 ffmpeg（系统 PATH 没有 ffmpeg，
            # 这正是 GIF 长期当默认的历史原因）。编码也丢线程池——子进程
            # 编码 25 帧约 1-2s，但事件循环不该等任何 IO。
            tmp, enc = await asyncio.to_thread(
                mp4encode_mod.encode_mp4, frames, fps=fps_out)
            try:
                with open(tmp, "rb") as f:
                    saved = storage.save_video_stream(
                        payload.projectId, f, suffix=".mp4", prefix="txtvideo")
            finally:
                tmp.unlink(missing_ok=True)
            # save_video_stream 的键是 id/file/url/bytes，结果卡要 assetId
            # （GIF 分支显式映射过，这里不能直接塞——漏了前端就拿不到资产 id）
            asset = {"assetId": saved["id"], "url": saved["url"],
                     "width": enc["size"][0], "height": enc["size"][1]}
        else:
            gif = storage.save_gif(payload.projectId, frames,
                                   prefix="txtvideo", fps=fps_out)
            asset = {
                "assetId": gif["id"], "url": gif["url"],
                "width": gif["width"], "height": gif["height"],
            }
        meta = dict(result.meta or {})
        after = DiffusersProvider.vram_gb().get("reserved")
        if after is not None:
            meta["vramAfterGB"] = after
        if fx:
            meta["post"] = fx
        # 结果卡徽章显示最终帧数：插帧后是 25 帧，不该显示生成时的 13
        meta["frames"] = len(frames)
        # 成片 fps 也要报真值：时长 = frames / fps，报错就显示成两倍长
        meta["fps"] = fps_out
        meta["format"] = fmt
        task_results[task_id] = {
            "taskId": task_id,
            "images": [asset],
            "video": {**meta, "engine": engine or "auto",
                      "hasRef": ref is not None,
                      "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/txtvideo")
async def txtvideo_create(payload: TxtVideoPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_txt_video(task_id, payload))
    return {"taskId": task_id}


# ---------------------------------------------------------------- 拆件 + 骨骼行走
class PartsPayload(BaseModel):
    """拆件：角色立绘 → 6 个骨骼部件 + 武器。

    混合路线（see-through 出躯干/四肢/道具 + 爆炸图出头），理由见
    backend/app/pipelines/parts.py 与 tools/extract_parts.py 的模块文档。
    """
    projectId: str
    sourceKind: str = "images"
    sourceFile: Optional[str] = None
    resolution: int = 1280
    withHead: bool = True                       # False = 纯 see-through（快 45s，头可能崩）
    seed: int = 7
    force: bool = False


async def _run_parts(task_id: str, payload: PartsPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        if not payload.sourceFile:
            raise RuntimeError("没有输入角色图：请连线或选择一张图")
        # 前端传的是 assetId（无扩展名），load_image 要文件名
        _name = payload.sourceFile
        if not _name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            _name = f"{_name}.png"
        img = storage.load_image(payload.projectId, payload.sourceKind, _name)
        if img is None:
            raise RuntimeError(f"图片读不出来：{_name}")
        result = await parts_mod.extract(
            payload.projectId, img,
            resolution=_clamp(payload.resolution, 768, 1600, 1280),
            with_head=bool(payload.withHead), seed=int(payload.seed),
            task_id=task_id, force=bool(payload.force))
        task_results[task_id] = {"taskId": task_id, "parts": result,
                                 "seconds": round(time.time() - t0, 1)}
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/parts")
async def parts_create(payload: PartsPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备拆件…")
    asyncio.create_task(_run_parts(task_id, payload))
    return {"taskId": task_id}


class RigWalkPayload(BaseModel):
    """骨骼行走：已拆好的部件 → 8 帧循环（纯 CPU、确定性、无闪烁）。"""
    projectId: str
    frames: int = 8
    swing: float = 13.0                         # 腿摆角：12 干净 / 18 动作大但腿根外翻
    arm: float = 12.0
    bob: float = 5.0
    lift: float = 7.0
    hold: bool = False                          # 握持模式：双臂同向小摆，不脱手
    fps: int = 8


async def _run_rigwalk(task_id: str, payload: RigWalkPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        pdir = parts_mod.parts_dir_of(payload.projectId)
        if pdir is None:
            raise RuntimeError("还没有拆件结果：先跑「拆件」")
        progress_mod.report(task_id, 0, 1, "渲染行走帧…")
        frames, meta = await asyncio.to_thread(
            rigwalk_mod.render, pdir,
            frames=_clamp(payload.frames, 4, 24, 8),
            swing=float(payload.swing), arm=float(payload.arm),
            bob=float(payload.bob), lift=float(payload.lift),
            hold=bool(payload.hold))

        # 帧 + 精灵条 + GIF 都存进项目资产
        infos = []
        for i, f in enumerate(frames):
            info = storage.save_image(payload.projectId, f, kind="images",
                                      prefix="rigframe", seq=i)
            infos.append({"assetId": info["id"], "url": info["url"],
                          "width": info["width"], "height": info["height"]})
        W = sum(f.width for f in frames) + 4 * (len(frames) - 1)
        strip = Image.new("RGB", (W, frames[0].height), (40, 44, 52))
        x = 0
        for f in frames:
            strip.paste(f.convert("RGB"), (x, 0), f)
            x += f.width + 4
        strip_info = storage.save_image(payload.projectId, strip, kind="images", prefix="rigstrip")
        gif_info = storage.save_gif(payload.projectId, frames,
                                    fps=_clamp(payload.fps, 4, 24, 8), prefix="rigwalk")
        task_results[task_id] = {
            "taskId": task_id,
            "frames": infos,
            "images": [{"assetId": strip_info["id"], "url": strip_info["url"],
                        "width": strip_info["width"], "height": strip_info["height"]}],
            "rig": {**meta, "gif": gif_info, "strip": strip_info,
                    "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/rigwalk")
async def rigwalk_create(payload: RigWalkPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备渲染…")
    asyncio.create_task(_run_rigwalk(task_id, payload))
    return {"taskId": task_id}


@app.post("/api/walkvideo")
async def walkvideo_create(payload: WalkVideoPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_walk_video(task_id, payload))
    return {"taskId": task_id}


class SequenceDirection(BaseModel):
    """序列帧的一路输入：某个方向的行走视频 GIF。"""
    dir: str
    gif: str = ""                              # images/ 下的文件名


class SequencePayload(BaseModel):
    projectId: str
    directions: List[SequenceDirection] = Field(default_factory=list)
    framesPerDir: int = 8
    fps: int = 8                                # 抽出来的循环按几帧/秒播放
    matte: bool = True
    tolerance: int = 40


async def _run_sequence(task_id: str, payload: SequencePayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        if not payload.directions:
            raise RuntimeError("没有输入方向：请先跑上游的「行走视频」节点")

        src_dirs = []
        for d in payload.directions:
            frames, _durations, gmeta = storage.load_gif(payload.projectId, "images", d.gif)
            if not frames:
                # 把解码错误原文带出来：吞掉异常时坏 GIF 和文件不存在
                # 返回完全一样，用户只看到「不存在」，无从排查
                why = gmeta.get("error") or "文件不存在"
                raise RuntimeError(
                    f"方向 {d.dir} 的视频读不出来（{why}）：{d.gif or '（未指定）'}")
            src_dirs.append({"dir": d.dir, "label": walkvideo_mod.DIR_LABELS.get(d.dir, d.dir),
                             "frames": frames, "fps": payload.fps})

        # 丢线程池：run_sequence 是纯 CPU（8 方向 × N 帧抠图，分钟级），
        # 直接 await 会把事件循环堵死——那期间所有 HTTP 请求停摆，
        # 前端 SSE 收不到进度、点取消没响应、开第二个任务没响应。
        directions, meta = await asyncio.to_thread(
            sequence_mod.run_sequence,
            src_dirs,
            frames_per_dir=_clamp(payload.framesPerDir, 2, 24, 8),
            matte=payload.matte,
            tolerance=_clamp(payload.tolerance, 10, 120, 40),
            task_id=task_id)

        out_dirs = []
        images = []
        for d in directions:
            # 透明帧逐个存档（精灵整图与引擎都能直接用）
            frame_infos = []
            for i, f in enumerate(d["frames"]):
                info = storage.save_image(payload.projectId, f, kind="images",
                                          prefix=f"seq-{d['dir']}", seq=i)
                frame_infos.append(info)
            strip = storage.save_image(payload.projectId, d["strip"], kind="images",
                                       prefix=f"seqstrip-{d['dir']}")
            images.append({"assetId": strip["id"], "url": strip["url"],
                           "width": strip["width"], "height": strip["height"],
                           "walkDir": d["dir"]})
            out_dirs.append({
                "dir": d["dir"], "label": d.get("label"),
                "count": d["count"], "sourceFrames": d.get("sourceFrames"),
                "dropped": d.get("dropped"),
                "frames": [{"assetId": f["id"], "url": f["url"]} for f in frame_infos],
                "strip": strip,
                "bgRemovedAvg": d.get("bgRemovedAvg"),
            })

        task_results[task_id] = {
            "taskId": task_id,
            "images": images,
            "sequence": {**meta, "directions": out_dirs,
                         "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/sequence")
async def sequence_create(payload: SequencePayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_sequence(task_id, payload))
    return {"taskId": task_id}


class SheetDirection(BaseModel):
    """精灵整图的一路输入：某个方向的序列帧文件。"""
    dir: str
    frames: List[str] = Field(default_factory=list)   # images/ 下的文件名


class SheetPayload(BaseModel):
    projectId: str
    directions: List[SheetDirection] = Field(default_factory=list)
    size: int = 128                              # 单元格边长
    cols: int = 0                                # 每行帧数，0 = 一行排完
    sharedScale: bool = True
    anchor: str = "bottom"


async def _run_sheets(task_id: str, payload: SheetPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        if not payload.directions:
            raise RuntimeError("没有输入方向：请先跑上游的「序列帧」节点")

        src_dirs = []
        for d in payload.directions:
            frames = []
            for fn in d.frames:
                img = storage.load_image(payload.projectId, "images", fn)
                if img is not None:
                    frames.append(img)
            if not frames:
                raise RuntimeError(f"方向 {d.dir} 没有序列帧")
            src_dirs.append({"dir": d.dir, "frames": frames})

        directions, meta = await asyncio.to_thread(
            spritesheet_mod.run_sheets,
            src_dirs,
            size=_clamp(payload.size, 16, 512, 128),
            cols=_clamp(payload.cols, 0, 64, 0),
            shared_scale=payload.sharedScale,
            anchor=payload.anchor if payload.anchor in ("bottom", "center", "top") else "bottom",
            task_id=task_id)

        out_dirs = []
        images = []
        for d in directions:
            sheet = storage.save_image(payload.projectId, d["sheet"], kind="images",
                                       prefix=f"sheet-{d['dir']}")
            images.append({"assetId": sheet["id"], "url": sheet["url"],
                           "width": sheet["width"], "height": sheet["height"],
                           "walkDir": d["dir"]})
            out_dirs.append({
                "dir": d["dir"], "label": d.get("label"),
                "sheet": sheet,
                "cols": d["index"]["grid"]["cols"],
                "rows": d["index"]["grid"]["rows"],
                "frameSize": d["index"]["frame_size"],
                "frames": d["index"]["frames"],
                "count": d["count"],
                "warnings": d.get("warnings"),
                "checks": d.get("checks"),
                "normalize": d.get("normalize"),
            })
        ov = spritesheet_mod.overview({d["dir"]: d["sheet"] for d in directions})
        ov_info = storage.save_image(payload.projectId, ov, kind="images", prefix="sheet-all")
        images.insert(0, {"assetId": ov_info["id"], "url": ov_info["url"],
                          "width": ov.width, "height": ov.height})

        task_results[task_id] = {
            "taskId": task_id,
            "images": images,
            "sheet": {**meta, "overview": ov_info, "directions": out_dirs,
                      "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/spritesheet")
async def spritesheet_create(payload: SheetPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_sheets(task_id, payload))
    return {"taskId": task_id}


# --------------------------------------------------------------------------- #
# 视频 / GIF → 序列帧 → 精灵图（通用素材链，不绑方向）
# --------------------------------------------------------------------------- #
class ClipSplitPayload(BaseModel):
    """拆帧入参。file 是项目 images/ 下的视频或 GIF 文件名。"""
    projectId: str
    file: str = ""
    fpsCap: float = 12.0        # 抽稀后的帧率上限（播放节奏）
    maxFrames: int = 120        # 单次最多拆多少帧（成本上限）
    start: float = 0.0          # 起始秒
    end: Optional[float] = None  # 结束秒（None=到结尾）
    matte: bool = True          # 抠背景
    # 抠图底座：auto（默认，按可用权重挑 BiRefNet-lite > ISNet > u2net）/
    # u2net（快但发丝边缘糊）/ birefnet-general-lite（细，CPU 上约 4~5 倍慢）
    matteModel: str = "auto"
    tolerance: int = 40
    # 循环闭合（默认开）：检测动作周期，按**整周期**裁帧，让末帧回绕到首帧正好
    # 是一步。只在真有接缝问题且有明显周期时动手；非循环素材会原样保留并给出说明。
    loopClose: bool = True
    maxSide: int = 384          # 解码时长边上限（精灵单元格一般 64~256）


# 拆帧产物前缀：重复拆同一段视频时按前缀清理，避免 images/ 里越堆越多
CLIP_FRAME_PREFIX = "clipframe"
CLIP_THUMB_PREFIX = "clipthumb"


def _purge_prefix(pid: str, prefix: str) -> int:
    """删掉 images/ 下以 prefix 开头的旧产物（只删文件，不动目录）。"""
    d = storage.assets_dir(pid, "images")
    if not d.is_dir():
        return 0
    n = 0
    for f in d.iterdir():
        if f.is_file() and f.name.startswith(prefix) and f.suffix.lower() == ".png":
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n


async def _run_clip_split(task_id: str, payload: ClipSplitPayload) -> None:
    t0 = time.time()
    try:
        if projects_mod.load_project(payload.projectId) is None:
            raise RuntimeError("项目不存在")
        if not payload.file:
            raise RuntimeError("没有指定视频文件：先在上一步选一段视频或 GIF")
        frames, src_fps, src_meta = storage.load_video_frames(
            payload.projectId, "images", payload.file,
            max_frames=900, max_side=_clamp(payload.maxSide, 64, 1024, 384))
        if not frames:
            why = src_meta.get("error") or "文件不存在或格式不支持"
            raise RuntimeError(f"视频读不出来（{why}）：{payload.file}")

        progress_mod.report(task_id, 0, 1, "拆帧中…")
        picked, meta = await asyncio.to_thread(
            clipframes_mod.run_split,
            frames,
            fps=_clamp(int(src_fps * 1000), 1000, 240000, 24000) / 1000.0,
            fps_cap=max(0.5, min(float(payload.fpsCap), 60.0)),
            max_frames=_clamp(payload.maxFrames, 2, 600, 120),
            start=max(0.0, float(payload.start)),
            end=(None if payload.end is None else float(payload.end)),
            matte=payload.matte,
            tolerance=_clamp(payload.tolerance, 10, 120, 40),
            matte_model=payload.matteModel if payload.matteModel in (
                "auto", "u2net", "birefnet-general-lite", "birefnet-general",
                "isnet-anime") else "auto",
            loop_close=bool(payload.loopClose),
            task_id=task_id)

        # 旧产物先清：只保留本次这一批，前端帧面板不需要历史批次
        _purge_prefix(payload.projectId, CLIP_FRAME_PREFIX)
        _purge_prefix(payload.projectId, CLIP_THUMB_PREFIX)

        out_frames = []
        for f in picked:
            info = storage.save_image(payload.projectId, f["image"], kind="images",
                                      prefix=CLIP_FRAME_PREFIX, seq=f["i"])
            thumb = f["image"].copy()
            thumb.thumbnail((96, 96), Image.LANCZOS)
            tinfo = storage.save_image(payload.projectId, thumb, kind="images",
                                       prefix=CLIP_THUMB_PREFIX, seq=f["i"])
            out_frames.append({
                "i": f["i"], "t": f["t"], "srcIndex": f["srcIndex"],
                "file": info["file"], "url": info["url"],
                "thumbFile": tinfo["file"], "thumbUrl": tinfo["url"],
                "w": info["width"], "h": info["height"],
                "diff": f["diff"], "sharp": f["sharp"], "hash": f["hash"],
            })

        first = out_frames[0] if out_frames else None
        task_results[task_id] = {
            "taskId": task_id,
            "clip": {
                "source": {
                    "file": payload.file,
                    "url": f"/api/projects/{payload.projectId}/images/{payload.file}",
                    "fps": meta.get("srcFps"),
                    "frames": meta.get("sourceFrames"),
                    "width": first["w"] if first else None,
                    "height": first["h"] if first else None,
                    "durationSec": meta.get("durationSec"),
                    "kind": "gif" if payload.file.lower().endswith(".gif") else "video",
                    "preScaled": bool(src_meta.get("preScaled")),
                },
                "frames": out_frames,
                "meta": {**meta, "seconds": round(time.time() - t0, 1)},
            },
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


@app.post("/api/clipsplit")
async def clip_split(payload: ClipSplitPayload) -> Dict[str, Any]:
    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_clip_split(task_id, payload))
    return {"taskId": task_id}


class SpriteGridPayload(BaseModel):
    projectId: str
    files: List[str] = Field(default_factory=list)   # 按选中顺序
    cell: int = 128
    cols: int = 0
    padding: int = 0
    margin: int = 0
    anchor: str = "bottom"
    sharedScale: bool = True
    # 落格方式：shared=统一裁剪窗口+统一缩放（默认，逐帧不重算居中，
    # 衣摆/袖子摆动不会被放大成整幅左右位移）；per-frame=逐帧裁自己 bbox 再居中
    pivot: str = "shared"
    fps: float = 12.0


@app.post("/api/spritegrid")
async def sprite_grid(payload: SpriteGridPayload) -> Dict[str, Any]:
    """选中帧 → 精灵图 + 索引 JSON。

    同步而不是任务制：规整 + 拼板是纯数组操作（120 帧 128px 约 200ms），
    用户点「生成精灵图」要的是立刻看到，走任务制反而多一层轮询。
    单帧文件缺失不致命（跳过并计数），但一帧都没有就报错——静默生成一张
    空图比报错更难排查。
    """
    if projects_mod.load_project(payload.projectId) is None:
        raise HTTPException(404, "项目不存在")
    if not payload.files:
        raise HTTPException(400, "没有选中任何帧")
    if len(payload.files) > 400:
        raise HTTPException(400, "一次最多 400 帧")

    frames = []
    missing = 0
    for fn in payload.files:
        img = storage.load_image(payload.projectId, "images", fn)
        if img is None:
            missing += 1
            continue
        frames.append(img)
    if not frames:
        raise HTTPException(400, "选中的帧都读不出来（可能已被清理）")

    try:
        sheet, index, meta = spritegrid_mod.run_grid(
            frames,
            cell=_clamp(payload.cell, 8, 512, 128),
            cols=_clamp(payload.cols, 0, 64, 0),
            padding=_clamp(payload.padding, 0, 32, 0),
            margin=_clamp(payload.margin, 0, 64, 0),
            anchor=payload.anchor if payload.anchor in spritegrid_mod.ANCHORS else "bottom",
            shared_scale=bool(payload.sharedScale),
            pivot=("per-frame" if payload.pivot in ("per-frame", "perframe")
                   else "shared"),
            fps=max(0.5, min(float(payload.fps), 60.0)),
        )
    except ValueError as e:
        raise HTTPException(400, str(e))

    info = storage.save_image(payload.projectId, sheet, kind="images",
                              prefix="gridsheet")
    index = {**index, "image": info["file"], "url": info["url"]}
    index["textures"][0]["image"] = info["file"]
    idx_path = storage.assets_dir(payload.projectId, "images") / (
        info["file"].rsplit(".", 1)[0] + ".json")
    idx_path.write_text(json.dumps(index, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    return {
        "ok": True,
        "sheet": info,
        "index": index,
        "indexUrl": f"/api/projects/{payload.projectId}/images/{idx_path.name}",
        "indexFile": idx_path.name,
        "meta": {**meta, "missing": missing},
    }


# --------------------------------------------------------------------------- #
# 精灵图 → Godot 4 · 2D 序列动画包
# --------------------------------------------------------------------------- #
class GodotExportPayload(BaseModel):
    projectId: str
    # 精灵图在项目里的文件名（images/<sheetFile>）；参数由前端从精灵图节点带过来
    sheetFile: str
    cell: int = 128
    cols: int = 0
    count: int = 0
    padding: int = 0
    margin: int = 0
    fps: float = 12.0
    animName: str = "default"
    name: str = "walk"
    loop: bool = True
    mode: str = "atlas"          # atlas（一张图集 + AtlasTexture）/ files（逐帧 PNG）
    folder: str = "sprites"      # res:// 下的相对目录
    origin: str = "bottom"       # bottom | center | top：节点原点对齐哪里
    textureFilter: str = "nearest"


@app.post("/api/godot2d")
async def godot2d_export(payload: GodotExportPayload) -> Dict[str, Any]:
    """精灵图 → Godot 4 工程包（.tres + .tscn + zip）。

    同步而不是任务制：只做文本生成 + 一次切图 + 打包，几十毫秒。走任务制
    反而让用户等一次轮询——「点完就拿到下载链接」才是这个节点的意义。
    """
    if projects_mod.load_project(payload.projectId) is None:
        raise HTTPException(404, "项目不存在")
    sheet_path = storage.asset_path(payload.projectId, "images", payload.sheetFile)
    if sheet_path is None:
        raise HTTPException(400, "找不到精灵图文件：先跑一遍「精灵图」节点")
    try:
        return await asyncio.to_thread(
            godot2d_mod.run_export,
            storage.project_dir(payload.projectId),
            sheet_path=sheet_path,
            url_base=f"/api/projects/{payload.projectId}/export",
            cell=_clamp(payload.cell, 8, 512, 128),
            cols=_clamp(payload.cols, 0, 64, 0),
            count=_clamp(payload.count, 0, 400, 0),
            padding=_clamp(payload.padding, 0, 32, 0),
            margin=_clamp(payload.margin, 0, 64, 0),
            fps=max(0.1, min(float(payload.fps), 60.0)),
            anim_name=(payload.animName or "default")[:64],
            stem=payload.name or "walk",
            loop=bool(payload.loop),
            mode=payload.mode,
            folder=payload.folder,
            origin=payload.origin,
            texture_filter=payload.textureFilter,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))


# --------------------------------------------------------------------------- #
# 图片局部重绘：涂抹蒙版 + 提示词 → 只重画这一块
# --------------------------------------------------------------------------- #
class InpaintPayload(BaseModel):
    projectId: str
    sourceKind: str = "images"          # images | refs（上游图在哪个目录）
    sourceFile: str
    maskFile: str = ""                  # masks/ 下的蒙版文件名（笔刷导出）
    instruction: str = ""
    constraint: str = ""                # "" = 用程序里的默认约束条款
    negative: str = ""                  # "" = 用程序里的默认负面词
    refs: List[str] = Field(default_factory=list)   # 可选参考图（最多 2 张）
    mode: str = "crop"                  # crop（只送选区+上下文）/ full（整图重画）
    context: float = 0.35               # crop 模式向外带的上下文比例
    feather: int = 4                    # 羽化像素（只向内）
    expand: int = 8                     # 蒙版外扩像素（给模型留余地）
    hard: bool = False                  # 硬边（像素风）
    matchColor: bool = True             # 接缝带色彩校正
    matchStrength: float = 0.45
    palette: str = "none"               # none | source（把接近源色的杂色吸回色板）
    paletteColors: int = 48
    steps: Optional[int] = None
    seed: Optional[int] = None
    maxSeqLen: Optional[int] = None


class InpaintInstructionPayload(BaseModel):
    instruction: str = ""
    constraint: str = ""
    negative: str = ""
    refCount: int = 0


@app.post("/api/inpaint/instruction")
async def inpaint_instruction(payload: InpaintInstructionPayload) -> Dict[str, Any]:
    """提示词拼装预览：把「我写的 / 程序加的 / 参考图声明」三段摊开给界面看。

    提示词不允许有用户看不见的部分——约束条款是程序加的，就必须显示出来、
    并且可改（`/api/inpaint` 里 constraint 非空时整段替换默认值）。
    """
    info = inpaint_mod.build_prompt(payload.instruction, payload.constraint,
                                    max(0, min(int(payload.refCount), 4)))
    return {
        **info,
        "negative": (payload.negative or "").strip() or inpaint_mod.INPAINT_NEGATIVE,
        "defaultNegative": inpaint_mod.INPAINT_NEGATIVE,
        "chars": len(info["full"]),
        "maxChars": limits_mod.chars_for_tokens(limits_mod.SEQ_LEN_MAX),
    }


@app.post("/api/projects/{pid}/mask")
async def upload_inpaint_mask(pid: str, file: UploadFile = File(...)) -> Dict[str, Any]:
    """上传涂抹蒙版（前端笔刷导出的 PNG，白色=重绘区）。

    单独一个入口而不是塞进 /refs：蒙版是「过程数据」，混进参考图目录会让
    「选择参考图」的列表里冒出几百张黑底白块的图。存进 masks/ 后按 PNG 提供，
    界面可以直接回显上次涂的区域。
    """
    if projects_mod.load_project(pid) is None:
        raise HTTPException(404, "项目不存在")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "蒙版是空的")
    if len(raw) > 16 * 1024 * 1024:
        raise HTTPException(400, "蒙版不能超过 16MB")
    try:
        img = Image.open(io.BytesIO(raw))
        img.load()
    except Exception:
        raise HTTPException(400, "无法识别的蒙版图片")
    mask = inpaint_mod.load_mask(img)
    box = inpaint_mod.mask_bbox(mask)
    if box is None:
        raise HTTPException(400, "蒙版里没有任何涂抹：先涂出一块区域")
    # 存成「白 + alpha」而不是灰阶不透明：alpha 蒙版是通行做法，浏览器能直接
    # 当遮罩用；灰阶不透明 PNG 在任何 alpha 语义的地方都会被当成实心块。
    rgba = Image.merge("RGBA", [Image.new("L", mask.size, 255)] * 3 + [mask])
    info = storage.save_image(pid, rgba, kind="masks", prefix="mask")
    return {
        "ok": True,
        "mask": {
            **info,
            "coverage": inpaint_mod.mask_coverage(mask),
            "box": list(box),
        },
    }


@app.post("/api/inpaint")
async def inpaint(payload: InpaintPayload) -> Dict[str, Any]:
    """局部重绘（任务制）。

    校验全部放在建任务之前：源图/蒙版不存在、蒙版和源图尺寸不一致、提示词为空
    这类错误必须当场 400，让用户看到具体哪里要改；进了任务再报错就只剩
    进度条上一行字。
    """
    if projects_mod.load_project(payload.projectId) is None:
        raise HTTPException(404, "项目不存在")
    if not (payload.instruction or "").strip():
        raise HTTPException(400, "提示词是空的：先写一句要重绘成什么")
    src = _load_source(payload.projectId, ThreeViewSource(
        kind=("refs" if payload.sourceKind == "refs" else "images"),
        file=payload.sourceFile))
    if src is None:
        raise HTTPException(404, "源图不存在：先把上游的图接过来（或重跑上游节点）")
    mask_name = (payload.maskFile or "").strip()
    if not mask_name:
        raise HTTPException(400, "还没有涂抹蒙版：先点「涂抹区域」圈出要重画的地方")
    mask_img = storage.load_image(payload.projectId, "masks", mask_name)
    if mask_img is None:
        raise HTTPException(404, "蒙版文件不存在：重新涂抹一次")
    m = inpaint_mod.load_mask(mask_img)
    if m.size != src.size:
        raise HTTPException(
            400, f"蒙版尺寸 {m.size[0]}×{m.size[1]} 与源图 {src.width}×{src.height} "
                 f"不一致：换图之后要重新涂抹")
    if inpaint_mod.mask_bbox(m) is None:
        raise HTTPException(400, "蒙版是空的：先用笔刷涂出要重画的区域")

    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_inpaint(task_id, payload))
    return {"taskId": task_id}


async def _run_inpaint(task_id: str, payload: InpaintPayload) -> None:
    t0 = time.time()
    try:
        src = _load_source(payload.projectId, ThreeViewSource(
            kind=("refs" if payload.sourceKind == "refs" else "images"),
            file=payload.sourceFile))
        if src is None:
            raise RuntimeError("源图不存在")
        mask_img = storage.load_image(payload.projectId, "masks", payload.maskFile)
        if mask_img is None:
            raise RuntimeError("蒙版文件不存在")

        refs: List[Image.Image] = []
        ref_missing = 0
        for name in (payload.refs or [])[:2]:
            im = _load_source(payload.projectId, ThreeViewSource(
                kind="refs", file=str(name)))
            if im is None:
                ref_missing += 1
                continue
            refs.append(im)

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        out, meta = await inpaint_mod.run_inpaint(
            src, inpaint_mod.load_mask(mask_img),
            instruction=payload.instruction,
            constraint=payload.constraint,
            negative=payload.negative,
            refs=refs,
            mode=payload.mode,
            context=max(0.0, min(float(payload.context), 2.0)),
            feather_px=_clamp(payload.feather, 0, 64, 4),
            expand=_clamp(payload.expand, 0, 64, 8),
            hard=bool(payload.hard),
            match_color=bool(payload.matchColor),
            match_strength=max(0.0, min(float(payload.matchStrength), 1.0)),
            palette="source" if payload.palette == "source" else "none",
            palette_colors=_clamp(payload.paletteColors, 8, 256, 48),
            steps=payload.steps,
            seed=payload.seed,
            max_seq_len=payload.maxSeqLen,
            provider_cfg=provider_cfg,
            task_id=task_id,
        )
        info = storage.save_image(payload.projectId, out, kind="images", prefix="ip")
        if ref_missing:
            meta.setdefault("warnings", []).append(
                f"{ref_missing} 张参考图读不出来（可能已被清理），本次未使用")
        orig_url = (f"/api/projects/{payload.projectId}/"
                    f"{'refs' if payload.sourceKind == 'refs' else 'images'}"
                    f"/{payload.sourceFile}")
        image = {
            "assetId": info["id"], "url": info["url"],
            "width": info["width"], "height": info["height"],
            "origAssetId": payload.sourceFile, "origUrl": orig_url,
        }
        task_results[task_id] = {
            "taskId": task_id,
            "image": image,
            "images": [image],
            "meta": {**meta, "seconds": round(time.time() - t0, 1)},
        }
        progress_mod.report(task_id, 1, 1, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


# --------------------------------------------------------------------------- #
# 去除局部：涂掉水印 / 杂物（三条非 AI 路线 + AI 补全）
# --------------------------------------------------------------------------- #
class ErasePayload(BaseModel):
    projectId: str
    sourceKind: str = "images"
    sourceFile: str
    maskFile: str = ""
    method: str = "telea"               # telea | offset | mirror | ai
    expand: int = 6                     # 有效蒙版外扩（吃掉抗锯齿残影）
    feather: int = 2
    hard: bool = False
    matchColor: bool = True
    matchStrength: float = 0.6
    palette: str = "none"
    paletteColors: int = 48
    # 以下只对 method=ai 有意义
    instruction: str = ""
    constraint: str = ""
    negative: str = ""
    aiMode: str = "crop"
    context: float = 0.35
    steps: Optional[int] = None
    seed: Optional[int] = None
    maxSeqLen: Optional[int] = None


class EraseInstructionPayload(BaseModel):
    instruction: str = ""
    constraint: str = ""


@app.get("/api/erase/methods")
async def erase_methods() -> Dict[str, Any]:
    """四种方式的清单（含「什么时候用」）：方法选择本身是产品知识，交给界面显示。"""
    return {"methods": erase_mod.methods_info()}


@app.post("/api/erase/instruction")
async def erase_instruction(payload: EraseInstructionPayload) -> Dict[str, Any]:
    """AI 方式的提示词拼装预览：默认条款也要能被用户看见、被改。"""
    info = erase_mod.build_prompt(payload.instruction, payload.constraint)
    return {
        **info,
        "negative": erase_mod.DEFAULT_NEGATIVE,
        "chars": len(info["full"]),
        "maxChars": limits_mod.chars_for_tokens(limits_mod.SEQ_LEN_MAX),
    }


@app.post("/api/erase")
async def erase(payload: ErasePayload) -> Dict[str, Any]:
    """去除局部（任务制）。

    非 AI 三条路线本身是毫秒级的，但仍然走任务制：一是和 AI 共用一条前端代码
    路径（都 awaitTask），二是任务制带取消，AI 那条几十秒的能中途停。
    """
    if projects_mod.load_project(payload.projectId) is None:
        raise HTTPException(404, "项目不存在")
    src = _load_source(payload.projectId, ThreeViewSource(
        kind=("refs" if payload.sourceKind == "refs" else "images"),
        file=payload.sourceFile))
    if src is None:
        raise HTTPException(404, "源图不存在：先把上游的图接过来（或重跑上游节点）")
    mask_name = (payload.maskFile or "").strip()
    if not mask_name:
        raise HTTPException(400, "还没有涂抹蒙版：先点「涂抹区域」圈出要擦掉的东西")
    mask_img = storage.load_image(payload.projectId, "masks", mask_name)
    if mask_img is None:
        raise HTTPException(404, "蒙版文件不存在：重新涂抹一次")
    m = inpaint_mod.load_mask(mask_img)
    if m.size != src.size:
        raise HTTPException(
            400, f"蒙版尺寸 {m.size[0]}×{m.size[1]} 与源图 {src.width}×{src.height} "
                 f"不一致：换图之后要重新涂抹")
    if inpaint_mod.mask_bbox(m) is None:
        raise HTTPException(400, "蒙版是空的：先用笔刷涂出要擦掉的东西")

    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 0, "准备中…")
    asyncio.create_task(_run_erase(task_id, payload))
    return {"taskId": task_id}


async def _run_erase(task_id: str, payload: ErasePayload) -> None:
    t0 = time.time()
    try:
        src = _load_source(payload.projectId, ThreeViewSource(
            kind=("refs" if payload.sourceKind == "refs" else "images"),
            file=payload.sourceFile))
        if src is None:
            raise RuntimeError("源图不存在")
        mask_img = storage.load_image(payload.projectId, "masks", payload.maskFile)
        if mask_img is None:
            raise RuntimeError("蒙版文件不存在")

        provider_cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
        out, meta = await erase_mod.run_erase(
            src, inpaint_mod.load_mask(mask_img),
            method=payload.method,
            expand=_clamp(payload.expand, 0, 64, 6),
            feather_px=_clamp(payload.feather, 0, 32, 2),
            hard=bool(payload.hard),
            match_color=bool(payload.matchColor),
            match_strength=max(0.0, min(float(payload.matchStrength), 1.0)),
            palette="source" if payload.palette == "source" else "none",
            palette_colors=_clamp(payload.paletteColors, 8, 256, 48),
            instruction=payload.instruction,
            constraint=payload.constraint,
            negative=payload.negative,
            ai_mode=payload.aiMode,
            context=max(0.0, min(float(payload.context), 2.0)),
            steps=payload.steps, seed=payload.seed,
            max_seq_len=payload.maxSeqLen,
            provider_cfg=provider_cfg, task_id=task_id,
        )
        info = storage.save_image(payload.projectId, out, kind="images", prefix="er")
        orig_url = (f"/api/projects/{payload.projectId}/"
                    f"{'refs' if payload.sourceKind == 'refs' else 'images'}"
                    f"/{payload.sourceFile}")
        image = {
            "assetId": info["id"], "url": info["url"],
            "width": info["width"], "height": info["height"],
            "origAssetId": payload.sourceFile, "origUrl": orig_url,
        }
        task_results[task_id] = {
            "taskId": task_id,
            "image": image,
            "images": [image],
            "meta": {**meta, "seconds": round(time.time() - t0, 2)},
        }
        progress_mod.report(task_id, 2, 2, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


# --------------------------------------------------------------------------- #
# 2.5D 分层模型（See-Through）：静态动漫图 → 可操作的分层 PSD
# --------------------------------------------------------------------------- #
class Layer25DPayload(BaseModel):
    projectId: str
    sourceKind: str = "images"
    sourceFile: str
    # 分辨率：see-through 官方推荐 1280，但一次要 2~3 分钟；
    # 1024 是质量与速度的折中（默认），768 试手用
    resolution: int = 1024
    steps: int = 30
    seed: int = 42
    # 左右拆分：眼睛/耳朵/手饰/头发拆成 l/r 两层（Live2D 的常规做法）
    lrsplit: bool = True


@app.get("/api/layer25d/status")
async def layer25d_status() -> Dict[str, Any]:
    """see-through 能不能用、缺什么。界面直接显示，别让用户跑 3 分钟才报错。"""
    return layer25d_mod.available()


@app.post("/api/layer25d")
async def layer25d_create(payload: Layer25DPayload) -> Dict[str, Any]:
    """静态立绘 → 分层 PSD（任务制：一次 2~3 分钟，必须能看进度）。"""
    if projects_mod.load_project(payload.projectId) is None:
        raise HTTPException(404, "项目不存在")
    env_info = layer25d_mod.available()
    if not env_info["ok"]:
        raise HTTPException(503, "see-through 不可用：" + "；".join(env_info["missing"]))
    src = _load_source(payload.projectId, ThreeViewSource(
        kind=("refs" if payload.sourceKind == "refs" else "images"),
        file=payload.sourceFile))
    if src is None:
        raise HTTPException(404, "源图不存在：先把上游的图接过来（或重跑上游节点）")

    task_id = f"t-{uuid.uuid4().hex[:12]}"
    progress_mod.create(task_id, 100, "准备中…")
    asyncio.create_task(_run_layer25d(task_id, payload))
    return {"taskId": task_id}


async def _run_layer25d(task_id: str, payload: Layer25DPayload) -> None:
    t0 = time.time()
    try:
        src = _load_source(payload.projectId, ThreeViewSource(
            kind=("refs" if payload.sourceKind == "refs" else "images"),
            file=payload.sourceFile))
        if src is None:
            raise RuntimeError("源图不存在")
        from . import storage as _st      # 同包（`..` 会指向 backend，那里没有 storage）
        # 源图落一份到工作区（see-through 要一个真实文件路径）
        work = _st.project_dir(payload.projectId) / "layer25d-work"
        work.mkdir(parents=True, exist_ok=True)
        src_path = work / f"source{Path(payload.sourceFile).suffix or '.png'}"
        src.save(src_path)

        result = await layer25d_mod.run_layer25d(
            payload.projectId, src_path,
            resolution=_clamp(payload.resolution, 512, 2048, 1024),
            steps=_clamp(payload.steps, 4, 60, 30),
            seed=_clamp(payload.seed, 0, 2 ** 31 - 1, 42),
            lrsplit=bool(payload.lrsplit),
            task_id=task_id,
        )
        result["seconds"] = round(time.time() - t0, 1)
        result["sourceFile"] = payload.sourceFile
        task_results[task_id] = {"taskId": task_id, "meta": result}
        progress_mod.report(task_id, 100, 100, "完成")
        progress_mod.finish(task_id)
    except progress_mod.CancelledError:
        progress_mod.finish(task_id, "已取消")
    except Exception as e:
        progress_mod.finish(task_id, str(e)[:600])
        task_results[task_id] = {"taskId": task_id, "error": str(e)[:600]}


# --------------------------------------------------------------------------- #
# 前端静态资源（生产构建存在时才挂；开发期走 Vite）
# --------------------------------------------------------------------------- #
_DIST = ROOT / "frontend" / "dist"


@app.middleware("http")
async def _static_cache_headers(request, call_next):
    """静态资源缓存策略：index.html 禁缓存，带内容 hash 的 assets 长缓存。

    踩过的坑：index.html 没有 Cache-Control 时浏览器会启发式缓存，重新构建后
    仍然加载旧壳 → 旧 JS 不认识新节点类型 → 表现为「功能改了但界面上就是没有」
    （txtvideo 节点就是这么"消失"的）。带 hash 的 assets 反而应该长缓存。
    用中间件而不是 StaticFiles 子类：子类里改 headers 实测拿不到（响应已定型）。
    """
    resp = await call_next(request)
    path = request.url.path
    if path in ("/", "/index.html") or path.endswith(".html"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.startswith("/assets/"):
        resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return resp


if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
