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
// 八方向行走（节点链：行走视频 → 序列帧 → 精灵整图 → 可操控预览）
// 模板 dir_walk 已下线（入口让给「视频转素材」链），这四个节点仍可在画布上
// 手动搭——八方向靠视频底座推理，属于「从零造素材」，与「视频转素材」互补。
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
   *  hy15 一致性更好（不闪烁不变形）、约 1 分钟/方向，但帧数上限 257（越长越慢）；
   *  wan 像素风更保真但长片会闪烁。 */
  engine?: WalkEngine
  /** hy15 分窗阈值（0=关闭，默认 41，见 lib/hy15Defaults）：numFrames 更大时切成
   *  多个 ≤window 的链式 I2V 段，把「随总长度线性增长」的峰值显存按回单窗口水平，
   *  让整段会 OOM 的长视频也能出（不是提速）。段数越少角色漂移越小，故默认 41
   *  而非更小的 21；仅 hy15 生效。 */
  window?: number
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
  /** 分辨率预设：480p（Wan 832×480 / hy15 848×480）/ 720p（1280×704，Wan
   *  原生级；hy15 是 480p 蒸馏，超训练分辨率会警告）/ custom（宽高自由）。
   *  取值必须是 32 的倍数——TI2V 的 patchify 要求，否则 diffusers 会静默改小。 */
  resolution: TxtVideoResolution
  /** 画布跟随参考图宽高比：null=跟随（默认）。方形立绘塞进 16:9 画布时角色
   *  只占中间一条，有效像素砍半；要横屏成片时显式关掉。 */
  fitAspect: boolean | null
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
  /** 输出格式：mp4（默认，H.264 + yuv420p，浏览器 video 原生播，最终成片语境）/
   *  gif（256 色，img 播，精灵管线中间产物语境——同规格大数倍） */
  format: 'mp4' | 'gif'
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
  /** 输出格式：mp4 / gif（决定结果卡用 video 还是 img 播） */
  format?: 'mp4' | 'gif'
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
  /** 文本生视频节点的产出（GIF：帧数/引擎/模式/后处理） */
  video?: TxtVideoMeta
  /** 骨骼行走节点的产出 */
  rigwalk?: RigWalkMeta
  /** 精灵整图节点的产出（八方向链） */
  sheet?: SheetMeta
  /** 可操控预览节点的播放载荷（八方向链） */
  play?: PlayMeta
  /** 局部重绘节点的产出（选区覆盖率 / 送模型尺寸 / 提示词全文） */
  inpaint?: InpaintMeta
  /** 去除局部节点的产出（补法 / 走的像素 / 平移量） */
  erase?: EraseMeta
  /** 2.5D 分层节点的产出（层列表 / 深度 / 下载链接） */
  layer25d?: Layer25DMeta
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

// --------------------------------------------------------------------------- #
// 视频转素材（clip_to_sprite 模板链：选择视频 → 拆解帧 → 精灵图 → 动画预览）
// --------------------------------------------------------------------------- #
/** 视频 / GIF 源（第一步，对应后端 _clean_clipsrc）。 */
export type ClipSrcOptions = {
  /** images/ 下的文件名 */
  file: string
  kind: 'video' | 'gif'
  /** 展示信息：时长 / 帧率 / 尺寸（不参与逻辑） */
  durationSec: number
  fps: number
  width: number
  height: number
}

/** 拆帧 + 挑帧参数（第二步，对应后端 _clean_framesplit）。 */
export type FrameSplitOptions = {
  enabled: boolean
  /** 抽稀后的帧率上限（播放节奏） */
  fpsCap: number
  /** 单次最多拆多少帧（成本上限） */
  maxFrames: number
  /** 片段起止（秒）；end=null 表示到结尾 */
  start: number
  end: number | null
  matte: boolean
  tolerance: number
  /** 抠图底座：auto=按可用权重自动挑（BiRefNet-lite > ISNet > u2net）；
   *  u2net=快但发丝边缘糊；birefnet-general-lite=发丝干净但 CPU 上约 4~5 倍慢 */
  matteModel: 'auto' | 'u2net' | 'birefnet-general-lite' | 'birefnet-general' | 'isnet-anime'
  /** 解码时长边上限 */
  maxSide: number
  /** 循环闭合：检测动作周期并按整周期裁帧，让末帧回绕到首帧正好是一步。
   *  只在真有接缝问题且找到明显周期时动手；非循环素材原样保留并给出说明。 */
  loopClose: boolean
  /** 跳帧步长：每 N 帧取 1 帧（1 = 全保留） */
  step: number
  /** 近似度过滤阈值（%）：与上一张保留帧差异低于它就算重复 */
  simPct: number
  /** 用户勾选的帧（按顺序，存 images/ 下的文件名） */
  picks: string[]
}

