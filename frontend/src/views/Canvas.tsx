import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribeTask } from '../api'
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
import SpriteSheetNode from '../components/SpriteSheetNode'
import ThreeViewNode from '../components/ThreeViewNode'
import ViewGenNode from '../components/ViewGenNode'
import WalkVideoNode from '../components/WalkVideoNode'
import type {
  BackgroundOption, CanvasNode, GenNode as GenNodeType, GenerateResult,
  InputNode as InputNodeType, MatteNode as MatteNodeType, PlayNode as PlayNodeType,
  Project, PromptExample, PromptPreview, RefineNode as RefineNodeType, ResultImage,
  ResultMeta, ResultNode as ResultNodeType, SequenceNode as SequenceNodeType,
  SpriteSheetNode as SpriteSheetNodeType, Style, TaskProgress, Template,
  ThreeViewNode as ThreeViewNodeType, ViewGenNode as ViewGenNodeType,
  WalkVideoNode as WalkVideoNodeType,
} from '../types'

type Props = {
  projectId: string
  onBack: () => void
  onDirtyChange?: (dirty: boolean) => void
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
const NODE_W: Record<string, number> = {
  generate: GEN_W, result: RES_W, input: INPUT_W, refine: REFINE_W, matte: MATTE_W,
  viewgen: VIEWGEN_W, threeview: THREEVIEW_W, walkvideo: WALKVIDEO_W,
  sequence: SEQUENCE_W, spritesheet: SPRITESHEET_W, play: PLAY_W,
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
  sequence: ['walkvideo'],
  spritesheet: ['sequence'],
  play: ['spritesheet'],
  result: ['generate', 'refine', 'matte', 'viewgen', 'threeview',
           'walkvideo', 'sequence', 'spritesheet', 'play'],
}

const UPSTREAM_HINT: Record<string, string> = {
  generate: '选择图片',
  refine: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  matte: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  viewgen: '选择图片 / 生成图 / 扣除背景 / 像素精修 / 图片结果',
  threeview: '生成图 / 侧视图 / 背视图 / 扣除背景 / 图片结果',
  walkvideo: '生成图 / 选择图片 / 扣除背景 / 像素精修',
  sequence: '行走视频',
  spritesheet: '序列帧',
  play: '精灵整图',
  result: '生成图 / 像素精修 / 扣除背景 / 视角图 / 三视图拼板 / 行走链',
}

function canConnect(from: CanvasNode | undefined, to: CanvasNode | undefined): boolean {
  if (!from || !to || from.id === to.id) return false
  const allowed = UPSTREAM_RULES[to.type]
  return !!allowed && allowed.includes(from.type)
}

/** 只能接收单条入边的类型：新连线替换旧入边，避免多源歧义。
 *  三视图拼板不在列：它要收正/侧/背三路。 */
const SINGLE_INPUT = new Set(['refine', 'matte', 'result', 'viewgen',
                              'walkvideo', 'sequence', 'spritesheet', 'play'])

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
      { type: 'threeview', label: '三视图拼板', dot: 'd-threeview',
        hint: '正/侧/背三张按共同基线拼成横向板' },
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
  viewgen: '生成视角', threeview: '三视图拼板',
  walkvideo: '行走视频', sequence: '序列帧',
  spritesheet: '精灵整图', play: '可操控预览',
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
  const [template, setTemplate] = useState<Template | null>(null)
  const [styles, setStyles] = useState<Style[]>([])
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
    Promise.all([
      api.getProject(projectId),
      api.styles(),
      api.promptExamples(),
      api.templates(),
    ]).then(([pr, st, ex, tp]) => {
      if (!alive) return
      const tpl = tp.templates.find((t) => t.id === pr.project.templateId) || null
      setProject(pr.project)
      setViewport(pr.project.viewport || { x: 80, y: 60, z: 1 })
      setStyles(st.styles)
      setBackgrounds(st.backgrounds)
      setExamples(ex.examples)
      setCategories(ex.categories)
      setTemplate(tpl)
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
      styleId: d.styleId || 'pixel_16bit',
      refs: [],
      params: {
        width: d.width || 512, height: d.height || 512, n: d.n || 1,
        steps: null, seed: null, background: (d as any).background || 'gray',
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

  // 离开时把未保存的改动压一次
  useEffect(() => () => {
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
    const d = template?.defaults || {}
    const node: GenNodeType = {
      id: newId('gen'),
      type: 'generate',
      x: Math.round(center.x - GEN_W / 2),
      y: Math.round(center.y - 160),
      prompt: '',
      styleId: (d as any).styleId || 'pixel_16bit',
      refs: [],
      params: {
        width: (d as any).width || 512, height: (d as any).height || 512,
        n: (d as any).n || 1, steps: null, seed: null,
        background: (d as any).background || 'gray',
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
    else if (type === 'viewgen') addViewGenNode(at, preset === 'back' ? 'back' : 'side')
    else if (type === 'threeview') addThreeViewNode(at)
    else if (type === 'walkvideo') addWalkVideoNode(at)
    else if (type === 'sequence') addSequenceNode(at)
    else if (type === 'spritesheet') addSpriteSheetNode(at)
    else if (type === 'play') addPlayNode(at)
    else addEmptyResultNode(at)
  }

  const addViewGenNode = (at: { x: number; y: number }, view: 'side' | 'back' = 'side') => {
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
        enabled: true, bg: '1B4332', gap: 12, baseline: 0.93, cell: null,
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
    const node: WalkVideoNodeType = {
      id: newId('wk'), type: 'walkvideo', x: spot.x, y: spot.y,
      walkvideo: {
        enabled: true, dirMode: 'eight', action: '行走', styleId: null,
        steps: null, seed: null, width: 384, height: 512,
        numFrames: 81, fps: 24, maxSide: 512, maxSeqLen: null,
      },
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

  // ---------- 角色三视图 / 八方向行走 ----------
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
   *  背视图不能拿侧视图当参考；精修 / 抠图这类透传节点取它们的结果。 */
  const resolveFrontOutput = (node: CanvasNode, runtime?: Map<string, ResultImage[]>) => {
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
          if (out?.images.length) return out
        }
        queue.push(src.id)
      }
    }
    throw new Error('找不到正视图：请把「生成图」或「选择图片」连到链条最上游，并确认它已经跑出结果。')
  }

  /** 节点的视角角色：视角节点看自己的 view，生成/选图就是正视图，
   *  其余（精修/抠图/结果）透传——角色由它自己的上游决定。
   *  path 只做环检测，不能跨分支共享，否则菱形连线会误判。 */
  const roleOf = (
    id: string, path: Set<string> = new Set(),
  ): 'front' | 'side' | 'back' | null => {
    if (path.has(id)) return null
    const p = projectRef.current
    const n = p?.nodes.find((x) => x.id === id)
    if (!n) return null
    if (n.type === 'viewgen') return n.viewgen?.view === 'back' ? 'back' : 'side'
    if (n.type === 'generate' || n.type === 'input') return 'front'
    const next = new Set(path)
    next.add(id)
    for (const e of (p?.edges || []).filter((x) => x.to === id)) {
      const r = roleOf(e.from, next)
      if (r) return r
    }
    return null
  }

  /** 收齐正/侧/背三路输入：先看连进来的入边，缺的沿链条回溯补。
   *  「生成图 → 侧视图 → 背视图 → 拼板」这条直线不用额外连线也能收齐三张。
   *  同时记下每张是从哪个节点收的，UI 上给个来源说明。 */
  const collectThreeViewOutputs = (
    node: ThreeViewNodeType, runtime?: Map<string, ResultImage[]>,
  ) => {
    const p = projectRef.current
    type Hit = { images: ResultImage[]; kind: 'images' | 'refs'; file: string; from: string }
    const found: Partial<Record<'front' | 'side' | 'back', Hit>> = {}
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
          else reject(new Error(prog.error || result?.error || '任务失败'))
        },
      )
      closers.current[nodeId] = close
    })

  const executeViewDerive = async (
    node: ViewGenNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<ResultImage> => {
    if (!project) throw new Error('项目未加载')
    const src = resolveFrontOutput(node, runtime)
    const im = src.images[0]
    const { taskId } = await api.threeviewDerive({
      projectId: project.id,
      origAssetId: src.kind === 'images' ? im.assetId : '',
      sourceKind: src.kind, sourceFile: src.kind === 'refs' ? src.file : undefined,
      view: node.viewgen?.view === 'back' ? 'back' : 'side',
      styleId: node.viewgen?.styleId ?? null,
      steps: node.viewgen?.steps ?? null,
      seed: node.viewgen?.seed ?? null,
      width: node.viewgen?.width ?? 512,
      height: node.viewgen?.height ?? 768,
      maxSeqLen: node.viewgen?.maxSeqLen ?? null,
    })
    activeTaskRef.current = taskId
    setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
    const r: any = await awaitTask<any>(taskId, node.id)
    if (activeTaskRef.current === taskId) activeTaskRef.current = null
    const out: any = r.image
    return {
      assetId: out.assetId, url: out.url, width: out.width, height: out.height,
      origAssetId: im.assetId, origUrl: im.url, origSize: [im.width, im.height],
      view: r.view,
      viewMeta: r.meta,
    }
  }

  const applyViewGenResult = (node: ViewGenNodeType, result: ResultImage) => {
    setProject((p) => p ? {
      ...p,
      nodes: p.nodes.map((n) => n.id === node.id
        ? ({ ...n, result } as CanvasNode) : n),
    } : p)
    pushDownstreamResult(node.id, [result], {})
    // 链条末端（没有下游）才补一张结果卡：中间节点补卡会让三视图链上多出废卡
    if (!projectRef.current?.edges.some((e) => e.from === node.id)) {
      ensureResultCard(node, [result], {})
    }
  }

  const runViewDerive = async (node: ViewGenNodeType) => {
    if (!node.viewgen?.enabled) return
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
    }
  }

  const executeCompose = async (
    node: ThreeViewNodeType, runtime?: Map<string, ResultImage[]>,
  ): Promise<ResultImage> => {
    if (!project) throw new Error('项目未加载')
    const found = collectThreeViewOutputs(node, runtime)
    const order: ('front' | 'side' | 'back')[] = ['front', 'side', 'back']
    const missing = order.filter((k) => !found[k])
    if (missing.length) {
      const cn: Record<string, string> = { front: '正视图', side: '侧视图', back: '背视图' }
      throw new Error(`三视图拼板还缺：${missing.map((m) => cn[m]).join('、')}。`
        + '把对应节点的输出连到本节点左侧，或保证链条上能回溯到它。')
    }
    const t = node.threeview
    const r = await api.threeviewCompose({
      projectId: project.id,
      sources: order.map((k) => {
        const s = found[k]!
        return { kind: s.kind, file: s.kind === 'images' ? s.images[0].assetId : s.file }
      }),
      bg: t?.bg || '1B4332',
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
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      const result = await executeCompose(node)
      applyComposeResult(node, result)
    } catch (e: any) {
      alert(`三视图拼板失败：${e.message || e}`)
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '' }), error: String(e.message || e) },
      }))
    }
  }

  /** 拼板节点要显示的三路输入（缺的也会列出来，让用户知道缺谁）。 */
  const threeViewSlots = (node: ThreeViewNodeType) => {
    const found = collectThreeViewOutputs(node)
    const cn: Record<string, string> = { front: '正视图', side: '侧视图', back: '背视图' }
    return (['front', 'side', 'back'] as const).map((k) => ({
      view: k, label: cn[k],
      image: found[k]?.images[0] ?? null,
      from: found[k]?.from ?? '',
    }))
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
    // 可操控预览只吃元数据（精灵表 URL + 帧索引），上游图片被清空也照样能播；
    // 其余链路节点确实要图，缺图就是没跑过上游。
    if (opts?.requireImages !== false && !out?.images.length) {
      throw new Error('上游还没有产出：先跑上一步的节点')
    }
    const src = p?.nodes.find((n) => n.id === inEdge.from)
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
    // 三视图锚点：沿链条回溯收集正/侧/背——有了它们，三个方向零推导、
    // 斜向只转 45 度，角色一致性明显更好
    const tv = collectThreeViewOutputs(node as any, runtime)
    const viewAssets: { role: string; file: string }[] = []
    for (const role of ['front', 'side', 'back'] as const) {
      const hit = tv[role]
      if (!hit || hit.kind !== 'images' || !hit.images.length) continue
      viewAssets.push({ role, file: `${hit.images[0].assetId}.png` })
    }
    const { taskId } = await api.walkVideo({
      projectId: project.id,
      origAssetId: src.kind === 'images' ? im.assetId : '',
      sourceKind: src.kind, sourceFile: src.kind === 'refs' ? src.file : undefined,
      // 三视图里没有正面时（例如上游是选图节点）退回单张正视图：
      // viewAssets 为空 → 后端按老链路从正视图推导
      viewAssets,
      dirImages: Object.entries(w?.dirImages || {})
        .filter(([, f]) => !!f)
        .map(([dir, file]) => ({ dir, file: file as string })),
      pixelArt: !!w?.pixelArt,
      dirMode: w?.dirMode ?? 'eight',
      action: w?.action ?? '行走',
      styleId: w?.styleId ?? null,
      steps: w?.steps ?? null,
      seed: w?.seed ?? null,
      width: w?.width ?? 384,
      height: w?.height ?? 512,
      numFrames: w?.numFrames ?? 81,
      fps: w?.fps ?? 24,
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
    try {
      const { images, meta } = await executePlay(node)
      applyPlayResult(node, images, meta)
    } catch (e: any) {
      alert(`预览组装失败：${e.message || e}`)
    }
  }

  const deleteNode = (id: string) => {
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
            else reject(new Error(prog.error || result?.error || '生成失败'))
          },
        )
        closers.current[node.id] = close
      }).catch(reject)
    })
  }

  /** 点生成卡上的「生成」时，把下游链条一并跑完（抠图 → 视角 → 拼板）。
   *  模板里预置了默认开启的链路，点一次生成就该直接拿到最终产物，
   *  而不是必须再点「运行工作流」。关掉的节点按透传处理，不断链。 */
  const autoDownstream = async (gen: GenNodeType, images: ResultImage[]) => {
    const p = projectRef.current
    if (!p) return { images, fromId: gen.id }
    const runtime = new Map<string, ResultImage[]>([[gen.id, images]])
    const metaRuntime = new Map<string, any>()
    let cur = images
    let fromId = gen.id
    const seen = new Set<string>([gen.id])
    for (;;) {
      const next = p.nodes.find((n): n is MatteNodeType | ViewGenNodeType | ThreeViewNodeType
        | WalkVideoNodeType | SequenceNodeType | SpriteSheetNodeType | PlayNodeType =>
        !seen.has(n.id)
        && p.edges.some((e) => e.from === fromId && e.to === n.id)
        && (n.type === 'matte' || n.type === 'viewgen' || n.type === 'threeview'
          || n.type === 'walkvideo' || n.type === 'sequence'
          || n.type === 'spritesheet' || n.type === 'play'))
      if (!next) break
      seen.add(next.id)
      if (next.type === 'matte') {
        if (!next.matte?.enabled) { fromId = next.id; continue }
        setMatting((s) => ({ ...s, [next.id]: true }))
        try {
          const out: ResultImage[] = []
          for (const im of cur) out.push(await executeMatte(next, im))
          applyMatteResult(next, out)
          cur = out
          runtime.set(next.id, out)
          fromId = next.id
        } finally {
          setMatting((s) => ({ ...s, [next.id]: false }))
        }
      } else if (next.type === 'viewgen') {
        if (!next.viewgen?.enabled) {
          runtime.set(next.id, cur)
          pushDownstreamResult(next.id, cur, {})
          fromId = next.id
          continue
        }
        try {
          const r = await executeViewDerive(next, runtime)
          applyViewGenResult(next, r)
          cur = [r]
          runtime.set(next.id, cur)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`生成视角失败：${e.message || e}`)
        }
      } else if (next.type === 'threeview') {
        if (!next.threeview?.enabled) {
          runtime.set(next.id, cur)
          fromId = next.id
          continue
        }
        try {
          const r = await executeCompose(next, runtime)
          applyComposeResult(next, r)
          cur = [r]
          runtime.set(next.id, cur)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`三视图拼板失败：${e.message || e}`)
        }
      } else if (next.type === 'walkvideo') {
        if (!next.walkvideo?.enabled) {
          runtime.set(next.id, cur)
          pushDownstreamResult(next.id, cur, {})
          fromId = next.id
          continue
        }
        try {
          const { images: imgs, meta } = await executeWalkVideo(next, runtime)
          applyWalkVideoResult(next, imgs, meta)
          cur = imgs
          runtime.set(next.id, imgs)
          metaRuntime.set(next.id, meta)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`行走视频失败：${e.message || e}`)
        }
      } else if (next.type === 'sequence') {
        if (!next.sequence?.enabled) {
          runtime.set(next.id, cur)
          pushDownstreamResult(next.id, cur, {})
          fromId = next.id
          continue
        }
        try {
          const { images: imgs, meta } = await executeSequence(next, runtime, metaRuntime)
          applySequenceResult(next, imgs, meta)
          cur = imgs
          runtime.set(next.id, imgs)
          metaRuntime.set(next.id, meta)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`序列帧失败：${e.message || e}`)
        }
      } else if (next.type === 'spritesheet') {
        if (!next.spritesheet?.enabled) {
          runtime.set(next.id, cur)
          pushDownstreamResult(next.id, cur, {})
          fromId = next.id
          continue
        }
        try {
          const { images: imgs, meta } = await executeSpriteSheet(next, runtime, metaRuntime)
          applySpriteSheetResult(next, imgs, meta)
          cur = imgs
          runtime.set(next.id, imgs)
          metaRuntime.set(next.id, meta)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`精灵整图失败：${e.message || e}`)
        }
      } else {
        if (!next.play?.enabled) {
          runtime.set(next.id, cur)
          pushDownstreamResult(next.id, cur, {})
          fromId = next.id
          continue
        }
        try {
          const { images: imgs, meta } = await executePlay(next, runtime, metaRuntime)
          applyPlayResult(next, imgs, meta)
          cur = imgs
          runtime.set(next.id, imgs)
          metaRuntime.set(next.id, meta)
          fromId = next.id
        } catch (e: any) {
          throw new Error(`可操控预览失败：${e.message || e}`)
        }
      }
    }
    return { images: cur, fromId }
  }

  const generate = async (node: GenNodeType) => {
    try {
      const result = await executeGenerate(node)
      // 模板默认带的链路（扣背景 / 视角 / 拼板）跟着一起跑完
      const hasDownstream = !!projectRef.current?.edges.some((e) => e.from === node.id)
      const run = hasDownstream ? await autoDownstream(node, result.images) : null
      addResultNode(node, run?.images ?? result.images, result.prompt, result.negative,
                    result.meta, {
                      fromId: run?.fromId ?? node.id,
                      lastImages: result.images,
                      deferCreate: hasDownstream,
                    })
    } catch (e: any) {
      setTasks((t) => ({
        ...t,
        [node.id]: { ...(t[node.id] || { taskId: '', progress: null }), error: String(e.message || e) },
      }))
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
    // 关掉的后处理节点也算一步：它只是透传，不参与执行，但要负责把图交给下游结果卡
    const isRunner = (n: CanvasNode): n is RunnerNode =>
      n.type === 'generate' || n.type === 'refine' || n.type === 'matte'
      || n.type === 'viewgen' || n.type === 'threeview'
      || n.type === 'walkvideo' || n.type === 'sequence'
      || n.type === 'spritesheet' || n.type === 'play'
    const byId = new Map(project.nodes.map((n) => [n.id, n]))
    const executable = project.nodes.filter(isRunner)
    if (!executable.length) {
      alert('画布上没有可执行的节点：先添加「生成图」「像素精修」「扣除背景」「视角生成」或「八方向行走」节点并连好线。')
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
            ? (n.viewgen?.view === 'back' ? '背视图' : '侧视图') : '跳过视角')
          : n.type === 'threeview' ? (n.threeview?.enabled ? '三视图拼板' : '跳过拼板')
          : n.type === 'walkvideo' ? (n.walkvideo?.enabled ? '行走视频' : '跳过行走视频')
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
            continue
          }
          try {
            const result = await executeViewDerive(n, runtime)
            applyViewGenResult(n, result)
            runtime.set(n.id, [result])
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（${kind}）：${e.message || e}`)
          }
          continue
        }
        if (n.type === 'threeview') {
          if (!n.threeview?.enabled) {
            const up = collectThreeViewOutputs(n, runtime)
            runtime.set(n.id, up.back?.images || up.front?.images || [])
            continue
          }
          try {
            const result = await executeCompose(n, runtime)
            applyComposeResult(n, result)
            runtime.set(n.id, [result])
          } catch (e: any) {
            throw new Error(`第 ${i + 1} 步（三视图拼板）：${e.message || e}`)
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
            if (up.length) pushDownstreamResult(n.id, up, {})
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
          // 否则三视图链上会先在生成卡旁边多出一张只放原图的废卡
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
            if (up.length) pushDownstreamResult(n.id, up, {})
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
  const walkVideoNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is WalkVideoNodeType => n.type === 'walkvideo'),
    [project?.nodes],
  )
  const sequenceNodes = useMemo(
    () => (project?.nodes || []).filter((n): n is SequenceNodeType => n.type === 'sequence'),
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
  if (!project || !template) {
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
              running={!!tasks[n.id]?.progress && !tasks[n.id]?.progress?.doneAt}
              sourceLabel={frontLabel(n.id)}
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
              running={!!tasks[n.id]?.progress && !tasks[n.id]?.progress?.doneAt}
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
              running={!!tasks[n.id]?.progress && !tasks[n.id]?.progress?.doneAt}
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

          {sequenceNodes.map((n, i) => (
            <SequenceNode
              key={n.id}
              node={n}
              index={i}
              running={!!tasks[n.id]?.progress && !tasks[n.id]?.progress?.doneAt}
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
              running={!!tasks[n.id]?.progress && !tasks[n.id]?.progress?.doneAt}
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
              onCopyPrompt={() => undefined}
              onFinishLink={finishLink}
            />
          ))}
        </div>
      </div>

      {/* 顶部项目栏 */}
      <div className="canvas-topbar">
        <button className="btn btn-sm" onClick={onBack}>← 项目</button>
        <input
          className="name-input"
          value={project.name}
          onChange={(e) => setProject({ ...project, name: e.target.value })}
          onBlur={() => { if (!project.name.trim()) setProject({ ...project, name: '未命名项目' }) }}
        />
        <span className="tpl-tag">{template.name}</span>
        <span className="save-state">
          {saveState === 'saving' ? '保存中…'
            : saveState === 'dirty' ? '未保存'
            : saveState === 'error' ? '保存失败'
            : lastSaved ? `已保存 ${new Date(lastSaved).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}`
            : '已保存'}
        </span>
        <button className="btn btn-sm" onClick={saveAsTemplate} title="把当前节点图存成自定义模板">
          存为模板
        </button>
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
