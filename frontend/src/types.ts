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
  /** 任何模板都可编辑：内置模板的编辑存本地覆盖，代码里的出厂定义不动 */
  editable?: boolean
  /** 内置模板已被用户改过（true 时列表显示「已修改」+ 可恢复默认） */
  overridden?: boolean
  updatedAt?: number
}

/** 提示词长度上限（后端按 token 预算换算，前端只负责展示与限制） */
export type PromptLimits = {
  charsPerToken: number
  overheadTokens: number
  seqLenMax: number
  seqLenOptions: number[]
  styles: Record<string, { plain: number; pixel: number }>
  character: { maxChars: number; note: string }
  walkAction: { maxChars: number; note: string }
  /** 行走视频整段提示词（用户完整输入）的字符上限 */
  walkPrompt: { maxChars: number; note: string }
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
  /** 抠完的透明像素占比（含输入图自带透明区） */
  transparentRatio?: number
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
  /** gray=中灰底 / green=绿幕 / none=不约束 / transparent=原生透明（Qwen-Image-2.1 RGBA） */
  background: 'gray' | 'green' | 'none' | 'transparent'
  // 提示词质量档位映射的文本序列长度：null=自动，512=快速，768=标准，1024=高质量
  maxSeqLen: number | null
  /** 负面词覆盖：空 = 「全局默认 + 像素契约自动追加」那套。
   *  换个画风（非像素）时常要把「抗锯齿/渐变」拿掉，所以放在节点上可改。 */
  negative?: string
}

