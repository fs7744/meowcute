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

生成是「任务制」：POST /api/generate 立即返回 taskId，前端订阅 SSE 看进度，
完成事件里带结果（同时留档 10 分钟供 /api/tasks/{id} 兜底查询）。
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
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
from . import storage
from . import custom_templates as ctpl
from .config import DATA_DIR, ROOT, load_settings, save_settings, scan_models
from .pipelines import matte as matte_mod
from .pipelines import refine as refine_mod
from .providers.diffusers_local import DiffusersProvider
from .styles import (
    BACKGROUND_PRESETS, background_phrase, build_prompt, detect_material,
    get_style, list_styles, resolve_pixel_negative,
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
    custom = [ctpl._public(t) for t in ctpl.list_templates()]
    return {"templates": list_templates() + custom}


@app.get("/api/templates/{tid}")
async def template_detail(tid: str) -> Dict[str, Any]:
    t = get_template(tid)
    if t:
        return t
    ct = ctpl.get_template(tid)
    if not ct:
        raise HTTPException(404, "模板不存在")
    return ctpl._public(ct)


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
    t = get_template(payload.templateId)
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


@app.get("/api/projects/{pid}/{kind}/{filename}")
async def project_asset(pid: str, kind: str, filename: str):
    if kind not in storage.ALLOWED_KINDS:
        raise HTTPException(404, "资产类型不存在")
    path = storage.asset_path(pid, kind, filename)
    if path is None:
        raise HTTPException(404, "资产不存在")
    return FileResponse(str(path), media_type="image/png",
                        headers={"Cache-Control": "public, max-age=31536000"})


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
                             edit: bool = False) -> Dict[str, Any]:
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
    built["negative"] = resolve_pixel_negative(base_neg, use_contract, material)
    built["useContract"] = use_contract
    built["grid"] = grid
    built["background"] = background
    built["material"] = material
    return built


@app.post("/api/generate/preview")
async def preview_prompt(payload: PreviewPayload) -> Dict[str, Any]:
    pixel = payload.pixel or PixelOptions()
    built = _build_generation_prompt(payload.prompt, payload.styleId, pixel,
                                     payload.width, payload.height, payload.background)
    return built


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
                                         edit=bool(refs))

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
            max_seq_len=params.maxSeqLen,
        )

        images = list(result.images)
        meta = dict(result.meta or {})

        # 像素精修只对「像素类画风」生效。写实/卡通/水彩这类画风如果也走
        # 网格对齐 + 调色板量化，渐变和细节会被直接压成色块——这是毁图，不是精修。
        style_def = get_style(payload.styleId)
        pixel_on = bool(pixel.enabled and style_def and style_def.get("pixelClass"))
        meta["pixelOn"] = pixel_on

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
                "seqLen": meta.get("seqLen"),
                "seed": seed,
                "seconds": elapsed,
                "autoRouted": bool(meta.get("autoRouted")),
                "vramWarning": meta.get("vramWarning"),
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


@app.get("/api/tasks/{task_id}")
async def task_state(task_id: str) -> Dict[str, Any]:
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
            # 先推一次当前快照，避免前端错过早期进度
            snap = progress_mod.get(task_id)
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
# 前端静态资源（生产构建存在时才挂；开发期走 Vite）
# --------------------------------------------------------------------------- #
_DIST = ROOT / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="web")
