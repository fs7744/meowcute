import type {
  BackgroundOption, DoctorReport, GenerateResult, GenParams, Layer25DStatus, MatteOptions, NegativePreview,
  PixelOptions, Project, ProjectFilesInfo, ProjectSummary, PromptExample, PromptPreview,
  ResultImage, PromptLimits, Style, TaskProgress, Template, RefAsset, VideoAsset,
} from './types'

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status} ${r.statusText}`
    try {
      const body = await r.json()
      if (typeof body?.detail === 'string') msg = body.detail
      else if (Array.isArray(body?.detail)) msg = body.detail.map((d: any) => d.msg).join('；')
    } catch { /* 非 JSON 错误体 */ }
    throw new Error(msg)
  }
  return r.json() as Promise<T>
}

const jsonInit = (body: unknown): RequestInit => ({
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify(body),
})

export type SettingsPayload = {
  providers: Record<string, any>
  defaults: { steps: number; width: number; height: number; n?: number }
  pixel: Partial<PixelOptions> & { displayScale?: number }
  aiLabel?: boolean
}

/** 长度上限全站共用一份：画布上多张卡、不同节点都读同一口径 */
let limitsCache: Promise<PromptLimits> | null = null
export const loadPromptLimits = (): Promise<PromptLimits> =>
  (limitsCache ??= api.limits().then((r) => r.limits))

export const api = {
  health: () => fetch('/api/health').then(j<{
    ok: boolean; version: string; dataDir: string; modelCount: number; families: string[]
  }>),

  doctor: () => fetch('/api/doctor').then(j<DoctorReport>),

  templates: () => fetch('/api/templates').then(j<{ templates: Template[] }>),

  createTemplate: (payload: {
    name: string; tagline?: string; description?: string
    nodes?: any[]; edges?: any[]; defaults?: Record<string, any>
  }) => fetch('/api/templates/custom', jsonInit(payload))
    .then(j<{ ok: boolean; template: Template }>),

  /** 编辑自定义模板（同样是整体保存：未提交的字段回到建模板时的值） */
  updateTemplate: (id: string, payload: {
    name?: string; tagline?: string; description?: string
    nodes?: any[]; edges?: any[]; graph?: { nodes: any[]; edges: any[] }
    defaults?: Record<string, any>
  }) => fetch(`/api/templates/custom/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(j<{ ok: boolean; template: Template }>),

  /** 编辑内置默认模板：存为本地覆盖补丁，只保存与出厂不同的字段 */
  saveBuiltinTemplate: (id: string, payload: {
    name?: string; tagline?: string; description?: string
    highlights?: string[]; notes?: string[]
    steps?: { title: string; detail: string }[]
    inputs?: any[]; outputs?: any[]
    defaults?: Record<string, any>
    graph?: { nodes: any[]; edges: any[] }
  }) => fetch(`/api/templates/builtin/${id}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  }).then(j<{ ok: boolean; template: Template }>),

  /** 恢复内置模板的出厂默认（删掉覆盖补丁） */
  resetBuiltinTemplate: (id: string) =>
    fetch(`/api/templates/builtin/${id}`, { method: 'DELETE' })
      .then(j<{ ok: boolean; template: Template }>),

  /** 把模板打开成画布草稿（同一个模板只会有一份，重复调用复用） */
  openTemplateDraft: (id: string) =>
    fetch(`/api/templates/${id}/draft`, { method: 'POST' })
      .then(j<{ ok: boolean; project: Project; template: Template; reused: boolean }>),

  /** 把画布草稿写回模板（运行时图片/参考图在后端清掉） */
  commitTemplateDraft: (id: string, projectId: string, keepDraft = false) =>
    fetch(`/api/templates/${id}/commit`, jsonInit({ projectId, keepDraft }))
      .then(j<{ ok: boolean; template: Template; draftDeleted: boolean }>),

  /** 另存为自定义副本（内置模板想大改又不想动默认时用） */
  duplicateTemplate: (id: string) =>
    fetch(`/api/templates/${id}/duplicate`, { method: 'POST' })
      .then(j<{ ok: boolean; template: Template }>),

  deleteTemplate: (id: string) =>
    fetch(`/api/templates/custom/${id}`, { method: 'DELETE' }).then(j<{ ok: boolean }>),

  /** 提示词长度上限：按画风与质量档位换算，用于输入框计数与硬限制 */
  limits: () => fetch('/api/limits').then(j<{ limits: PromptLimits }>),

  styles: () => fetch('/api/styles').then(j<{
    styles: Style[]; backgrounds: BackgroundOption[]
  }>),

  promptExamples: () => fetch('/api/prompt-examples').then(j<{
    categories: string[]; examples: PromptExample[]
  }>),

  upsertExample: (payload: Partial<PromptExample>) =>
    fetch('/api/prompt-examples', jsonInit(payload)).then(j<{ ok: boolean; example: PromptExample }>),

  deleteExample: (id: string) =>
    fetch(`/api/prompt-examples/${id}`, { method: 'DELETE' }).then(j<{ ok: boolean }>),

  resetExamples: () =>
    fetch('/api/prompt-examples/reset', { method: 'POST' }).then(j<{
      ok: boolean; examples: PromptExample[]
    }>),

  settings: () => fetch('/api/settings').then(j<{
    settings: SettingsPayload & { providers: Record<string, any> }
    models: { path: string; family: string; name: string; gb: number }[]
  }>),

  saveSettings: (payload: Partial<SettingsPayload>) =>
    fetch('/api/settings', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(j<{ ok: boolean; settings: any }>),

  testModel: () => fetch('/api/settings/test', { method: 'POST' }).then(j<{ ok: boolean; message: string }>),

  unloadModel: () => fetch('/api/settings/unload', { method: 'POST' }).then(j<{ ok: boolean; message: string }>),

  projects: () => fetch('/api/projects').then(j<{ projects: ProjectSummary[] }>),

  createProject: (name: string, templateId: string) =>
    fetch('/api/projects', jsonInit({ name, templateId })).then(j<{ ok: boolean; project: Project }>),

  getProject: (id: string) => fetch(`/api/projects/${id}`).then(j<{ project: Project }>),

  saveProject: (id: string, payload: Partial<Project>) =>
    fetch(`/api/projects/${id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) })
      .then(j<{ ok: boolean; project: Project }>),

  renameProject: (id: string, name: string) =>
    fetch(`/api/projects/${id}/name`, { method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) })
      .then(j<{ ok: boolean; project: Project }>),

  deleteProject: (id: string) =>
    fetch(`/api/projects/${id}`, { method: 'DELETE' }).then(j<{ ok: boolean }>),

  /** 项目文件夹清单：输入（refs/）与产出（images/）都落在项目目录下。 */
  projectFiles: (id: string) => fetch(`/api/projects/${id}/files`).then(j<ProjectFilesInfo>),

  /** 在系统文件管理器里打开项目文件夹。 */
  revealProject: (id: string) =>
    fetch(`/api/projects/${id}/reveal`, { method: 'POST' })
      .then(j<{ ok: boolean; root: string; message: string }>),

  uploadRef: (pid: string, file: File) => {    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/projects/${pid}/refs`, { method: 'POST', body: fd })
      .then(j<{ ok: boolean; ref: RefAsset }>)
  },

  preview: (payload: {
    prompt: string; styleId: string | null; width: number; height: number
    background: string; pixel: PixelOptions
    /** 生成卡上覆盖的负面词（空 = 全局默认 + 像素契约） */
    negative?: string
  }) => fetch('/api/generate/preview', jsonInit(payload)).then(j<PromptPreview>),

  /** 当前生效的负面词正文（自动态也看得见，用于生成卡直接展示 + 直接改）。 */
  genNegative: (payload: {
    prompt?: string; styleId: string | null; pixel: PixelOptions; negative?: string
  }) => fetch('/api/generate/negative', jsonInit(payload)).then(j<NegativePreview>),

  generate: (payload: {
    projectId: string
    genNodeId: string
    prompt: string
    styleId: string | null
    refs: string[]
    params: GenParams
    pixel: PixelOptions
  }) => fetch('/api/generate', jsonInit(payload)).then(j<{ taskId: string }>),

  cancelTask: (taskId: string) =>
    fetch(`/api/tasks/${taskId}/cancel`, { method: 'POST' }).then(j<{ ok: boolean; message: string }>),

  refine: (payload: {
    projectId: string; origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    pixel: PixelOptions
  }) =>
    fetch('/api/pixel/refine', jsonInit(payload)).then(j<{
      ok: boolean
      image: ResultImage
      palette: string
    }>),

  /** 扣背景：走 u2net → GrabCut → 颜色键，返回透明 PNG 资产。 */
  matte: (payload: {
    projectId: string; origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    matte: MatteOptions
  }) =>
    fetch('/api/matte', jsonInit(payload)).then(j<{ ok: boolean; image: ResultImage }>),

  /** 抠图引擎体检：现在会走哪条路径（u2net 权重缺失时给出提示）。 */
  matteStatus: () => fetch('/api/matte/status').then(j<{
    available: boolean; primary: string; note: string
    u2net: boolean; cv2: boolean; u2netError: string | null
  }>),

  /** 四视图：由正视图接力生成侧/背/右视图（任务制，跑编辑底座）。
   *  background 等参数复用正视图（生成卡）的设置，节点上不重复填写。 */
  threeviewDerive: (payload: {
    projectId: string
    origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    view: 'side' | 'back' | 'side_r'
    styleId?: string | null
    steps?: number | null
    seed?: number | null
    width?: number; height?: number
    maxSeqLen?: number | null
    background?: string
    /** 正视图的角色描述原文：视角指令会把它当「部件清单」，防止细长配件漏画 */
    character?: string
    /** 多张正视图候选：按顺序每张各出一张视角图（抽卡 n 张 → n 张视角图），
     *  返回的每张都带 pairIndex，拼板按组挑图靠它对齐 */
    sources?: { kind: 'images' | 'refs'; file: string }[]
    /** 用户改过的视角指令正文（空 = 用程序里的默认条款） */
    instruction?: string
    /** 用户改过的负面词（空 = 用程序常量） */
    negative?: string
  }) =>
    fetch('/api/threeview/derive', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 行走节点里由程序提供的默认文本（负面词 / 重试强调句 / 默认提示词）。
   *  以前这些都硬编码在管线里看不到，现在回显给卡片展示。 */
  walkDefaults: () =>
    fetch('/api/walkvideo/defaults').then(j<{
      negative: string; emphasisTail: string; prompt: string
    }>),

  /** 四视图：视角提示词的拼装结果（卡片上展示 + 编辑时预填）。
   *  body = 可编辑的指令正文，full = 真正送进模型的整段（含背景声明），
   *  parts = 默认条款的逐段拆解（让用户知道程序加了什么）。 */
  threeviewInstruction: (payload: {
    view: 'side' | 'back' | 'side_r'
    character?: string
    background?: string
    instruction?: string
    negative?: string
  }) =>
    fetch('/api/threeview/instruction', jsonInit(payload)).then(j<{
      view: string
      body: string
      full: string
      defaultBody: string
      custom: boolean
      background: string
      parts: { key: string; label: string; text: string }[]
      negative: string | null
      negativeCustom: boolean
      negativeMaxChars: number
      chars: number
      maxChars: number
    }>),

  /** 四视图：正/侧/背/右拼成一张横向板（确定性后处理，立即返回）。
   *  传 3 张按老三视图拼板，传 4 张为四视图。 */
  threeviewCompose: (payload: {
    projectId: string
    sources: { kind: 'images' | 'refs'; file: string }[]
    bg?: string; gap?: number; baseline?: number
    /** true = 输出 RGBA 透明底板（忽略 bg） */
    transparentBg?: boolean
    cell?: [number, number] | null
  }) =>
    fetch('/api/threeview/compose', jsonInit(payload)).then(j<{ ok: boolean; image: ResultImage }>),

  /** 文本生视频：提示词 + 可选参考图 → 一段视频 GIF（任务制）。
   *  不传 origAssetId/sourceFile 就是纯文本（hy15 T2V / Wan 文/图统一）。 */
  txtvideo: (payload: {
    projectId: string
    origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    prompt: string
    negative?: string
    steps?: number | null
    seed?: number | null
    width?: number; height?: number
    numFrames?: number; fps?: number
    maxSide?: number
    maxSeqLen?: number | null
    pixelArt?: boolean
    guidanceScale?: number | null
    interpolate?: boolean
    upscale?: boolean
    /** 输出格式：mp4（默认）/ gif */
    format?: 'mp4' | 'gif'
    engine?: string | null
    /** 画布跟随参考图宽高比：null=跟随（默认），false=按给定宽高固定 */
    fitAspect?: boolean | null
  }) =>
    fetch('/api/txtvideo', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 八方向 · 行走视频：正视图 → 各方向立绘 + Wan 行走视频（任务制）。
   *  sources 里填了文件名的方向跳过生成，直接用现成视频。 */
  walkVideo: (payload: {
    projectId: string
    origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    dirMode?: 'eight' | 'four' | 'single'
    action?: string
    /** 视频引擎：hy15（HunyuanVideo-1.5，默认）/ wan（Wan2.2） */
    engine?: 'hy15' | 'wan'
    /** hy15 分窗阈值（0=关闭，默认 41）：numFrames 更大时链式分窗，把「随总长度
     *  线性增长」的峰值显存按回单窗口水平，用于整段会 OOM 的长视频；不是提速，
     *  段数越少漂移越小。仅 hy15 生效。 */
    window?: number
    styleId?: string | null
    steps?: number | null
    seed?: number | null
    width?: number; height?: number
    numFrames?: number; fps?: number; maxSide?: number
    maxSeqLen?: number | null
    /** 行走动作控制（轻量闭环） */
    guidanceScale?: number | null
    motionThreshold?: number
    retries?: number
    sources?: { dir: string; file: string }[]
    /** 四视图锚点：有了它们，正/侧/背/右零推导，斜向只转 45 度 */
    viewAssets?: { role: string; file: string }[]
    /** 每个方向直接指定的立绘图（跳过推导，仍会跑 Wan） */
    dirImages?: { dir: string; file: string }[]
    /** 像素保真：像素素材用最近邻缩放 */
        /** 整段动作提示词：用户完整输入，程序不拼接；为空时后端退回 action 模板 */
    prompt?: string
    /** 自定义负面词：空 = 用后端默认常量 */
    negative?: string
pixelArt?: boolean
  }) =>
    fetch('/api/walkvideo', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 拆件：角色立绘 → 6 个骨骼部件 + 武器（see-through 出身体 + 爆炸图出头）。 */
  parts: (payload: {
    projectId: string
    sourceKind?: 'images' | 'refs'
    sourceFile?: string
    resolution?: number
    withHead?: boolean
    seed?: number
    force?: boolean
  }) =>
    fetch('/api/parts', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 骨骼行走：已拆好的部件 → 8 帧循环（纯 CPU、确定性、天然透明底）。 */
  rigwalk: (payload: {
    projectId: string
    frames?: number
    swing?: number
    arm?: number
    bob?: number
    lift?: number
    hold?: boolean
    fps?: number
  }) =>
    fetch('/api/rigwalk', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 八方向 · 行走视频：项目内可选的现成视频（历史生成 + 上传）。 */
  listProjectVideos: (projectId: string) =>
    fetch(`/api/projects/${encodeURIComponent(projectId)}/videos`)
      .then(j<{ videos: VideoAsset[] }>),

  /** 八方向 · 行走视频：上传一段现成视频（mp4/mov/webm/gif…）。 */
  uploadProjectVideo: (projectId: string, file: File) => {
    const fd = new FormData()
    fd.append('file', file)
    return fetch(`/api/projects/${encodeURIComponent(projectId)}/videos/upload`,
      { method: 'POST', body: fd })
      .then(j<{ ok: boolean; video: { id: string; file: string; url: string; bytes: number } }>)
  },

  /** 视频转素材 · 拆帧：一段视频 / GIF → 帧列表 + 指纹（任务制）。 */
  clipSplit: (payload: {
    projectId: string
    file: string
    fpsCap?: number
    maxFrames?: number
    start?: number
    end?: number | null
    matte?: boolean
    tolerance?: number
    /** 抠图底座：auto / u2net / birefnet-general-lite … */
    matteModel?: string
    /** 循环闭合：按整周期裁帧（默认开，后端缺省也是 true） */
    loopClose?: boolean
    maxSide?: number
  }) =>
    fetch('/api/clipsplit', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 精灵图动作复刻第 1 步：精灵图 → 逐帧（任务制，去底较慢）。 */
  spriteSplit: (payload: {
    projectId: string
    file: string
    /** refs=用户上传的精灵图 / images=项目里已有的精灵图产物 */
    kind?: 'refs' | 'images'
    cols?: number
    rows?: number
    cell?: number
    count?: number
    matte?: boolean
    tolerance?: number
  }) =>
    fetch('/api/spritesplit', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 精灵图动作复刻第 2 步：动作帧 + 角色四视图 → 换角色复刻（任务制，逐帧推理）。 */
  poseClone: (payload: {
    projectId: string
    mode?: 'per-frame' | 'whole-sheet'
    files?: string[]
    sheetFile?: string
    sheetKind?: 'refs' | 'images'
    refs?: string[]
    instruction?: string
    negative?: string
    steps?: number
    size?: number
    background?: string
    anchor?: string
    refMode?: string
    seed?: number | null
    maxSeqLen?: number | null
  }) =>
    fetch('/api/poseclone', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 视频转素材 · 精灵图：选中帧 → 网格拼图 + 索引 JSON（同步）。 */
  spriteGrid: (payload: {
    projectId: string
    files: string[]
    cell?: number
    cols?: number
    padding?: number
    margin?: number
    anchor?: string
    sharedScale?: boolean
    /** 落格方式：shared=统一裁剪窗口（默认） / per-frame=逐帧居中 */
    pivot?: 'shared' | 'per-frame'
    fps?: number
  }) =>
    fetch('/api/spritegrid', jsonInit(payload)).then(j<{
      ok: boolean
      sheet: { id: string; file: string; url: string; width: number; height: number }
      index: import('./types').SpriteGridIndex
      indexUrl: string
      indexFile: string
      meta: import('./types').SpriteGridMeta
    }>),

  /** 精灵图 → Godot 4 工程包（.tres + .tscn + zip，同步）。 */
  godotExport: (payload: {
    projectId: string
    sheetFile: string
    cell?: number
    cols?: number
    count?: number
    padding?: number
    margin?: number
    fps?: number
    animName?: string
    name?: string
    loop?: boolean
    mode?: 'atlas' | 'files'
    folder?: string
    origin?: 'bottom' | 'center' | 'top'
    textureFilter?: 'nearest' | 'linear'
  }) =>
    fetch('/api/godot2d', jsonInit(payload)).then(j<{
      ok: boolean
      stem: string
      folder: string
      mode: string
      files: import('./types').Godot2DFile[]
      zip: import('./types').Godot2DFile
      resPaths: { png: string; tres: string; scene: string }
      meta: import('./types').Godot2DMeta
    }>),

  /** 局部重绘：提示词拼装预览（我写的 / 程序加的 / 参考图声明）。 */
  inpaintInstruction: (payload: {
    instruction?: string
    constraint?: string
    negative?: string
    refCount?: number
  }) =>
    fetch('/api/inpaint/instruction', jsonInit(payload)).then(j<{
      body: string
      constraint: string
      defaultConstraint: string
      constraintCustom: boolean
      refClause: string
      refCount: number
      full: string
      negative: string
      defaultNegative: string
      chars: number
      maxChars: number
    }>),

  /** 上传涂抹蒙版（笔刷导出的 PNG，白色=重绘区）。 */
  uploadMask: (pid: string, blob: Blob) => {
    const fd = new FormData()
    fd.append('file', blob, 'mask.png')
    return fetch(`/api/projects/${pid}/mask`, { method: 'POST', body: fd })
      .then((r) => (r.ok ? r.json() : r.json().then((e: any) => Promise.reject(
        new Error(String(e?.detail || `上传蒙版失败（HTTP ${r.status}）`))))))
      .then((d: any) => d as { ok: boolean; mask: import('./types').InpaintMaskInfo })
  },

  /** 2.5D 分层：see-through 运行环境体检（缺 venv / 缺权重时提前说清）。 */
  layer25dStatus: () => fetch('/api/layer25d/status').then(j<Layer25DStatus>),

  /** 2.5D 分层（任务制：一次 2~3 分钟）。 */
  layer25d: (payload: {
    projectId: string
    sourceKind?: 'images' | 'refs'
    sourceFile: string
    resolution?: number
    steps?: number
    seed?: number
    lrsplit?: boolean
  }) => fetch('/api/layer25d', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 去除局部：四种补法及其适用场景（界面直接显示，不在前端硬编码知识）。 */
  eraseMethods: () =>
    fetch('/api/erase/methods').then(j<{ methods: import('./types').EraseMethodInfo[] }>),

  /** 去除局部：AI 方式的提示词拼装预览。 */
  eraseInstruction: (payload: { instruction?: string; constraint?: string }) =>
    fetch('/api/erase/instruction', jsonInit(payload)).then(j<{
      body: string
      constraint: string
      defaultConstraint: string
      constraintCustom: boolean
      refClause: string
      full: string
      negative: string
      chars: number
      maxChars: number
    }>),

  /** 去除局部（任务制：非 AI 毫秒级，AI 几十秒）。 */
  erase: (payload: {
    projectId: string
    sourceKind?: 'images' | 'refs'
    sourceFile: string
    maskFile: string
    method?: import('./types').EraseMethod
    expand?: number
    feather?: number
    hard?: boolean
    matchColor?: boolean
    matchStrength?: number
    palette?: 'none' | 'source'
    paletteColors?: number
    instruction?: string
    constraint?: string
    negative?: string
    aiMode?: 'crop' | 'full'
    context?: number
    steps?: number | null
    seed?: number | null
    maxSeqLen?: number | null
  }) => fetch('/api/erase', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 局部重绘（任务制）。 */
  inpaint: (payload: {
    projectId: string
    sourceKind?: 'images' | 'refs'
    sourceFile: string
    maskFile: string
    instruction: string
    constraint?: string
    negative?: string
    refs?: string[]
    mode?: 'crop' | 'full'
    context?: number
    feather?: number
    expand?: number
    hard?: boolean
    matchColor?: boolean
    matchStrength?: number
    palette?: 'none' | 'source'
    paletteColors?: number
    steps?: number | null
    seed?: number | null
    maxSeqLen?: number | null
  }) =>
    fetch('/api/inpaint', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 八方向 · 序列帧：每方向视频 → 抽帧/去重/抠图（任务制）。 */
  sequence: (payload: {
    projectId: string
    directions: { dir: string; gif: string }[]
    framesPerDir?: number
    fps?: number
    matte?: boolean
    tolerance?: number
  }) =>
    fetch('/api/sequence', jsonInit(payload)).then(j<{ taskId: string }>),

  /** 八方向 · 精灵整图：每方向透明帧 → 共享缩放规整 → 横排精灵表（任务制）。 */
  spriteSheet: (payload: {
    projectId: string
    directions: { dir: string; frames: string[] }[]
    size?: number
    cols?: number
    sharedScale?: boolean
    anchor?: 'bottom' | 'center' | 'top'
  }) =>
    fetch('/api/spritesheet', jsonInit(payload)).then(j<{ taskId: string }>),
}

/** 订阅任务 SSE：progress 回调每次进度，done 回调最终结果。返回取消订阅函数。 */
export function subscribeTask(
  taskId: string,
  onProgress: (p: TaskProgress) => void,
  onDone: (result: GenerateResult | null, progress: TaskProgress) => void,
): () => void {
  const es = new EventSource(`/api/tasks/${taskId}/stream`)
  let closed = false
  let finished = false
  const close = () => { if (!closed) { closed = true; es.close() } }

  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'done') {
        finished = true
        close()
        onDone((data.result as GenerateResult) ?? null, data as TaskProgress)
      } else if (data.doneAt) {
        // 服务端的 progress 快照里已经带 doneAt：任务其实结束了。
        // 毫秒级任务常在建连之前就完成，只发 progress 不发 done——不在这兜住
        // 就会永远「处理中」。等一次微任务再查一次真实状态拿结果。
        finished = true
        close()
        fetch(`/api/tasks/${taskId}`)
          .then((r) => (r.ok ? r.json() : null))
          .then((res) => onDone((res?.result as GenerateResult) ?? null,
                                (res?.progress as TaskProgress) ?? data))
          .catch(() => onDone(null, data as TaskProgress))
      } else {
        onProgress(data as TaskProgress)
      }
    } catch { /* 忽略心跳/坏帧 */ }
  }
  es.onerror = () => {
    // 流断了不代表任务失败：关掉连接，查一次真实状态再决定。
    // 旧写法的坑：无条件把 doneAt 补成 now 并回调 onDone，于是
    //   - 后端还在跑 → result 是 null → 上层 reject 成「任务失败」，图随后还是会出来；
    //   - 用户点取消 → cancelled 且没有 error → 也报成「任务失败」。
    // 现在只在**确实终态**（doneAt / cancelled / error 有值）时才 onDone，
    // 否则把状态当进度继续等，必要时退化成轮询。
    close()
    if (finished) return
    let polls = 0
    const poll = () => {
      fetch(`/api/tasks/${taskId}`)
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data) return
          const prog = (data.progress || {}) as TaskProgress
          const done = prog.doneAt != null || !!prog.cancelled || !!prog.error
          if (!done) {
            if (polls++ < 600) {          // 最多再等 10 分钟（1s 一次）
              onProgress(prog)
              setTimeout(poll, 1000)
            }
            return
          }
          finished = true
          onDone((data.result as GenerateResult) ?? null, prog)
        })
        .catch(() => undefined)
    }
    poll()
  }
  return close
}
