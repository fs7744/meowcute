/** 与后端 project.json / API 对齐的类型。 */

export type Style = {
  id: string
  name: string
  group: string
  tags: string[]
  hint: string
  words: string
  pixelClass: boolean
  pixelize?: { block: number; colors: number; grid?: number } | null
}

export type TemplateInput = { key: string; label: string; required: boolean; hint: string }
export type TemplateStep = { n: number; title: string; detail: string }
export type Template = {
  id: string
  name: string
  tagline: string
  status: 'available' | 'planned'
  accent: string
  icon: string
  description: string
  highlights: string[]
  inputs: TemplateInput[]
  steps: TemplateStep[]
  outputs: { label: string; detail: string }[]
  defaults: {
    styleId?: string
    width?: number
    height?: number
    n?: number
    pixel?: Partial<PixelOptions>
  }
  notes: string[]
  /** 自定义模板标记 + 节点图（内置模板没有） */
  custom?: boolean
  graph?: { nodes: any[]; edges: any[] }
}

export type PromptExample = {
  id: string
  category: string
  title: string
  content: string
  note: string
  builtin?: boolean
}

export type PixelOptions = {
  enabled: boolean
  gridSnap: boolean
  colors: number
  palette: string
  gridSize: [number, number] | null
  targetSize: [number, number] | null
  /** 精修后把纯色背景置为透明 */
  transparentBg?: boolean
}

/** 扣背景参数（对应后端 MatteOptions）。 */
export type MatteOptions = {
  /** 使用开关：关掉时节点原样透传上游图片，节点仍保留在图上 */
  enabled: boolean
  /** auto=u2net→GrabCut→颜色键逐级退化 */
  method: 'auto' | 'u2net' | 'grabcut' | 'key'
  tolerance: number
  /** true=硬边 alpha（像素素材）；false=羽化（照片） */
  hard: boolean
  feather: number
  despill: boolean
  /** 抠完裁到主体包围盒 */
  trim: boolean
  /** 边界偏移：负=内缩（去掉一圈底色残影），正=外扩（补回被啃掉的轮廓） */
  edge: number
  /** 从主体底部切掉的比例（0-0.3），用于去掉 AI 画的接地投影 */
  bottomTrim: number
}

/** 抠图指标：走的是哪条路径、抠掉了多少、有没有可疑 */
export type MatteMeta = {
  method: string
  background: string
  tolerance?: number
  hard?: boolean
  despilledPixels?: number
  holesFilled?: number
  removedRatio?: number
  keptRatio?: number
  residualKeyPixels?: number
  warning?: string
  fallbackReason?: string
  /** 分割结果不可靠时的原因（说明为什么换了条路径） */
  retryReason?: string
  edgeOffset?: number
  specksRemoved?: number
  trim?: { box: [number, number, number, number] }
}


export type GenParams = {
  width: number
  height: number
  n: number
  steps: number | null
  seed: number | null
  background: 'gray' | 'green' | 'none'
  // 提示词质量档位映射的文本序列长度：null=自动，512=快速，768=标准，1024=高质量
  maxSeqLen: number | null
}

export type BackgroundOption = {
  id: 'gray' | 'green' | 'none'
  label: string
  swatch: string
  phrase: string
  hint: string
  default?: boolean
}

export type RefAsset = {
  id: string
  file: string
  url: string
  width: number
  height: number
  name: string
}

export type GenNode = {
  id: string
  type: 'generate'
  x: number
  y: number
  prompt: string
  styleId: string | null
  refs: RefAsset[]
  params: GenParams
  pixel: PixelOptions
  /** 最近一次生成的图片（供下游精修节点取图，不落盘也可以重建） */
  lastImages?: ResultImage[]
  collapsed?: boolean
  createdAt?: number
}

/** 选择图片节点：上传/挑选参考图，向下游输出图片。 */
export type InputNode = {
  id: string
  type: 'input'
  x: number
  y: number
  label?: string
  refs: RefAsset[]
  createdAt?: number
}

/** 像素精修节点：接收上游图片，按参数精修后向下游输出。 */
export type RefineNode = {
  id: string
  type: 'refine'
  x: number
  y: number
  pixel: PixelOptions
  result?: ResultImage | null
  createdAt?: number
}

/** 扣除背景节点：接收上游图片，抠掉底后向下游输出透明 PNG。 */
export type MatteNode = {
  id: string
  type: 'matte'
  x: number
  y: number
  matte: MatteOptions
  result?: ResultImage | null
  createdAt?: number
}

export type ResultImage = {
  assetId: string
  url: string
  width: number
  height: number
  origAssetId: string
  origUrl: string
  origSize?: [number, number]
  refine?: {
    grid: [number, number]
    detected: boolean
    palette: string
    paletteSize: number
    colors: number
    offPalette: number
    alphaBinary: number
    size: [number, number]
  }
  /** 这张图是否经过扣背景节点 */
  matte?: MatteMeta
}

export type ResultMeta = {
  model?: string
  family?: string
  mode?: string
  steps?: number
  seed?: number
  seconds?: number
  autoRouted?: boolean
  vramWarning?: string | null
  refCount?: number
  pixelEnabled?: boolean
  palette?: string
  /** 结果图走过的抠图路径（u2net / grabcut / key） */
  matteMethod?: string
}

export type ResultNode = {
  id: string
  type: 'result'
  x: number
  y: number
  genId: string | null
  images: ResultImage[]
  prompt: string
  negative: string
  meta: ResultMeta
  createdAt?: number
}

export type CanvasNode = GenNode | ResultNode | InputNode | RefineNode | MatteNode
export type Edge = { from: string; to: string }

export type Project = {
  id: string
  name: string
  templateId: string
  createdAt: number
  updatedAt: number
  viewport: { x: number; y: number; z: number }
  nodes: CanvasNode[]
  edges: Edge[]
}

export type ProjectSummary = {
  id: string
  name: string
  templateId: string
  createdAt: number
  updatedAt: number
  nodeCount: number
  resultCount: number
  imageCount: number
  thumbnail: string | null
  sizeMB: number
}

export type DoctorItem = {
  key: string
  label: string
  ok: boolean
  detail: string
  fix: string
  level: 'ok' | 'warn' | 'error'
}

export type DoctorReport = {
  ok: boolean
  canGenerate: boolean
  summary: string
  items: DoctorItem[]
  warnings: number
  errors: number
  gpu: string
  vramGB: number
  freeVRAMGB: number
  models: { path: string; family: string; name: string; gb: number }[]
}

export type TaskProgress = {
  taskId: string
  done: number
  total: number
  label: string
  elapsed: number
  cancelled: boolean
  doneAt: number | null
  error: string | null
}

export type GenerateResult = {
  taskId: string
  images: ResultImage[]
  prompt: string
  negative: string
  breakdown: { label: string; text: string }[]
  meta: ResultMeta
  error?: string
}

export type ProjectFile = {
  path: string
  kind: 'images' | 'refs' | 'other'
  bytes: number
  mtime: number
}

export type ProjectFilesInfo = {
  root: string
  files: ProjectFile[]
  counts: { images?: number; refs?: number; other?: number }
  sizeMB: number
}

export type PromptPreview = {
  prompt: string
  negative: string
  styleWords: string
  contract: string
  breakdown: { label: string; text: string }[]
  useContract: boolean
  grid: [number, number]
}
