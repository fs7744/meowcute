import { useEffect, useMemo, useRef, useState } from 'react'
import type { AnimPreviewNode as AnimPreviewNodeType } from '../types'

type Props = {
  node: AnimPreviewNodeType
  index: number
  /** 上游精灵图节点（没生成时提示用） */
  upstreamCount?: number | null
  onChange: (patch: Partial<AnimPreviewNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
}

const BG_LABEL: Record<string, string> = {
  checker: '棋盘', dark: '深色', light: '浅色', green: '绿幕',
}

/**
 * 动画预览节点（素材链第 4 步）：把精灵图按设定帧率逐格播放。
 *
 * 纯前端，不调后端：数据就是上游精灵图的 `url` + 帧坐标表，canvas 逐格
 * `drawImage` 最近邻放大。这样调帧率、拖进度、逐帧步进都是零延迟——如果每次
 * 都回后端渲染 GIF，改个 fps 要等好几秒，用户根本没法「调到顺眼为止」。
 *
 * 除了播一遍，这个节点还提供一个**容易被忽略但很有用的判据**：逐帧步进 +
 * 首尾帧对比。循环动画最常见的毛病是末帧和首帧太像（播起来卡一拍），肉眼看
 * 着「有点顿」，但说不出哪里不对——逐帧走一遍能立刻确认。
 */
export default function AnimPreviewNode({
  node, index, upstreamCount, onChange, onDelete, onDragStart,
  onStartLink, onFinishLink,
}: Props) {
  const DEFAULTS: AnimPreviewNodeType['animpreview'] = {
    enabled: true, fps: 12, zoom: 4, loop: true, bg: 'checker',
  }
  const v = { ...DEFAULTS, ...node.animpreview }
  const patch = (p: Partial<typeof v>) => onChange({ animpreview: { ...v, ...p } })

  const meta = node.meta
  const total = meta?.frames?.length ?? 0
  const [playing, setPlaying] = useState(true)
  const [frame, setFrame] = useState(0)

  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const imgRef = useRef<HTMLImageElement | null>(null)
  const [loaded, setLoaded] = useState(0)
  // 棋盘底缓存：按画布尺寸生成一次（见 PlayNode 里的同款注释）
  const bgCache = useRef<{ key: string; canvas: HTMLCanvasElement } | null>(null)

  const url = meta?.url ?? ''

  // 预加载精灵图
  useEffect(() => {
    if (!url) return
    if (imgRef.current?.src.endsWith(url) && imgRef.current.complete) return
    const img = new Image()
    img.onload = () => setLoaded((n) => n + 1)
    img.onerror = () => setLoaded((n) => n + 1)
    img.src = url
    imgRef.current = img
  }, [url])

  // 帧数变化时把当前帧夹回合法范围（换精灵图后旧帧号可能越界）
  useEffect(() => {
    setFrame((f) => (total ? Math.min(f, total - 1) : 0))
  }, [total])

  // 播放循环
  useEffect(() => {
    if (!playing || total < 2 || !v.enabled) return
    const period = 1000 / Math.max(0.5, v.fps)
    const timer = window.setInterval(() => {
      setFrame((f) => (f + 1 >= total ? (v.loop ? 0 : total - 1) : f + 1))
    }, period)
    return () => window.clearInterval(timer)
  }, [playing, total, v.fps, v.loop, v.enabled])

  const size = useMemo(() => {
    const fw = meta?.frameSize?.w ?? 0
    const fh = meta?.frameSize?.h ?? 0
    return { w: fw * v.zoom, h: fh * v.zoom }
  }, [meta?.frameSize?.w, meta?.frameSize?.h, v.zoom])

  // 画当前帧
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !meta || !total) return
    const img = imgRef.current
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    canvas.width = size.w
    canvas.height = size.h
    ctx.imageSmoothingEnabled = false
    ctx.clearRect(0, 0, canvas.width, canvas.height)

    if (v.bg === 'checker') {
      const key = `${canvas.width}x${canvas.height}`
      if (!bgCache.current || bgCache.current.key !== key) {
        const bg = document.createElement('canvas')
        bg.width = canvas.width
        bg.height = canvas.height
        const bctx = bg.getContext('2d')
        if (bctx) {
          const cell = Math.max(6, Math.round(8 * Math.max(1, v.zoom / 2)))
          for (let y = 0; y < bg.height; y += cell) {
            for (let x = 0; x < bg.width; x += cell) {
              bctx.fillStyle = ((x / cell + y / cell) % 2 === 0) ? '#2a2d3a' : '#23263a'
              bctx.fillRect(x, y, cell, cell)
            }
          }
        }
        bgCache.current = { key, canvas: bg }
      }
      ctx.drawImage(bgCache.current.canvas, 0, 0)
    } else {
      ctx.fillStyle = v.bg === 'dark' ? '#14161f'
        : v.bg === 'light' ? '#f3f4f8' : '#00b140'
      ctx.fillRect(0, 0, canvas.width, canvas.height)
    }

    if (!img || !img.complete || img.naturalWidth === 0) return
    const f = meta.frames[Math.min(frame, total - 1)]
    const fw = meta.frameSize.w
    const fh = meta.frameSize.h
    ctx.drawImage(img, f.x, f.y, fw, fh, 0, 0, canvas.width, canvas.height)
  }, [meta, frame, total, v.zoom, v.bg, size.w, size.h, loaded])

  const step = (d: number) => {
    setPlaying(false)
    setFrame((f) => (total ? (f + d + total) % total : 0))
  }

  return (
    <div className={`node animpreview ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 340 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">ANIM</span>
        <span className="node-title">动画预览</span>
        <span className="node-idx">A{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游节点的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink(node.id) }} />
        <div className="port out" title="按住拖到目标节点即可连线"
          onMouseDown={(e) => {
            e.stopPropagation()
            onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })
          }} />
      </div>

      <div className="node-body">
        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>
            使用此节点{v.enabled ? '' : '（已关闭）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换动画预览">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{typeof upstreamCount === 'number' && upstreamCount > 0
            ? <b>{upstreamCount} 帧精灵图</b>
            : '还没有精灵图（把「精灵图」连到左侧端口）'}
        </div>

        {meta && total ? (
          <>
            <div className="anim-stage">
              <canvas ref={canvasRef} className="anim-canvas"
                style={{ width: size.w, height: size.h }} />
            </div>

            <div className="anim-controls">
              <button className="btn btn-xs btn-ghost" onClick={() => step(-1)}
                title="上一帧" disabled={!total}>◀</button>
              <button className="btn btn-xs" onClick={() => setPlaying((p) => !p)}
                title={playing ? '暂停' : '播放'} disabled={total < 2} style={{ minWidth: 54 }}>
                {playing ? '暂停' : '播放'}
              </button>
              <button className="btn btn-xs btn-ghost" onClick={() => step(1)}
                title="下一帧" disabled={!total}>▶</button>
              <span className="anim-counter" title="当前帧 / 总帧数">
                {Math.min(frame + 1, total)}/{total}
              </span>
            </div>

            <input className="anim-scrub" type="range" min={0} max={Math.max(0, total - 1)}
              step={1} value={Math.min(frame, total - 1)}
              onChange={(e) => { setPlaying(false); setFrame(Number(e.target.value)) }}
              aria-label="帧进度" />

            <div className="param-grid">
              <label className="gen-field">
                <span className="gen-field-label">帧率</span>
                <input className="input" type="number" min={1} max={60} step={1}
                  value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
              </label>
              <label className="gen-field">
                <span className="gen-field-label">倍率</span>
                <select className="input" value={v.zoom}
                  onChange={(e) => patch({ zoom: Number(e.target.value) })}>
                  {[1, 2, 3, 4, 6, 8].map((z) => <option key={z} value={z}>{z}×</option>)}
                </select>
              </label>
              <label className="gen-field">
                <span className="gen-field-label">底色</span>
                <select className="input" value={v.bg}
                  onChange={(e) => patch({ bg: e.target.value as typeof v.bg })}>
                  {Object.entries(BG_LABEL).map(([k, label]) => (
                    <option key={k} value={k}>{label}</option>
                  ))}
                </select>
              </label>
              <label className="gen-field">
                <span className="gen-field-label">循环</span>
                <button className={`switch ${v.loop ? 'on' : ''}`}
                  onClick={() => patch({ loop: !v.loop })} aria-label="切换循环">
                  <i />
                </button>
              </label>
            </div>

            <div className="hint" style={{ fontSize: 11 }}>
              时长 ≈ {(total / Math.max(0.5, v.fps)).toFixed(2)}s ·
              单元格 {meta.frameSize.w}×{meta.frameSize.h}
              {meta.indexUrl ? (
                <>
                  {' · '}
                  <a href={meta.indexUrl} download>下载索引 JSON</a>
                </>
              ) : null}
            </div>
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有可播放的精灵图。先把上游「精灵图」跑出来。
          </div>
        )}
      </div>
    </div>
  )
}