/** 拆出来的一帧（后端返回，带指纹供前端实时过滤）。 */
export type ClipFrame = {
  i: number
  t: number
  srcIndex?: number
  file: string
  url: string
  thumbFile: string
  thumbUrl: string
  w: number
  h: number
  /** 与前一帧的差异（0~100），后端算好的默认值 */
  diff: number
  /** 锐度：糊帧会明显偏低 */
  sharp: number
  /** 16×16 灰度指纹（base64）：前端算相似度用 */
  hash: string
  /** 动作复刻专用：与源帧的剪影重合度（0~1，越高说明动作保真越好）。
   *  只有 poseclone 产出的帧有，拆帧/拆精灵图的帧是 undefined。 */
  iou?: number | null
  /** 复刻时整体平移了多少像素才对回源帧落脚点 */
  dx?: number | null
  dy?: number | null
  /** 该帧推理耗时（秒） */
  seconds?: number | null
}

export type ClipSource = {
  file: string
  url: string
  fps: number
  frames: number
  width: number | null
  height: number | null
  durationSec: number
  kind: 'video' | 'gif'
  preScaled?: boolean
}

/** 拆帧节点的产出元数据（对应后端 _run_clip_split 的 meta）。 */
export type FrameSplitMeta = {
  sourceFrames: number
  rangeFrames: number
  step: number
  picked: number
  thinned: boolean
  srcFps: number
  effFps: number
  durationSec: number
  start: number
  end: number
  fpsCap: number
  maxFrames: number
  matte: boolean
  tolerance: number
  count: number
  matteErrors: number
  /** 帧间差异的中位数 / 最大值：滑杆默认值照它调 */
  diffMedian: number
  diffMax: number
  seconds: number
  /** 循环闭合的体检结论（后端 plan_loop 产出）。
   *  action：trimmed=已按整周期裁 / alreadyClosed=本来就闭合 /
   *  noPeriod=这段不是循环素材（接缝会跳）/ cannotTrim / noGain / tooShort / off */
  loop?: {
    action: string
    period?: number
    cycles?: number
    countBefore?: number
    countAfter?: number
    dropped?: number
    /** 首尾接缝 / 段内相邻差异（%），与 diffMedian 同一口径 */
    seam?: number
    adjacent?: number
    ratio?: number
    seamAfter?: number
    ratioAfter?: number
    confidence?: number
  }
}

export type ClipSrcNode = {
  id: string
  type: 'clipsrc'
  x: number
  y: number
  clipsrc: ClipSrcOptions
  /** 这个节点本身不产图（只是素材入口）；留着字段是为了让「取上游图」的
   *  通用逻辑能安全地把它当作「没有图」处理，而不是类型上被排除。 */
  result?: ResultImage | null
  createdAt?: number
}

export type FrameSplitNode = {
  id: string
  type: 'framesplit'
  x: number
  y: number
  framesplit: FrameSplitOptions
  /** 拆出来的全部帧（带指纹） */
  frames?: ClipFrame[]
  /** 源片段信息（预览播放用） */
  source?: ClipSource | null
  result?: ResultImage | null
  meta?: FrameSplitMeta | null
  createdAt?: number
}

/** 精灵图拆解参数（对应后端 _clean_spritesplit）。 */
export type SpriteSplitOptions = {
  enabled: boolean
  /** 列数 / 行数 / 单元格边长：0 = 自动识别网格 */
  cols: number
  rows: number
  cell: number
  /** 只取前 N 格（0 = 全部） */
  count: number
  /** 不透明底精灵图需要逐帧去底 */
  matte: boolean
  tolerance: number
  /** 勾选了哪些帧（文件名），与 framesplit.picks 同语义 */
  picks: string[]
}

