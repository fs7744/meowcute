import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, loadPromptLimits, subscribeTask } from '../api'
import { promptLimitFor } from '../lib/promptLimit'
import { HY15_DEFAULT_WINDOW } from '../lib/hy15Defaults'
import { setViewMode, useViewMode } from '../lib/viewMode'
import AnimPreviewNode from '../components/AnimPreviewNode'
import ClipSrcNode from '../components/ClipSrcNode'
import FramesplitNode from '../components/FramesplitNode'
import SpriteGridNode from '../components/SpriteGridNode'
import GodotExportNode from '../components/GodotExportNode'
import InpaintNode from '../components/InpaintNode'
import EraseNode from '../components/EraseNode'
import Layer25DNode from '../components/Layer25DNode'
import MaskEditor from '../components/MaskEditor'
import GenNode from '../components/GenNode'
import InputNode from '../components/InputNode'
import MatteNode from '../components/MatteNode'
import PlayNode from '../components/PlayNode'
import PreviewOverlay from '../components/PreviewOverlay'
import ProjectFiles from '../components/ProjectFiles'
import PromptModal from '../components/PromptModal'
import RefineNode from '../components/RefineNode'
import ResultNode from '../components/ResultNode'
import SequenceNode from '../components/SequenceNode'
import PartsNode from '../components/PartsNode'
import TxtVideoNode from '../components/TxtVideoNode'
import RigWalkNode from '../components/RigWalkNode'
import SpriteSheetNode from '../components/SpriteSheetNode'
import ThreeViewNode from '../components/ThreeViewNode'
import ViewGenNode from '../components/ViewGenNode'
import WalkVideoNode from '../components/WalkVideoNode'
import type {
  AnimPreviewNode as AnimPreviewNodeType,
  BackgroundOption, CanvasNode, ClipSrcNode as ClipSrcNodeType, GenerateResult,
  FrameSplitNode as FrameSplitNodeType, FrameSplitMeta,
  GenNode as GenNodeType,
  Godot2DNode as Godot2DNodeType, Godot2DMeta,
  InpaintNode as InpaintNodeType,
  EraseMethodInfo, EraseNode as EraseNodeType,
  Layer25DNode as Layer25DNodeType,
  InputNode as InputNodeType, MatteNode as MatteNodeType, PlayNode as PlayNodeType,
  Project, PromptExample, PromptLimits, PromptPreview, RefineNode as RefineNodeType,
  ResultImage,
  ResultMeta, ResultNode as ResultNodeType, SequenceNode as SequenceNodeType,
  PartsNode as PartsNodeType, RigWalkNode as RigWalkNodeType,
  SpriteGridNode as SpriteGridNodeType, SpriteGridMeta,
  SpriteSheetNode as SpriteSheetNodeType, Style, TaskProgress, Template,
  ThreeViewNode as ThreeViewNodeType,
  TxtVideoNode as TxtVideoNodeType, TxtVideoMeta,
  ViewGenNode as ViewGenNodeType,
  WalkVideoNode as WalkVideoNodeType,
} from '../types'

type Props = {
  projectId: string
  onBack: () => void
}

type TaskState = { taskId: string; progress: TaskProgress | null; error: string | null }

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v))
const GEN_W = 404
const RES_W = 420
const INPUT_W = 340
const REFINE_W = 360
const MATTE_W = 340
const VIEWGEN_W = 360
const THREEVIEW_W = 380
const WALKVIDEO_W = 380
const SEQUENCE_W = 380
const SPRITESHEET_W = 380
const PLAY_W = 380
const TXTVIDEO_W = 420
const CLIPSRC_W = 360
const FRAMESPLIT_W = 460
const SPRITEGRID_W = 420
const ANIMPREVIEW_W = 340
const GODOT_W = 380
const INPAINT_W = 400
const ERASE_W = 400
const LAYER25D_W = 420
const NODE_W: Record<string, number> = {
  generate: GEN_W, result: RES_W, input: INPUT_W, refine: REFINE_W, matte: MATTE_W,
  viewgen: VIEWGEN_W, threeview: THREEVIEW_W, walkvideo: WALKVIDEO_W,
  sequence: SEQUENCE_W, spritesheet: SPRITESHEET_W, play: PLAY_W,
  txtvideo: TXTVIDEO_W,
  clipsrc: CLIPSRC_W, framesplit: FRAMESPLIT_W, spritegrid: SPRITEGRID_W,
  animpreview: ANIMPREVIEW_W, godot2d: GODOT_W, inpaint: INPAINT_W,
  erase: ERASE_W, layer25d: LAYER25D_W,
}

/** 哪些类型的节点可以作为某个节点的上游（决定自定义工作流是否合法）。 */
const UPSTREAM_RULES: Record<string, string[]> = {
  generate: ['input'],
  refine: ['input', 'generate', 'refine', 'matte', 'result'],
  matte: ['input', 'generate', 'refine', 'matte', 'result'],
  // 视角生成只认「能给出正视图」的来源；result 也在列：用户可能先把正视图
  // 落到结果卡再回来接，那种图一样是正视图
  viewgen: ['input', 'generate', 'matte', 'refine', 'result'],
  // 拼板允许多条入边（正/侧/背各一路），所以不在 SINGLE_INPUT 里
  threeview: ['input', 'generate', 'matte', 'refine', 'viewgen', 'result'],
  // 八方向链是一条直线：每步只收上一步的产物
  walkvideo: ['input', 'generate', 'matte', 'refine', 'result'],
  // 文本视频的参考图可选：入边规则同行走视频，但不连也能跑
  txtvideo: ['input', 'generate', 'matte', 'refine', 'result'],
  sequence: ['walkvideo'],
  spritesheet: ['sequence'],
  play: ['spritesheet'],
  // Godot 导出读的是精灵图节点的产出；动画预览只是播放器、不产图，但画布上
  // 常把它排在精灵图和导出之间（模板就是这条直线），所以两种上游都放行——
  // 解析时会穿过播放器找到最近的精灵图
  godot2d: ['spritegrid', 'animpreview'],
  // 局部重绘 / 去除局部要一张「可以改的图」：上传的、生成的、精修/抠过的、结果卡的都行
  inpaint: ['input', 'generate', 'matte', 'refine', 'result'],
  erase: ['input', 'generate', 'matte', 'refine', 'result'],
  // 2.5D 分层要一张干净的立绘：上传的、生成的、精修/抠过的、结果卡的都行
  layer25d: ['input', 'generate', 'matte', 'refine', 'result'],
  result: ['generate', 'refine', 'matte', 'viewgen', 'threeview',
           'walkvideo', 'sequence', 'spritesheet', 'play', 'txtvideo',
           'parts', 'rigwalk', 'inpaint', 'erase', 'layer25d'],
}

const UPSTREAM_HINT: Record<string, string> = {
  generate: '选择图片',
  refine: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  matte: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  viewgen: '选择图片 / 生成图 / 扣除背景 / 像素精修 / 图片结果',
  threeview: '生成图 / 侧视图 / 右视图 / 背视图 / 扣除背景 / 图片结果',
  walkvideo: '生成图 / 选择图片 / 扣除背景 / 像素精修',
  txtvideo: '选择图片 / 生成图 / 扣除背景 / 像素精修 / 图片结果（可不连，纯文本）',
  sequence: '行走视频',
  spritesheet: '序列帧',
  play: '精灵整图',
  godot2d: '精灵图（可隔着「动画预览」）',
  inpaint: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  erase: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  layer25d: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  result: '生成图 / 像素精修 / 扣除背景 / 视角图 / 四视图拼板 / 行走链 / 局部重绘 / 去除局部 / 2.5D 分层',
}

function canConnect(from: CanvasNode | undefined, to: CanvasNode | undefined): boolean {
  if (!from || !to || from.id === to.id) return false
  const allowed = UPSTREAM_RULES[to.type]
  return !!allowed && allowed.includes(from.type)
}

/** 只能接收单条入边的类型：新连线替换旧入边，避免多源歧义。
 *  四视图拼板不在列：它要收正/侧/背/右四路。 */
const SINGLE_INPUT = new Set(['refine', 'matte', 'result', 'viewgen',
                              'walkvideo', 'sequence', 'spritesheet', 'play',
                              'godot2d', 'inpaint', 'erase', 'layer25d'])

/** 右键「添加节点」的目录：分类 → 具体节点。
 *  节点类型只会越来越多，平铺成一条长列表很快就用不了了。 */
const NODE_CATALOG: {
  id: string
  label: string
  hint: string
  items: { type: CanvasNode['type']; label: string; hint: string; dot: string; preset?: string }[]
}[] = [
  {
    id: 'input', label: '输入', hint: '素材入口：已有的图、参考图',
    items: [
      { type: 'input', label: '选择图片', dot: 'd-input',
        hint: '上传图片，可当下游参考图或直接交给后处理' },
    ],
  },
  {
    id: 'generate', label: '生成', hint: '跑本地模型，把文字变成图',
    items: [
      { type: 'generate', label: '生成图', dot: 'd-gen',
        hint: '文字描述 → AI 出图，可挂参考图走编辑底座' },
    ],
  },
  {
    id: 'view', label: '视角', hint: '从一张正视图推出其它角度',
    items: [
      { type: 'viewgen', label: '生成侧视图', dot: 'd-view', preset: 'side',
        hint: '编辑底座指令接力：转成侧面，造型以正视图为准' },
      { type: 'viewgen', label: '生成右视图', dot: 'd-view', preset: 'side_r',
        hint: '同链路转向画面右侧：与侧视图镜像，补全四视图' },
      { type: 'viewgen', label: '生成背视图', dot: 'd-view', preset: 'back',
        hint: '同链路转 180°：脸部不可见，不新增衣物配件' },
    ],
  },
  {
    id: 'post', label: '后处理', hint: '不跑模型，纯确定性图像处理',
    items: [
      { type: 'refine', label: '像素精修', dot: 'd-refine',
        hint: '网格对齐 + 调色板量化，输出原生像素尺寸' },
      { type: 'matte', label: '扣除背景', dot: 'd-matte',
        hint: 'u2net / GrabCut 抠图，输出透明底 PNG' },
      { type: 'threeview', label: '四视图拼板', dot: 'd-threeview',
        hint: '正/侧/背三张按共同基线拼成横向板' },
    ],
  },
  {
    id: 'layer25d', label: '2.5D / 分层', hint: '静态图 → 会动的分层模型',
    items: [
      { type: 'layer25d', label: '2.5D 分层模型', dot: 'd-layer25d',
        hint: 'See-Through 拆层 → 分层 PSD（每层补全遮挡，可直接绑骨）' },
    ],
  },
  {
    id: 'edit', label: '局部编辑', hint: '只改一块，其余像素不动',
    items: [
      { type: 'inpaint', label: '局部重绘', dot: 'd-inpaint',
        hint: '笔刷涂出要改的地方 + 一句话，只重画这一块' },
      { type: 'erase', label: '去除局部', dot: 'd-erase',
        hint: '涂掉水印/杂物，用周围内容补上（3 种非 AI 方式 + AI 补全）' },
    ],
  },
  {
    id: 'txtvideo', label: '文本生视频', hint: '提示词 + 可选参考图 → 一段视频',
    items: [
      { type: 'txtvideo', label: '文本视频', dot: 'd-walk',
        hint: 'Wan2.2 纯文生视频；连上游图则切图生视频（首帧锚定）' },
    ],
  },
  {
    id: 'clip', label: '视频转素材', hint: '视频 / GIF → 挑帧 → 精灵图 → 动画预览',
    items: [
      { type: 'clipsrc', label: '选择视频 / GIF', dot: 'd-walk',
        hint: '导入 mp4 / mov / webm / gif，先播一遍确认片段' },
      { type: 'framesplit', label: '拆解帧', dot: 'd-sequence',
        hint: '拆成一摞帧：跳帧步长 + 近似度过滤，再逐张勾选' },
      { type: 'spritegrid', label: '精灵图', dot: 'd-sheet',
        hint: '选中帧按网格拼图，导出帧索引 JSON（Phaser / Unity / Godot）' },
      { type: 'animpreview', label: '动画预览', dot: 'd-play',
        hint: '按设定帧率循环播放，可逐帧步进检查接缝' },
      { type: 'godot2d', label: 'Godot 导出', dot: 'd-godot',
        hint: 'SpriteFrames(.tres) + AnimatedSprite2D 场景(.tscn)，解压即用' },
    ],
  },
  {
    id: 'walk', label: '八方向行走', hint: '一张角色图 → 八方向行走精灵表',
    items: [
      { type: 'walkvideo', label: '行走视频', dot: 'd-walk',
        hint: '各方向立绘 + Wan 图生视频：右侧方向由左侧镜像' },
      { type: 'sequence', label: '序列帧', dot: 'd-sequence',
        hint: '每方向视频 → 抽帧 / 去重 / 抠图 → 透明底帧' },
      { type: 'spritesheet', label: '精灵整图', dot: 'd-sheet',
        hint: '共享缩放规整后拼成每方向一张横排精灵表' },
      { type: 'play', label: '可操控预览', dot: 'd-play',
        hint: '方向键 / 按钮切换八个方向播放行走循环' },
    ],
  },
  {
    id: 'rig', label: '骨骼路线', hint: '拆件 → 骨骼行走：纯 CPU、确定性、不闪烁',
    items: [
      { type: 'parts', label: '拆件', dot: 'd-matte',
        hint: 'see-through 出躯干/四肢/道具 + 爆炸图补头 → 6 个骨骼部件' },
      { type: 'rigwalk', label: '骨骼行走', dot: 'd-play',
        hint: '6 个部件 → 8 帧行走循环，约 1 秒，天然透明底' },
    ],
  },
  {
    id: 'output', label: '输出', hint: '看结果、导出素材',
    items: [
      { type: 'result', label: '图片结果', dot: 'd-result',
        hint: '展示最终产出，可放大预览 / 右键另存' },
    ],
  },
]

const NODE_LABEL: Record<string, string> = {
  input: '选择图片', generate: '生成图', refine: '像素精修',
  matte: '扣除背景', result: '图片结果',
  viewgen: '生成视角', threeview: '四视图拼板',
  walkvideo: '行走视频', sequence: '序列帧',
  spritesheet: '精灵整图', play: '可操控预览',
  txtvideo: '文本生视频', parts: '拆件', rigwalk: '骨骼行走',
  clipsrc: '选择视频', framesplit: '拆解帧', spritegrid: '精灵图',
  animpreview: '动画预览', godot2d: 'Godot 导出', inpaint: '局部重绘',
  erase: '去除局部', layer25d: '2.5D 分层模型',
}
const nodeLabel = (t: string) => NODE_LABEL[t] || t

function newId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
}

/** 把一张已产出的图转成「上游源」描述，统一喂给精修 / 抠图接口。 */
function imageSource(im: ResultImage) {
  return {
    kind: 'images' as const, assetId: im.assetId, file: '',
    url: im.url, w: im.width, h: im.height,
  }
}

