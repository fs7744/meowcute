import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribeTask } from '../api'
import GenNode from '../components/GenNode'
import InputNode from '../components/InputNode'
import MatteNode from '../components/MatteNode'
import PreviewOverlay from '../components/PreviewOverlay'
import ProjectFiles from '../components/ProjectFiles'
import PromptModal from '../components/PromptModal'
import RefineNode from '../components/RefineNode'
import ResultNode from '../components/ResultNode'
import type {
  BackgroundOption, CanvasNode, GenNode as GenNodeType, GenerateResult,
  InputNode as InputNodeType, MatteNode as MatteNodeType, Project, PromptExample,
  PromptPreview, RefineNode as RefineNodeType, ResultImage, ResultMeta,
  ResultNode as ResultNodeType, Style, TaskProgress, Template,
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
const NODE_W: Record<string, number> = {
  generate: GEN_W, result: RES_W, input: INPUT_W, refine: REFINE_W, matte: MATTE_W,
}

/** 哪些类型的节点可以作为某个节点的上游（决定自定义工作流是否合法）。 */
const UPSTREAM_RULES: Record<string, string[]> = {
  generate: ['input'],
  refine: ['input', 'generate', 'refine', 'matte', 'result'],
  matte: ['input', 'generate', 'refine', 'matte', 'result'],
  result: ['generate', 'refine', 'matte'],
}

const UPSTREAM_HINT: Record<string, string> = {
  generate: '选择图片',
  refine: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  matte: '选择图片 / 生成图 / 像素精修 / 扣除背景 / 图片结果',
  result: '生成图 / 像素精修 / 扣除背景',
}

function canConnect(from: CanvasNode | undefined, to: CanvasNode | undefined): boolean {
  if (!from || !to || from.id === to.id) return false
  const allowed = UPSTREAM_RULES[to.type]
  return !!allowed && allowed.includes(from.type)
}

/** 只能接收单条入边的类型：新连线替换旧入边，避免多源歧义。 */
const SINGLE_INPUT = new Set(['refine', 'matte', 'result'])

/** 右键「添加节点」的目录：分类 → 具体节点。
 *  节点类型只会越来越多，平铺成一条长列表很快就用不了了。 */
const NODE_CATALOG: {
  id: string
  label: string
  hint: string
  items: { type: CanvasNode['type']; label: string; hint: string; dot: string }[]
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
    id: 'post', label: '后处理', hint: '不跑模型，纯确定性图像处理',
    items: [
      { type: 'refine', label: '像素精修', dot: 'd-refine',
        hint: '网格对齐 + 调色板量化，输出原生像素尺寸' },
      { type: 'matte', label: '扣除背景', dot: 'd-matte',
        hint: 'u2net / GrabCut 抠图，输出透明底 PNG' },
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

  /** 右键菜单的统一入口：按类型分发到对应的添加函数。 */
  const addNodeAt = (type: CanvasNode['type'], at: { x: number; y: number }) => {
    if (type === 'input') addInputNode(at)
    else if (type === 'generate') addGenNode(at)
    else if (type === 'refine') addRefineNode(at)
    else if (type === 'matte') addMatteNode(at)
    else addEmptyResultNode(at)
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

  /** 单张生成卡直接点「生成」时，自动把下游的扣背景节点跑掉。
   *  模板里放了默认开启的抠图节点，用户点一次生成就该直接拿到透明底图，
   *  而不是必须再点「运行工作流」。关掉的节点按透传处理。 */
  const autoMatte = async (gen: GenNodeType, images: ResultImage[]) => {
    const chain = matteChain(gen.id)
    if (!chain.length) return { images, fromId: gen.id }
    let cur = images
    let fromId = gen.id
    for (const node of chain) {
      if (!node.matte?.enabled) {
        // 透传：图片原样交给下游，但下游结果卡仍要拿到图
        pushDownstreamResult(node.id, cur, {})
        fromId = node.id
        continue
      }
      setMatting((s) => ({ ...s, [node.id]: true }))
      try {
        const out: ResultImage[] = []
        for (const im of cur) out.push(await executeMatte(node, im))
        applyMatteResult(node, out)
        cur = out
        fromId = node.id
      } finally {
        setMatting((s) => ({ ...s, [node.id]: false }))
      }
    }
    return { images: cur, fromId }
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

  const generate = async (node: GenNodeType) => {
    try {
      const result = await executeGenerate(node)
      // 模板默认带一个开启的扣背景节点：点一次生成就直接出透明底图
      const chain = matteChain(node.id)
      const run = chain.length ? await autoMatte(node, result.images) : null
      addResultNode(node, run?.images ?? result.images, result.prompt, result.negative,
                    result.meta, {
                      fromId: run?.fromId ?? node.id,
                      lastImages: result.images,
                      deferCreate: chain.length > 0,
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

  /** 一键运行：按连线拓扑顺序跑完所有生成/精修/扣背景节点，结果自动落到结果卡。 */
  const runWorkflow = async () => {
    if (!project || wf.running) return
    type RunnerNode = GenNodeType | RefineNodeType | MatteNodeType
    // 关掉的扣背景节点也算一步：它只是透传，不参与执行，但要负责把图交给下游结果卡
    const isRunner = (n: CanvasNode): n is RunnerNode =>
      n.type === 'generate' || n.type === 'refine' || n.type === 'matte'
    const byId = new Map(project.nodes.map((n) => [n.id, n]))
    const executable = project.nodes.filter(isRunner)
    if (!executable.length) {
      alert('画布上没有可执行的节点：先添加「生成图」「像素精修」或「扣除背景」并连好线。')
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
    try {
      for (let i = 0; i < order.length; i++) {
        if (abortRef.current) throw new Error('已停止')
        const n = order[i]
        const kind = n.type === 'generate' ? '生成' : n.type === 'refine' ? '精修'
          : n.matte?.enabled ? '扣背景' : '跳过扣背景'
        setWf({
          running: true, done: i, total: order.length, error: null,
          label: `${kind} ${i + 1}/${order.length}`,
        })
        const inEdge = project.edges.find((e) => e.to === n.id)
        const upstream = inEdge ? runtime.get(inEdge.from) : undefined
        if (n.type === 'generate') {
          if (!n.prompt.trim()) throw new Error(`第 ${i + 1} 步：生成节点的提示词为空`)
          const r = await executeGenerate(n)
          const chain = matteChain(n.id)
          addResultNode(n, r.images, r.prompt, r.negative, r.meta, {
            lastImages: r.images,
            // 结果卡挂在这条链的末端（跑了抠图就挂到抠图卡上），
            // 否则会多建一张空卡；提示词先落到卡上，抠图之后只覆盖图片
            fromId: chain.length ? chain[chain.length - 1].id : n.id,
            deferCreate: chain.length > 0,
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

  /** 某个节点的入边来自哪一类上游（节点卡上显示「输入：xxx」）。 */
  const upstreamLabel = useCallback((id: string): string | null => {
    const e = project?.edges.find((x) => x.to === id)
    const s = e ? project?.nodes.find((x) => x.id === e.from) : null
    return s ? nodeLabel(s.type) : null
  }, [project?.edges, project?.nodes])

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
                  <button key={it.type}
                    onClick={() => {
                      addNodeAt(it.type, { x: ctxMenu.wx, y: ctxMenu.wy })
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