export type SpriteSplitMeta = {
  cols: number
  rows: number
  cellW: number
  cellH: number
  count: number
  /** auto=网格探测 / manual=用户指定 / cell=按边长 / single=看起来只有一帧 / fallback */
  gridMethod: string
  sourceSize: [number, number]
  matte: boolean
  bgHolesPunched: number
  diffMedian: number
  diffMax: number
  seconds: number
  truncated?: number
  warnings: string[]
}

export type SpriteSplitNode = {
  id: string
  type: 'spritesplit'
  x: number
  y: number
  spritesplit: SpriteSplitOptions
  frames?: ClipFrame[]
  source?: { file: string; url: string; width: number; height: number } | null
  result?: ResultImage | null
  meta?: SpriteSplitMeta | null
  createdAt?: number
}

/** 动作复刻参数（对应后端 _clean_poseclone）。 */
export type PoseCloneOptions = {
  enabled: boolean
  /** per-frame=逐帧复刻（默认，可控）；whole-sheet=整张精灵图一次改（草稿预览） */
  mode: 'per-frame' | 'whole-sheet'
  /** 空 = 用程序里的默认指令；非空 = 用户在节点上编辑过的正文 */
  instruction: string
  negative: string
  /** 0 = 默认 40 步 */
  steps: number
  /** 0 = 跟随源帧尺寸 */
  size: number
  background: 'transparent' | 'white' | 'gray' | 'black'
  /** 对齐锚点：bottom=脚底（推荐）/ center=包围盒中心 */
  anchor: 'bottom' | 'center'
  /** auto=长条四视图板自动重排成方形（提升参考图有效像素） */
  refMode: 'auto' | 'as-is'
  seed: number | null
  maxSeqLen: number | null
}

export type PoseCloneMeta = {
  count: number
  failed: number
  steps: number
  size: [number, number] | null
  anchor: string
  background: string
  /** 与源帧的剪影重合度（越高说明动作保真越好） */
  iouMean: number | null
  iouMin: number | null
  seconds: number
  refCount: number
  /** 参考图版面重排说明，如 "4x1→2x2" */
  refPack?: string
  instruction: string
  negative: string
  warnings: string[]
  /** 复刻结果是否与网格同构（whole-sheet 模式） */
  gridKept?: boolean
  grid?: { cols: number; rows: number; cellW: number; cellH: number }
  /** 有多少帧因为第一次跑飞而自动重跑过 */
  retried?: number
  /** 有多少帧背景没透明、由程序自动抠掉 */
  autoMatted?: number
  // ---- 结构校验三项（只看剪影重合度发现不了的问题）----
  /** 有多少帧角色贴到画布边 = 被裁掉了边缘（裁剪会在精灵图里变成切平的头顶/手脚） */
  clipped?: number
  /** 有多少帧四角不透明 = 背景没抠干净（拼图后会成实底方块） */
  cornersDirty?: number
  /** 各帧底边所在行。长度 >1 = 脚底线不齐，循环播放会轻微上下跳 */
  baselineRows?: number[]
  baselineStable?: boolean
}

export type PoseCloneNode = {
  id: string
  type: 'poseclone'
  x: number
  y: number
  poseclone: PoseCloneOptions
  frames?: ClipFrame[]
  result?: ResultImage | null
  meta?: PoseCloneMeta | null
  createdAt?: number
}


/** 精灵图参数（第三步，对应后端 _clean_spritegrid）。 */
export type SpriteGridOptions = {
  enabled: boolean
  /** 单元格边长 */
  cell: number
  /** 每行帧数，0 = 一行排完 */
  cols: number
  /** 相邻单元格之间的透明间隔（Unity 自动切片靠它认边界） */
  padding: number
  /** 整图四周外边距 */
  margin: number
  anchor: 'bottom' | 'center' | 'top'
  /** 共享缩放：整段循环用同一系数，角色不忽大忽小 */
  sharedScale: boolean
  /** 落格方式：shared = 所有帧同一个裁剪窗口与缩放（默认，逐帧不重算居中，
   *  衣摆/袖子摆动不会被放大成整幅左右位移）；per-frame = 逐帧裁自己 bbox 再
   *  居中，会把小幅摆动放大成明显抖动（只在素材各帧完全静止时用）。 */
  pivot: 'shared' | 'per-frame'
  /** 动画帧率（写进索引 JSON，动画预览默认取它） */
  fps: number
}