export type BackgroundOption = {
  id: 'gray' | 'green' | 'none' | 'transparent'
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

/** 视角生成参数（四视图的侧/背/右视图，对应后端 DEFAULT_VIEWGEN）。
 *  view 固定 side | back | side_r——同一个节点组件、同一个接口，靠它区分。 */
export type ViewGenOptions = {
  /** 使用开关：关掉时透传上游正视图，节点仍留在链上 */
  enabled: boolean
  view: 'side' | 'back' | 'side_r'
  /** 以下参数全部继承正视图（生成卡）：画风 / 尺寸 / 步数 / 种子 / 质量 /
   *  背景都在正视图上填一次，侧/背/右自动复用，节点上不再重复填写。
   *  字段保留是为了兼容旧项目数据；执行时以正视图的值为准，
   *  仅当正视图不是生成卡（如选择图片）时才回退到这里的值。 */
  styleId: string | null
  steps: number | null
  seed: number | null
  width: number
  height: number
  maxSeqLen: number | null
  /** 多张候选里当前预览的那张（数组下标）：正视图抽卡 n 张时，本视角
   *  也生成 n 张，卡片里可以逐张看；拼板用哪张在拼板节点上选 */
  pick?: number
  /** 自定义视角指令正文：空 = 用程序拼的默认条款（卡片上可展开查看）。
   *  非空时原样送给编辑底座，背景声明仍由 background 参数追加。 */
  instruction?: string
  /** 自定义负面词：空 = 用程序常量 VIEW_NEGATIVE */
  negative?: string
}

/** 四视图拼板参数（对应后端 DEFAULT_THREEVIEW）。 */
export type ThreeViewOptions = {
  enabled: boolean
  /** 底板色，6 位十六进制（不带 #），默认暗绿 1B4332 */
  bg: string
  /** 透明底板：开时忽略 bg，拼板输出 RGBA（各视图 alpha 原样带出） */
  transparentBg?: boolean
  gap: number
  /** 主体底部对齐到画布高度的比例 */
  baseline: number
  /** 单格尺寸 [宽, 高]，null = 取所有张的最大值 */
  cell: [number, number] | null
  /** 每个视角手动挑的那张（{view: assetId}）：多张候选时逐格选图；
   *  空 = 按 pairIndex 自动取第一组（正视图第 k 张 ↔ 视角第 k 张） */
  picks?: Partial<Record<'front' | 'side' | 'back' | 'side_r', string>>
}

/** 视角生成节点：以「正视图」为参考，用编辑底座指令接力生成侧/背视图。 */
export type ViewGenNode = {
  id: string
  type: 'viewgen'
  x: number
  y: number
  viewgen: ViewGenOptions
  result?: ResultImage | null
  /** 全部候选（每张与正视图的一组按 pairIndex 对齐）：正视图 n 张 →
   *  本视角 n 张，拼板时逐张挑「同一组」才不串角色 */
  images?: ResultImage[]
  createdAt?: number
}

/** 四视图拼板节点：收齐正/侧/背/右四张，按共同基线拼成横向板（缺右视图按三视图拼板）。 */
export type ThreeViewNode = {
  id: string
  type: 'threeview'
  x: number
  y: number
  threeview: ThreeViewOptions
  result?: ResultImage | null
  createdAt?: number
}

/** 拼板指标：每张图的抠图方法、包围盒与对齐偏移。 */
export type ThreeViewMeta = {
  cell: [number, number]
  gap: number
  baseline: number
  /** 底板色：透明底板时为字符串 "transparent" */
  bg: string
  /** true = 输出是 RGBA 透明底板 */
  alpha?: boolean
  size: [number, number]
  views: {
    view: 'front' | 'side' | 'back' | 'side_r'
    matte: string
    bbox: [number, number, number, number] | null
    offset: [number, number]
  }[]
}

// --------------------------------------------------------------------------- #
// 八方向行走（dir_walk 模板链：行走视频 → 序列帧 → 精灵整图 → 可操控预览）
// --------------------------------------------------------------------------- #
/** 方向 id：顺时针一圈，下 → 左下 → 左 → 左上 → 上 → 右上 → 右 → 右下。 */
export type WalkDir =
  | 'down' | 'down-left' | 'left' | 'up-left'
  | 'up' | 'up-right' | 'right' | 'down-right'

/** 行走视频参数（八方向第一步，对应后端 DEFAULT_WALKVIDEO）。 */
export type WalkVideoOptions = {
  /** 使用开关：关掉时透传上游正视图 */
  enabled: boolean
  /** eight=八方向（5 实拍 + 3 镜像）/ four=四方向 / single=仅正面 */
  dirMode: 'eight' | 'four' | 'single'
  /** 整段动作提示词：用户完整输入，程序不拼接。节点上直接改全文；
   *  示例库「行走视频」分类可一键填入完整写法。 */
  prompt?: string
  /** 自定义负面词：空 = 用管线默认（模糊/重影/站桩/单帧定格…）。
   *  默认值由 /api/walkvideo/defaults 回显，卡片上能看能改。 */
  negative?: string
  /** 老项目的短动作词：仅在没有 prompt 时用于一次性迁移，界面上不再展示 */
  action?: string
  /**
   * 现成视频来源：{方向: 项目内视频文件名}。填了的方向跳过「立绘推导 +
   * Wan 生成」，直接用文件里的帧；没填的照常生成。用来换参数重跑下游、
   * 替换不满意的方向、或完全用外部素材。
   */
  sources?: Partial<Record<WalkDir, string>>
  /** 每个方向可指定一张现成立绘图：跳过该方向的推导，但仍会用它跑 Wan。
   *  用来手动修某个方向（比如编辑底座把侧视图画歪了）。 */
  dirImages?: Partial<Record<WalkDir, string>>
  /** 像素保真：像素素材用最近邻缩放，别被视频底座的重采样糊掉 */
  pixelArt?: boolean
  /** 视频引擎：hy15（HunyuanVideo-1.5，默认优先）/ wan（Wan2.2 TI2V-5B）。
   *  hy15 一致性更好（不闪烁不变形）、约 1 分钟/方向，但帧数上限 33；
   *  wan 像素风更保真但长片会闪烁。 */
  engine?: WalkEngine
  styleId: string | null
  steps: number | null
  seed: number | null
  width: number
  height: number
  /** Wan 帧数（后端对齐 4k+1） */
  numFrames: number
  fps: number
  maxSide: number
  maxSeqLen: number | null
  /** 引导强度 CFG：默认 5.0；行走动作偏弱时上调可逼出更大动作幅度 */
  guidanceScale: number | null
  /** 动作幅度合格线（剪影 1-IoU，真实行走约 5~20，定格 <3）；低于此值自动重试 */
  motionThreshold: number
  /** 动作不达标时自动加压重跑的次数（0 = 不重试） */
  retries: number
}

/** 行走视频引擎标识。 */
export type WalkEngine = 'hy15' | 'wan'

/** 项目内可选用的视频资产（GET /api/projects/{id}/videos）。 */
export type VideoAsset = {
  file: string
  url: string
  /** generated=以前生成的行走视频 / upload=用户上传 */
  origin: 'generated' | 'upload'
  /** 生成视频带的方向（walk-down-… 解析得出），上传的没有 */
  dir: WalkDir | null
  bytes: number
  mtime: number
}

/** 序列帧参数（八方向第二步，对应后端 DEFAULT_SEQUENCE）。 */
export type SequenceOptions = {
  enabled: boolean
  /** 每方向抽多少帧 */
  framesPerDir: number
  /** 抽出来的循环按几帧/秒播放 */
  fps: number
  matte: boolean
  tolerance: number
}

/** 精灵整图参数（八方向第三步，对应后端 DEFAULT_SPRITESHEET）。 */
export type SpriteSheetOptions = {
  enabled: boolean
  /** 单元格边长 */
  size: number
  /** 每行帧数，0 = 一行排完 */
  cols: number
  /** 共享缩放：整段循环用同一缩放系数，角色不忽大忽小 */
  sharedScale: boolean
  anchor: 'bottom' | 'center' | 'top'
}

/** 可操控预览参数（八方向第四步，纯前端渲染，对应后端 DEFAULT_PLAY）。 */
export type PlayOptions = {
  enabled: boolean
  fps: number
  zoom: number
}

/** 行走视频的一个方向产物。 */
export type WalkDirection = {
  dir: WalkDir
  label: string
  gif: {
    assetId: string
    url: string
    width: number
    height: number
    frames: number
    fps: number
  }
  /** 这一方向实际用的立绘图（可放大查看），现成视频用首帧 */
  view?: { assetId: string; url: string; width: number; height: number } | null
  count: number
  seed: number | null
  mirrored: boolean
  mirroredFrom: WalkDir | null
  /** existing = 这一段来自现成视频（跳过了推导 + Wan） */
  walk?: {
    mode?: string; file?: string | null; model?: string | null
    /** 相邻帧剪影变化（0~100）：低于 3 说明基本没走 */
    motion?: number
    /** 剪影面积波动百分比：高于 80 说明画面在闪/糊 */
    instability?: number
  }
}

/** 行走视频节点的产出元数据。 */
export type WalkMeta = {
  dirMode: string
  dirModeLabel: string
  action: string
  realDirections: number
  mirroredDirections: number
  /** 用现成视频的方向数（跳过推导 + Wan） */
  existingDirections: number
  /** 单个方向失败不连累整批：这里列出缺席的方向与原因 */
  failures?: { dir: WalkDir; label: string; stage: string; error: string }[]
  /** 一段视频的峰值显存（GB），取各方向最大值 */
  vramPeakGB?: number | null
  /** 出完片并卸载底座后的显存占用（GB） */
  vramAfterGB?: number | null
  /** 用到的四视图锚点（front/side/back/side_r） */
  anchors?: string[]
  /** 投入的参考图是 N 联拼板，已拆成四视图锚点（正/侧/背/右零推导） */
  boardSplit?: number
  /** 每个方向用了哪个锚点、转了多少度 */
  anchorPlan?: Partial<Record<string, { anchor: string; rotation: number }>>
  fps: number
  numFrames: number
  size: [number, number]
  maxSide: number
  seconds: number
  directions: WalkDirection[]
}

/** 序列帧的一个方向产物。 */
export type SequenceDirection = {
  dir: WalkDir
  label: string
  count: number
  sourceFrames: number
  dropped: number
  frames: { assetId: string; url: string }[]
  strip: { assetId: string; url: string; width: number; height: number }
  bgRemovedAvg: number
}

/** 序列帧节点的产出元数据。 */
export type SequenceMeta = {
  framesPerDir: number
  fps: number
  matte: boolean
  tolerance: number
  counts: number[]
  dropped: number
  /** 抠图失败、退回未抠图的帧数（单帧失败不中断整批） */
  matteErrors?: number
  seconds: number
  directions: SequenceDirection[]
}

/** 精灵整图的一个方向产物。 */
export type SheetDirection = {
  dir: WalkDir
  label: string
  sheet: { assetId: string; url: string; width: number; height: number }
  cols: number
  rows: number
  frameSize: { w: number; h: number }
  frames: { i: number; x: number; y: number; w: number; h: number }[]
  count: number
  warnings: string[]
  checks: {
    cellsMatchFrames: boolean
    reason?: string
    mismatchAt?: number
    cells?: number
  }
  normalize?: {
    size: [number, number]
    frames: number
    scaleRange: number[]
    sharedScale: boolean
    anchor: string
  }
}

/** 精灵整图节点的产出元数据。 */
export type SheetMeta = {
  size: number
  sharedScale: boolean
  /** 全方向统一缩放系数（sharedScale 开启时给出） */
  globalScale?: number | null
  anchor: string
  overviewSize: [number, number]
  warnings: string[]
  checksOk: boolean
  seconds: number
  overview: { assetId: string; url: string; width: number; height: number }
  directions: SheetDirection[]
}

/** 可操控预览节点的播放载荷（由上游精灵整图的元数据组装）。 */
export type PlayMeta = {
  fps: number
  zoom: number
  seconds: number
  directions: {
    dir: WalkDir
    label: string
    url: string
    cols: number
    rows: number
    frameSize: { w: number; h: number }
    frames: { i: number; x: number; y: number; w: number; h: number }[]
  }[]
}

/** 行走视频节点：正视图 → 八方向立绘 + Wan 行走视频（每方向一个 GIF）。 */
export type WalkVideoNode = {
  id: string
  type: 'walkvideo'
  x: number
  y: number
  walkvideo: WalkVideoOptions
  /** 主图（第一个方向的 GIF），供缩略图与 outputOf 用 */
  result?: ResultImage | null
  /** 全部产出（按方向顺序的 GIF） */
  images?: ResultImage[]
  meta?: WalkMeta | null
  createdAt?: number
}

/** 序列帧节点：每方向视频 → 抽帧/去重/抠图 → 透明底帧。 */
export type SequenceNode = {
  id: string
  type: 'sequence'
  x: number
  y: number
  sequence: SequenceOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: SequenceMeta | null
  createdAt?: number
}

/** 拆件节点：角色立绘 → 6 个骨骼部件 + 武器。
 *  混合路线：see-through 出躯干/四肢/道具（道具单独成层不会丢），爆炸图只出头
 *  （半写实角色的头部 see-through 会崩）。实测面积稳定性 ±2% vs 纯视频 ±5%。 */
export type PartsOptions = {
  enabled: boolean
  /** see-through 生成分辨率（1280 约 3 分钟） */
  resolution: number
  /** 是否用爆炸图补头（关掉快 45s，但半写实角色的头可能崩） */
  withHead: boolean
  seed: number
}

export type PartsNode = {
  id: string
  type: 'parts'
  x: number
  y: number
  parts: PartsOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: PartsMeta | null
  createdAt?: number
}

export type PartsMeta = {
  source?: string
  seconds?: number
  parts?: Partial<Record<'head' | 'torso' | 'arm_l' | 'arm_r' | 'leg_l' | 'leg_r', ResultImage>>
  weapon?: ResultImage | null
}

/** 骨骼行走节点：已拆好的部件 → 8 帧循环（纯 CPU、确定性、不闪烁）。 */
export type RigWalkOptions = {
  enabled: boolean
  frames: number
  /** 腿摆角：12 干净 / 18 动作大但腿根会外翻 */
  swing: number
  arm: number
  bob: number
  lift: number
  /** 握持模式：双臂同向小摆，双手持枪不脱手 */
  hold: boolean
  fps: number
}

export type RigWalkNode = {
  id: string
  type: 'rigwalk'
  x: number
  y: number
  rigwalk: RigWalkOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: RigWalkMeta | null
  createdAt?: number
}

/** 文本生视频参数（对应后端 TxtVideoPayload）。
 *  与行走视频的本质区别：参考图可选——没有图也能跑（Wan2.2 TI2V-5B
 *  纯文生视频路径），连了上游图就走图生视频。 */
/** 文本生视频的画质档位与分辨率预设（UI 便利层，前端解析成具体数值下发）。 */
export type TxtVideoQuality = 'draft' | 'standard' | 'fine'
export type TxtVideoResolution = '480p' | '720p' | 'custom'

export type TxtVideoOptions = {
  enabled: boolean
  /** 整段画面/动作提示词：用户完整输入，程序不拼接 */
  prompt: string
  negative: string
  /** 画质档位：按引擎映射步数+CFG（hy15 蒸馏 8/12/20 步无 CFG；Wan 20/30/40
   *  步 + CFG 3/5/6）。映射后 steps/guidanceScale 仍可手改，以手改值为准。 */
  quality: TxtVideoQuality
  /** 分辨率预设：480p（hy15 原生 848×480）/ 720p（1216×688，Wan 原生级，
   *  hy15 超训练分辨率会警告）/ custom（宽高自由）。 */
  resolution: TxtVideoResolution
  steps: number | null
  seed: number | null
  width: number
  height: number
  numFrames: number
  fps: number
  maxSide: number
  maxSeqLen: number | null
  /** 像素保真：像素素材用最近邻缩放，别被视频底座糊掉 */
  pixelArt: boolean
  guidanceScale: number | null
  /** 后处理：插帧 2×（OpenCV 光流，非 AI）/ 放大 2×（像素最近邻 / 其余 Lanczos+锐化） */
  interpolate: boolean
  upscale: boolean
  /** 视频引擎：null=自动（默认 hy15，权重不全回退 wan） */
  engine: string | null
}

/** 文本视频节点：提示词 + 可选参考图 → 一段视频（GIF）。 */
export type TxtVideoNode = {
  id: string
  type: 'txtvideo'
  x: number
  y: number
  txtvideo: TxtVideoOptions
  result?: ResultImage | null
  meta?: TxtVideoMeta | null
  createdAt?: number
}

/** 文本视频的 meta：provider 产出的显存/帧数/模式 + 后处理记录 + 任务耗时。 */
export type TxtVideoMeta = {
  steps?: number
  cfg?: number
  family?: string
  /** t2v=纯文本 / i2v=带了参考图 */
  mode?: string
  engine?: string
  frames?: number
  fps?: number
  size?: [number, number]
  vram?: { peakGB?: number; reservedGB?: number; totalGB?: number }
  vramAfterGB?: number
  /** 后处理记录：in/out 帧数 + interpolated/upscaled 标记 */
  post?: { in?: number; out?: number; interpolated?: boolean; upscaled?: boolean }
  hasRef?: boolean
  seconds?: number
}

export type RigWalkMeta = {  frames?: number
  stridePx?: number
  areaMean?: number
  areaStdPct?: number
  loopGap?: number
  motion?: number
  renderSecs?: number
  seconds?: number
  gif?: { id: string; url: string; width: number; height: number } | null
}

/** 精灵整图节点：每方向透明帧 → 共享缩放规整 → 横排精灵表 + 总览。 */
export type SpriteSheetNode = {
  id: string
  type: 'spritesheet'
  x: number
  y: number
  spritesheet: SpriteSheetOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: SheetMeta | null
  createdAt?: number
}

/** 可操控预览节点：八方向精灵表 → 方向键操控的行走播放器（纯前端）。 */
export type PlayNode = {
  id: string
  type: 'play'
  x: number
  y: number
  play: PlayOptions
  result?: ResultImage | null
  meta?: PlayMeta | null
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
  /** 这张图是哪个视角生成的（side / back / side_r） */
  view?: 'side' | 'back' | 'side_r'
  /** 视角生成的附加信息 */
  viewMeta?: {
    view: string
    name: string
    mode: string
    instruction?: string
    steps?: number
    seed?: number
    model?: string
    seconds?: number
  }
  /** 这张图是否是四视图拼板 */
  threeview?: ThreeViewMeta
  /** 这张图属于哪个方向（八方向行走链） */
  walkDir?: WalkDir
  /** 视角接力：对应正视图候选的序号（第 k 张正视图 ↔ 第 k 张视角图） */
  pairIndex?: number
  /** 视角接力：这张图的参考图 assetId（配对核对用） */
  srcAssetId?: string
}

export type ResultMeta = {
  model?: string
  family?: string
  mode?: string
  steps?: number
  seed?: number
  seconds?: number
  vramWarning?: string | null
  /** 选了透明背景但当前底座不支持原生 RGBA 时的提示 */
  bgWarning?: string | null
  /** 这次生成的峰值显存（GB），用于让用户看得见资源消耗 */
  vram?: { peakGB: number; reservedGB: number; totalGB: number } | null
  refCount?: number
  pixelEnabled?: boolean
  palette?: string
  /** 结果图走过的抠图路径（u2net / grabcut / key） */
  matteMethod?: string
  /** 行走视频节点的产出（八方向链） */
  walk?: WalkMeta
  /** 序列帧节点的产出（八方向链） */
  sequence?: SequenceMeta
  /** 拆件节点的产出 */
  parts?: PartsMeta
  /** 骨骼行走节点的产出 */
  rigwalk?: RigWalkMeta
  /** 精灵整图节点的产出（八方向链） */
  sheet?: SheetMeta
  /** 可操控预览节点的播放载荷（八方向链） */
  play?: PlayMeta
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
  | ViewGenNode | ThreeViewNode
  | WalkVideoNode | SequenceNode | SpriteSheetNode | PlayNode
  | PartsNode | RigWalkNode | TxtVideoNode
export type Edge = { from: string; to: string }

export type Project = {
  id: string
  name: string
  templateId: string
  /** 非空 = 这个项目是某个模板的编辑草稿（画布顶栏给「保存为模板」） */
  templateDraftOf?: string | null
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
  templateDraftOf?: string | null
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
  /** 八方向链的任务产出：行走视频 / 序列帧 / 精灵整图的结构化数据 */
  walk?: WalkMeta
  sequence?: SequenceMeta
  sheet?: SheetMeta
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
  /** 负面词的三层来源：全局默认 / 像素契约追加 / 用户覆盖（有 custom 则前两层不生效） */
  negativeParts?: {
    base: string; contract: string; custom: string; overridden: boolean
  }
}
