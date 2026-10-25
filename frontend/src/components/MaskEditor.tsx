import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { InpaintMaskInfo } from '../types'

type Tool = 'brush' | 'eraser' | 'rect'

type Op =
  | { t: 'brush'; pts: number[][]; size: number; erase: boolean }
  | { t: 'rect'; x0: number; y0: number; x1: number; y1: number; erase: boolean }

type Props = {
  open: boolean
  projectId: string
  /** 要涂抹的源图（同尺寸的蒙版才有意义，所以尺寸必须一起给） */
  srcUrl: string
  srcW: number
  srcH: number
  /** 上次涂的蒙版（可继续编辑） */
  initialMaskUrl?: string | null
  title?: string
  onSaved: (mask: InpaintMaskInfo) => void
  onClose: () => void
}

/** 显示透明度的默认值：留 60% 让下面的图透出来——涂错位置时能一眼看见。 */
const OVERLAY_ALPHA_DEFAULT = 0.4
/** 下限不是 0：完全透明等于没涂，用户会以为笔刷坏了。8% 已经几乎只看得到轮廓。 */
const OVERLAY_ALPHA_MIN = 0.08
/** 常用蒙版色：撞色时一键换。红/绿/蓝/黄/白/黑覆盖了绝大多数画面底色。 */
const SWATCHES = ['#ff3b6b', '#00e08a', '#2f8bff', '#ffd400', '#ffffff', '#000000']