/** 精灵图节点的产出元数据。 */
export type SpriteGridMeta = {
  cell: number
  cols: number
  rows: number
  count: number
  padding: number
  margin: number
  anchor: string
  sharedScale: boolean
  scaleRange?: number[]
  /** 落格方式（shared / per-frame） */
  pivotMode?: string
  /** 落格时的实际缩放：<1 表示内容被缩小（细节有损） */
  scale?: number | null
  /** 缩小用面积平均（box）、放大用最近邻（nearest） */
  resample?: string | null
  /** 统一裁剪窗口（源坐标系）；只有 shared 模式有 */
  cropBox?: number[] | null
  place?: number[] | null
  sheetSize: [number, number]
  checks: { cellsMatchFrames: boolean; reason?: string; mismatchAt?: number; cells?: number }
  warnings: string[]
  fps: number
  pivot: { x: number; y: number }
  missing?: number
}

/** 精灵图索引 JSON（自带帧表 + Phaser 3 兼容段）。 */
export type SpriteGridIndex = {
  version: string
  image: string
  url?: string
  sheetSize: { w: number; h: number }
  frameSize: { w: number; h: number }
  grid: { cols: number; rows: number; count: number; padding: number; margin: number }
  anchor: string
  pivot: { x: number; y: number }
  fps: number
  frames: { i: number; x: number; y: number; w: number; h: number }[]
  textures?: {
    image: string
    format: string
    size: { w: number; h: number }
    scale: number
    frames: {
      filename: string
      frame: { x: number; y: number; w: number; h: number }
      rotated: boolean
      trimmed: boolean
      spriteSourceSize: { x: number; y: number; w: number; h: number }
      sourceSize: { w: number; h: number }
      pivot: { x: number; y: number }
    }[]
  }[]
}

export type SpriteGridNode = {
  id: string
  type: 'spritegrid'
  x: number
  y: number
  spritegrid: SpriteGridOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: SpriteGridMeta | null
  /** 索引 JSON 的 URL（结果卡上提供下载） */
  indexUrl?: string | null
  createdAt?: number
}

/** 动画预览参数（第四步，纯前端渲染，对应后端 _clean_animpreview）。 */
export type AnimPreviewOptions = {
  enabled: boolean
  fps: number
  zoom: number
  loop: boolean
  bg: 'checker' | 'dark' | 'light' | 'green'
}

/** 动画预览节点的播放载荷（由上游精灵图的元数据组装）。 */
export type AnimPreviewMeta = {
  url: string
  cols: number
  rows: number
  count: number
  frameSize: { w: number; h: number }
  frames: { i: number; x: number; y: number; w: number; h: number }[]
  fps: number
  pivot?: { x: number; y: number }
  indexUrl?: string | null
}

export type AnimPreviewNode = {
  id: string
  type: 'animpreview'
  x: number
  y: number
  animpreview: AnimPreviewOptions
  /** 纯前端播放器，不产图；同 ClipSrcNode，保留字段让通用逻辑安全处理 */
  result?: ResultImage | null
  meta?: AnimPreviewMeta | null
  createdAt?: number
}

/** Godot 导出参数（第五步，对应后端 _clean_godot2d）。 */
export type Godot2DOptions = {
  enabled: boolean
  /** Godot 里的动画名（SpriteFrames 的 animation 名，可中文） */
  animName: string
  /** 文件名主干（ASCII）：tres / tscn / png 都用它 */
  name: string
  fps: number
  loop: boolean
  /** atlas = 一张图集 + AtlasTexture 切格 / files = 逐帧 PNG */
  mode: 'atlas' | 'files'
  /** res:// 下的相对目录：导出物必须整体落在这里 */
  folder: string
  /** 节点原点对齐：脚底 / 居中 / 顶部 */
  origin: 'bottom' | 'center' | 'top'
  textureFilter: 'nearest' | 'linear'
}

/** 导出包里的一个文件。 */
export type Godot2DFile = { name: string; bytes: number; url: string }

/** Godot 导出节点的产物元数据（存盘，刷新后仍能下载上次的包）。
 *  后端响应是 { files, zip, resPaths, meta }，落盘时合成一个对象，
 *  节点自己就是一份完整清单——不用再去关联网关。 */
