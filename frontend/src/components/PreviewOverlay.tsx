import { useCallback, useEffect, useRef, useState } from 'react'

type Props = {
  open: boolean
  src: string | null
  /** 精修前的原图（有值时显示「原图 / 精修」切换） */
  origSrc?: string | null
  title?: string
  pixelSize?: [number, number] | null
  onClose: () => void
}

/** 全屏放大预览：滚轮缩放（以光标为中心）、拖拽平移、Esc 关闭、0 复位。 */
export default function PreviewOverlay({ open, src, origSrc, title, pixelSize, onClose }: Props) {
  const [zoom, setZoom] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [drag, setDrag] = useState(false)
  const [showOrig, setShowOrig] = useState(false)
  const stageRef = useRef<HTMLDivElement>(null)
  const dragStart = useRef<{ x: number; y: number; px: number; py: number } | null>(null)

  useEffect(() => {
    if (!open) return
    setZoom(1)
    setPan({ x: 0, y: 0 })
    setShowOrig(false)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
      if (e.key === '0') { setZoom(1); setPan({ x: 0, y: 0 }) }
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(16, z * 1.25))
      if (e.key === '-') setZoom((z) => Math.max(0.25, z / 1.25))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const onWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault()
    const rect = stageRef.current?.getBoundingClientRect()
    const cx = rect ? e.clientX - rect.left - rect.width / 2 : 0
    const cy = rect ? e.clientY - rect.top - rect.height / 2 : 0
    setZoom((z) => {
      const next = Math.min(16, Math.max(0.25, z * (e.deltaY < 0 ? 1.15 : 1 / 1.15)))
      const k = next / z
      // 以光标为中心缩放：平移量按比例补差
      setPan((p) => ({ x: cx - (cx - p.x) * k, y: cy - (cy - p.y) * k }))
      return next
    })
  }, [])

  if (!open || !src) return null
  const shown = showOrig && origSrc ? origSrc : src

  return (
    <div className="pv-mask" onMouseDown={(e) => e.stopPropagation()}>
      <div className="pv-bar">
        <div className="pv-name">{title || '预览'}</div>
        {pixelSize ? (
          <div className="pv-size">{pixelSize[0]}×{pixelSize[1]}</div>
        ) : null}
        {origSrc ? (
          <div className="pv-toggle">
            <button className={showOrig ? '' : 'on'} onClick={() => setShowOrig(false)}>精修</button>
            <button className={showOrig ? 'on' : ''} onClick={() => setShowOrig(true)}>原图</button>
          </div>
        ) : null}
        <div className="grow" />
        <a className="btn btn-sm" href={shown} download title="下载当前显示的这一版">下载</a>
        <button className="btn btn-sm" onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }) }}>复位</button>
        <button className="btn btn-sm" onClick={() => setZoom((z) => Math.max(0.25, z / 1.25))}>−</button>
        <div className="pv-zoom">{Math.round(zoom * 100)}%</div>
        <button className="btn btn-sm" onClick={() => setZoom((z) => Math.min(16, z * 1.25))}>+</button>
        <button className="btn btn-sm btn-danger" onClick={onClose}>关闭 Esc</button>
      </div>
      <div
        ref={stageRef}
        className={`pv-stage ${drag ? 'dragging' : ''}`}
        onWheel={onWheel}
        onMouseDown={(e) => {
          if (e.button !== 0) return   // 右键留给浏览器原生菜单（另存为）
          setDrag(true)
          dragStart.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y }
        }}
        onMouseMove={(e) => {
          if (!dragStart.current) return
          setPan({
            x: dragStart.current.px + (e.clientX - dragStart.current.x),
            y: dragStart.current.py + (e.clientY - dragStart.current.y),
          })
        }}
        onMouseUp={() => { setDrag(false); dragStart.current = null }}
        onMouseLeave={() => { setDrag(false); dragStart.current = null }}
      >
        <img
          src={shown}
          alt={title || '预览'}
          draggable={false}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
        />
      </div>
      <div className="pv-tip">
        滚轮缩放 · 拖拽平移 · 0 复位 · Esc 关闭
        {origSrc ? ' · 可切换原图对比' : ''}
      </div>
    </div>
  )
}
