import type {
  BackgroundOption, DoctorReport, GenerateResult, GenParams, MatteOptions, PixelOptions,
  Project, ProjectFilesInfo, ProjectSummary, PromptExample, PromptPreview, ResultImage,
  Style, TaskProgress, Template, RefAsset, VideoAsset,
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

  deleteTemplate: (id: string) =>
    fetch(`/api/templates/custom/${id}`, { method: 'DELETE' }).then(j<{ ok: boolean }>),

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
  }) => fetch('/api/generate/preview', jsonInit(payload)).then(j<PromptPreview>),

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
  }) =>
    fetch('/api/threeview/derive', jsonInit(payload)).then(j<{ taskId: string }>),

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

  /** 八方向 · 行走视频：正视图 → 各方向立绘 + Wan 行走视频（任务制）。
   *  sources 里填了文件名的方向跳过生成，直接用现成视频。 */
  walkVideo: (payload: {
    projectId: string
    origAssetId?: string
    sourceKind?: 'images' | 'refs'; sourceFile?: string
    dirMode?: 'eight' | 'four' | 'single'
    action?: string
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
    pixelArt?: boolean
  }) =>
    fetch('/api/walkvideo', jsonInit(payload)).then(j<{ taskId: string }>),

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
      } else {
        onProgress(data as TaskProgress)
      }
    } catch { /* 忽略心跳/坏帧 */ }
  }
  es.onerror = () => {
    // 流断了不代表任务失败：关掉连接，查一次任务状态兜底。
    // 不做这一步的话，一键工作流会永远等在这个节点上。
    close()
    if (finished) return
    fetch(`/api/tasks/${taskId}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => {
        if (!data) return
        const prog = data.progress || {}
        onDone(data.result ?? null, { ...prog, doneAt: prog.doneAt ?? Date.now() } as TaskProgress)
      })
      .catch(() => undefined)
  }
  return close
}