export type Godot2DMeta = {
  /** 文件名主干（后端清洗后的实际值） */
  stem: string
  folder: string
  resPaths: { png: string; tres: string; scene: string }
  files: Godot2DFile[]
  zip: Godot2DFile
  animName: string
  count: number
  fps: number
  loop: boolean
  cell: number
  cols: number
  rows: number
  padding: number
  margin: number
  sheetSize: [number, number]
  mode: 'atlas' | 'files'
  origin: string
  textureFilter: string
  /** 原点对齐导致的 offset.y（脚底 = -cell/2） */
  originOffsetY: number
  checks: {
    pathsMatch: boolean
    missing: string[]
    refsChecked: number
    framesInResource: number
    framesMatch: boolean
  }
  warnings: string[]
  seconds: number
}

export type Godot2DNode = {
  id: string
  type: 'godot2d'
  x: number
  y: number
  godot2d: Godot2DOptions
  /** 上游精灵图的产出信息 + 导出文件清单 */
  meta?: Godot2DMeta | null
  result?: ResultImage | null
  createdAt?: number
}

/** 涂抹蒙版的落盘信息（masks/<mask-*.png>）。 */
export type InpaintMaskInfo = {
  file: string
  url: string
  width: number
  height: number
  /** 涂抹面积占整图的比例（0~1） */
  coverage: number
  /** 涂抹包围盒 [x0, y0, x1, y1] */
  box: number[]
  id?: string
}

/** 局部重绘参数（对应后端 _clean_inpaint）。 */
export type InpaintOptions = {
  enabled: boolean
  /** 这块要变成什么（必填） */
  instruction: string
  /** "" = 用程序默认约束条款；非空整段替换 */
  constraint: string
  /** "" = 用程序默认负面词 */
  negative: string
  /** 可选参考图（0-2 张，来自项目 refs/） */
  refs: RefAsset[]
  /** crop = 只送选区+上下文 / full = 整图重画 */
  mode: 'crop' | 'full'
  /** 选区外扩的上下文比例 */
  context: number
  /** 羽化像素（只向内，保证选区外不动） */
  feather: number
  /** 蒙版外扩像素 */
  expand: number
  /** 硬边（像素风：不羽化、不做色彩校正） */
  hard: boolean
  matchColor: boolean
  matchStrength: number
  /** none | source：把接近源色的杂色吸回源图色板 */
  palette: 'none' | 'source'
  paletteColors: number
  steps: number | null
  seed: number | null
  maxSeqLen: number | null
  maskFile: string
  mask: InpaintMaskInfo | null
}

/** 局部重绘的产出元数据。 */
export type InpaintMeta = {
  mode: 'crop' | 'full'
  box: number[]
  cropSize: [number, number]
  targetSize: [number, number]
  coverage: number
  context: number
  expand: number
  feather: number
  hard: boolean
  matchColor: boolean
  matchStrength: number
  palette: string
  paletteColors: number
  paletteSnapped: number
  prompt: string
  promptBody: string
  constraint: string
  constraintCustom: boolean
  refCount: number
  negative: string
  seqLen: number
  steps?: number | null
  seed?: number | null
  model?: string
  seconds: number
  warnings: string[]
  vram?: ResultMeta['vram']
}

export type InpaintNode = {
  id: string
  type: 'inpaint'
  x: number
  y: number
  inpaint: InpaintOptions
  /** 最近一次重绘结果（下游结果卡取它） */
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: InpaintMeta | null
  createdAt?: number
}

/** 去除局部的补法。 */
export type EraseMethod = 'telea' | 'offset' | 'mirror' | 'ai'

/** 后端 /api/erase/methods 返回的一种补法（含适用场景）。 */
export type EraseMethodInfo = {
  id: EraseMethod
  label: string
  hint: string
  needsModel: boolean
}