/** #rrggbb + alpha → rgba()，供 canvas 的 fillStyle/strokeStyle 直接用。 */
function tint(hex: string, alpha = 1): string {
  const h = (hex || '').replace('#', '')
  const v = h.length === 3 ? h.split('').map((c) => c + c).join('') : h
  const n = Number.parseInt(v || 'ff3b6b', 16)
  const r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/**
 * 涂抹蒙版编辑器（全屏弹层）。
 *
 * 三层画布叠加，各管一件事：
 * - **图**：源图，只在尺寸变化时重画；
 * - **蒙版**：白色，导出成 PNG 交给后端（白=重绘区），只在拖动时增量画；
 * - **覆盖**：半透明红，给眼睛看的，擦除时 `destination-out` 只擦它自己，
 *   下面的图不会被擦掉。
 *
 * 撤销做成**操作列表 + 全量重放**而不是存 ImageData 快照：1024² 的快照是
 * 4MB/步，撤销 20 步就是 80MB 显存；操作列表每步只有几十字节，重放一张
 * 1024² 的图在几毫秒内完成。
 *
 * 笔刷一律硬边：柔和边界交给后端的「羽化」，那里是按像素算的、可复现；
 * 在画布上做软笔刷只会得到一层不稳定的半透明，还让「选区外不动」的承诺
 * 变得没法验证。
 */
export default function MaskEditor({
  open, projectId, srcUrl, srcW, srcH, initialMaskUrl, title, onSaved, onClose,
}: Props) {
  const [tool, setTool] = useState<Tool>('brush')
  const [size, setSize] = useState(() => Math.max(8, Math.round(Math.min(srcW, srcH) / 12)))
  /** 蒙版显示色（笔刷与覆盖层同色）。默认亮红；画面本身偏红/粉时换掉。
   *  记在 localStorage：换过一次颜色的人不希望每次重开又要再挑一遍。 */
  const [overlayHex, setOverlayHex] = useState(() => {
    try { return localStorage.getItem('pf.maskColor') || '#ff3b6b' } catch { return '#ff3b6b' }
  })
  /** 蒙版**显示**透明度（只影响覆盖层，不影响导出的蒙版）。
   *
   *  两个作用：调低能看清下面的图（判断有没有涂错位置），调高能在杂乱背景上
   *  确认哪里涂到了。和颜色一样记在 localStorage。
   */
  const [overlayAlpha, setOverlayAlpha] = useState(() => {
    try {
      const v = Number(localStorage.getItem('pf.maskAlpha'))
      return Number.isFinite(v) && v >= OVERLAY_ALPHA_MIN && v <= 1
        ? v : OVERLAY_ALPHA_DEFAULT
    } catch { return OVERLAY_ALPHA_DEFAULT }
  })
  /** 按住 H 临时隐藏蒙版：用原图核对「到底涂到哪了」，松手恢复。 */
  const [peek, setPeek] = useState(false)
  /** 覆盖层一律用**不透明**色绘制，透明度交给 canvas 元素的 CSS opacity。
   *
   *  这不是实现细节：如果直接把 rgba(...,0.4) 画进画布，同一笔拖动时相邻线段
   *  会互相叠加（0.4 → 0.64 → 0.78…），涂过两遍的地方比涂一遍深，看起来像
   *  「有的地方没涂上」。用不透明色 + CSS opacity，叠加多少次都是同一个深度。
   */
  const overlaySolid = useMemo(() => tint(overlayHex, 1), [overlayHex])
  useEffect(() => {
    try { localStorage.setItem('pf.maskColor', overlayHex) } catch { /* 隐私模式忽略 */ }
  }, [overlayHex])
  useEffect(() => {
    try { localStorage.setItem('pf.maskAlpha', String(overlayAlpha)) } catch { /* 同上 */ }
  }, [overlayAlpha])
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [cursor, setCursor] = useState<{ x: number; y: number } | null>(null)
  /** 供撤销/重做触发的重绘计数（ops 用 ref 存，避免每次落笔都 re-render） */
  const [tick, setTick] = useState(0)


  const imgRef = useRef<HTMLCanvasElement | null>(null)
  const maskRef = useRef<HTMLCanvasElement | null>(null)
  const overRef = useRef<HTMLCanvasElement | null>(null)
  const bandRef = useRef<HTMLCanvasElement | null>(null)
  const wrapRef = useRef<HTMLDivElement | null>(null)
  const stageRef = useRef<HTMLDivElement | null>(null)

  /** 历史蒙版（从文件载入的那部分）单独存一张底图：它不是操作列表，
   *  撤销重放时必须先把底图铺回去，否则撤销一步会把「上次涂的区域」一起抹掉。 */
  const baseRef = useRef<HTMLCanvasElement | null>(null)
  const opsRef = useRef<Op[]>([])
  const undosRef = useRef<Op[]>([])
  const drawing = useRef<Op | null>(null)
  const panning = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  const spaceRef = useRef(false)

  const ensureBase = useCallback(() => {
    let b = baseRef.current
    if (!b) {
      b = document.createElement('canvas')
      baseRef.current = b
    }
    if (b.width !== srcW || b.height !== srcH) {
      b.width = srcW
      b.height = srcH
    }
    return b
  }, [srcW, srcH])

  /** 把一条操作画到指定画布上（color=null 表示用 destination-out 擦除）。 */
  const paint = useCallback((ctx: CanvasRenderingContext2D, op: Op, color: string | null) => {
    ctx.save()
    ctx.globalCompositeOperation = color ? 'source-over' : 'destination-out'
    ctx.fillStyle = color || '#000'
    ctx.strokeStyle = color || '#000'
    if (op.t === 'brush') {
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.lineWidth = op.size
      const pts = op.pts
      if (pts.length === 1) {
        ctx.beginPath()
        ctx.arc(pts[0][0], pts[0][1], op.size / 2, 0, Math.PI * 2)
        ctx.fill()
      } else {
        ctx.beginPath()
        ctx.moveTo(pts[0][0], pts[0][1])
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0], pts[i][1])
        ctx.stroke()
      }
    } else {
      const x = Math.min(op.x0, op.x1), y = Math.min(op.y0, op.y1)
      ctx.fillRect(x, y, Math.abs(op.x1 - op.x0), Math.abs(op.y1 - op.y0))
    }
    ctx.restore()
  }, [])

  /** 一次性重画蒙版层与覆盖层（撤销/重做/清空/反选都用它）。 */
  const redraw = useCallback((ops: Op[]) => {
    const mask = maskRef.current
    const over = overRef.current
    if (!mask || !over) return
    const mc = mask.getContext('2d')
    const oc = over.getContext('2d')
    if (!mc || !oc) return
    mc.clearRect(0, 0, mask.width, mask.height)
    oc.clearRect(0, 0, over.width, over.height)
    const base = baseRef.current
    if (base && base.width) {
      mc.drawImage(base, 0, 0)
      oc.save()
      oc.drawImage(base, 0, 0)
      oc.globalCompositeOperation = 'source-in'
      oc.fillStyle = overlaySolid
      oc.fillRect(0, 0, over.width, over.height)
      oc.restore()
    }
    for (const op of ops) {
      paint(mc, op, op.t === 'rect' ? '#fff' : (op.erase ? null : '#fff'))
      paint(oc, op, op.erase ? null : overlaySolid)
    }
  }, [paint, overlaySolid])

  // 换蒙版色时把已有内容重涂一遍（底图 + 操作列表都按新色重画）
  useEffect(() => {
    if (!open) return
    redraw(opsRef.current)
  }, [overlaySolid, open, redraw])

  // ---- 打开时初始化三块画布（尺寸 = 源图像素尺寸） ----
  useEffect(() => {
    if (!open) return
    opsRef.current = []
    undosRef.current = []
    drawing.current = null
    setDirty(false)
    setPan({ x: 0, y: 0 })
    const stage = stageRef.current
    const fit = stage
      ? Math.min(1, (stage.clientWidth - 120) / srcW, (stage.clientHeight - 120) / srcH)
      : 1
    setZoom(Math.max(0.05, fit))
    setSize(Math.max(8, Math.round(Math.min(srcW, srcH) / 12)))
    setTool('brush')
    setTick((n) => n + 1)
  }, [open, srcW, srcH, srcUrl])

  // 画源图 + 载入已有蒙版
  useEffect(() => {
    if (!open) return
    const canvas = imgRef.current
    if (!canvas) return
    canvas.width = srcW
    canvas.height = srcH
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.imageSmoothingEnabled = false
    const img = new Image()
    img.onload = () => {
      ctx.clearRect(0, 0, srcW, srcH)
      ctx.drawImage(img, 0, 0, srcW, srcH)
    }
    img.src = srcUrl

    for (const c of [maskRef.current, overRef.current, bandRef.current]) {
      if (c) { c.width = srcW; c.height = srcH }
    }
    if (initialMaskUrl) {
      const m = new Image()
      m.onload = () => {
        const mask = maskRef.current
        const over = overRef.current
        const mc = mask?.getContext('2d')
        if (!mask || !over || !mc) return
        // 统一成「白 + alpha」的语义再画：历史蒙版是灰阶不透明 PNG
        // （白色=重绘区，但整张不透明），直接 source-in 会把整幅图上成红色。
        // 这里把亮度折进 alpha，灰阶与 alpha 两种格式就都变成一个意思了。
        const flat = document.createElement('canvas')
        flat.width = srcW
        flat.height = srcH
        const fc = flat.getContext('2d')
        if (!fc) return
        fc.drawImage(m, 0, 0, srcW, srcH)
        const id = fc.getImageData(0, 0, srcW, srcH)
        const d = id.data
        for (let i = 0; i < d.length; i += 4) {
          const lum = 0.299 * d[i] + 0.587 * d[i + 1] + 0.114 * d[i + 2]
          d[i + 3] = Math.round(d[i + 3] * lum / 255)
          d[i] = 255; d[i + 1] = 255; d[i + 2] = 255
        }
        fc.putImageData(id, 0, 0)

        const base = ensureBase()
        const bc = base.getContext('2d')
        if (!bc) return
        bc.clearRect(0, 0, srcW, srcH)
        bc.drawImage(flat, 0, 0)
        opsRef.current = []
        undosRef.current = []
        redraw([])
      }
      m.src = initialMaskUrl
    }
  }, [open, srcUrl, srcW, srcH, initialMaskUrl])

  const toImage = useCallback((clientX: number, clientY: number) => {
    const el = wrapRef.current
    if (!el) return null
    const r = el.getBoundingClientRect()
    // getBoundingClientRect 已含 transform，所以除以 zoom 就回到图像素坐标
    const x = (clientX - r.left) / zoom
    const y = (clientY - r.top) / zoom
    return { x: Math.max(0, Math.min(srcW, x)), y: Math.max(0, Math.min(srcH, y)) }
  }, [zoom, srcW, srcH])

  const commit = useCallback((op: Op) => {
    opsRef.current = [...opsRef.current, op]
    undosRef.current = []
    setDirty(true)
    setTick((n) => n + 1)
  }, [])

  const onPointerDown = (e: React.PointerEvent) => {
    if (e.button === 1 || spaceRef.current) {
      panning.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
      return
    }
    if (e.button !== 0) return
    const p = toImage(e.clientX, e.clientY)
    if (!p) return
    ;(e.target as HTMLElement).setPointerCapture?.(e.pointerId)
    if (tool === 'rect') {
      drawing.current = { t: 'rect', x0: p.x, y0: p.y, x1: p.x, y1: p.y, erase: false }
    } else {
      drawing.current = {
        t: 'brush', pts: [[p.x, p.y]], size, erase: tool === 'eraser',
      }
      // 落笔立即画一个点，否则单击看不出反应
      const mask = maskRef.current, over = overRef.current
      if (mask && over) {
        const op = drawing.current
        paint(mask.getContext('2d')!, op, op.erase ? null : '#fff')
        paint(over.getContext('2d')!, op, op.erase ? null : overlaySolid)
      }
    }
    setDirty(true)
  }

  const onPointerMove = (e: React.PointerEvent) => {
    const p = toImage(e.clientX, e.clientY)
    if (p) setCursor(p)
    if (panning.current) {
      setPan({
        x: panning.current.px + (e.clientX - panning.current.x),
        y: panning.current.py + (e.clientY - panning.current.y),
      })
      return
    }
    const op = drawing.current
    if (!op || !p) return
    const mask = maskRef.current, over = overRef.current
    if (!mask || !over) return
    if (op.t === 'brush') {
      const last = op.pts[op.pts.length - 1]
      if (Math.hypot(p.x - last[0], p.y - last[1]) < Math.max(0.6, 1 / zoom)) return
      op.pts.push([p.x, p.y])
      // 增量画最后一段：全量重放只在撤销时做
      const seg: Op = { t: 'brush', pts: [last, [p.x, p.y]], size: op.size, erase: op.erase }
      paint(mask.getContext('2d')!, seg, op.erase ? null : '#fff')
      paint(over.getContext('2d')!, seg, op.erase ? null : overlaySolid)
    } else {
      op.x1 = p.x
      op.y1 = p.y
      const band = bandRef.current?.getContext('2d')
      if (band) {
        band.clearRect(0, 0, srcW, srcH)
        band.fillStyle = overlaySolid
        band.fillRect(Math.min(op.x0, op.x1), Math.min(op.y0, op.y1),
          Math.abs(op.x1 - op.x0), Math.abs(op.y1 - op.y0))
      }
    }
  }

  const onPointerUp = () => {
    panning.current = null
    const op = drawing.current
    drawing.current = null
    if (!op) return
    if (op.t === 'rect') {
      const band = bandRef.current?.getContext('2d')
      if (band) band.clearRect(0, 0, srcW, srcH)
      if (Math.abs(op.x1 - op.x0) < 2 || Math.abs(op.y1 - op.y0) < 2) return
      const mask = maskRef.current, over = overRef.current
      if (!mask || !over) return
      paint(mask.getContext('2d')!, op, op.erase ? null : '#fff')
      paint(over.getContext('2d')!, op, overlaySolid)
      commit(op)
      return
    }
    if (op.t === 'brush' && op.pts.length) commit(op)
  }

  const undo = useCallback(() => {
    const ops = opsRef.current
    if (!ops.length) return
    const last = ops[ops.length - 1]
    opsRef.current = ops.slice(0, -1)
    undosRef.current = [...undosRef.current, last]
    redraw(opsRef.current)
    setDirty(true)
    setTick((n) => n + 1)
  }, [redraw])

  const redo = useCallback(() => {
    const undos = undosRef.current
    if (!undos.length) return
    const op = undos[undos.length - 1]
    undosRef.current = undos.slice(0, -1)
    opsRef.current = [...opsRef.current, op]
    redraw(opsRef.current)
    setDirty(true)
    setTick((n) => n + 1)
  }, [redraw])

  const clearAll = useCallback(() => {
    opsRef.current = []
    undosRef.current = []
    const base = baseRef.current
    if (base) base.getContext('2d')?.clearRect(0, 0, base.width, base.height)
    redraw([])
    setDirty(true)
    setTick((n) => n + 1)
  }, [redraw])

  /** 反选：图层取反（用 XOR 补集），像素级、和涂抹次数无关。 */
  const invert = useCallback(() => {
    const mask = maskRef.current, over = overRef.current
    if (!mask || !over) return
    const mc = mask.getContext('2d')
    if (!mc || !over.getContext('2d')) return
    const src = mc.getImageData(0, 0, srcW, srcH)
    const d = src.data
    for (let i = 3; i < d.length; i += 4) {
      const on = d[i] > 127
      d[i] = on ? 0 : 255
      d[i - 1] = 255; d[i - 2] = 255; d[i - 3] = 255
    }
    mc.putImageData(src, 0, 0)
    // 反选结果直接落成新底图：原来的操作列表已经不能表达当前形状，
    // 之后的重绘 / 撤销都以这张新底图为起点
    const base = ensureBase()
    const bc = base.getContext('2d')
    if (!bc) return
    bc.clearRect(0, 0, srcW, srcH)
    bc.drawImage(mask, 0, 0)
    opsRef.current = []
    undosRef.current = []
    // 用新底图重画两遍（蒙版 + 红色覆盖），否则覆盖层还停着反选前的样子
    redraw([])
    setDirty(true)
    setTick((n) => n + 1)
  }, [srcW, srcH, ensureBase, redraw])

  const save = useCallback(async () => {
    const mask = maskRef.current
    if (!mask) return
    setSaving(true)
    try {
      const blob: Blob | null = await new Promise((res) => mask.toBlob(res, 'image/png'))
      if (!blob) throw new Error('蒙版导出失败')
      const r = await api.uploadMask(projectId, blob)
      onSaved(r.mask)
      setDirty(false)
      onClose()
    } catch (e: any) {
      alert(`保存蒙版失败：${e.message || e}`)
    } finally {
      setSaving(false)
    }
  }, [projectId, onSaved, onClose])

  const requestClose = useCallback(() => {
    if (dirty && !confirm('涂抹还没保存，关闭会丢掉这次的笔迹。确定关闭？')) return
    onClose()
  }, [dirty, onClose])

  // 快捷键：B/E/R 工具、[ ] 笔刷、Ctrl+Z 撤销、Ctrl+Shift+Z 重做、Esc 关闭
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName
      if (tag === 'INPUT' || tag === 'TEXTAREA') return
      if (e.key === 'Escape') { requestClose(); return }
      if (e.code === 'Space') { spaceRef.current = true; return }
      const k = e.key.toLowerCase()
      // 按住 H 看原图：核对「涂到的位置对不对」时最常用，比反复调透明度快
      if (k === 'h') { setPeek(true); return }
      if ((e.ctrlKey || e.metaKey) && k === 'z') {
        e.preventDefault()
        if (e.shiftKey) redo(); else undo()
        return
      }
      if (k === 'b') setTool('brush')
      else if (k === 'e') setTool('eraser')
      else if (k === 'r') setTool('rect')
      else if (k === '[') setSize((s) => Math.max(2, Math.round(s / 1.25)))
      else if (k === ']') setSize((s) => Math.min(400, Math.round(s * 1.25)))
    }
    const onUp = (e: KeyboardEvent) => {
      if (e.code === 'Space') spaceRef.current = false
      if (e.key.toLowerCase() === 'h') setPeek(false)
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('keyup', onUp)
    // 切走窗口时 keyup 收不到：不复位就会一直「按住」状态
    const onBlur = () => { setPeek(false); spaceRef.current = false }
    window.addEventListener('blur', onBlur)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('keyup', onUp)
      window.removeEventListener('blur', onBlur)
    }
  }, [open, undo, redo, requestClose])

  const stageSize = useMemo(() => ({ w: srcW * zoom, h: srcH * zoom }), [srcW, srcH, zoom])
  void tick      // 仅用于触发重渲染（撤销后按钮态）

  if (!open) return null

  return (
    <div className="me-mask" onMouseDown={(e) => e.stopPropagation()}>
      <div className="me-bar">
        <div className="me-name">{title || '涂抹区域'}</div>
        <div className="me-tools">
          {([['brush', '笔刷 B'], ['eraser', '橡皮 E'], ['rect', '矩形 R']] as const).map(
            ([t, label]) => (
              <button key={t} className={`btn btn-sm ${tool === t ? 'on' : 'btn-ghost'}`}
                onClick={() => setTool(t)}>{label}</button>
            ))}
        </div>
        <label className="me-size">
          笔刷
          <input type="range" min={2} max={Math.max(64, Math.round(srcW / 3))}
            value={size} onChange={(e) => setSize(Number(e.target.value))} />
          <span>{size}px</span>
        </label>
        <label className="me-size"
          title="蒙版显示透明度：调低能看清下面的图（核对有没有涂错位置），调高能在杂乱背景上确认涂到哪；不影响导出的蒙版">
          透明度
          <input type="range" min={Math.round(OVERLAY_ALPHA_MIN * 100)} max={100}
            value={Math.round(overlayAlpha * 100)}
            onChange={(e) => setOverlayAlpha(Number(e.target.value) / 100)} />
          <span>{Math.round(overlayAlpha * 100)}%</span>
        </label>
        <button className={`btn btn-sm ${peek ? 'on' : 'btn-ghost'}`}
          title="按住 H 临时隐藏蒙版，用原图核对涂到哪了（松手恢复）"
          onPointerDown={() => setPeek(true)}
          onPointerUp={() => setPeek(false)}
          onPointerLeave={() => setPeek(false)}>
          看原图
        </button>
        <div className="me-color" title="蒙版颜色：画面本身偏红/粉时换一个，避免看不出涂到哪">
          <span>蒙版色</span>
          {SWATCHES.map((c) => (
            <button key={c} className={`me-sw ${overlayHex === c ? 'on' : ''}`}
              style={{ background: c }} title={c}
              onClick={() => setOverlayHex(c)} aria-label={`蒙版色 ${c}`} />
          ))}
          <input type="color" value={overlayHex} className="me-color-input"
            title="自定义颜色" onChange={(e) => setOverlayHex(e.target.value)} />
        </div>
        <button className="btn btn-sm btn-ghost" onClick={undo}
          disabled={!opsRef.current.length}>撤销</button>
        <button className="btn btn-sm btn-ghost" onClick={redo}
          disabled={!undosRef.current.length}>重做</button>
        <button className="btn btn-sm btn-ghost" onClick={clearAll}>清空</button>
        <button className="btn btn-sm btn-ghost" onClick={invert}
          title="把没涂的地方当选区（例：抠掉角色以外）">反选</button>
        <div className="grow" />
        <button className="btn btn-sm" onClick={() => setZoom((z) => Math.max(0.05, z / 1.25))}>−</button>
        <div className="pv-zoom">{Math.round(zoom * 100)}%</div>
        <button className="btn btn-sm" onClick={() => setZoom((z) => Math.min(8, z * 1.25))}>+</button>
        <button className="btn btn-sm btn-ghost"
          onClick={() => { setZoom(Math.min(1, 900 / Math.max(srcW, srcH))); setPan({ x: 0, y: 0 }) }}>
          适应
        </button>
        <button className="btn btn-sm" onClick={save} disabled={saving}>
          {saving ? <><span className="spin" /> 保存中…</> : '保存涂抹'}
        </button>
        <button className="btn btn-sm btn-danger" onClick={requestClose}>关闭 Esc</button>
      </div>

      <div
        ref={stageRef}
        className="me-stage"
        onWheel={(e) => {
          e.preventDefault()
          setZoom((z) => Math.min(8, Math.max(0.05, z * (e.deltaY < 0 ? 1.12 : 1 / 1.12))))
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerLeave={() => { setCursor(null); onPointerUp() }}
        style={{ cursor: panning.current ? 'grabbing' : 'crosshair' }}
      >
        <div
          ref={wrapRef}
          className="me-wrap"
          style={{
            width: stageSize.w, height: stageSize.h,
            transform: `translate(${pan.x}px, ${pan.y}px)`,
            marginLeft: pan.x * 0,      // 平移只走 transform，保持布局居中
          }}
        >
          <canvas ref={imgRef} className="me-canvas" style={{ width: stageSize.w, height: stageSize.h }} />
          {/* 蒙版层不显示（红色覆盖层负责显示），但必须挂进 DOM：
              没有它 maskRef.current 是 null，涂抹和载入历史蒙版都会静默失效。 */}
          <canvas ref={maskRef} className="me-canvas me-canvas-hidden" />
          <canvas ref={overRef} className="me-canvas"
            style={{ width: stageSize.w, height: stageSize.h, opacity: peek ? 0 : overlayAlpha }} />
          <canvas ref={bandRef} className="me-canvas"
            style={{ width: stageSize.w, height: stageSize.h, opacity: peek ? 0 : overlayAlpha }} />
          {cursor ? (
            <div className="me-ring" style={{
              left: (cursor.x - size / 2) * zoom,
              top: (cursor.y - size / 2) * zoom,
              width: size * zoom, height: size * zoom,
            }} />
          ) : null}
        </div>
      </div>

      <div className="me-tip">
        左键涂抹（蒙版色高亮=会被重画）· 按住 H 看原图 · 空格/中键拖动平移 · 滚轮缩放 ·
        B 笔刷 / E 橡皮 / R 矩形 · [ ] 调笔刷 · Ctrl+Z 撤销
        {dirty ? ' · 有未保存的笔迹' : ''}
      </div>
    </div>
  )
}