export default function Canvas({ projectId, onBack }: Props) {
  const [project, setProject] = useState<Project | null>(null)
  /** 简略 / 专业：顶栏可切，节点卡片据此决定露多少参数 */
  const viewMode = useViewMode()
  const [template, setTemplate] = useState<Template | null>(null)
  const [styles, setStyles] = useState<Style[]>([])
  /** 去除局部的四种补法（含适用场景）：知识在后端，前端只显示 */
  const [eraseMethods, setEraseMethods] = useState<EraseMethodInfo[]>([])
  /** 设置页「新建节点时的默认值」（width/height/n/background） */
  const [nodeDefaults, setNodeDefaults] = useState<Record<string, any>>({})
  /** 提示词上限：生成卡自己会拦，工作流入口也要拦（否则绕过按钮照样发出去） */
  const [limits, setLimits] = useState<PromptLimits | null>(null)
  /** 模板编辑草稿：提交中 */
  const [tplBusy, setTplBusy] = useState(false)
  const [backgrounds, setBackgrounds] = useState<BackgroundOption[]>([])
  const [examples, setExamples] = useState<PromptExample[]>([])
  const [categories, setCategories] = useState<string[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)

  const [viewport, setViewport] = useState({ x: 80, y: 60, z: 1 })
  const [selected, setSelected] = useState<string | null>(null)
  const [tasks, setTasks] = useState<Record<string, TaskState>>({})
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'dirty' | 'error'>('saved')
  const [lastSaved, setLastSaved] = useState<number | null>(null)
  const [filesOpen, setFilesOpen] = useState(false)
  // 连线模式：点了输出端口后记住源节点，再点目标节点的输入端口完成连线
  const [linkFrom, setLinkFrom] = useState<string | null>(null)
  const [refining, setRefining] = useState<Record<string, boolean>>({})
  const [matting, setMatting] = useState<Record<string, boolean>>({})
  /** 「这个节点正在跑」的独立标记：跨 POST 在途 → SSE 完成全过程。
   *  不能只看 tasks[id].progress：请求还没回来时它是 null，按钮就不禁用，
   *  用户双击会起两个任务、两条 SSE（closers 被覆盖 → 前一条泄漏）。 */
  const [busy, setBusy] = useState<Record<string, boolean>>({})
  const markBusy = (id: string, on: boolean) =>
    setBusy((b) => (b[id] === on ? b : { ...b, [id]: on }))
  const isRunning = (id: string) =>
    !!busy[id] || (!!tasks[id]?.progress && !tasks[id]?.progress?.doneAt)
  // 右键菜单：屏幕坐标定位，世界坐标用于在该处创建节点
  const [ctxMenu, setCtxMenu] = useState<{
    sx: number; sy: number; wx: number; wy: number
  } | null>(null)
  // 右键菜单里展开的二级分类（键盘/点击可用；悬停由 CSS 负责）
  const [ctxOpen, setCtxOpen] = useState<string | null>(null)
  // 选中的连线（点击线选中，Delete 删除，双击直接删）
  const [selectedEdge, setSelectedEdge] = useState<{ from: string; to: string } | null>(null)
  // 拖拽连线：按住输出端口拖动时，实时画一条临时曲线到光标
  const [linking, setLinking] = useState<{ from: string; x: number; y: number } | null>(null)
  const linkDragRef = useRef<{
    from: string; startX: number; startY: number; moved: boolean
  } | null>(null)
  // 一键运行工作流：状态 + 中断标记 + 当前生成任务（停止时取消它）
  const [wf, setWf] = useState<{
    running: boolean; label: string; done: number; total: number; error: string | null
  }>({ running: false, label: '', done: 0, total: 0, error: null })
  const abortRef = useRef(false)
  const activeTaskRef = useRef<string | null>(null)

  const [preview, setPreview] = useState<{
    src: string; origSrc?: string; title?: string; pixelSize?: [number, number]
  } | null>(null)
  const [promptModal, setPromptModal] = useState<{ open: boolean; loading: boolean; error: string | null; data: PromptPreview | null }>(
    { open: false, loading: false, error: null, data: null },
  )
  /** 涂抹蒙版编辑器（全屏弹层）：必须挂在画布根节点，不能挂进带 transform
   *  的世界层——position:fixed 会相对那个变换后的容器定位。 */
  const [maskEdit, setMaskEdit] = useState<{
    nodeId: string; srcUrl: string; w: number; h: number; maskUrl: string | null
    /** true = 去除局部（文案不同：涂的是要去掉的东西，不是要重画的地方） */
    erase?: boolean
  } | null>(null)

  const viewportRef = useRef<HTMLDivElement>(null)
  const worldRef = useRef<HTMLDivElement>(null)
  const panRef = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null)
  const dragRef = useRef<{ id: string; ox: number; oy: number } | null>(null)
  const [panning, setPanning] = useState(false)
  const loadedRef = useRef(false)
  const saveTimer = useRef<number | null>(null)
  const lastPayload = useRef<string>('')
  const closers = useRef<Record<string, () => void>>({})
  const projectRef = useRef<Project | null>(null)

  projectRef.current = project

  // ---------- 加载 ----------
  useEffect(() => {
    let alive = true
    // 项目本体失败才是致命的；画风/示例/模板/设置只是辅助数据，
    // 任何一个挂了都不该把整块画布拖成错误页（旧写法是一个 Promise.all）。
    const optional = <T,>(pr: Promise<T>, fallback: T) =>
      pr.catch(() => fallback)
    Promise.all([
      api.getProject(projectId),
      optional(api.styles(), { styles: [], backgrounds: [] } as any),
      optional(api.promptExamples(), { examples: [], categories: [] } as any),
      optional(api.templates(), { templates: [] } as any),
      optional(api.settings(), { settings: { defaults: {} }, models: [] } as any),
    ]).then(([pr, st, ex, tp, se]) => {
      if (!alive) return
      // 模板被删过（自定义模板可以删）时不能把项目卡死在「加载中」：
      // 模板只用来给空画布补第一张卡 + 提供默认值，缺了照常用默认值。
      const tpl = (tp.templates as Template[]).find((t) => t.id === pr.project.templateId)
        || { id: pr.project.templateId, name: '（模板已删除）', defaults: {}, graph: undefined } as any
      setProject(pr.project)
      setViewport(pr.project.viewport || { x: 80, y: 60, z: 1 })
      setStyles(st.styles)
      setBackgrounds(st.backgrounds)
      setExamples(ex.examples)
      setCategories(ex.categories)
      setTemplate(tpl)
      // 设置页的「新建节点默认值」：新建生成卡时预填（模板 defaults 仍可覆盖）
      setNodeDefaults(se.settings?.defaults || {})
      loadPromptLimits().then(setLimits).catch(() => undefined)
      // 补法清单只是辅助数据：拿不到也能用（卡片里有内置兜底）
      api.eraseMethods().then((r) => alive && setEraseMethods(r.methods))
        .catch(() => undefined)
      lastPayload.current = JSON.stringify({
        name: pr.project.name, nodes: pr.project.nodes,
        edges: pr.project.edges, viewport: pr.project.viewport,
      })
      loadedRef.current = true
      setSaveState('saved')
    }).catch((e) => alive && setLoadError(String(e.message || e)))
    return () => { alive = false }
  }, [projectId])

  // 首次进入：没有节点就补一张生成卡
  useEffect(() => {
    if (!project || !template) return
    if (project.nodes.length > 0) return
    const d = template.defaults || {}
    const node: GenNodeType = {
      id: newId('gen'),
      type: 'generate',
      x: 80,
      y: 120,
      prompt: '',
      // 模板显式给了 null = 这节点不要画风（提示词自己带），别用默认值盖掉
      styleId: d.styleId === undefined ? 'pixel_16bit' : d.styleId,
      refs: [],
      params: {
        width: d.width || 512, height: d.height || 512, n: d.n || 1,
        steps: null, seed: null,
        // 同上：'' 与 'none' 都是「不指定背景」，只有没设置过才回落中灰
        background: (d as any).background === undefined
          ? 'gray' : (d as any).background,
        maxSeqLen: (d as any).maxSeqLen ?? null,
      },
      pixel: {
        enabled: true, gridSnap: true, colors: 16, palette: 'auto',
        gridSize: null, targetSize: null, transparentBg: false, ...(d.pixel || {}),
      },
      createdAt: Date.now() / 1000,
    }
    setProject({ ...project, nodes: [node] })
  }, [project, template])

  // ---------- 自动保存 ----------
  const doSave = useCallback(async () => {
    const p = projectRef.current
    if (!p) return
    const payload = { name: p.name, nodes: p.nodes, edges: p.edges, viewport }
    const json = JSON.stringify(payload)
    if (json === lastPayload.current) { setSaveState('saved'); return }
    setSaveState('saving')
    try {
      await api.saveProject(p.id, payload as any)
      lastPayload.current = json
      setSaveState('saved')
      setLastSaved(Date.now())
    } catch {
      setSaveState('error')
    }
  }, [viewport])

  useEffect(() => {
    if (!loadedRef.current || !project) return
    setSaveState('dirty')
    if (saveTimer.current) window.clearTimeout(saveTimer.current)
    saveTimer.current = window.setTimeout(doSave, 800)
    return () => { if (saveTimer.current) window.clearTimeout(saveTimer.current) }
  }, [project?.nodes, project?.edges, project?.name, viewport, doSave, project])

  // 离开画布：关掉所有 SSE + **把未保存的改动压一次**。
  // 800ms 防抖窗口内点「← 项目」或刷新，改动本来会直接丢（注释写了要 flush，
  // 代码只关了 SSE）。这里用 sendBeacon 走同步语义：页面卸载后 fetch 会被取消，
  // beacon 不会。体量只有几十 KB 的 JSON，远小于 64KB 上限。
  const saveBeaconRef = useRef<() => void>(() => undefined)
  /** 放弃编辑时会先删草稿项目：此时再 flush 一次等于对着已删目录写文件 */
  const skipFlushRef = useRef(false)
  saveBeaconRef.current = () => {
    const p = projectRef.current
    if (!p || skipFlushRef.current) return
    const payload = { name: p.name, nodes: p.nodes, edges: p.edges, viewport }
    const json = JSON.stringify(payload)
    if (json === lastPayload.current) return
    lastPayload.current = json
    const body = new Blob([JSON.stringify(payload)], { type: 'application/json' })
    navigator.sendBeacon?.(`/api/projects/${p.id}`, body)
  }
  useEffect(() => () => {
    saveBeaconRef.current()
    Object.values(closers.current).forEach((f) => f())
  }, [])

  // ---------- 画布交互 ----------
  const toWorld = useCallback((clientX: number, clientY: number) => {
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return { x: 0, y: 0 }
    return {
      x: (clientX - rect.left - viewport.x) / viewport.z,
      y: (clientY - rect.top - viewport.y) / viewport.z,
    }
  }, [viewport])

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!rect) return
    const cx = e.clientX - rect.left
    const cy = e.clientY - rect.top
    setViewport((v) => {
      const next = clamp(v.z * (e.deltaY < 0 ? 1.08 : 1 / 1.08), 0.2, 2.5)
      const k = next / v.z
      return { z: next, x: cx - (cx - v.x) * k, y: cy - (cy - v.y) * k }
    })
  }, [])

  const startPan = (e: React.MouseEvent) => {
    if (e.button !== 0 && e.button !== 1) return
    if (e.target !== e.currentTarget && (e.target as HTMLElement).closest('.node')) return
    panRef.current = { x: e.clientX, y: e.clientY, vx: viewport.x, vy: viewport.y }
    setPanning(true)
    setSelected(null)
    setLinkFrom(null)
    setSelectedEdge(null)
  }

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (panRef.current) {
        setViewport((v) => ({
          ...v,
          x: panRef.current!.vx + (e.clientX - panRef.current!.x),
          y: panRef.current!.vy + (e.clientY - panRef.current!.y),
        }))
      }
      if (dragRef.current) {
        const d = dragRef.current
        const wx = (e.clientX - (viewportRef.current?.getBoundingClientRect().left || 0) - viewport.x) / viewport.z
        const wy = (e.clientY - (viewportRef.current?.getBoundingClientRect().top || 0) - viewport.y) / viewport.z
        setProject((p) => p ? {
          ...p,
          nodes: p.nodes.map((n) => n.id === d.id ? { ...n, x: wx - d.ox, y: wy - d.oy } : n),
        } : p)
      }
    }
    const onUp = () => {
      panRef.current = null
      dragRef.current = null
      setPanning(false)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [viewport])

  const startDragNode = (id: string) => (e: React.MouseEvent) => {
    if (e.button !== 0) return
    e.stopPropagation()
    setSelected(id)
    const node = project?.nodes.find((n) => n.id === id)
    if (!node) return
    const w = toWorld(e.clientX, e.clientY)
    dragRef.current = { id, ox: w.x - node.x, oy: w.y - node.y }
  }

  const fitView = useCallback(() => {
    const world = worldRef.current
    const rect = viewportRef.current?.getBoundingClientRect()
    if (!world || !rect) return
    const els = world.querySelectorAll<HTMLElement>('[data-node-id]')
    if (!els.length) return
    let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity
    els.forEach((el) => {
      const x = parseFloat(el.style.left || '0')
      const y = parseFloat(el.style.top || '0')
      minX = Math.min(minX, x)
      minY = Math.min(minY, y)
      maxX = Math.max(maxX, x + el.offsetWidth)
      maxY = Math.max(maxY, y + el.offsetHeight)
    })
    const pad = 70
    const bw = Math.max(1, maxX - minX)
    const bh = Math.max(1, maxY - minY)
    const z = clamp(Math.min((rect.width - pad * 2) / bw, (rect.height - pad * 2) / bh), 0.2, 1.1)
    setViewport({
      z,
      x: (rect.width - bw * z) / 2 - minX * z,
      y: (rect.height - bh * z) / 2 - minY * z,
    })
  }, [])

  // ---------- 节点操作 ----------
  const patchNode = (id: string, patch: any) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === id ? ({ ...n, ...patch } as CanvasNode) : n),
    } : p)
  }

  const addGenNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const rect = viewportRef.current?.getBoundingClientRect()
    const center = at
      ? { x: at.x + GEN_W / 2, y: at.y + 160 }
      : (rect
        ? toWorld(rect.left + rect.width / 2, rect.top + rect.height / 2)
        : { x: 200, y: 160 })
    // 优先级：模板 defaults > 设置页 defaults > 硬编码兜底。
    // 设置页那一层以前只是摆设（没有任何代码读它），用户改了不生效。
    const d = { ...nodeDefaults, ...(template?.defaults || {}) } as any
    const node: GenNodeType = {
      id: newId('gen'),
      type: 'generate',
      x: Math.round(center.x - GEN_W / 2),
      y: Math.round(center.y - 160),
      prompt: '',
      styleId: (d as any).styleId === undefined
        ? 'pixel_16bit' : (d as any).styleId,
      refs: [],
      params: {
        width: (d as any).width || 512, height: (d as any).height || 512,
        n: (d as any).n || 1, steps: null, seed: null,
        background: (d as any).background === undefined
          ? 'gray' : (d as any).background,
        maxSeqLen: (d as any).maxSeqLen ?? null,
      },
      pixel: {
        enabled: true, gridSnap: true, colors: 16, palette: 'auto',
        gridSize: null, targetSize: null, transparentBg: false, ...((d as any).pixel || {}),
      },
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const canvasSpot = (w: number, h: number) => {
    const rect = viewportRef.current?.getBoundingClientRect()
    const c = rect
      ? toWorld(rect.left + rect.width / 2, rect.top + rect.height / 2)
      : { x: 200, y: 160 }
    return { x: Math.round(c.x - w / 2), y: Math.round(c.y - h / 2) }
  }

  const addInputNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(INPUT_W, 240)
    const node: InputNodeType = {
      id: newId('inp'), type: 'input', x: spot.x, y: spot.y,
      refs: [], label: '', createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addRefineNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(REFINE_W, 360)
    const node: RefineNodeType = {
      id: newId('rf'), type: 'refine', x: spot.x, y: spot.y,
      pixel: {
        enabled: true, gridSnap: true, colors: 16, palette: 'auto',
        gridSize: null, targetSize: null, transparentBg: false,
      },
      result: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addMatteNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(MATTE_W, 380)
    const node: MatteNodeType = {
      id: newId('mt'), type: 'matte', x: spot.x, y: spot.y,
      matte: {
        enabled: true, method: 'auto', tolerance: 80,
        hard: true, feather: 0, despill: true, trim: false,
        edge: 0, bottomTrim: 0,
      },
      result: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  /** 右键菜单的统一入口：按类型分发到对应的添加函数。
   *  preset 给同类型节点的不同口味用（侧视图 / 背视图都是 viewgen）。 */
  const addNodeAt = (
    type: CanvasNode['type'], at: { x: number; y: number }, preset?: string,
  ) => {
    if (type === 'input') addInputNode(at)
    else if (type === 'generate') addGenNode(at)
    else if (type === 'refine') addRefineNode(at)
    else if (type === 'matte') addMatteNode(at)
    else if (type === 'viewgen') {
      const pv = preset === 'back' ? 'back' : preset === 'side_r' ? 'side_r' : 'side'
      addViewGenNode(at, pv)
    }
    else if (type === 'threeview') addThreeViewNode(at)
    else if (type === 'walkvideo') addWalkVideoNode(at)
    else if (type === 'txtvideo') addTxtVideoNode(at)
    else if (type === 'parts') addPartsNode(at)
    else if (type === 'rigwalk') addRigWalkNode(at)
    else if (type === 'sequence') addSequenceNode(at)
    else if (type === 'spritesheet') addSpriteSheetNode(at)
    else if (type === 'play') addPlayNode(at)
    else if (type === 'clipsrc') addClipSrcNode(at)
    else if (type === 'framesplit') addFrameSplitNode(at)
    else if (type === 'spritegrid') addSpriteGridNode(at)
    else if (type === 'animpreview') addAnimPreviewNode(at)
    else if (type === 'godot2d') addGodotNode(at)
    else if (type === 'inpaint') addInpaintNode(at)
    else if (type === 'erase') addEraseNode(at)
    else if (type === 'layer25d') addLayer25DNode(at)
    else addEmptyResultNode(at)
  }

  const addViewGenNode = (
    at: { x: number; y: number }, view: 'side' | 'back' | 'side_r' = 'side',
  ) => {
    if (!project) return
    const spot = { x: Math.round(at.x), y: Math.round(at.y) }
    const node: ViewGenNodeType = {
      id: newId('vg'), type: 'viewgen', x: spot.x, y: spot.y,
      viewgen: {
        enabled: true, view, styleId: null,
        steps: null, seed: null, width: 512, height: 768, maxSeqLen: null,
      },
      result: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addThreeViewNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(THREEVIEW_W, 400)
    const node: ThreeViewNodeType = {
      id: newId('tv'), type: 'threeview', x: spot.x, y: spot.y,
      threeview: {
        enabled: true, bg: '1B4332', transparentBg: false,
        gap: 12, baseline: 0.93, cell: null,
      },
      result: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addWalkVideoNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(WALKVIDEO_W, 420)
    // 提示词预填示例库「行走视频」的第一条（后端单一事实来源）；
    // 没加载到示例时留空，后端会补默认值
    const walkEx = examples.find((e) => e.category === '行走视频' && e.id === 'builtin-walk-walk')
    const node: WalkVideoNodeType = {
      id: newId('wk'), type: 'walkvideo', x: spot.x, y: spot.y,
      walkvideo: {
        enabled: true, dirMode: 'eight', action: '行走', styleId: null,
        prompt: walkEx?.content || '',
        engine: 'hy15',
        steps: null, seed: null, width: 384, height: 512,
        numFrames: 21, fps: 12, maxSide: 512, maxSeqLen: null,
        guidanceScale: null, motionThreshold: 6.0, retries: 2, window: HY15_DEFAULT_WINDOW,
      },
      result: null, images: [], meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addPartsNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at ? { x: Math.round(at.x), y: Math.round(at.y) } : canvasSpot(400, 420)
    const node: PartsNodeType = {
      id: newId('pt'), type: 'parts', x: spot.x, y: spot.y,
      parts: { enabled: true, resolution: 1280, withHead: true, seed: 7 },
      result: null, images: [], meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addRigWalkNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at ? { x: Math.round(at.x), y: Math.round(at.y) } : canvasSpot(390, 460)
    const node: RigWalkNodeType = {
      id: newId('rw'), type: 'rigwalk', x: spot.x, y: spot.y,
      rigwalk: { enabled: true, frames: 8, swing: 13, arm: 12, bob: 5, lift: 7,
        hold: false, fps: 8 },
      result: null, images: [], meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addSequenceNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(SEQUENCE_W, 420)
    const node: SequenceNodeType = {
      id: newId('sq'), type: 'sequence', x: spot.x, y: spot.y,
      sequence: {
        enabled: true, framesPerDir: 8, fps: 8, matte: true, tolerance: 40,
      },
      result: null, images: [], meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addSpriteSheetNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(SPRITESHEET_W, 420)
    const node: SpriteSheetNodeType = {
      id: newId('sh'), type: 'spritesheet', x: spot.x, y: spot.y,
      spritesheet: {
        enabled: true, size: 128, cols: 0, sharedScale: true, anchor: 'bottom',
      },
      result: null, images: [], meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addPlayNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(PLAY_W, 460)
    const node: PlayNodeType = {
      id: newId('pl'), type: 'play', x: spot.x, y: spot.y,
      play: { enabled: true, fps: 8, zoom: 2 },
      result: null, meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addClipSrcNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(CLIPSRC_W, 420)
    const node: ClipSrcNodeType = {
      id: newId('cl'), type: 'clipsrc', x: spot.x, y: spot.y,
      clipsrc: {
        file: '', kind: 'video', durationSec: 0, fps: 0, width: 0, height: 0,
      },
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addFrameSplitNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(FRAMESPLIT_W, 520)
    const node: FrameSplitNodeType = {
      id: newId('fs'), type: 'framesplit', x: spot.x, y: spot.y,
      framesplit: {
        enabled: true, fpsCap: 12, maxFrames: 120, start: 0, end: null,
        matte: true, tolerance: 40, matteModel: 'auto', maxSide: 384, loopClose: true,
        step: 1, simPct: 0, picks: [],
      },
      frames: [], source: null, result: null, meta: null,
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addSpriteGridNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(SPRITEGRID_W, 480)
    const node: SpriteGridNodeType = {
      id: newId('sg'), type: 'spritegrid', x: spot.x, y: spot.y,
      spritegrid: {
        enabled: true, cell: 128, cols: 0, padding: 0, margin: 0,
        anchor: 'bottom', sharedScale: true, pivot: 'shared', fps: 12,
      },
      result: null, images: [], meta: null, indexUrl: null,
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addAnimPreviewNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(ANIMPREVIEW_W, 460)
    const node: AnimPreviewNodeType = {
      id: newId('ap'), type: 'animpreview', x: spot.x, y: spot.y,
      animpreview: { enabled: true, fps: 12, zoom: 4, loop: true, bg: 'checker' },
      meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addGodotNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(GODOT_W, 420)
    const node: Godot2DNodeType = {
      id: newId('gd'), type: 'godot2d', x: spot.x, y: spot.y,
      godot2d: {
        enabled: true, animName: 'default', name: 'walk', fps: 12, loop: true,
        mode: 'atlas', folder: 'sprites', origin: 'bottom',
        textureFilter: 'nearest',
      },
      meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addInpaintNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(INPAINT_W, 560)
    const node: InpaintNodeType = {
      id: newId('ip'), type: 'inpaint', x: spot.x, y: spot.y,
      inpaint: {
        enabled: true, instruction: '', constraint: '', negative: '', refs: [],
        mode: 'crop', context: 0.35, feather: 4, expand: 8, hard: false,
        matchColor: true, matchStrength: 0.45, palette: 'none',
        paletteColors: 48, steps: null, seed: null, maxSeqLen: null,
        maskFile: '', mask: null,
      },
      result: null, images: [], meta: null,
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addEraseNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(ERASE_W, 520)
    const node: EraseNodeType = {
      id: newId('er'), type: 'erase', x: spot.x, y: spot.y,
      erase: {
        enabled: true, method: 'telea', expand: 6, feather: 2, hard: false,
        matchColor: true, matchStrength: 0.6, palette: 'none', paletteColors: 48,
        instruction: '', constraint: '', negative: '', aiMode: 'crop',
        context: 0.35, steps: null, seed: null, maxSeqLen: null,
        maskFile: '', mask: null,
      },
      result: null, images: [], meta: null,
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addLayer25DNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(LAYER25D_W, 520)
    const node: Layer25DNodeType = {
      id: newId('l2d'), type: 'layer25d', x: spot.x, y: spot.y,
      layer25d: {
        enabled: true, resolution: 1024, steps: 30, seed: 42, lrsplit: true,
      },
      result: null, images: [], meta: null,
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addEmptyResultNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at
      ? { x: Math.round(at.x), y: Math.round(at.y) }
      : canvasSpot(RES_W, 320)
    const node: ResultNodeType = {
      id: newId('res'), type: 'result', x: spot.x, y: spot.y, genId: null,
      images: [], prompt: '', negative: '', meta: {},
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const startLink = (id: string, client?: { clientX: number; clientY: number }) => {
    setSelected(id)
    if (!client) {
      setLinkFrom(id)
      return
    }
    const w = toWorld(client.clientX, client.clientY)
    linkDragRef.current = {
      from: id, startX: client.clientX, startY: client.clientY, moved: false,
    }
    setLinking({ from: id, x: w.x, y: w.y })
    setLinkFrom(null)
  }

  const finishLinkId = (fromId: string, toId: string) => {
    if (!project) return
    const fromNode = project.nodes.find((n) => n.id === fromId)
    const toNode = project.nodes.find((n) => n.id === toId)
    if (!canConnect(fromNode, toNode)) {
      alert(`这两类节点不能这样连。${toNode ? `「${nodeLabel(toNode.type)}」的输入可以是：${UPSTREAM_HINT[toNode.type] || '无'}` : ''}`)
      return
    }
    setProject((p) => {
      if (!p) return p
      // 精修 / 抠图 / 结果只保留一条入边：新连线替换旧入边，避免多源歧义
      const kept = p.edges.filter((e) => !(e.to === toId && !!toNode && SINGLE_INPUT.has(toNode.type)))
      if (kept.some((e) => e.from === fromId && e.to === toId)) return { ...p, edges: kept }
      return { ...p, edges: [...kept, { from: fromId, to: toId }] }
    })
    setLinkFrom(null)
  }

  const finishLink = (toId: string) => {
    if (linkFrom) finishLinkId(linkFrom, toId)
  }

  /** 解析任意节点的输入源：沿入边找到上游，取它当前最新的产出图。
   *  精修与扣背景共用这一套（上游可能是输入图、生成图、结果卡、上一次的后处理产出）。 */
  const resolveUpstreamSource = (node: CanvasNode) => {
    const inEdge = project?.edges.find((e) => e.to === node.id)
    const src = inEdge ? project?.nodes.find((n) => n.id === inEdge.from) : null
    if (src?.type === 'generate') {
      const im = src.lastImages?.[0]
      if (im) return imageSource(im)
    } else if (src?.type === 'result') {
      const im = src.images?.[0]
      if (im) return imageSource(im)
    } else if (src?.type === 'input') {
      const r0 = src.refs?.[0]
      if (r0) return { kind: 'refs' as const, assetId: r0.id, file: r0.file,
                       url: r0.url, w: r0.width, h: r0.height }
    } else if (src?.type === 'refine' || src?.type === 'matte') {
      const im = src.result
      if (im) return imageSource(im)
    }
    throw new Error(`「${nodeLabel(node.type)}」没有可用输入：请把上游节点的输出连到它的左侧端口，`
      + '并确认上游已经跑出结果。')
  }

  /** 解析精修节点的输入源：优先用工作流本次刚产出的图，其次读画布状态。 */
  const resolveRefineSource = (node: RefineNodeType, override?: ResultImage[] | null) => {
    if (override?.length) return imageSource(override[0])
    return resolveUpstreamSource(node)
  }

  const executeRefine = async (
    node: RefineNodeType, override?: ResultImage[] | null,
  ): Promise<ResultImage> => {
    if (!project) throw new Error('项目未加载')
    const src = resolveRefineSource(node, override)
    const r = await api.refine({
      projectId: project.id, origAssetId: src.assetId,
      sourceKind: src.kind, sourceFile: src.file || undefined,
      pixel: node.pixel,
    })
    const im: any = r.image
    return {
      assetId: im.assetId, url: im.url, width: im.width, height: im.height,
      origAssetId: src.assetId, origUrl: src.url, origSize: [src.w, src.h],
      refine: im.refine,
    }
  }

  const applyRefineResult = (node: RefineNodeType, result: ResultImage) => {
    setProject((p) => {
      if (!p) return p
      const nodes = p.nodes.map((n) =>
        n.id === node.id ? ({ ...n, result } as CanvasNode) : n)
      // 精修结果同步到下游结果卡，结果卡复用现有渲染
      for (const e of p.edges.filter((x) => x.from === node.id)) {
        const target = nodes.find((n) => n.id === e.to)
        if (target?.type === 'result') {
          Object.assign(target, {
            images: [result], prompt: '', negative: '',
            meta: { ...(target.meta || {}), pixelEnabled: true },
          })
        }
      }
      return { ...p, nodes }
    })
  }

  const runRefine = async (node: RefineNodeType) => {
    setRefining((s) => ({ ...s, [node.id]: true }))
    try {
      const result = await executeRefine(node)
      applyRefineResult(node, result)
    } catch (e: any) {
      alert(`精修失败：${e.message || e}`)
    } finally {
      setRefining((s) => ({ ...s, [node.id]: false }))
    }
  }

  // ---------- 扣除背景 ----------
  /** 执行一次抠图。override 给定时用它当源图（工作流里传上游刚产出的图）。 */
  const executeMatte = async (
    node: MatteNodeType, override?: ResultImage | null,
  ): Promise<ResultImage> => {
    if (!project) throw new Error('项目未加载')
    const src = override ? imageSource(override) : resolveUpstreamSource(node)
    const r = await api.matte({
      projectId: project.id, origAssetId: src.assetId,
      sourceKind: src.kind, sourceFile: src.file || undefined,
      matte: node.matte,
    })
    const im: any = r.image
    return {
      assetId: im.assetId, url: im.url, width: im.width, height: im.height,
      origAssetId: src.assetId, origUrl: src.url, origSize: [src.w, src.h],
      // 上游是精修产物时把精修指标带下去，结果卡才不会退化成「未精修」
      refine: override?.refine,
      matte: im.matte,
    }
  }

  /** 把图片推给某节点的下游结果卡（结果卡复用现有渲染，不另建一套）。
   *  提示词保持原样：抠图只是换了一张图，上游是生成图时提示词仍然有意义。 */
  const pushDownstreamResult = (
    nodeId: string, images: ResultImage[], metaPatch: Partial<ResultMeta>,
  ) => {
    setProject((p) => {
      if (!p) return p
      const nodes = [...p.nodes]
      for (const e of p.edges.filter((x) => x.from === nodeId)) {
        const i = nodes.findIndex((n) => n.id === e.to && n.type === 'result')
        if (i < 0) continue
        const t = nodes[i] as ResultNodeType
        nodes[i] = { ...t, images, meta: { ...(t.meta || {}), ...metaPatch } }
      }
      return { ...p, nodes }
    })
  }

  const applyMatteResult = (node: MatteNodeType, images: ResultImage[]) => {
    if (!images.length) return
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, {
      pixelEnabled: !!images[0].refine,
      matteMethod: images[0].matte?.method,
    })
  }

  const runMatte = async (node: MatteNodeType) => {
    setMatting((s) => ({ ...s, [node.id]: true }))
    try {
      const result = await executeMatte(node)
      applyMatteResult(node, [result])
    } catch (e: any) {
      alert(`扣背景失败：${e.message || e}`)
    } finally {
      setMatting((s) => ({ ...s, [node.id]: false }))
    }
  }

  /** 收集从 startId 出发、首尾相接的一串扣背景节点（含被关掉的：
   *  关掉的那个要「透传」而不是把链路断在这里）。 */
  const matteChain = (startId: string): MatteNodeType[] => {
    const p = projectRef.current
    if (!p) return []
    const out: MatteNodeType[] = []
    const seen = new Set<string>()
    let fromId = startId
    for (;;) {
      const next = p.nodes.find((n): n is MatteNodeType =>
        n.type === 'matte' && !seen.has(n.id)
        && p.edges.some((e) => e.from === fromId && e.to === n.id))
      if (!next) break
      seen.add(next.id)
      out.push(next)
      fromId = next.id
    }
    return out
  }

  // ---------- 角色四视图 / 八方向行走 ----------
  /** 节点的产出图，外加它在磁盘上的位置（images/ 产出还是 refs/ 上传）。
   *  runtime 给定时优先用它——一键工作流里上游刚跑完，React 状态还没刷新。
   *  meta 是八方向链的结构化产出（walk / sequence / sheet），下游节点靠它
   *  知道「哪张图属于哪个方向」。 */
  const outputOf = (
    id: string, runtime?: Map<string, ResultImage[]>,
  ): { images: ResultImage[]; kind: 'images' | 'refs'; file: string;
        meta?: any } | null => {
    const p = projectRef.current
    const n = p?.nodes.find((x) => x.id === id)
    if (!n) return null
    if (runtime) {
      const r = runtime.get(id)
      if (r?.length) {
        const meta = (n.type === 'walkvideo' || n.type === 'sequence'
          || n.type === 'spritesheet') ? n.meta : undefined
        return { images: r, kind: 'images', file: '', meta }
      }
    }
    if (n.type === 'generate') {
      return n.lastImages?.length ? { images: n.lastImages, kind: 'images', file: '' } : null
    }
    if (n.type === 'result') {
      return n.images?.length ? { images: n.images, kind: 'images', file: '' } : null
    }
    // 视角节点：多张候选全给下游（拼板逐格挑图 / 按组对齐都靠这个列表）。
    // 「生成图 → 侧视图 → 背视图」这条链上，背视图要的是正视图而不是侧视图，
    // 上游取图由 resolveFrontSource 绕过视角节点，这里只影响下游。
    if (n.type === 'viewgen') {
      const images = n.images?.length ? n.images : (n.result ? [n.result] : [])
      return images.length ? { images, kind: 'images', file: '' } : null
    }
    if (n.type === 'input') {
      const r0 = n.refs?.[0]
      if (!r0) return null
      return {
        images: [{ assetId: r0.id, url: r0.url, width: r0.width, height: r0.height,
                   origAssetId: r0.id, origUrl: r0.url }],
        kind: 'refs', file: r0.file,
      }
    }
    // 八方向链的四个节点：多图产出 + 结构化 meta
    if (n.type === 'walkvideo' || n.type === 'sequence' || n.type === 'spritesheet') {
      const images = (n.images?.length ? n.images : (n.result ? [n.result] : []))
      // 有 meta 就算有产出：存成模板 / 重新加载后 images 会被清空，
      // 但方向与帧索引都在 meta 里——可操控预览节点只靠 meta 就能播。
      if (!images.length && !n.meta) return null
      return { images, kind: 'images', file: '', meta: n.meta ?? undefined }
    }
    return n.result ? { images: [n.result], kind: 'images', file: '' } : null
  }

  /** 视角生成节点的参考图：沿链条回溯到最上游的「正视图」。
   *  中间隔着视角节点要穿过去——「生成图 → 侧视图 → 背视图」这条直线里，
   *  背视图不能拿侧视图当参考；精修 / 抠图这类透传节点取它们的结果。
   *  同时返回源节点：侧/背/右要复用正视图（生成卡）的画风/尺寸/背景等参数。 */
  const resolveFrontSource = (node: CanvasNode, runtime?: Map<string, ResultImage[]>) => {
    const p = projectRef.current
    if (!p) throw new Error('项目未加载')
    const seen = new Set<string>([node.id])
    const queue = [node.id]
    while (queue.length) {
      const id = queue.shift() as string
      for (const e of p.edges.filter((x) => x.to === id)) {
        if (seen.has(e.from)) continue
        seen.add(e.from)
        const src = p.nodes.find((n) => n.id === e.from)
        if (!src) continue
        if (src.type !== 'viewgen') {
          const out = outputOf(src.id, runtime)
          if (out?.images.length) return { out, src }
        }
        queue.push(src.id)
      }
    }
    throw new Error('找不到正视图：请把「生成图」或「选择图片」连到链条最上游，并确认它已经跑出结果。')
  }

  const resolveFrontOutput = (node: CanvasNode, runtime?: Map<string, ResultImage[]>) =>
    resolveFrontSource(node, runtime).out

  /** 节点的视角角色：视角节点看自己的 view，生成/选图就是正视图，
   *  其余（精修/抠图/结果）透传——角色由它自己的上游决定。
   *  path 只做环检测，不能跨分支共享，否则菱形连线会误判。 */
  const roleOf = (
    id: string, path: Set<string> = new Set(),
  ): 'front' | 'side' | 'back' | 'side_r' | null => {
    if (path.has(id)) return null
    const p = projectRef.current
    const n = p?.nodes.find((x) => x.id === id)
    if (!n) return null
    if (n.type === 'viewgen') {
      const vw = n.viewgen?.view
      return vw === 'back' ? 'back' : vw === 'side_r' ? 'side_r' : 'side'
    }
    if (n.type === 'generate' || n.type === 'input') return 'front'
    const next = new Set(path)
    next.add(id)
    for (const e of (p?.edges || []).filter((x) => x.to === id)) {
      const r = roleOf(e.from, next)
      if (r) return r
    }
    return null
  }

  /** 收齐正/侧/背/右四路输入：先看连进来的入边，缺的沿链条回溯补。
   *  「生成图 → 侧视图 → 背视图 → 拼板」这条直线不用额外连线也能收齐三张。
   *  同时记下每张是从哪个节点收的，UI 上给个来源说明。 */
  const collectThreeViewOutputs = (
    node: ThreeViewNodeType, runtime?: Map<string, ResultImage[]>,
  ) => {
    const p = projectRef.current
    type Hit = { images: ResultImage[]; kind: 'images' | 'refs'; file: string; from: string }
    const found: Partial<Record<'front' | 'side' | 'back' | 'side_r', Hit>> = {}
    if (!p) return found
    const seen = new Set<string>([node.id])
    const queue = [node.id]
    while (queue.length) {
      const id = queue.shift() as string
      for (const e of p.edges.filter((x) => x.to === id)) {
        if (seen.has(e.from)) continue
        seen.add(e.from)
        const src = p.nodes.find((n) => n.id === e.from)
        if (!src) continue
        const role = roleOf(src.id)
        if (role && !found[role]) {
          const out = outputOf(src.id, runtime)
          if (out?.images.length) {
            found[role] = { ...out, from: nodeLabel(src.type) }
          }
        }
        queue.push(src.id)
      }
    }
    return found
  }

  /** 跑一个后端任务并等结果（SSE 进度 + 完成后回调），供视角生成用。 */
  const awaitTask = <T,>(taskId: string, nodeId: string): Promise<T> =>
    new Promise<T>((resolve, reject) => {
      let settled = false
      const close = subscribeTask(
        taskId,
        (p) => setTasks((t) => ({
          ...t, [nodeId]: { ...(t[nodeId] || { taskId, error: null }), progress: p },
        })),
        (result, prog) => {
          if (settled) return
          settled = true
          close()
          setTasks((t) => ({
            ...t,
            [nodeId]: { taskId, progress: prog, error: prog.error || result?.error || null },
          }))
          if (result && !result.error) resolve(result as unknown as T)
          else reject(new Error(prog.error || result?.error || (prog.cancelled ? '已取消' : '任务失败')))
        },
      )
      closers.current[nodeId] = close
    })

  const executeViewDerive = async (
    node: ViewGenNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<ResultImage[]> => {
    if (!project) throw new Error('项目未加载')
    const { out: srcOut, src: srcNode } = resolveFrontSource(node, runtime)
    const im = srcOut.images[0]
    // 抽卡：正视图有几张就接力几张（每张各出一张本视角），结果按 pairIndex
    // 与源图对齐。以前只取 images[0]，抽 4 张里最好看的那张永远进不了四视图。
    const candidates = srcOut.images.slice(0, 8)
    // 参数复用：侧/背/右只认正视图生成卡（画风/尺寸/步数/种子/质量/背景），
    // 用户只在生成卡上填一次。上游不是生成卡时（选择图片等直接喂图）没有参数
    // 可继承：尺寸跟随参考图、其余走默认，不再留节点级参数框。
    const gen = srcNode.type === 'generate' ? (srcNode as GenNodeType) : null
    const gp = gen?.params
    const styleId = gen ? gen.styleId : null
    const width = gp?.width ?? (im.width || 512)
    const height = gp?.height ?? (im.height || 768)
    const steps = gp?.steps ?? null
    const seed = gp?.seed ?? null
    const maxSeqLen = gp?.maxSeqLen ?? null
    // 背景必须跟正视图一致：拼板按各张底色抠图，底色不同就会咬出异色边
    const background = gp?.background ?? 'gray'
    // 角色描述跟着正视图走：视角指令把它当部件清单，腰挎的刀/背上的弓这类
    // 细长配件不会在重画身体时被漏掉（上游是选图节点时没有可继承的描述）
    const character = gen?.prompt?.trim() ?? ''
    const { taskId } = await api.threeviewDerive({
      projectId: project.id,
      origAssetId: srcOut.kind === 'images' ? im.assetId : '',
      sourceKind: srcOut.kind, sourceFile: srcOut.kind === 'refs' ? srcOut.file : undefined,
      view: (() => {
        const vw = node.viewgen?.view
        return vw === 'back' ? 'back' : vw === 'side_r' ? 'side_r' : 'side'
      })(),
      styleId,
      steps,
      seed,
      width,
      height,
      maxSeqLen,
      background,
      character,
      // 卡片上改过的视角指令正文（空 = 后端那套默认条款）。
      // 用户看不到也改不了的东西不该存在——这段以前整段硬编码在 threeview。
      instruction: (node.viewgen?.instruction || '').trim(),
      negative: (node.viewgen?.negative || '').trim(),
      // 多源：refs 只能整批一张（上传的一张图），images 逐张传
      sources: candidates.map((c, i) => (srcOut.kind === 'images'
        ? { kind: 'images' as const, file: c.assetId }
        : i === 0 ? { kind: 'refs' as const, file: srcOut.file } : null))
        .filter((x): x is { kind: 'images' | 'refs'; file: string } => !!x),
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const outs: any[] = r.images?.length ? r.images : (r.image ? [r.image] : [])
    const metas: any[] = r.meta?.batch ?? [r.meta]
    return outs.map((out: any, i: number) => {
      const src = candidates[out.pairIndex ?? i] ?? im
      return {
        assetId: out.assetId, url: out.url, width: out.width, height: out.height,
        origAssetId: src.assetId, origUrl: src.url, origSize: [src.width, src.height],
        view: r.view,
        viewMeta: metas[out.pairIndex ?? i] ?? metas[0],
        pairIndex: out.pairIndex ?? i,
        srcAssetId: out.srcAssetId ?? src.assetId,
      } as ResultImage
    })
  }

  const applyViewGenResult = (node: ViewGenNodeType, results: ResultImage[]) => {
    const pick = Math.min(node.viewgen?.pick ?? 0, Math.max(0, results.length - 1))
    const shown = results[pick] ?? results[0] ?? null
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: shown, images: results } as CanvasNode) : n),
    } : p)
    // 下游拿到全部候选：拼板逐格挑图要用整列，行走视频取第一张当锚点
    pushDownstreamResult(node.id, results, {})
    // 链条末端（没有下游）才补一张结果卡：中间节点补卡会让四视图链上多出废卡
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, results, {})
    }
  }

  const runViewDerive = async (node: ViewGenNodeType) => {
    if (!node.viewgen?.enabled) return
    if (isRunning(node.id)) return          // 在途时忽略重复点击
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const result = await executeViewDerive(node)
      applyViewGenResult(node, result)
    } catch (e: any) {
      alert(`生成视角失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const executeCompose = async (
    node: ThreeViewNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<ResultImage> => {
    if (!project) throw new Error('项目未加载')
    const found = collectThreeViewOutputs(node, runtime)
    // 四视图：正/侧/背必需，右视图可选（缺了按三视图拼板，向后兼容旧项目）
    const order = ['front', 'side', 'back', 'side_r'] as const
    const required = ['front', 'side', 'back'] as const
    const missing = required.filter((k) => !found[k])
    if (missing.length) {
      const cn: Record<string, string> = {
        front: '正视图', side: '侧视图', back: '背视图', side_r: '右视图',
      }
      throw new Error(`四视图拼板还缺：${missing.map((m) => cn[m]).join('、')}。`
        + '把对应节点的输出连到本节点左侧，或保证链条上能回溯到它。')
    }
    const t = node.threeview
    // 逐视角选图：picks[view] 是用户在拼板卡上点选的那张 assetId；
    // 没选过（或选中的图已经不在了）就按 pairIndex 取第一组。
    const picks = t?.picks || {}
    // 与 threeViewSlots 用同一套解析：缩略图上看到哪张，拼进去的就是哪张
    const frontPair = resolveViewPick('front', found.front?.images ?? [], picks, 0)
      .image?.pairIndex ?? 0
    const pickOf = (k: 'front' | 'side' | 'back' | 'side_r', s: { images: ResultImage[] }) =>
      resolveViewPick(k, s.images, picks, frontPair).image ?? s.images[0]
    const r = await api.threeviewCompose({
      projectId: project.id,
      sources: order.filter((k) => found[k]).map((k) => {
        const s = found[k]!
        if (s.kind !== 'images') return { kind: s.kind, file: s.file }
        return { kind: s.kind, file: pickOf(k, s).assetId }
      }),
      bg: t?.bg || '1B4332',
      transparentBg: !!t?.transparentBg,
      gap: t?.gap ?? 12,
      baseline: t?.baseline ?? 0.93,
      cell: t?.cell ?? null,
    })
    const im: any = r.image
    const first = found.front!.images[0]
    return {
      assetId: im.assetId, url: im.url, width: im.width, height: im.height,
      origAssetId: first.assetId, origUrl: first.url,
      origSize: [first.width, first.height],
      threeview: im.threeview,
    }
  }

  const applyComposeResult = (node: ThreeViewNodeType, result: ResultImage) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, [result], {})
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, [result], {})
    }
  }

  /** 给某个节点的产出确保有一张结果卡：已经连了就填，没连就新建一张挂在右边。 */
  const ensureResultCard = (
    node: CanvasNode, images: ResultImage[], meta: ResultMeta = {},
  ) => {
    setProject((p) => {
      if (!p || !images.length) return p
      const nodes = [...p.nodes]
      for (const e of p.edges.filter((x) => x.from === node.id)) {
        const i = nodes.findIndex((n) => n.id === e.to && n.type === 'result')
        if (i < 0) continue
        const t = nodes[i] as ResultNodeType
        nodes[i] = { ...t, images, meta: { ...(t.meta || {}), ...meta } }
        return { ...p, nodes }
      }
      const w = NODE_W[node.type] || 360
      const res: ResultNodeType = {
        id: newId('res'), type: 'result',
        x: node.x + w + 96, y: node.y,
        genId: null, images, prompt: '', negative: '', meta,
        createdAt: Date.now() / 1000,
      }
      return { ...p, nodes: [...nodes, res], edges: [...p.edges, { from: node.id, to: res.id }] }
    })
  }

  const runCompose = async (node: ThreeViewNodeType) => {
    if (!node.threeview?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const result = await executeCompose(node)
      applyComposeResult(node, result)
    } catch (e: any) {
      alert(`四视图拼板失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  /** 每个视角最终用哪张：先认用户点选的 assetId，认不到（重跑后 assetId 变了）
   *  就按 pairIndex 对齐到正视图那一组，最后才回落第一张。
   *  只按 assetId 会在重跑后静默退回第 1 组——用户挑的「第 3 组整套」白挑了。 */
  const resolveViewPick = (
    k: 'front' | 'side' | 'back' | 'side_r',
    images: ResultImage[],
    picks: Partial<Record<'front' | 'side' | 'back' | 'side_r', string>>,
    frontPair: number,
  ): { image: ResultImage | null, stale: boolean } => {
    if (!images.length) return { image: null, stale: false }
    const want = picks[k]
    const hit = want ? images.find((i) => i.assetId === want) : undefined
    if (hit) return { image: hit, stale: false }
    const byPair = images.find((im, i) => (im.pairIndex ?? i) === frontPair)
    return { image: byPair ?? images[0], stale: !!want }
  }

  /** 拼板节点要显示的四路输入（缺的也会列出来，让用户知道缺谁；
   *  右视图缺失不阻塞运行——按三视图拼板）。 */
  const threeViewSlots = (node: ThreeViewNodeType) => {
    const found = collectThreeViewOutputs(node)
    const cn: Record<string, string> = {
      front: '正视图', side: '侧视图', back: '背视图', side_r: '右视图',
    }
    const picks = node.threeview?.picks || {}
    const frontImgs = found.front?.images ?? []
    const frontPick = resolveViewPick('front', frontImgs, picks, 0)
    const frontPair = frontPick.image?.pairIndex ?? 0
    return (['front', 'side', 'back', 'side_r'] as const).map((k) => {
      const images = found[k]?.images ?? []
      const { image, stale } = resolveViewPick(k, images, picks, frontPair)
      return {
        view: k, label: cn[k],
        image,
        // 全部候选：拼板卡上用缩略图行让用户逐视角挑图（抽卡后挑同一组）
        images,
        from: found[k]?.from ?? '',
        /** 当前选中的是第几组（pairIndex）：一键「按第 k 组」用它对齐 */
        pair: image?.pairIndex ?? 0,
        /** 之前选中的那张已经不在候选里（重跑过），已按组自动重选 */
        stale,
      }
    })
  }

  // ---------- 八方向行走链 ----------
  /** 直线链节点（行走视频/序列帧/精灵整图/可操控预览）的唯一上游。
   *  metaRuntime 里放着一键工作流刚跑出来的结构化产出——上游节点的
   *  setProject 还没刷进 projectRef，不能直接读节点状态。 */
  const resolveChainInput = (
    node: CanvasNode, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
    opts?: { requireImages?: boolean },
  ) => {
    const p = projectRef.current
    const inEdge = p?.edges.find((e) => e.to === node.id)
    if (!inEdge) {
      throw new Error(`「${nodeLabel(node.type)}」没有上游：把上一步连到左侧端口`)
    }
    const out = outputOf(inEdge.from, runtime)
    const src = p?.nodes.find((n) => n.id === inEdge.from)
    // 可操控预览只吃元数据（精灵表 URL + 帧索引），上游图片被清空也照样能播；
    // 其余链路节点确实要图，缺图就是没跑过上游。
    // 上游是「选择图片」时不能说「去跑上一步」——它没有可跑的东西，
    // 用户要做的是上传一张图（八方向模板的入口就是它）。
    if (opts?.requireImages !== false && !out?.images.length) {
      throw new Error(src?.type === 'input'
        ? '上游「选择图片」还没有图：先在节点里上传一张角色图'
        : '上游还没有产出：先跑上一步的节点')
    }
    return {
      out: out ?? { images: [], kind: 'images' as const, file: '', meta: null },
      fromId: inEdge.from,
      label: src ? nodeLabel(src.type) : '',
      meta: metaRuntime?.get(inEdge.from) ?? out?.meta ?? (src as any)?.meta,
    }
  }

  const executeWalkVideo = async (
    node: WalkVideoNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    if (!project) throw new Error('项目未加载')
    const src = resolveFrontOutput(node, runtime)
    const im = src.images[0]
    const w = node.walkvideo
    // 四视图锚点：沿链条回溯收集正/侧/背/右——有了它们，四个方向零推导、
    // 斜向只转 45 度，角色一致性明显更好
    const tv = collectThreeViewOutputs(node as any, runtime)
    const viewAssets: { role: string; file: string }[] = []
    for (const role of ['front', 'side', 'back', 'side_r'] as const) {
      const hit = tv[role]
      if (!hit || hit.kind !== 'images' || !hit.images.length) continue
      viewAssets.push({ role, file: `${hit.images[0].assetId}.png` })
    }
    const { taskId } = await api.walkVideo({
      projectId: project.id,
      origAssetId: src.kind === 'images' ? im.assetId : '',
      sourceKind: src.kind, sourceFile: src.kind === 'refs' ? src.file : undefined,
      // 四视图里没有正面时（例如上游是选图节点）退回单张正视图：
      // viewAssets 为空 → 后端按老链路从正视图推导
      viewAssets,
      dirImages: Object.entries(w?.dirImages || {})
        .filter(([, f]) => !!f)
        .map(([dir, file]) => ({ dir, file: file as string })),
      pixelArt: !!w?.pixelArt,
      dirMode: w?.dirMode ?? 'eight',
      action: w?.action ?? '行走',
      // 整段提示词：用户完整输入，程序不拼接（为空时后端才退回 action 模板）
      prompt: w?.prompt || '',
      // 自定义负面词（空 = 后端默认常量）
      negative: (w?.negative || '').trim(),
      styleId: w?.styleId ?? null,
      // 视频引擎：默认 hy15（HunyuanVideo-1.5），用户可在节点上切 wan
      engine: w?.engine ?? 'hy15',
      // hy15 分窗阈值：0=关闭（整段生成，长片会 OOM）；>0 且帧数更大时链式分窗
      // （仅 hy15 生效）。默认 41：段数越少漂移越小，见 lib/hy15Defaults 的依据
      window: w?.window ?? HY15_DEFAULT_WINDOW,
      steps: w?.steps ?? null,
      seed: w?.seed ?? null,
      width: w?.width ?? 384,
      height: w?.height ?? 512,
      numFrames: w?.numFrames ?? 21,
      fps: w?.fps ?? 12,
      maxSide: w?.maxSide ?? 512,
      maxSeqLen: w?.maxSeqLen ?? null,
      // 现成视频来源：这些方向跳过推导 + Wan，直接用文件里的帧
      sources: Object.entries(w?.sources || {})
        .filter(([, f]) => !!f)
        .map(([dir, file]) => ({ dir, file: file as string })),
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const meta: any = r.walk || {}
    const images: ResultImage[] = (r.images || []).map((o: any, i: number) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
      origAssetId: im.assetId, origUrl: im.url, origSize: [im.width, im.height],
      walkDir: o.walkDir ?? meta.directions?.[i]?.dir,
    }))
    return { images, meta }
  }

  const applyWalkVideoResult = (
    node: WalkVideoNodeType, images: ResultImage[], meta: any,
  ) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { walk: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { walk: meta })
    }
  }

  const runWalkVideo = async (node: WalkVideoNodeType) => {
    if (!node.walkvideo?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { images, meta } = await executeWalkVideo(node)
      applyWalkVideoResult(node, images, meta)
    } catch (e: any) {
      alert(`行走视频失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const executeSequence = async (
    node: SequenceNodeType, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    if (!project) throw new Error('项目未加载')
    const up = resolveChainInput(node, runtime, metaRuntime)
    const s = node.sequence
    // 方向清单：优先用上游结构化 meta（GIF 文件名），退回按图上的 walkDir 分组
    let directions: { dir: string; gif: string }[] = []
    const metaDirs: any[] = up.meta?.directions || []
    if (metaDirs.length && metaDirs[0]?.gif?.url) {
      directions = metaDirs.map((d) => ({
        dir: d.dir, gif: `${d.gif.assetId}.gif`,
      }))
    } else {
      for (const im of up.out.images) {
        if (im.walkDir) directions.push({ dir: im.walkDir, gif: `${im.assetId}.gif` })
      }
    }
    if (!directions.length) {
      throw new Error('上游没有方向信息：先跑「行走视频」节点')
    }
    const { taskId } = await api.sequence({
      projectId: project.id,
      directions,
      framesPerDir: s?.framesPerDir ?? 8,
      fps: s?.fps ?? 8,
      matte: s?.matte ?? true,
      tolerance: s?.tolerance ?? 40,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const meta: any = r.sequence || {}
    const images: ResultImage[] = (r.images || []).map((o: any) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
      origAssetId: up.out.images[0]?.assetId || '', origUrl: up.out.images[0]?.url || '',
      walkDir: o.walkDir,
    }))
    return { images, meta }
  }

  const applySequenceResult = (
    node: SequenceNodeType, images: ResultImage[], meta: any,
  ) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { sequence: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { sequence: meta })
    }
  }

  const runSequence = async (node: SequenceNodeType) => {
    if (!node.sequence?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { images, meta } = await executeSequence(node)
      applySequenceResult(node, images, meta)
    } catch (e: any) {
      alert(`序列帧失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }


  // ---------- 视频转素材：选择视频 → 拆帧选帧 → 精灵图 → 动画预览 ----------
  /** 沿入边找上游「选择视频」节点的文件名。 */
  const upstreamClipFile = (nodeId: string): string | null => {
    const p = projectRef.current
    const e = p?.edges.find((x) => x.to === nodeId)
    const s = e ? p?.nodes.find((n) => n.id === e.from) : null
    if (s && s.type === 'clipsrc') return (s as ClipSrcNodeType).clipsrc?.file || null
    return null
  }

  const executeClipSplit = async (
    node: FrameSplitNodeType,
  ): Promise<{ frames: any[]; source: any; meta: FrameSplitMeta }> => {
    if (!project) throw new Error('项目未加载')
    const file = upstreamClipFile(node.id) || node.source?.file || ''
    if (!file) {
      throw new Error('没有视频源：先把「选择视频 / GIF」连到这个节点的左侧端口')
    }
    const fs = node.framesplit
    const { taskId } = await api.clipSplit({
      projectId: project.id,
      file,
      fpsCap: fs?.fpsCap ?? 12,
      maxFrames: fs?.maxFrames ?? 120,
      start: fs?.start ?? 0,
      end: fs?.end ?? null,
      matte: fs?.matte ?? true,
      tolerance: fs?.tolerance ?? 40,
      matteModel: fs?.matteModel ?? 'auto',
      loopClose: fs?.loopClose ?? true,
      maxSide: fs?.maxSide ?? 384,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const clip: any = r.clip || {}
    return {
      frames: clip.frames || [],
      source: clip.source ?? null,
      meta: (clip.meta || {}) as FrameSplitMeta,
    }
  }

  const runFrameSplit = async (node: FrameSplitNodeType) => {
    if (!node.framesplit?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { frames, source, meta } = await executeClipSplit(node)
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.map((n) => n.id === node.id ? ({
          ...n,
          frames, source, meta,
          // 重拆之后文件名全变了（旧产物被清理），旧的 picks 必然失效，
          // 直接全选——比留一堆指向不存在文件的路径好排查
          framesplit: { ...node.framesplit, picks: frames.map((f: any) => f.file) },
        } as CanvasNode) : n),
      } : p)
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  /** 上游拆帧节点里「已选」的帧文件，按帧序排列。 */
  const upstreamPicks = (nodeId: string): { files: string[]; fps: number } | null => {
    const p = projectRef.current
    const e = p?.edges.find((x) => x.to === nodeId)
    const s = e ? p?.nodes.find((n) => n.id === e.from) : null
    if (!s || s.type !== 'framesplit') return null
    const fs = s as FrameSplitNodeType
    const frames = fs.frames || []
    const picks = new Set(fs.framesplit?.picks || [])
    // 只保留「既有勾选、又真的还在」的帧：用户换素材后旧的 picks 会指向
    // 已删除的文件，直接下发会让后端整批读不出来
    const files = frames.filter((f) => picks.has(f.file)).map((f) => f.file)
    return { files, fps: fs.meta?.effFps || 12 }
  }

  const executeSpriteGrid = async (
    node: SpriteGridNodeType,
  ): Promise<{ sheet: ResultImage; index: any; meta: SpriteGridMeta; indexUrl: string }> => {
    if (!project) throw new Error('项目未加载')
    const up = upstreamPicks(node.id)
    if (!up) throw new Error('上游没有「拆解帧」节点：先把挑好帧的节点连到左侧端口')
    if (!up.files.length) throw new Error('上游还没有勾选任何帧：在「拆解帧」里选几张')
    const sg = node.spritegrid
    const r = await api.spriteGrid({
      projectId: project.id,
      files: up.files,
      cell: sg?.cell ?? 128,
      cols: sg?.cols ?? 0,
      padding: sg?.padding ?? 0,
      margin: sg?.margin ?? 0,
      anchor: sg?.anchor ?? 'bottom',
      sharedScale: sg?.sharedScale ?? true,
      pivot: sg?.pivot ?? 'shared',
      fps: sg?.fps ?? 12,
    })
    const sheet: ResultImage = {
      assetId: r.sheet.id, url: r.sheet.url,
      width: r.sheet.width, height: r.sheet.height,
      origAssetId: r.sheet.id, origUrl: r.sheet.url,
    }
    return { sheet, index: r.index, meta: r.meta, indexUrl: r.indexUrl }
  }

  const runSpriteGrid = async (node: SpriteGridNodeType) => {
    if (!node.spritegrid?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { sheet, index, meta, indexUrl } = await executeSpriteGrid(node)
      // 把精灵图的播放说明推给下游「动画预览」：那个节点是纯前端播放器，
      // 自己不请求接口，全靠这里把 url + 帧坐标塞进去
      const play = {
        url: sheet.url,
        cols: index.grid?.cols ?? meta.cols,
        rows: index.grid?.rows ?? meta.rows,
        count: meta.count,
        frameSize: { w: meta.cell, h: meta.cell },
        frames: index.frames || [],
        fps: node.spritegrid?.fps ?? 12,
        pivot: index.pivot || meta.pivot,
        indexUrl,
      }
      setProject((p) => {
        if (!p) return p
        const nodes = p.nodes.map((n) => {
          if (n.id === node.id) {
            return {
              ...n, result: sheet, images: [sheet], meta, indexUrl,
            } as CanvasNode
          }
          // 直接下游的动画预览节点：写入播放载荷（帧率沿用它的设置，
          // 但换了新精灵图时把帧数/坐标刷新掉）
          if (n.type === 'animpreview'
            && p.edges.some((e) => e.from === node.id && e.to === n.id)) {
            return { ...n, meta: play } as CanvasNode
          }
          return n
        })
        return { ...p, nodes }
      })
      pushDownstreamResult(node.id, [sheet], {
        spriteGrid: meta as any,
        sheetUrl: sheet.url,
        indexUrl,
      } as any)
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  // ---- 素材链第 5 步：精灵图 → Godot 4 工程包（纯文本生成，同步） ----
  /** 沿入边向上找最近的「精灵图」节点。
   *  中间隔着「动画预览」（纯前端播放器、不产图）时要能穿过去：模板就是
   *  选择视频 → 拆解帧 → 精灵图 → 动画预览 → Godot 导出 这条直线。 */
  const upstreamGrid = (nodeId: string) => {
    const p = projectRef.current
    if (!p) return null
    const seen = new Set<string>([nodeId])
    let cur = nodeId
    for (let depth = 0; depth < 6; depth++) {
      const e = p.edges.find((x) => x.to === cur)
      if (!e || seen.has(e.from)) return null
      seen.add(e.from)
      const n = p.nodes.find((x) => x.id === e.from)
      if (!n) return null
      if (n.type === 'spritegrid') {
        const g = n as SpriteGridNodeType
        const sheet = g.result ?? (g.images ?? [])[0] ?? null
        const meta = g.meta
        if (!sheet || !meta?.count) {
          return { ready: false as const, count: 0, cell: 0, cols: 0 }
        }
        return {
          ready: true as const,
          sheetFile: `${sheet.assetId}.png`,
          count: meta.count, cell: meta.cell, cols: meta.cols,
          padding: meta.padding, margin: meta.margin, anchor: meta.anchor,
          fps: meta.fps,
        }
      }
      if (n.type !== 'animpreview') return null
      cur = n.id
    }
    return null
  }

  const runGodotExport = async (node: Godot2DNodeType) => {
    if (!node.godot2d?.enabled) return
    if (isRunning(node.id)) return
    const up = upstreamGrid(node.id)
    if (!up) {
      alert('左上角没有「精灵图」节点：把「精灵图」（或「动画预览」）连到它的输入端口')
      return
    }
    if (!up.ready) {
      alert('上游还没有精灵图：先在「精灵图」节点上点一次「生成精灵图」')
      return
    }
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const o = node.godot2d
      const r = await api.godotExport({
        projectId: project!.id,
        sheetFile: up.sheetFile,
        cell: up.cell, cols: up.cols, count: up.count,
        padding: up.padding, margin: up.margin,
        fps: o.fps, animName: o.animName, name: o.name, loop: o.loop,
        mode: o.mode, folder: o.folder, origin: o.origin,
        textureFilter: o.textureFilter,
      })
      // 落盘成一份自包含清单：刷新页面后 zip / tres / tscn 的下载链接还在
      const meta: Godot2DMeta = {
        ...r.meta, stem: r.stem, folder: r.folder,
        files: r.files, zip: r.zip, resPaths: r.resPaths,
      }
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.map((n) => n.id === node.id
          ? ({ ...n, meta } as CanvasNode) : n),
      } : p)
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  // ---- 局部重绘：涂抹蒙版 + 提示词 → 只重画选区（任务制） ----
  /** 上游源图：比 resolveUpstreamSource 多带一个「能显示/能涂抹的 url」。 */
  const upstreamImage = (node: CanvasNode) => {
    const inEdge = project?.edges.find((e) => e.to === node.id)
    const up = inEdge ? project?.nodes.find((n) => n.id === inEdge.from) : null
    try {
      const src = resolveUpstreamSource(node)
      return {
        url: src.url, width: src.w || 0, height: src.h || 0,
        // kind/file 是给后端定位文件的：images 目录里文件名 = assetId.png，
        // 所以 assetId 也能被 _load_source 认出来（它会补 .png 再试一次）
        kind: src.kind, file: src.file || src.assetId,
        label: up ? nodeLabel(up.type) : '上游',
      }
    } catch {
      return null
    }
  }

  const runInpaint = async (node: InpaintNodeType) => {
    if (!node.inpaint?.enabled) return
    if (isRunning(node.id)) return
    const up = upstreamImage(node)
    if (!up) {
      alert('没有可用的源图：把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到它的输入端口')
      return
    }
    if (!node.inpaint.maskFile) {
      alert('还没有涂抹区域：先点「涂抹区域」把要改的地方圈出来')
      return
    }
    if (!node.inpaint.instruction.trim()) {
      alert('提示词是空的：先写一句「这块要变成什么」')
      return
    }
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const o = node.inpaint
      const { taskId } = await api.inpaint({
        projectId: project!.id,
        sourceKind: up.kind,
        sourceFile: up.file,
        maskFile: o.maskFile,
        instruction: o.instruction,
        constraint: o.constraint,
        negative: o.negative,
        refs: (o.refs || []).map((r) => r.file || r.id),
        mode: o.mode, context: o.context, feather: o.feather, expand: o.expand,
        hard: o.hard, matchColor: o.matchColor, matchStrength: o.matchStrength,
        palette: o.palette, paletteColors: o.paletteColors,
        steps: o.steps, seed: o.seed, maxSeqLen: o.maxSeqLen,
      })
      activeTaskRef.current = taskId
      setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
      const r: any = await awaitTask<any>(taskId, node.id)
      if (activeTaskRef.current === taskId) activeTaskRef.current = null
      const im = r.image || {}
      const result: ResultImage = {
        assetId: im.assetId, url: im.url, width: im.width, height: im.height,
        origAssetId: im.origAssetId || up.file,
        origUrl: im.origUrl || up.url,
        origSize: [up.width, up.height],
      }
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.map((n) => n.id === node.id
          ? ({ ...n, result, images: [result], meta: r.meta } as CanvasNode) : n),
      } : p)
      pushDownstreamResult(node.id, [result], { inpaint: r.meta })
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const applyMask = (nodeId: string, mask: NonNullable<InpaintNodeType['inpaint']['mask']>) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => {
        if (n.id !== nodeId) return n
        // 局部重绘与去除局部共用同一个涂抹编辑器，蒙版回写按节点类型分流
        if (n.type === 'inpaint') {
          const nd = n as InpaintNodeType
          return { ...nd, inpaint: { ...nd.inpaint, maskFile: mask.file, mask } } as CanvasNode
        }
        if (n.type === 'erase') {
          const nd = n as EraseNodeType
          return { ...nd, erase: { ...nd.erase, maskFile: mask.file, mask } } as CanvasNode
        }
        return n
      }),
    } : p)
  }

  // ---- 2.5D 分层：See-Through 子进程，2~3 分钟（任务制） ----
  const runLayer25D = async (node: Layer25DNodeType) => {
    if (!node.layer25d?.enabled) return
    if (isRunning(node.id)) return
    const up = upstreamImage(node)
    if (!up) {
      alert('没有可用的源图：把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到它的输入端口')
      return
    }
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const o = node.layer25d
      const { taskId } = await api.layer25d({
        projectId: project!.id,
        sourceKind: up.kind,
        sourceFile: up.file,
        resolution: o.resolution, steps: o.steps, seed: o.seed,
        lrsplit: o.lrsplit,
      })
      activeTaskRef.current = taskId
      setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
      const r: any = await awaitTask<any>(taskId, node.id)
      if (activeTaskRef.current === taskId) activeTaskRef.current = null
      const meta = r.meta
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.map((n) => n.id === node.id
          ? ({ ...n, meta } as CanvasNode) : n),
      } : p)
      // 合成预览推给下游结果卡：分层本身是「一堆层」，给张叠好的图更好接
      if (meta?.previewUrl) {
        const result: ResultImage = {
          assetId: 'layer25d-preview', url: meta.previewUrl,
          width: meta.frameSize?.[0] || 0, height: meta.frameSize?.[1] || 0,
          origAssetId: up.file, origUrl: up.url,
          origSize: [up.width, up.height],
        }
        pushDownstreamResult(node.id, [result], { layer25d: meta })
      }
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  // ---- 去除局部：非 AI 毫秒级 / AI 几十秒，共用任务制 ----
  const runErase = async (node: EraseNodeType) => {
    if (!node.erase?.enabled) return
    if (isRunning(node.id)) return
    const up = upstreamImage(node)
    if (!up) {
      alert('没有可用的源图：把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到它的输入端口')
      return
    }
    if (!node.erase.maskFile) {
      alert('还没有涂抹区域：先点「涂抹要去掉的东西」把水印/杂物圈出来')
      return
    }
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const o = node.erase
      const { taskId } = await api.erase({
        projectId: project!.id,
        sourceKind: up.kind,
        sourceFile: up.file,
        maskFile: o.maskFile,
        method: o.method, expand: o.expand, feather: o.feather, hard: o.hard,
        matchColor: o.matchColor, matchStrength: o.matchStrength,
        palette: o.palette, paletteColors: o.paletteColors,
        instruction: o.instruction, constraint: o.constraint, negative: o.negative,
        aiMode: o.aiMode, context: o.context,
        steps: o.steps, seed: o.seed, maxSeqLen: o.maxSeqLen,
      })
      activeTaskRef.current = taskId
      setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
      const r: any = await awaitTask<any>(taskId, node.id)
      if (activeTaskRef.current === taskId) activeTaskRef.current = null
      const im = r.image || {}
      const result: ResultImage = {
        assetId: im.assetId, url: im.url, width: im.width, height: im.height,
        origAssetId: im.origAssetId || up.file,
        origUrl: im.origUrl || up.url,
        origSize: [up.width, up.height],
      }
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.map((n) => n.id === node.id
          ? ({ ...n, result, images: [result], meta: r.meta } as CanvasNode) : n),
      } : p)
      pushDownstreamResult(node.id, [result], { erase: r.meta })
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  // ---- 骨骼路线：拆件 → 骨骼行走（纯 CPU，确定性） ----
  const executeParts = async (
    node: PartsNodeType, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    if (!project) throw new Error('项目未加载')
    const up = resolveChainInput(node, runtime, metaRuntime)
    const src = up.out.images[0]
    if (!src) throw new Error('上游没有角色图：先连「选择图片」或「生成图」')
    const pt = node.parts
    const { taskId } = await api.parts({
      projectId: project.id,
      sourceKind: 'images',
      sourceFile: src.assetId,
      resolution: pt?.resolution ?? 1280,
      withHead: pt?.withHead ?? true,
      seed: pt?.seed ?? 7,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const res: any = r.parts || {}
    const pmap: Record<string, any> = res.parts || {}
    const images: ResultImage[] = Object.entries(pmap).map(([role, o]: any) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
      origAssetId: src.assetId, origUrl: src.url, walkDir: role,
    }))
    const meta = {
      parts: pmap,
      weapon: res.weapon || null,
      source: res.source,
      seconds: res.seconds,
    }
    return { images, meta }
  }

  const applyPartsResult = (node: PartsNodeType, images: ResultImage[], meta: any) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { parts: meta })
  }

  const addTxtVideoNode = (at?: { x: number; y: number }) => {
    if (!project) return
    const spot = at ? { x: Math.round(at.x), y: Math.round(at.y) } : canvasSpot(420, 420)
    const node: TxtVideoNodeType = {
      id: newId('tx'), type: 'txtvideo', x: spot.x, y: spot.y,
      txtvideo: {
        enabled: true, prompt: '', negative: '',
        quality: 'standard', resolution: '480p',
        steps: null, seed: null, width: 832, height: 480,
        numFrames: 21, fps: 12, maxSide: 832, maxSeqLen: null,
        pixelArt: false, guidanceScale: null,
        interpolate: false, upscale: false, engine: null, format: 'mp4',
        fitAspect: null,
      },
      result: null, meta: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const runParts = async (node: PartsNodeType) => {
    if (!node.parts?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { images, meta } = await executeParts(node)
      applyPartsResult(node, images, meta)
    } catch (e: any) {
      alert(`拆件失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  // ---------- 文本生视频 ----------
  /** 执行一次文本生视频，返回完整结果（供手动按钮和一键工作流共用）。
   *  参考图可选：有入边取上游第一张，没入边就是纯文本（后端走 Wan T2V）。 */
  const executeTxtVideo = async (
    node: TxtVideoNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<{ images: ResultImage[]; meta: TxtVideoMeta }> => {
    if (!project) throw new Error('项目未加载')
    const inEdge = project.edges.find((e) => e.to === node.id)
    const up = inEdge ? outputOf(inEdge.from, runtime) : null
    const src = up?.images?.[0] || null
    const tv = node.txtvideo
    if (!(tv?.prompt || '').trim()) throw new Error('请先写提示词')
    // 上游可能是「选择图片」节点上传的参考图（refs/），也可能是生成图（images/）。
    // 一律按 images 发，后端就会去 images/ 找一个根本不存在的文件 → 「参考图不存在」。
    // 这里按 URL 里的实际目录判类型，文件名从 URL 末段取。
    const srcUrl = src?.url || ''
    const srcIsRef = srcUrl.includes('/refs/')
    const srcName = srcUrl.split('/').pop() || ''
    const { taskId } = await api.txtvideo({
      projectId: project.id,
      origAssetId: srcIsRef ? '' : (src?.assetId || ''),
      sourceKind: srcIsRef ? 'refs' : 'images',
      sourceFile: srcIsRef ? srcName : undefined,
      prompt: (tv?.prompt || '').trim(),
      negative: tv?.negative || '',
      steps: tv?.steps ?? null,
      seed: tv?.seed ?? null,
      width: tv?.width ?? 832,
      height: tv?.height ?? 480,
      numFrames: tv?.numFrames ?? 21,
      fps: tv?.fps ?? 12,
      maxSide: tv?.maxSide ?? 832,
      maxSeqLen: tv?.maxSeqLen ?? null,
      pixelArt: !!tv?.pixelArt,
      guidanceScale: tv?.guidanceScale ?? null,
      interpolate: !!tv?.interpolate,
      upscale: !!tv?.upscale,
      format: tv?.format === 'gif' ? 'gif' : 'mp4',
      engine: tv?.engine ?? null,
      // 画布跟随参考图宽高比（null=跟随）。方形立绘塞 16:9 会让角色只占中间一条
      fitAspect: tv?.fitAspect ?? null,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const images: ResultImage[] = (r.images || []).map((o: any) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
      origAssetId: src?.assetId, origUrl: src?.url,
    }))
    return { images, meta: (r.video || {}) as TxtVideoMeta }
  }

  const applyTxtVideoResult = (
    node: TxtVideoNodeType, images: ResultImage[], meta: TxtVideoMeta,
  ) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    // 结果视频要推到下游结果卡（和 walkvideo 同一套）：只 set 节点自己的
    // result 是不够的——用户在一键工作流里就看不到产出。
    pushDownstreamResult(node.id, images, { video: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { video: meta })
    }
  }

  const runTxtVideo = async (node: TxtVideoNodeType) => {
    if (!node.txtvideo?.enabled) return
    // 先置忙：第一条 SSE 可能要等几秒（模型加载阶段），
    // 这期间按钮/边框必须已经是「运行中」，否则用户以为点了没反应。
    markBusy(node.id, true)
    try {
      const { images, meta } = await executeTxtVideo(node)
      applyTxtVideoResult(node, images, meta)
    } catch (e: any) {
      alert(`文本生视频失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const executeRigWalk = async (
    node: RigWalkNodeType, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    if (!project) throw new Error('项目未加载')
    resolveChainInput(node, runtime, metaRuntime)   // 仅用于校验上游已连线
    const rw = node.rigwalk
    const { taskId } = await api.rigwalk({
      projectId: project.id,
      frames: rw?.frames ?? 8,
      swing: rw?.swing ?? 13,
      arm: rw?.arm ?? 12,
      bob: rw?.bob ?? 5,
      lift: rw?.lift ?? 7,
      hold: rw?.hold ?? false,
      fps: rw?.fps ?? 8,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const meta: any = r.rig || {}
    const images: ResultImage[] = (r.images || []).map((o: any) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
    }))
    return { images, meta }
  }

  const applyRigWalkResult = (node: RigWalkNodeType, images: ResultImage[], meta: any) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { rigwalk: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { rigwalk: meta })
    }
  }

  const runRigWalk = async (node: RigWalkNodeType) => {
    if (!node.rigwalk?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { images, meta } = await executeRigWalk(node)
      applyRigWalkResult(node, images, meta)
    } catch (e: any) {
      alert(`骨骼行走失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const executeSpriteSheet = async (
    node: SpriteSheetNodeType, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    if (!project) throw new Error('项目未加载')
    const up = resolveChainInput(node, runtime, metaRuntime)
    const sh = node.spritesheet
    // 每方向的帧文件清单：从上游序列帧的结构化 meta 拿（含顺序）
    const metaDirs: any[] = up.meta?.directions || []
    let directions: { dir: string; frames: string[] }[] = []
    if (metaDirs.length && metaDirs[0]?.frames?.length) {
      directions = metaDirs.map((d) => ({
        dir: d.dir,
        frames: (d.frames || []).map((f: any) => `${f.assetId}.png`),
      }))
    } else {
      for (const im of up.out.images) {
        if (im.walkDir) directions.push({ dir: im.walkDir, frames: [`${im.assetId}.png`] })
      }
    }
    if (!directions.length) {
      throw new Error('上游没有序列帧：先跑「序列帧」节点')
    }
    const { taskId } = await api.spriteSheet({
      projectId: project.id,
      directions,
      size: sh?.size ?? 128,
      cols: sh?.cols ?? 0,
      sharedScale: sh?.sharedScale ?? true,
      anchor: sh?.anchor ?? 'bottom',
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const meta: any = r.sheet || {}
    const images: ResultImage[] = (r.images || []).map((o: any, i: number) => ({
      assetId: o.assetId, url: o.url, width: o.width, height: o.height,
      origAssetId: up.out.images[0]?.assetId || '', origUrl: up.out.images[0]?.url || '',
      walkDir: o.walkDir ?? (i === 0 ? undefined : meta.directions?.[i - 1]?.dir),
    }))
    return { images, meta }
  }

  const applySpriteSheetResult = (
    node: SpriteSheetNodeType, images: ResultImage[], meta: any,
  ) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, images, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { sheet: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { sheet: meta })
    }
  }

  const runSpriteSheet = async (node: SpriteSheetNodeType) => {
    if (!node.spritesheet?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const { images, meta } = await executeSpriteSheet(node)
      applySpriteSheetResult(node, images, meta)
    } catch (e: any) {
      alert(`精灵整图失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  /** 可操控预览：不调后端，把上游精灵整图的元数据组装成播放载荷。 */
  const executePlay = async (
    node: PlayNodeType, runtime?: Map<string, ResultImage[]>,
    metaRuntime?: Map<string, any>,
  ): Promise<{ images: ResultImage[]; meta: any }> => {
    const up = resolveChainInput(node, runtime, metaRuntime, { requireImages: false })
    const sheetMeta: any = up.meta || {}
    const dirs: any[] = sheetMeta.directions || []
    if (!dirs.length) {
      throw new Error('上游还没有精灵表：先跑「精灵整图」节点')
    }
    const p = node.play
    const playMeta = {
      fps: p?.fps ?? 8,
      zoom: p?.zoom ?? 2,
      seconds: 0,
      directions: dirs.map((d) => ({
        dir: d.dir, label: d.label, url: d.sheet?.url,
        cols: d.cols, rows: d.rows, frameSize: d.frameSize, frames: d.frames || [],
      })),
    }
    const ov = sheetMeta.overview
    const images: ResultImage[] = ov ? [{
      assetId: ov.assetId, url: ov.url, width: ov.width, height: ov.height,
      origAssetId: up.out.images[0]?.assetId || '', origUrl: up.out.images[0]?.url || '',
    }] : up.out.images.slice(0, 1)
    return { images, meta: playMeta }
  }

  const applyPlayResult = (node: PlayNodeType, images: ResultImage[], meta: any) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result: images[0] ?? null, meta } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, images, { play: meta })
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, images, { play: meta })
    }
  }

  const runPlay = async (node: PlayNodeType) => {
    if (!node.play?.enabled) return
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    try {
      const { images, meta } = await executePlay(node)
      applyPlayResult(node, images, meta)
    } catch (e: any) {
      alert(`预览组装失败：${e.message || e}`)
    } finally {
      markBusy(node.id, false)
    }
  }

  const deleteNode = (id: string) => {
    // 先掐掉这个节点的 SSE：否则任务结束时还会往已删除的节点写进度
    closers.current[id]?.()
    delete closers.current[id]
    const node = project?.nodes.find((n) => n.id === id)
    if (!node) return
    if (node.type === 'generate') {
      // 只连带删除「结果卡」；下游的精修节点保留（它可能还接了别的输入）
      const resultIds = (project?.edges || [])
        .filter((e) => e.from === id)
        .map((e) => project?.nodes.find((n) => n.id === e.to))
        .filter((n): n is ResultNodeType => !!n && n.type === 'result')
        .map((n) => n.id)
      if (resultIds.length && !confirm('删除这张生成卡会同时删除它生成的结果卡（图片文件仍保留在项目目录）。继续？')) return
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.filter((n) => n.id !== id && !resultIds.includes(n.id)),
        edges: p.edges.filter((e) => e.from !== id && e.to !== id && !resultIds.includes(e.to)),
      } : p)
    } else {
      setProject((p) => p ? {
        ...p,
        nodes: p.nodes.filter((n) => n.id !== id),
        edges: p.edges.filter((e) => e.from !== id && e.to !== id),
      } : p)
    }
    setSelected(null)
  }

  // 右键菜单：点任意处 / Esc 关闭
  useEffect(() => {
    if (!ctxMenu) return
    const close = () => { setCtxMenu(null); setCtxOpen(null) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') close() }
    window.addEventListener('mousedown', close)
    window.addEventListener('keydown', onKey)
    return () => {
      window.removeEventListener('mousedown', close)
      window.removeEventListener('keydown', onKey)
    }
  }, [ctxMenu])

  // 拖拽连线：从输出端口按下后，曲线实时跟随光标；松开在目标节点上即连线。
  // 原地松开（几乎没移动）则退回「点选」模式，兼容原来的两步操作。
  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      const d = linkDragRef.current
      if (!d) return
      if (Math.hypot(e.clientX - d.startX, e.clientY - d.startY) > 6) d.moved = true
      const w = toWorld(e.clientX, e.clientY)
      setLinking({ from: d.from, x: w.x, y: w.y })
    }
    const onUp = (e: MouseEvent) => {
      const d = linkDragRef.current
      if (!d) return
      linkDragRef.current = null
      setLinking(null)
      if (!d.moved) {
        setLinkFrom(d.from)
        return
      }
      const el = document.elementFromPoint(e.clientX, e.clientY) as HTMLElement | null
      const host = el?.closest('[data-node-id]') as HTMLElement | null
      const toId = host?.dataset.nodeId
      if (toId && toId !== d.from) finishLinkId(d.from, toId)
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [toWorld, project])

  // Delete 删除选中节点/连线；Esc 取消连线与选中
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        linkDragRef.current = null
        setLinking(null)
        setLinkFrom(null)
        setSelectedEdge(null)
        setCtxMenu(null)
        setCtxOpen(null)
        return
      }
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      const tag = (document.activeElement?.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return
      if (selectedEdge) {
        setProject((p) => p ? {
          ...p,
          edges: p.edges.filter((x) => !(x.from === selectedEdge.from && x.to === selectedEdge.to)),
        } : p)
        setSelectedEdge(null)
        return
      }
      if (!selected) return
      deleteNode(selected)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, selectedEdge, project])

  // ---------- 生成 ----------
  /** 执行一次生成，返回完整结果（供手动按钮和一键工作流共用）。 */
  const executeGenerate = (node: GenNodeType): Promise<GenerateResult> => {
    return new Promise((resolve, reject) => {
      if (!project) { reject(new Error('项目未加载')); return }
      setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
      // 上游「选择图片」连进来时，用它作为参考图；否则用生成卡自带的参考图
      const inEdge = project.edges.find((e) => e.to === node.id
        && project.nodes.find((n) => n.id === e.from)?.type === 'input')
      const up = inEdge ? project.nodes.find((n) => n.id === inEdge.from) : null
      const refFiles = up?.type === 'input' && up.refs.length
        ? up.refs.map((r) => r.file)
        : node.refs.map((r) => r.file)
      api.generate({
        projectId: project.id,
        genNodeId: node.id,
        prompt: node.prompt,
        styleId: node.styleId,
        refs: refFiles,
        params: node.params,
        pixel: node.pixel,
      }).then(({ taskId }) => {
        activeTaskRef.current = taskId
        setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
        let settled = false
        const close = subscribeTask(
          taskId,
          (p) => setTasks((t) => ({ ...t, [node.id]: { ...(t[node.id] || { taskId, error: null }), progress: p } })),
          (result, prog) => {
            if (settled) return
            settled = true
            close()
            if (activeTaskRef.current === taskId) activeTaskRef.current = null
            setTasks((t) => ({
              ...t,
              [node.id]: { taskId, progress: prog, error: prog.error || result?.error || null },
            }))
            if (result && !result.error && result.images?.length) resolve(result)
            else reject(new Error(prog.error || result?.error || (prog.cancelled ? '已取消' : '生成失败')))
          },
        )
        closers.current[node.id] = close
      }).catch(reject)
    })
  }

  /** 点生成卡上的「生成」：只跑正视图这一步。
   *  下游的视角 / 拼板节点各自带运行按钮，想跑哪个点哪个；
   *  想一次跑完全部，用顶部「运行工作流」。 */
  const generate = async (node: GenNodeType) => {
    if (isRunning(node.id)) return
    markBusy(node.id, true)
    try {
      const result = await executeGenerate(node)
      addResultNode(node, result.images, result.prompt, result.negative,
                    result.meta, { lastImages: result.images })
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '', progress: null }), error: String(e.message || e) },
      }))
    } finally {
      markBusy(node.id, false)
    }
  }

  const stopWorkflow = () => {
    abortRef.current = true
    const tid = activeTaskRef.current
    if (tid) api.cancelTask(tid).catch(() => undefined)
  }

  /** 一键运行：按连线拓扑顺序跑完所有生成/精修/扣背景/视角/八方向节点，
   *  结果自动落到结果卡。 */
  const runWorkflow = async () => {
    if (!project || wf.running) return
    type RunnerNode = GenNodeType | RefineNodeType | MatteNodeType
      | ViewGenNodeType | ThreeViewNodeType
      | WalkVideoNodeType | SequenceNodeType | SpriteSheetNodeType | PlayNodeType
      | TxtVideoNodeType
    // 关掉的后处理节点也算一步：它只是透传，不参与执行，但要负责把图交给下游结果卡
    const isRunner = (n: CanvasNode): n is RunnerNode =>
      n.type === 'generate' || n.type === 'refine' || n.type === 'matte'
      || n.type === 'viewgen' || n.type === 'threeview'
      || n.type === 'walkvideo' || n.type === 'sequence'
      || n.type === 'spritesheet' || n.type === 'play'
      || n.type === 'txtvideo'
    const byId = new Map(project.nodes.map((n) => [n.id, n]))
    const executable = project.nodes.filter(isRunner)
    if (!executable.length) {
      alert('画布上没有可执行的节点：先添加「生成图」「像素精修」「扣除背景」「视角生成」或「八方向行走」节点并连好线。')
      return
    }
    // 提示词超上限时不能跑：模型只读前一半，用户却以为整段生效。
    // 生成卡自己的按钮已经禁用了，但「运行工作流」是另一条入口，必须一起挡。
    const overprompt = executable.filter((n) => {
      if (n.type !== 'generate') return false
      const st = styles.find((x) => x.id === n.styleId)
      const pixelOn = !!n.pixel?.enabled && !!st?.pixelClass
      const max = promptLimitFor(limits, {
        styleId: n.styleId, maxSeqLen: n.params?.maxSeqLen, pixelOn,
      })
      return (n.prompt || '').length > max
    }) as GenNodeType[]
    if (overprompt.length) {
      const detail = overprompt.map((n) => {
        const st = styles.find((x) => x.id === n.styleId)
        const pixelOn = !!n.pixel?.enabled && !!st?.pixelClass
        const max = promptLimitFor(limits, {
          styleId: n.styleId, maxSeqLen: n.params?.maxSeqLen, pixelOn,
        })
        return `· 「${(n.prompt || '').slice(0, 12)}…」${(n.prompt || '').length} 字 / 上限 ${max} 字`
      }).join('\n')
      alert(`有 ${overprompt.length} 张生成卡的提示词超出上限，超出的部分模型读不到。\n` +
        `删到上限内、或把「提示词质量」调高一档后再运行：\n${detail}`)
      return
    }
    // 拓扑序：先跑上游，再跑下游（有环也不会死循环，seen 兜底）
    const order: RunnerNode[] = []
    const seen = new Set<string>()
    const visit = (id: string) => {
      if (seen.has(id)) return
      seen.add(id)
      for (const e of project.edges.filter((x) => x.to === id)) visit(e.from)
      const n = byId.get(id)
      if (n && isRunner(n)) order.push(n)
    }
    executable.forEach((n) => visit(n.id))

    abortRef.current = false
    setWf({ running: true, label: '准备中…', done: 0, total: order.length, error: null })
    const runtime = new Map<string, ResultImage[]>()
    const metaRuntime = new Map<string, any>()
    try {
      for (let i = 0; i < order.length; i++) {
        if (abortRef.current) throw new Error('已停止')
        const n = order[i]
        const kind = n.type === 'generate' ? '生成'
          : n.type === 'refine' ? '精修'
          : n.type === 'matte' ? (n.matte?.enabled ? '扣背景' : '跳过扣背景')
          : n.type === 'viewgen' ? (n.viewgen?.enabled
            ? (n.viewgen?.view === 'back' ? '背视图'
              : n.viewgen?.view === 'side_r' ? '右视图' : '侧视图')
            : '跳过视角')
          : n.type === 'threeview' ? (n.threeview?.enabled ? '四视图拼板' : '跳过拼板')
          : n.type === 'walkvideo' ? (n.walkvideo?.enabled ? '行走视频' : '跳过行走视频')
          : n.type === 'txtvideo' ? (n.txtvideo?.enabled ? '文本视频' : '跳过文本视频')
          : n.type === 'sequence' ? (n.sequence?.enabled ? '序列帧' : '跳过序列帧')
          : n.type === 'spritesheet' ? (n.spritesheet?.enabled ? '精灵整图' : '跳过精灵整图')
          : (n.play?.enabled ? '可操控预览' : '跳过预览')
        setWf({
          running: true, done: i, total: order.length, error: null,
          label: `${kind} ${i + 1}/${order.length}`,
        })
        // 视角与拼板节点的输入不一定是直接上游：视角要最上游的正视图，
        // 拼板要正/侧/背三张。两者都沿链条回溯，runtime 优先。
        if (n.type === 'viewgen') {
          if (!n.viewgen?.enabled) {
            // 透传：把正视图原样交给下游，别把链路断在这里
            const front = resolveFrontOutput(n, runtime)
            runtime.set(n.id, front.images)
            pushDownstreamResult(n.id, front.images, {})
            // 链条末端的关闭节点也要有一张结果卡，否则跑完什么都看不到
            if (front.images.length) ensureResultCard(n, front.images, {})
            continue
          }
          try {
            const result = await executeViewDerive(n, runtime)
            applyViewGenResult(n, result)
            runtime.set(n.id, result)
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（${kind}）：${e.message || e}`)
          }
          continue
        }
        if (n.type === 'threeview') {
          if (!n.threeview?.enabled) {
            const up = collectThreeViewOutputs(n, runtime)
            const passed = up.back?.images || up.front?.images || []
            runtime.set(n.id, passed)
            if (passed.length) {
              pushDownstreamResult(n.id, passed, {})
              ensureResultCard(n, passed, {})
            }
            continue
          }
          try {
            const result = await executeCompose(n, runtime)
            applyComposeResult(n, result)
            runtime.set(n.id, [result])
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（四视图拼板）：${e.message || e}`)
          }
          continue
        }
        // 文本生视频：参考图可选，关掉时透传上游（和行走链同一套）
        if (n.type === 'txtvideo') {
          if (!n.txtvideo?.enabled) {
            const inEdge = project.edges.find((e) => e.to === n.id)
            const up = inEdge ? runtime.get(inEdge.from) || [] : []
            runtime.set(n.id, up)
            if (up.length) {
              pushDownstreamResult(n.id, up, {})
              ensureResultCard(n, up, {})
            }
            continue
          }
          try {
            const result = await executeTxtVideo(n, runtime)
            applyTxtVideoResult(n, result.images, result.meta)
            runtime.set(n.id, result.images)
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（文本视频）：${e.message || e}`)
          }
          continue
        }
        // 八方向行走链：直线四步，每步只收上一步；关掉的节点透传
        if (n.type === 'walkvideo' || n.type === 'sequence'
          || n.type === 'spritesheet' || n.type === 'play') {
          const opts = n.type === 'walkvideo' ? n.walkvideo
            : n.type === 'sequence' ? n.sequence
            : n.type === 'spritesheet' ? n.spritesheet : n.play
          if (!opts?.enabled) {
            const inEdge = project.edges.find((e) => e.to === n.id)
            const up = inEdge ? runtime.get(inEdge.from) || [] : []
            runtime.set(n.id, up)
            metaRuntime.set(n.id, metaRuntime.get(inEdge?.from || '') ?? null)
            if (up.length) {
              pushDownstreamResult(n.id, up, {})
              ensureResultCard(n, up, {})
            }
            continue
          }
          // 行走视频关掉 = 整条链没有方向视频可用，下游三步必失败。
          // 与其在第 2 步抛「上游没有方向信息」中断整个工作流，不如整链跳过。
          if (n.type !== 'walkvideo' && project.nodes.some(
            (x) => x.type === 'walkvideo' && !x.walkvideo?.enabled)) {
            runtime.set(n.id, [])
            metaRuntime.set(n.id, null)
            continue
          }
          try {
            if (n.type === 'walkvideo') {
              const { images: imgs, meta } = await executeWalkVideo(n, runtime)
              applyWalkVideoResult(n, imgs, meta)
              runtime.set(n.id, imgs)
              metaRuntime.set(n.id, meta)
            } else if (n.type === 'sequence') {
              const { images: imgs, meta } = await executeSequence(n, runtime, metaRuntime)
              applySequenceResult(n, imgs, meta)
              runtime.set(n.id, imgs)
              metaRuntime.set(n.id, meta)
            } else if (n.type === 'spritesheet') {
              const { images: imgs, meta } = await executeSpriteSheet(n, runtime, metaRuntime)
              applySpriteSheetResult(n, imgs, meta)
              runtime.set(n.id, imgs)
              metaRuntime.set(n.id, meta)
            } else {
              const { images: imgs, meta } = await executePlay(n, runtime, metaRuntime)
              applyPlayResult(n, imgs, meta)
              runtime.set(n.id, imgs)
              metaRuntime.set(n.id, meta)
            }
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（${kind}）：${e.message || e}`)
          }
          continue
        }
        const inEdge = project.edges.find((e) => e.to === n.id)
        const upstream = inEdge ? runtime.get(inEdge.from) : undefined
        if (n.type === 'generate') {
          if (!n.prompt.trim()) throw new Error(`第 ${i + 1} 步：生成节点的提示词为空`)
          const r = await executeGenerate(n)
          // 下游还有后处理节点（抠图/精修/视角/拼板）时，结果卡由链条末端建，
          // 否则四视图链上会先在生成卡旁边多出一张只放原图的废卡
          const down = project.edges.filter((e) => e.from === n.id)
            .map((e) => byId.get(e.to))
            .filter((x): x is RunnerNode => !!x && x.type !== 'result')
          const chain = matteChain(n.id)
          addResultNode(n, r.images, r.prompt, r.negative, r.meta, {
            lastImages: r.images,
            // 结果卡挂在这条链的末端（跑了抠图就挂到抠图卡上），
            // 否则会多建一张空卡；提示词先落到卡上，抠图之后只覆盖图片
            fromId: chain.length ? chain[chain.length - 1].id : n.id,
            deferCreate: chain.length > 0 || down.length > 0,
          })
          runtime.set(n.id, r.images)
        } else if (n.type === 'refine') {
          setRefining((s) => ({ ...s, [n.id]: true }))
          try {
            const result = await executeRefine(n, upstream)
            applyRefineResult(n, result)
            runtime.set(n.id, [result])
          } finally {
            setRefining((s) => ({ ...s, [n.id]: false }))
          }
        } else {
          // 关掉的节点：图片原样透传给下游，不要在链路上留个空洞
          if (!n.matte?.enabled) {
            const up = upstream || []
            runtime.set(n.id, up)
            if (up.length) {
              pushDownstreamResult(n.id, up, {})
              ensureResultCard(n, up, {})
            }
            continue
          }
          setMatting((s) => ({ ...s, [n.id]: true }))
          try {
            const out: ResultImage[] = []
            for (const im of upstream || []) out.push(await executeMatte(n, im))
            if (!out.length) throw new Error('上游没有产出图，无法扣背景')
            applyMatteResult(n, out)
            runtime.set(n.id, out)
          } finally {
            setMatting((s) => ({ ...s, [n.id]: false }))
          }
        }
      }
      setWf({ running: false, label: '完成', done: order.length, total: order.length, error: null })
    } catch (e: any) {
      setWf({ running: false, label: '已停止', done: 0, total: order.length, error: String(e.message || e) })
      alert(`工作流中断：${e.message || e}`)
    }
  }

  const addResultNode = (
    gen: GenNodeType,
    images: ResultImage[],
    prompt: string,
    negative: string,
    meta: ResultMeta,
    opts?: {
      /** 结果卡挂在哪个节点下游（默认是生成卡；跑了抠图就是抠图卡） */
      fromId?: string
      /** 生成卡记住的原始产出（下游精修取的是未抠图的版本） */
      lastImages?: ResultImage[]
      /** 下游还有后处理节点时不要在这里新建结果卡，交给它填 */
      deferCreate?: boolean
    },
  ) => {
    const fromId = opts?.fromId || gen.id
    setProject((p) => {
      if (!p) return p
      // 记住生成卡的最新输出，供下游「像素精修 / 扣除背景」节点取图
      const withLast = p.nodes.map((n) =>
        n.id === gen.id ? ({ ...n, lastImages: opts?.lastImages || images } as CanvasNode) : n)
      // 已经手动连到结果卡：把结果填进去，而不是再新建一张
      const linked = p.nodes.find((n) =>
        n.type === 'result' && p.edges.some((e) => e.from === fromId && e.to === n.id))
      if (linked) {
        return {
          ...p,
          nodes: withLast.map((n) => n.id === linked.id
            ? ({ ...n, images, prompt, negative, meta, genId: gen.id } as CanvasNode) : n),
        }
      }
      // 下游还有开着的扣背景节点：结果卡由它填，这里只更新生成卡的 lastImages
      if (opts?.deferCreate) return { ...p, nodes: withLast }
      const siblings = p.nodes.filter((n) => n.type === 'result' && (n as ResultNodeType).genId === gen.id)
      const node: ResultNodeType = {
        id: newId('res'),
        type: 'result',
        x: gen.x + GEN_W + 96,
        y: gen.y + siblings.length * 460,
        genId: gen.id,
        images,
        prompt,
        negative,
        meta,
        createdAt: Date.now() / 1000,
      }
      return {
        ...p,
        nodes: [...withLast, node],
        edges: [...p.edges, { from: fromId, to: node.id }],
      }
    })
  }

  const cancelTask = async (genId: string) => {
    const t = tasks[genId]
    if (!t?.taskId) return
    try { await api.cancelTask(t.taskId) } catch (e: any) { alert(String(e.message || e)) }
  }

  const saveAsTemplate = async () => {
    if (!project) return
    const name = window.prompt('模板名称', `${project.name} · 工作流`)
    if (!name?.trim()) return
    try {
      await api.createTemplate({
        name: name.trim(),
        tagline: '自定义工作流',
        nodes: project.nodes,
        edges: project.edges,
      })
      alert('已存为自定义模板。回到模板 / 新建项目页即可选到它。')
    } catch (e: any) {
      alert(`保存模板失败：${e.message || e}`)
    }
  }

  /** 把画布草稿写回模板（模板编辑的主路径：在无限画布上改完，点一下保存） */
  const commitTemplate = async () => {
    const p = projectRef.current
    if (!p?.templateDraftOf) return
    setTplBusy(true)
    try {
      // 先显式落盘：commit 是后端读 project.json 再写回模板，
      // 光靠 800ms 防抖可能在提交时还没落盘，写回去的就是上一版节点图
      await api.saveProject(p.id, {
        name: p.name, nodes: p.nodes, edges: p.edges, viewport,
      } as any)
      const r = await api.commitTemplateDraft(p.templateDraftOf, p.id)
      skipFlushRef.current = true
      alert(`模板「${r.template.name}」已更新${r.draftDeleted ? '，编辑草稿已清理' : ''}。`)
      window.location.hash = '#/templates'
    } catch (e: any) {
      alert(`保存模板失败：${e.message || e}`)
    } finally {
      setTplBusy(false)
    }
  }

  const discardDraft = async () => {
    const p = projectRef.current
    if (!p?.templateDraftOf) return
    if (!confirm('放弃这次模板编辑？草稿会被删除，模板保持原样。')) return
    setTplBusy(true)
    try {
      skipFlushRef.current = true
      await api.deleteProject(p.id)
      window.location.hash = '#/templates'
    } catch (e: any) {
      skipFlushRef.current = false
      alert(`删除草稿失败：${e.message || e}`)
    } finally {
      setTplBusy(false)
    }
  }

  const openPromptPreview = async (node: GenNodeType) => {
    setPromptModal({ open: true, loading: true, error: null, data: null })
    try {
      const data = await api.preview({
        prompt: node.prompt,
        styleId: node.styleId,
        width: node.params.width,
        height: node.params.height,
        background: node.params.background,
        pixel: node.pixel,
        negative: node.params.negative || '',
      })
      setPromptModal({ open: true, loading: false, error: null, data })
    } catch (e: any) {
      setPromptModal({ open: true, loading: false, error: String(e.message || e), data: null })
    }
  }

  // ---------- 渲染 ----------
  const genNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is GenNodeType => n.type === 'generate'),
    [project?.nodes],
  )
  const resultNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is ResultNodeType => n.type === 'result'),
    [project?.nodes],
  )
  const inputNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is InputNodeType => n.type === 'input'),
    [project?.nodes],
  )
  const refineNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is RefineNodeType => n.type === 'refine'),
    [project?.nodes],
  )
  const matteNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is MatteNodeType => n.type === 'matte'),
    [project?.nodes],
  )
  const viewGenNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is ViewGenNodeType => n.type === 'viewgen'),
    [project?.nodes],
  )
  const threeViewNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is ThreeViewNodeType => n.type === 'threeview'),
    [project?.nodes],
  )
  /** 画布上有没有四视图拼板节点：有的话背景提示要交代拼板侧的底色处理 */
  const boardMode = useMemo(
    () => (project?.nodes || []).some((n) => n.type === 'threeview'),
    [project?.nodes],
  )
  /** 那张拼板是否开了透明底板：透明板下选「透明」背景可 alpha 直通 */
  const boardTransparent = useMemo(
    () => (project?.nodes || []).some(
      (n) => n.type === 'threeview' && !!(n as ThreeViewNodeType).threeview?.transparentBg),
    [project?.nodes],
  )
  /** 视角节点复用的正视图参数摘要：节点卡上展示「继承正视图」的明细，
   *  让用户知道画风/尺寸/背景这些值从哪来，不用再填一遍 */
  const frontInherit = useCallback((id: string) => {
    const p = project
    if (!p) return null
    try {
      const node = p.nodes.find((n) => n.id === id)
      if (!node) return null
      const { src } = resolveFrontSource(node)
      if (src.type !== 'generate') return null
      const g = src as GenNodeType
      const st = styles.find((s) => s.id === g.styleId)
      // 空串与 'none' 都是「跟随提示词」：归一成一个 id，否则标签会是空的
      const bgId = g.params.background || 'none'
      const bg = backgrounds.find((b) => b.id === bgId)
      return {
        styleName: st?.name ?? null,
        width: g.params.width, height: g.params.height,
        steps: g.params.steps, seed: g.params.seed,
        maxSeqLen: g.params.maxSeqLen,
        background: bg?.label ?? null,
        // 预览视角提示词要用：背景 id（label 是给眼睛看的）+ 上游那句角色描述
        // （视角指令把它当辨认清单拼进去，不传就和真实生成不一致）
        backgroundId: bgId,
        character: g.prompt?.trim() ?? '',
      }
    } catch { return null }
  }, [project, styles, backgrounds])
  const walkVideoNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is WalkVideoNodeType => n.type === 'walkvideo'),
    [project?.nodes],
  )
  const sequenceNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is SequenceNodeType => n.type === 'sequence'),
    [project?.nodes],
  )
  const partsNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is PartsNodeType => n.type === 'parts'),
    [project?.nodes],
  )
  const rigWalkNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is RigWalkNodeType => n.type === 'rigwalk'),
    [project?.nodes],
  )
  const txtVideoNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is TxtVideoNodeType => n.type === 'txtvideo'),
    [project?.nodes],
  )
  const spriteSheetNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is SpriteSheetNodeType => n.type === 'spritesheet'),
    [project?.nodes],
  )
  const playNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is PlayNodeType => n.type === 'play'),
    [project?.nodes],
  )
  const clipSrcNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is ClipSrcNodeType => n.type === 'clipsrc'),
    [project?.nodes],
  )
  const frameSplitNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is FrameSplitNodeType => n.type === 'framesplit'),
    [project?.nodes],
  )
  const spriteGridNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is SpriteGridNodeType => n.type === 'spritegrid'),
    [project?.nodes],
  )
  const animPreviewNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is AnimPreviewNodeType => n.type === 'animpreview'),
    [project?.nodes],
  )
  const godotNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is Godot2DNodeType => n.type === 'godot2d'),
    [project?.nodes],
  )
  const inpaintNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is InpaintNodeType => n.type === 'inpaint'),
    [project?.nodes],
  )
  const eraseNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is EraseNodeType => n.type === 'erase'),
    [project?.nodes],
  )
  const layer25dNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is Layer25DNodeType => n.type === 'layer25d'),
    [project?.nodes],
  )

  /** 某个节点的入边来自哪一类上游（节点卡上显示「输入：xxx」）。 */
  const upstreamLabel = useCallback((id: string): string | null => {
    const e = project?.edges.find((x) => x.to === id)
    const s = e ? project?.nodes.find((x) => x.id === e.from) : null
    return s ? nodeLabel(s.type) : null
  }, [project?.edges, project?.nodes])

  /** 视角节点的参考来自哪一类上游：沿链条回溯到最上游那张正视图，
   *  所以中间隔着别的视角节点时，标签显示的还是正视图的来源。 */
  const frontLabel = useCallback((id: string): string | null => {
    const p = project
    if (!p) return null
    const seen = new Set<string>([id])
    const queue = [id]
    while (queue.length) {
      const cur = queue.shift() as string
      for (const e of p.edges.filter((x) => x.to === cur)) {
        if (seen.has(e.from)) continue
        seen.add(e.from)
        const src = p.nodes.find((x) => x.id === e.from)
        if (!src) continue
        if (src.type !== 'viewgen') return nodeLabel(src.type)
        queue.push(src.id)
      }
    }
    return null
  }, [project])

  const edges = useMemo(() => {
    if (!project) return []
    const byId = new Map(project.nodes.map((n) => [n.id, n]))
    return project.edges.map((e, i) => {
      const a = byId.get(e.from)
      const b = byId.get(e.to)
      if (!a || !b) return null
      const x1 = a.x + (NODE_W[a.type] || GEN_W)
      const y1 = a.y + 44
      const x2 = b.x
      const y2 = b.y + 44
      const dx = Math.max(48, Math.abs(x2 - x1) * 0.42)
      const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
      const sel = selectedEdge?.from === e.from && selectedEdge?.to === e.to
      return (
        <g key={`${e.from}-${e.to}-${i}`}>
          <path d={d} fill="none"
            stroke={sel ? 'var(--pink)' : 'rgba(0,255,136,0.55)'}
            strokeWidth={sel ? 3 : 2}
            strokeDasharray="0"
            markerEnd={sel ? 'url(#edge-arrow-sel)' : 'url(#edge-arrow)'} />
          {/* 透明加粗命中区：点线选中、双击删除 */}
          <path d={d} fill="none" stroke="transparent" strokeWidth={16}
            style={{ pointerEvents: 'stroke', cursor: 'pointer' }}
            onMouseDown={(ev) => ev.stopPropagation()}
            onClick={(ev) => {
              ev.stopPropagation()
              setSelectedEdge({ from: e.from, to: e.to })
              setSelected(null)
              setLinkFrom(null)
            }}
            onDoubleClick={(ev) => {
              ev.stopPropagation()
              setProject((p) => p ? {
                ...p,
                edges: p.edges.filter((x) => !(x.from === e.from && x.to === e.to)),
              } : p)
              setSelectedEdge(null)
            }} />
        </g>
      )
    })
  }, [project, selectedEdge])

  // 拖拽连线的临时曲线：从源节点输出端口实时跟到光标
  const tempLink = useMemo(() => {
    if (!linking || !project) return null
    const a = project.nodes.find((n) => n.id === linking.from)
    if (!a) return null
    const x1 = a.x + (NODE_W[a.type] || GEN_W)
    const y1 = a.y + 44
    const x2 = linking.x
    const y2 = linking.y
    const dx = Math.max(48, Math.abs(x2 - x1) * 0.42)
    return `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`
  }, [linking, project])

  if (loadError) {
    return (
      <div className="page">
        <div className="err">项目加载失败：{loadError}</div>
        <button className="btn" style={{ marginTop: 12 }} onClick={onBack}>返回项目列表</button>
      </div>
    )
  }
  if (loadError) {
    return (
      <div className="page">
        <div className="err">打不开这个项目：{loadError}</div>
        {onBack ? (
          <button className="btn btn-sm" style={{ marginTop: 12 }} onClick={onBack}>
            ← 返回项目列表
          </button>
        ) : null}
      </div>
    )
  }
  if (!project) {
    return <div className="page"><div className="hint">加载中…</div></div>
  }

  return (
    <div className={`canvas-root ${(linking || linkFrom) ? 'linking' : ''}`}>
      <div
        ref={viewportRef}
        className={`canvas-viewport ${panning ? 'panning' : ''}`}
        onMouseDown={startPan}
        onWheel={onWheel}
        onContextMenu={(e) => {
          const t = e.target as HTMLElement
          // 图片保留浏览器原生右键菜单（右键另存为）；输入控件保留复制粘贴
          if (t.closest('img, .result-img-wrap, .ref-thumb, input, textarea, select, [contenteditable="true"]')) return
          e.preventDefault()
          const w = toWorld(e.clientX, e.clientY)
          setCtxMenu({ sx: e.clientX, sy: e.clientY, wx: w.x, wy: w.y })
        }}
      >
        <div
          className="canvas-grid"
          style={{
            backgroundSize: `${24 * viewport.z}px ${24 * viewport.z}px, ${120 * viewport.z}px ${120 * viewport.z}px, ${120 * viewport.z}px ${120 * viewport.z}px`,
            backgroundPosition: `${viewport.x}px ${viewport.y}px`,
          }}
        />
        <div
          ref={worldRef}
          className="canvas-world"
          style={{ transform: `translate(${viewport.x}px, ${viewport.y}px) scale(${viewport.z})` }}
        >
          <svg className="canvas-links" width={1} height={1}>
            <defs>
              <marker id="edge-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="rgba(0,255,136,0.75)" />
              </marker>
              <marker id="link-arrow" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#00ff88" />
              </marker>
              <marker id="edge-arrow-sel" viewBox="0 0 10 10" refX="9" refY="5"
                markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" fill="#ff3366" />
              </marker>
            </defs>
            {edges}
            {tempLink ? (
              <path d={tempLink} fill="none" stroke="#00ff88" strokeWidth={2}
                strokeDasharray="7 5" markerEnd="url(#link-arrow)" opacity={0.9} />
            ) : null}
          </svg>

          {inputNodes.map((n, i) => (
            <InputNode
              key={n.id}
              node={n}
              index={i}
              projectId={project.id}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onStartLink={startLink}
            />
          ))}

          {genNodes.map((n, i) => (
            <GenNode
              key={n.id}
              node={n}
              index={i}
              styles={styles}
              backgrounds={backgrounds}
              examples={examples}
              categories={categories}
              projectId={project.id}
              task={tasks[n.id] || null}
              /** 画布上存在四视图拼板节点时，生成卡禁选透明背景：
               *  拼板会把各张图抠底后统一贴到底板色上，透明底与之互斥 */
              boardMode={boardMode}
              boardTransparent={boardTransparent}
              onChange={(patch) => patchNode(n.id, patch)}
              onGenerate={() => generate(n)}
              onPreview={() => openPromptPreview(n)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onCancel={() => cancelTask(n.id)}
              onFinishLink={finishLink}
              onStartLink={startLink}
            />
          ))}

          {refineNodes.map((n, i) => (
            <RefineNode
              key={n.id}
              node={n}
              index={i}
              running={!!refining[n.id]}
              sourceLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runRefine(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
            />
          ))}

          {matteNodes.map((n, i) => (
            <MatteNode
              key={n.id}
              node={n}
              index={i}
              running={!!matting[n.id]}
              sourceLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runMatte(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({
                src: url, title,
                pixelSize: n.result ? [n.result.width, n.result.height] : undefined,
              })}
            />
          ))}

          {viewGenNodes.map((n, i) => (
            <ViewGenNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              sourceLabel={frontLabel(n.id)}
              inherit={frontInherit(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runViewDerive(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({
                src: url, title,
                pixelSize: n.result ? [n.result.width, n.result.height] : undefined,
              })}
            />
          ))}

          {threeViewNodes.map((n, i) => (
            <ThreeViewNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              slots={threeViewSlots(n)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runCompose(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({
                src: url, title,
                pixelSize: n.result ? [n.result.width, n.result.height] : undefined,
              })}
            />
          ))}

          {walkVideoNodes.map((n, i) => (
            <WalkVideoNode
              key={n.id}
              node={n}
              index={i}
              projectId={project.id}
              examples={examples.filter((e) => e.category === '行走视频')}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              sourceLabel={frontLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runWalkVideo(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {partsNodes.map((n, i) => (
            <PartsNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              sourceLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runParts(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {rigWalkNodes.map((n, i) => (
            <RigWalkNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              upstreamLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runRigWalk(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {txtVideoNodes.map((n, i) => (
            <TxtVideoNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              pct={(() => {
                const tp = tasks[n.id]?.progress as any
                return tp && tp.total ? Math.round((tp.done / tp.total) * 100) : null
              })()}
              elapsed={(tasks[n.id]?.progress as any)?.elapsed ?? null}
              error={tasks[n.id]?.error ?? null}
              sourceLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runTxtVideo(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {clipSrcNodes.map((n, i) => (
            <ClipSrcNode
              key={n.id}
              node={n}
              index={i}
              projectId={project.id}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {frameSplitNodes.map((n, i) => (
            <FramesplitNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              error={tasks[n.id]?.error ?? null}
              upstreamFile={upstreamClipFile(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runFrameSplit(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {spriteGridNodes.map((n, i) => (
            <SpriteGridNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              error={tasks[n.id]?.error ?? null}
              upstreamPicked={upstreamPicks(n.id)?.files.length ?? null}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runSpriteGrid(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {animPreviewNodes.map((n, i) => (
            <AnimPreviewNode
              key={n.id}
              node={n}
              index={i}
              upstreamCount={(() => {
                const e = project.edges.find((x) => x.to === n.id)
                const s = e ? project.nodes.find((x) => x.id === e.from) : null
                return s && s.type === 'spritegrid'
                  ? ((s as SpriteGridNodeType).meta?.count ?? 0) : null
              })()}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onStartLink={startLink}
              onFinishLink={finishLink}
            />
          ))}

          {godotNodes.map((n, i) => {
            const up = upstreamGrid(n.id)
            return (
              <GodotExportNode
                key={n.id}
                node={n}
                index={i}
                running={isRunning(n.id)}
                error={tasks[n.id]?.error ?? null}
                upstream={up && up.ready
                  ? { count: up.count, cell: up.cell, cols: up.cols } : null}
                onChange={(patch) => patchNode(n.id, patch)}
                onDelete={() => deleteNode(n.id)}
                onDragStart={startDragNode(n.id)}
                onRun={() => runGodotExport(n)}
                onStartLink={startLink}
                onFinishLink={finishLink}
              />
            )
          })}

          {inpaintNodes.map((n, i) => {
            const up = upstreamImage(n)
            return (
              <InpaintNode
                key={n.id}
                node={n}
                index={i}
                running={isRunning(n.id)}
                progress={tasks[n.id]?.progress?.label}
                error={tasks[n.id]?.error ?? null}
                projectId={project.id}
                upstream={up}
                onChange={(patch) => patchNode(n.id, patch)}
                onDelete={() => deleteNode(n.id)}
                onDragStart={startDragNode(n.id)}
                onRun={() => runInpaint(n)}
                onOpenMask={() => {
                  if (!up) { alert('先接一张源图，再涂抹'); return }
                  setMaskEdit({
                    nodeId: n.id, srcUrl: up.url, w: up.width, h: up.height,
                    maskUrl: n.inpaint?.mask?.url || null,
                  })
                }}
                onStartLink={startLink}
                onFinishLink={finishLink}
                onPreview={(url, title, orig) => setPreview({ src: url, origSrc: orig, title })}
              />
            )
          })}

          {eraseNodes.map((n, i) => {
            const up = upstreamImage(n)
            return (
              <EraseNode
                key={n.id}
                node={n}
                index={i}
                running={isRunning(n.id)}
                progress={tasks[n.id]?.progress?.label}
                error={tasks[n.id]?.error ?? null}
                methods={eraseMethods}
                upstream={up}
                onChange={(patch) => patchNode(n.id, patch)}
                onDelete={() => deleteNode(n.id)}
                onDragStart={startDragNode(n.id)}
                onRun={() => runErase(n)}
                onOpenMask={() => {
                  if (!up) { alert('先接一张源图，再涂抹'); return }
                  setMaskEdit({
                    nodeId: n.id, srcUrl: up.url, w: up.width, h: up.height,
                    maskUrl: n.erase?.mask?.url || null, erase: true,
                  })
                }}
                onStartLink={startLink}
                onFinishLink={finishLink}
                onPreview={(url, title, orig) => setPreview({ src: url, origSrc: orig, title })}
              />
            )
          })}

          {layer25dNodes.map((n, i) => {
            const up = upstreamImage(n)
            return (
              <Layer25DNode
                key={n.id}
                node={n}
                index={i}
                running={isRunning(n.id)}
                progress={tasks[n.id]?.progress?.label}
                error={tasks[n.id]?.error ?? null}
                upstream={up}
                onChange={(patch) => patchNode(n.id, patch)}
                onDelete={() => deleteNode(n.id)}
                onDragStart={startDragNode(n.id)}
                onRun={() => runLayer25D(n)}
                onStartLink={startLink}
                onFinishLink={finishLink}
                onPreview={(url, title) => setPreview({ src: url, title })}
              />
            )
          })}

          {sequenceNodes.map((n, i) => (
            <SequenceNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              upstreamLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runSequence(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {spriteSheetNodes.map((n, i) => (
            <SpriteSheetNode
              key={n.id}
              node={n}
              index={i}
              running={isRunning(n.id)}
              progress={tasks[n.id]?.progress?.label}
              upstreamLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runSpriteSheet(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {playNodes.map((n, i) => (
            <PlayNode
              key={n.id}
              node={n}
              index={i}
              running={false}
              upstreamLabel={upstreamLabel(n.id)}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runPlay(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
              onPreview={(url, title) => setPreview({ src: url, title })}
            />
          ))}

          {resultNodes.map((n, i) => (
            <ResultNode
              key={n.id}
              node={n}
              index={i}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onPreview={(img, meta) => setPreview({
                src: img.url,
                origSrc: img.origUrl,
                title: `${meta.model ? String(meta.model).split('/').pop() : '结果'} · ${img.width}×${img.height}`,
                pixelSize: [img.width, img.height],
              })}
              onFinishLink={finishLink}
            />
          ))}
        </div>
      </div>

      {/* 顶部项目栏 */}
      <div className="canvas-topbar">
        <button className="btn btn-sm" onClick={() => {
          // 模板草稿的「回去」应该是模板页，不是项目列表
          if (project.templateDraftOf) window.location.hash = '#/templates'
          else onBack()
        }}>{project.templateDraftOf ? '← 模板' : '← 项目'}</button>
        <input
          className="name-input"
          value={project.name}
          disabled={!!project.templateDraftOf}
          title={project.templateDraftOf ? '模板编辑草稿：名字不用改，保存的是节点图' : undefined}
          onChange={(e) => setProject({ ...project, name: e.target.value })}
          onBlur={() => { if (!project.name.trim()) setProject({ ...project, name: '未命名项目' }) }}
        />
        <span className="tpl-tag" style={project.templateDraftOf
          ? { borderColor: 'var(--yellow)', color: 'var(--yellow)' } : undefined}>
          {project.templateDraftOf ? '模板编辑 · ' : ''}{template?.name || '未知模板'}
        </span>
        <span className="save-state">
          {saveState === 'saving' ? '保存中…'
            : saveState === 'dirty' ? '未保存'
            : saveState === 'error' ? '保存失败'
            : lastSaved ? `已保存 ${new Date(lastSaved).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
            : '已保存'}
        </span>
        {/* 参数视图模式：简略只留「决定结果」的那几项，专业铺开全部。
            放在顶栏而不是每个节点上：这是「我用这个工具的习惯」，一份就够，
            而且切一次全场生效，比逐个节点展开省事。 */}
        <div className="viewmode-toggle"
          title={viewMode === 'simple'
            ? '简略：每个节点只露关键选项，其余收在「更多设置」里'
            : '专业：所有参数平铺，分组显示'}>
          <button className={viewMode === 'simple' ? 'on' : ''}
            onClick={() => setViewMode('simple')}>简略</button>
          <button className={viewMode === 'pro' ? 'on' : ''}
            onClick={() => setViewMode('pro')}>专业</button>
        </div>
        {project.templateDraftOf ? (
          <>
            <button className="btn btn-sm btn-primary" onClick={commitTemplate}
              disabled={tplBusy} title="把当前节点图 / 参数写回这个模板">
              {tplBusy ? '保存中…' : '保存为模板'}
            </button>
            <button className="btn btn-sm" onClick={discardDraft} disabled={tplBusy}
              title="删除草稿，模板保持原样">放弃编辑</button>
          </>
        ) : (
          <button className="btn btn-sm" onClick={saveAsTemplate} title="把当前节点图存成自定义模板">
            存为模板
          </button>
        )}
        <button className="btn btn-sm" onClick={() => setFilesOpen(true)} title="项目文件夹：输入与产出都在这里">
          文件夹
        </button>
      </div>

      {/* 底部工具栏 */}
      <div className="canvas-toolbar">
        <button className="btn btn-sm" onClick={() => setViewport((v) => ({ ...v, z: clamp(v.z / 1.15, 0.2, 2.5) }))}>−</button>
        <span className="zoom-val">{Math.round(viewport.z * 100)}%</span>
        <button className="btn btn-sm" onClick={() => setViewport((v) => ({ ...v, z: clamp(v.z * 1.15, 0.2, 2.5) }))}>+</button>
        <button className="btn btn-sm" onClick={() => setViewport({ x: 80, y: 60, z: 1 })}>100%</button>
        <button className="btn btn-sm" onClick={fitView}>适应视图</button>
        <div style={{ width: 1, height: 20, background: 'var(--line-strong)', margin: '0 4px' }} />
        {wf.running ? (
          <>
            <span className="hint" style={{ margin: '0 4px' }}>{wf.label} · {wf.done}/{wf.total}</span>
            <button className="btn btn-sm btn-danger" onClick={stopWorkflow}>停止</button>
          </>
        ) : (
          <button className="btn btn-sm btn-primary" onClick={runWorkflow}
            title="按连线顺序自动跑完所有生成 / 精修节点">
            ▶ 运行工作流
          </button>
        )}
        <button className="btn btn-sm" onClick={() => doSave()}>立即保存</button>
      </div>

      <div className="canvas-hint">
        <b>右键</b> 添加节点 · <b>拖端口</b> 连线 · <b>点线选中 / 双击删线</b> · <b>Esc</b> 取消
      </div>

      {linkFrom ? (
        <div style={{
          position: 'fixed', left: '50%', top: 72, transform: 'translateX(-50%)',
          zIndex: 260, padding: '6px 14px', fontSize: 12,
          background: 'rgba(0,255,136,0.12)', border: '1px solid rgba(0,255,136,0.45)',
          color: 'var(--green)', borderRadius: 4,
        }}>
          正在连线：点击目标节点左侧的输入端口完成，点空白处取消
        </div>
      ) : null}

      {ctxMenu ? (
        <div
          className={`ctx-menu ${ctxMenu.sx > window.innerWidth - 460 ? 'flip' : ''}`}
          style={{
            left: Math.min(ctxMenu.sx, window.innerWidth - 200),
            top: Math.min(ctxMenu.sy, window.innerHeight - 250),
          }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="ctx-title">添加节点</div>
          {NODE_CATALOG.map((cat) => (
            <div key={cat.id} className={`ctx-group ${ctxOpen === cat.id ? 'open' : ''}`}>
              <button className="ctx-cat"
                onClick={() => setCtxOpen(ctxOpen === cat.id ? null : cat.id)}>
                {cat.label}
                <span className="ctx-caret">▸</span>
              </button>
              <div className="ctx-sub">
                <div className="ctx-subhint">{cat.hint}</div>
                {cat.items.map((it) => (
                  <button key={it.type + (it.preset || '')}
                    onClick={() => {
                      addNodeAt(it.type, { x: ctxMenu.wx, y: ctxMenu.wy }, it.preset)
                      setCtxMenu(null)
                      setCtxOpen(null)
                    }}>
                    <span className={`ctx-dot ${it.dot}`} />
                    <span className="ctx-item">
                      <b>{it.label}</b>
                      <em>{it.hint}</em>
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ))}
          <div className="ctx-sep" />
          <button onClick={() => { setCtxMenu(null); setCtxOpen(null) }}>取消</button>
        </div>
      ) : null}

      <PreviewOverlay
        open={!!preview}
        src={preview?.src || null}
        origSrc={preview?.origSrc}
        title={preview?.title}
        pixelSize={preview?.pixelSize}
        onClose={() => setPreview(null)}
      />

      <MaskEditor
        open={!!maskEdit}
        projectId={projectId}
        srcUrl={maskEdit?.srcUrl || ''}
        srcW={maskEdit?.w || 1}
        srcH={maskEdit?.h || 1}
        initialMaskUrl={maskEdit?.maskUrl || null}
        title={maskEdit?.erase ? '涂抹要去掉的东西' : '涂抹要重绘的区域'}
        onSaved={(mask) => {
          if (maskEdit) applyMask(maskEdit.nodeId, mask)
        }}
        onClose={() => setMaskEdit(null)}
      />

      <PromptModal
        open={promptModal.open}
        loading={promptModal.loading}
        error={promptModal.error}
        data={promptModal.data}
        onClose={() => setPromptModal({ open: false, loading: false, error: null, data: null })}
      />

      <ProjectFiles
        open={filesOpen}
        projectId={projectId}
        onClose={() => setFilesOpen(false)}
      />
    </div>
  )
}
