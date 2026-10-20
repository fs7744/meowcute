import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, subscribeTask } from '../api'
import GenNode from '../components/GenNode'
import InputNode from '../components/InputNode'
import PreviewOverlay from '../components/PreviewOverlay'
import ProjectFiles from '../components/ProjectFiles'
import PromptModal from '../components/PromptModal'
import RefineNode from '../components/RefineNode'
import ResultNode from '../components/ResultNode'
import type {
  BackgroundOption, CanvasNode, GenNode as GenNodeType, InputNode as InputNodeType,
  Project, PromptExample, PromptPreview, RefineNode as RefineNodeType, ResultImage,
  ResultMeta, ResultNode as ResultNodeType, Style, TaskProgress, Template,
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
const NODE_W: Record<string, number> = {
  generate: GEN_W, result: RES_W, input: INPUT_W, refine: REFINE_W,
}

/** 哪些节点可以连到哪些节点（决定自定义工作流是否合法）。 */
function canConnect(from: CanvasNode | undefined, to: CanvasNode | undefined): boolean {
  if (!from || !to || from.id === to.id) return false
  if (to.type === 'generate') return from.type === 'input'
  if (to.type === 'refine') return from.type === 'input' || from.type === 'generate'
  if (to.type === 'result') return from.type === 'generate' || from.type === 'refine'
  return false
}

function newId(prefix: string) {
  return `${prefix}-${Math.random().toString(36).slice(2, 10)}`
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
        gridSize: null, targetSize: null, ...(d.pixel || {}),
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

  const addGenNode = () => {
    if (!project) return
    const rect = viewportRef.current?.getBoundingClientRect()
    const center = rect
      ? toWorld(rect.left + rect.width / 2, rect.top + rect.height / 2)
      : { x: 200, y: 160 }
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
        gridSize: null, targetSize: null, ...((d as any).pixel || {}),
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

  const addInputNode = () => {
    if (!project) return
    const spot = canvasSpot(INPUT_W, 240)
    const node: InputNodeType = {
      id: newId('inp'), type: 'input', x: spot.x, y: spot.y,
      refs: [], label: '', createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addRefineNode = () => {
    if (!project) return
    const spot = canvasSpot(REFINE_W, 360)
    const node: RefineNodeType = {
      id: newId('rf'), type: 'refine', x: spot.x, y: spot.y,
      pixel: {
        enabled: true, gridSnap: true, colors: 16, palette: 'auto',
        gridSize: null, targetSize: null,
      },
      result: null, createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const addEmptyResultNode = () => {
    if (!project) return
    const spot = canvasSpot(RES_W, 320)
    const node: ResultNodeType = {
      id: newId('res'), type: 'result', x: spot.x, y: spot.y, genId: null,
      images: [], prompt: '', negative: '', meta: {},
      createdAt: Date.now() / 1000,
    }
    setProject((p) => p ? { ...p, nodes: [...p.nodes, node] } : p)
    setSelected(node.id)
  }

  const startLink = (id: string) => {
    setLinkFrom(id)
    setSelected(id)
  }

  const finishLink = (toId: string) => {
    if (!project || !linkFrom) return
    const fromNode = project.nodes.find((n) => n.id === linkFrom)
    const toNode = project.nodes.find((n) => n.id === toId)
    if (!canConnect(fromNode, toNode)) {
      alert('这两类节点不能这样连。可用：选择图片 → 生成图/精修；生成图 → 精修/结果；精修 → 结果')
      return
    }
    setProject((p) => {
      if (!p) return p
      // 精修/结果只保留一条入边：新连线替换旧入边，避免多源歧义
      const kept = p.edges.filter((e) => !(e.to === toId && (toNode?.type === 'refine' || toNode?.type === 'result')))
      if (kept.some((e) => e.from === linkFrom && e.to === toId)) return { ...p, edges: kept }
      return { ...p, edges: [...kept, { from: linkFrom, to: toId }] }
    })
    setLinkFrom(null)
  }

  const runRefine = async (node: RefineNodeType) => {
    if (!project) return
    const inEdge = project.edges.find((e) => e.to === node.id)
    const src = inEdge ? project.nodes.find((n) => n.id === inEdge.from) : null
    let srcKind: 'images' | 'refs' = 'images'
    let srcAssetId = ''
    let srcFile = ''
    let srcUrl = ''
    let srcW = 0
    let srcH = 0
    if (src?.type === 'generate') {
      const im = src.lastImages?.[0]
      if (im) { srcAssetId = im.assetId; srcUrl = im.url; srcW = im.width; srcH = im.height }
    } else if (src?.type === 'result') {
      const im = src.images?.[0]
      if (im) { srcAssetId = im.assetId; srcUrl = im.url; srcW = im.width; srcH = im.height }
    } else if (src?.type === 'input') {
      const r0 = src.refs?.[0]
      if (r0) {
        srcKind = 'refs'; srcFile = r0.file; srcAssetId = r0.id
        srcUrl = r0.url; srcW = r0.width; srcH = r0.height
      }
    }
    if (!srcUrl || (!srcAssetId && !srcFile)) {
      alert('精修节点没有可用输入：请把「选择图片」或「生成图」的输出连到它的左侧端口。')
      return
    }
    setRefining((s) => ({ ...s, [node.id]: true }))
    try {
      const r = await api.refine({
        projectId: project.id, origAssetId: srcAssetId,
        sourceKind: srcKind, sourceFile: srcFile || undefined,
        pixel: node.pixel,
      })
      const im: any = r.image
      const result: ResultImage = {
        assetId: im.assetId, url: im.url, width: im.width, height: im.height,
        origAssetId: srcAssetId, origUrl: srcUrl,
        origSize: [srcW, srcH],
        refine: im.refine,
      }
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
    } catch (e: any) {
      alert(`精修失败：${e.message || e}`)
    } finally {
      setRefining((s) => ({ ...s, [node.id]: false }))
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

  // Delete 键删除选中节点（输入框内不拦截）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Delete' && e.key !== 'Backspace') return
      const tag = (document.activeElement?.tagName || '').toLowerCase()
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return
      if (!selected) return
      deleteNode(selected)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [selected, project])

  // ---------- 生成 ----------
  const generate = async (node: GenNodeType) => {
    if (!project) return
    setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: null } }))
    try {
      // 上游「选择图片」连进来时，用它作为参考图；否则用生成卡自带的参考图
      const inEdge = project.edges.find((e) => e.to === node.id
        && project.nodes.find((n) => n.id === e.from)?.type === 'input')
      const up = inEdge ? project.nodes.find((n) => n.id === inEdge.from) : null
      const refFiles = up?.type === 'input' && up.refs.length
        ? up.refs.map((r) => r.file)
        : node.refs.map((r) => r.file)
      const { taskId } = await api.generate({
        projectId: project.id,
        genNodeId: node.id,
        prompt: node.prompt,
        styleId: node.styleId,
        refs: refFiles,
        params: node.params,
        pixel: node.pixel,
      })
      setTasks((t) => ({ ...t, [node.id]: { taskId, progress: null, error: null } }))
      closers.current[node.id] = subscribeTask(
        taskId,
        (p) => setTasks((t) => ({ ...t, [node.id]: { ...(t[node.id] || { taskId, error: null }), progress: p } })),
        (result, prog) => {
          setTasks((t) => ({
            ...t,
            [node.id]: {
              taskId,
              progress: prog,
              error: prog.error || result?.error || null,
            },
          }))
          if (result && !result.error && result.images?.length) {
            addResultNode(node, result.images, result.prompt, result.negative, result.meta)
          }
        },
      )
    } catch (e: any) {
      setTasks((t) => ({ ...t, [node.id]: { taskId: '', progress: null, error: String(e.message || e) } }))
    }
  }

  const addResultNode = (
    gen: GenNodeType,
    images: ResultImage[],
    prompt: string,
    negative: string,
    meta: ResultMeta,
  ) => {
    setProject((p) => {
      if (!p) return p
      // 记住生成卡的最新输出，供下游「像素精修」节点取图
      const withLast = p.nodes.map((n) =>
        n.id === gen.id ? ({ ...n, lastImages: images } as CanvasNode) : n)
      // 已经手动连到结果卡：把结果填进去，而不是再新建一张
      const linked = p.nodes.find((n) =>
        n.type === 'result' && p.edges.some((e) => e.from === gen.id && e.to === n.id))
      if (linked) {
        return {
          ...p,
          nodes: withLast.map((n) => n.id === linked.id
            ? ({ ...n, images, prompt, negative, meta, genId: gen.id } as CanvasNode) : n),
        }
      }
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
        edges: [...p.edges, { from: gen.id, to: node.id }],
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
      return (
        <path key={`${e.from}-${e.to}-${i}`}
          d={`M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`}
          fill="none" stroke="rgba(0,255,136,0.55)" strokeWidth={2}
          strokeDasharray="0" />
      )
    })
  }, [project])

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
    <div className="canvas-root">
      <div
        ref={viewportRef}
        className={`canvas-viewport ${panning ? 'panning' : ''}`}
        onMouseDown={startPan}
        onWheel={onWheel}
        onContextMenu={(e) => {
          // 图片保留浏览器原生右键菜单：素材工具里「右键另存为」是刚需，
          // 只拦截空白画布的右键（避免和将来的右键菜单冲突）
          const t = e.target as HTMLElement
          if (t.closest('img, .result-img-wrap, .ref-thumb')) return
          e.preventDefault()
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
          <svg className="canvas-links" width={1} height={1}>{edges}</svg>

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
            />
          ))}

          {refineNodes.map((n, i) => (
            <RefineNode
              key={n.id}
              node={n}
              index={i}
              running={!!refining[n.id]}
              sourceLabel={(() => {
                const e = project.edges.find((x) => x.to === n.id)
                const s = e ? project.nodes.find((x) => x.id === e.from) : null
                if (!s) return null
                return s.type === 'generate' ? '生成图'
                  : s.type === 'input' ? '选择图片'
                  : s.type === 'refine' ? '精修' : '结果'
              })()}
              onChange={(patch) => patchNode(n.id, patch)}
              onDelete={() => deleteNode(n.id)}
              onDragStart={startDragNode(n.id)}
              onRun={() => runRefine(n)}
              onStartLink={startLink}
              onFinishLink={finishLink}
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
        <button className="btn btn-sm" onClick={addInputNode}>＋ 选图</button>
        <button className="btn btn-sm" onClick={addGenNode}>＋ 生成图</button>
        <button className="btn btn-sm" onClick={addRefineNode}>＋ 像素精修</button>
        <button className="btn btn-sm" onClick={addEmptyResultNode}>＋ 结果</button>
        <button className="btn btn-sm" onClick={() => doSave()}>立即保存</button>
      </div>

      <div className="canvas-hint">
        <b>滚轮</b> 缩放 · <b>拖拽空白</b> 平移 · <b>拖节点标题</b> 移动 · <b>Delete</b> 删除选中
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
