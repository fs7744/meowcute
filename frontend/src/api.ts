import type {
  BackgroundOption, DoctorReport, GenerateResult, GenParams, PixelOptions, Project,
  ProjectFilesInfo, ProjectSummary, PromptExample, PromptPreview, ResultImage, Style,
  TaskProgress, Template, RefAsset,
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

  refine: (payload: { projectId: string; origAssetId: string; pixel: PixelOptions }) =>
    fetch('/api/pixel/refine', jsonInit(payload)).then(j<{
      ok: boolean
      image: ResultImage
      palette: string
    }>),
}

/** 订阅任务 SSE：progress 回调每次进度，done 回调最终结果。返回取消订阅函数。 */
export function subscribeTask(
  taskId: string,
  onProgress: (p: TaskProgress) => void,
  onDone: (result: GenerateResult | null, progress: TaskProgress) => void,
): () => void {
  const es = new EventSource(`/api/tasks/${taskId}/stream`)
  let closed = false
  const close = () => { if (!closed) { closed = true; es.close() } }

  es.onmessage = (ev) => {
    try {
      const data = JSON.parse(ev.data)
      if (data.type === 'done') {
        close()
        onDone((data.result as GenerateResult) ?? null, data as TaskProgress)
      } else {
        onProgress(data as TaskProgress)
      }
    } catch { /* 忽略心跳/坏帧 */ }
  }
  es.onerror = () => {
    // 流断了不代表任务失败：关掉连接，前端会用 /api/tasks/{id} 兜底查询
    close()
  }
  return close
}