/** 去除局部参数（对应后端 _clean_erase）。 */
export type EraseOptions = {
  enabled: boolean
  method: EraseMethod
  /** 有效蒙版外扩：吃掉水印边缘的抗锯齿残影 */
  expand: number
  feather: number
  hard: boolean
  matchColor: boolean
  matchStrength: number
  palette: 'none' | 'source'
  paletteColors: number
  /** 以下只对 method=ai 有意义 */
  instruction: string
  constraint: string
  negative: string
  aiMode: 'crop' | 'full'
  context: number
  steps: number | null
  seed: number | null
  maxSeqLen: number | null
  maskFile: string
  mask: InpaintMaskInfo | null
}

/** 去除局部的产出元数据。 */
export type EraseMeta = {
  method: EraseMethod
  methodLabel: string
  engine?: string
  expand: number
  feather: number
  hard: boolean
  matchColor: boolean
  matchStrength: number
  palette: string
  paletteColors: number
  paletteSnapped: number
  holePixels: number
  coverage: number
  /** offset 方式匹配到的平移量 */
  offset?: number[] | null
  score?: number
  diffuseFilled?: number
  reason?: string
  ai?: {
    mode?: string
    targetSize?: [number, number]
    cropSize?: [number, number]
    steps?: number | null
    model?: string
    seqLen?: number
    prompt?: string
  } | null
  prompt?: string
  constraint?: string
  constraintCustom?: boolean
  negative?: string
  warnings: string[]
  seconds: number
}

export type EraseNode = {
  id: string
  type: 'erase'
  x: number
  y: number
  erase: EraseOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: EraseMeta | null
  createdAt?: number
}

/** 2.5D 分层参数（对应后端 _clean_layer25d）。 */
export type Layer25DOptions = {
  enabled: boolean
  /** see-through 工作分辨率（512~2048）：越大越慢，官方推荐 1280 */
  resolution: number
  steps: number
  seed: number
  /** 左右拆分：眼/耳/手饰/头发拆 l / r 两层 */
  lrsplit: boolean
}

/** 一层（Live2D 语义层：face / topwear / handwear-l …）。 */
export type Layer25DLayer = {
  name: string
  file: string
  url: string
  width: number
  height: number
  /** 该层裁边后左上角在画布上的坐标（CSS 像素 = 图像像素） */
  x: number
  y: number
  box: [number, number, number, number]
  /** 深度中位数：0 = 最前（最上层），1 = 最后（最底层） */
  depth: number | null
  /** 绘制顺序：0 = 最前 */
  order: number
  /** 摆动幅度建议（0~1）：头发/五官大，身体大块面小 */
  swing: number
}

/** 2.5D 分层的产出。 */
export type Layer25DMeta = {
  psdUrl: string
  psdJsonUrl: string
  depthPsdUrl: string | null
  lrsplitPsdUrl: string | null
  manifestUrl: string
  frameSize: [number, number]
  resolution: number
  steps: number
  seed: number
  seconds: number
  layers: Layer25DLayer[]
  bytes?: number
  sourceFile?: string
}

/** see-through 运行环境体检结果。 */
export type Layer25DStatus = {
  ok: boolean
  dir: string
  python: string
  missing: string[]
}

export type Layer25DNode = {
  id: string
  type: 'layer25d'
  x: number
  y: number
  layer25d: Layer25DOptions
  result?: ResultImage | null
  images?: ResultImage[]
  meta?: Layer25DMeta | null
  createdAt?: number
}

export type CanvasNode = GenNode | ResultNode | InputNode | RefineNode | MatteNode
  | ViewGenNode | ThreeViewNode
  | WalkVideoNode | SequenceNode | SpriteSheetNode | PlayNode
  | PartsNode | RigWalkNode | TxtVideoNode
  | ClipSrcNode | FrameSplitNode | SpriteGridNode | AnimPreviewNode
  | SpriteSplitNode | PoseCloneNode
  | Godot2DNode | InpaintNode | EraseNode | Layer25DNode
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
  /** 已完成的分片（逐帧/逐方向落盘的任务会边跑边追加，界面能实时显示）。
   *  中途取消或失败时，已跑出来的那部分就在这里，不会白等。 */
  partial?: any[] | null
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

/** 当前生效的负面词（自动态也能看见具体内容）。 */
export type NegativePreview = {
  negative: string
  parts: { base: string; contract: string; custom: string; overridden: boolean }
  useContract: boolean
  material: string
  /** 每段各有几项，界面直接显示「9 项 + 7 项」 */
  counts: { base: number; contract: number; custom: number }
}
