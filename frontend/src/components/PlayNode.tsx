import { useEffect, useMemo, useRef, useState } from 'react'
import type { PlayNode as PlayNodeType, PlayMeta, WalkDir } from '../types'

type Props = {
  node: PlayNodeType
  index: number
  running?: boolean
  /** 上游精灵整图节点给出了哪几个方向 */
  upstreamLabel?: string | null
  onChange: (patch: Partial<PlayNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const DIR_ORDER: WalkDir[] = [
  'up-left', 'up', 'up-right',
  'left', 'down', 'right',
  'down-left', 'down-right',
]
const DIR_CN: Record<WalkDir, string> = {
  'down': '正面', 'down-left': '左下', 'left': '左侧', 'up-left': '左上',
  'up': '背面', 'up-right': '右上', 'right': '右侧', 'down-right': '右下',
}
/** 键盘 → 方向：方向键/WASD 管四向，Q/E/Z/C 管斜向，空格播放暂停。 */
const KEY_DIR: Record<string, WalkDir> = {
  arrowup: 'up', w: 'up',
  arrowdown: 'down', s: 'down',
  arrowleft: 'left', a: 'left',
  arrowright: 'right', d: 'right',
  q: 'up-left', e: 'up-right', z: 'down-left', c: 'down-right',
}

/**
 * 可操控预览节点（八方向链的第 4 步）：把八个方向的行走精灵表变成一个
 * 能用键盘/按钮操控的播放器。
 *
 * 纯前端：不调后端，产物就是播放器本身。数据全部来自上游「精灵整图」节点
 * 的元数据（每方向一张横排精灵表 + 帧索引），画布逐格 drawImage 最近邻
 * 放大——像素素材不能插值。
 */
export default function PlayNode({
  node, index, running, upstreamLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: PlayNodeType['play'] = { enabled: true, fps: 8, zoom: 2 }
  const v = { ...DEFAULTS, ...node.play }
  const patch = (p: Partial<typeof v>) => onChange({ play: { ...v, ...p } })

  const meta: PlayMeta | null | undefined = node.meta
  const dirs = useMemo(() => meta?.directions ?? [], [meta])
  const [dir, setDir] = useState<WalkDir>('down')
  const [playing, setPlaying] = useState(true)
  const [frame, setFrame] = useState(0)
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  // 每方向的精灵表 Image 缓存：加载完成后才能逐格画
  const imgCache = useRef<Map<string, HTMLImageElement>>(new Map())
  const [ready, setReady] = useState(0)

  const cur = dirs.find((d) => d.dir === dir) ?? dirs[0] ?? null
  const total = cur?.frames.length ?? 0

  // 预加载所有方向的精灵表
  useEffect(() => {
    let alive = true
    const cache = imgCache.current
    let loaded = 0
    for (const d of dirs) {
      if (cache.has(d.dir)) { loaded += 1; continue }
      const img = new Image()
      img.onload = () => { if (alive) { loaded += 1; setReady(loaded) } }
      img.onerror = () => { if (alive) { loaded += 1; setReady(loaded) } }
      img.src = d.url
      cache.set(d.dir, img)
    }
    if (loaded === dirs.length) setReady(loaded)
    return () => { alive = false }
  }, [dirs])

  // 播放循环：按 fps 推进帧
  useEffect(() => {
    if (!playing || total < 2) return
    const period = 1000 / Math.max(1, v.fps)
    const timer = window.setInterval(() => {
      setFrame((f) => (f + 1) % total)
    }, period)
    return () => window.clearInterval(timer)
  }, [playing, total, v.fps])

  // 换方向时回到第 0 帧
  useEffect(() => { setFrame(0) }, [dir])

  // 画当前帧
  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || !cur || !total) return
    const img = imgCache.current.get(cur.dir)
    const ctx = canvas.getContext('2d')
    if (!ctx) return
    const fw = cur.frameSize.w
    const fh = cur.frameSize.h
    canvas.width = fw * v.zoom
    canvas.height = fh * v.zoom
    ctx.imageSmoothingEnabled = false
    ctx.clearRect(0, 0, canvas.width, canvas.height)
    // 棋盘格底：透明区域一眼能看出来
    const cell = Math.max(6, Math.round(8 * v.zoom))
    for (let y = 0; y < canvas.height; y += cell) {
      for (let x = 0; x < canvas.width; x += cell) {
        ctx.fillStyle = ((x / cell + y / cell) % 2 === 0) ? '#2a2d3a' : '#23263a'
        ctx.fillRect(x, y, cell, cell)
      }
    }
    if (!img || !img.complete || img.naturalWidth === 0) return
    const f = cur.frames[Math.min(frame, total - 1)]
    ctx.drawImage(img, f.x, f.y, fw, fh, 0, 0, canvas.width, canvas.height)
  }, [cur, frame, total, v.zoom, ready])

  const onKeyDown = (e: React.KeyboardEvent) => {
    const k = e.key.toLowerCase()
    if (k === ' ') { e.preventDefault(); setPlaying((p) => !p); return }
    const target = KEY_DIR[k]
    if (target && dirs.some((d) => d.dir === target)) {
      e.preventDefault()
      setDir(target)
      setPlaying(true)
    }
  }

  const hasData = dirs.length > 0

  return (
    <div className={`node play ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">PLAY</span>
        <span className="node-title">可操控预览</span>
        <span className="node-idx">L{index + 1}</span>
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
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换可操控预览节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamLabel ? <b>{upstreamLabel}</b> : '未连线（把「精灵整图」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">播放帧率</span>
            <input className="input" type="number" min={2} max={30}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">显示倍率</span>
            <input className="input" type="number" min={1} max={8}
              value={v.zoom} onChange={(e) => patch({ zoom: Number(e.target.value) })} />
          </label>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 组装中…</> : '刷新预览'}
        </button>

        {hasData && v.enabled ? (
          <div className="play-wrap" tabIndex={0} onKeyDown={onKeyDown}
            title="点这里后用 方向键/WASD 转向，Q/E/Z/C 走斜向，空格暂停">
            <div className="play-stage">
              <canvas ref={canvasRef} className="play-canvas" />
              <div className="play-dir-tag">{DIR_CN[cur?.dir ?? 'down']}</div>
            </div>
            <div className="play-pad">
              {DIR_ORDER.map((d) => {
                const has = dirs.some((x) => x.dir === d)
                return (
                  <button key={d}
                    className={`play-key ${cur?.dir === d ? 'on' : ''} ${has ? '' : 'miss'}`}
                    title={`${DIR_CN[d]}${has ? '' : '（缺）'}`}
                    onClick={() => { setDir(d); setPlaying(true) }}>
                    {DIR_CN[d].replace('正面', '正').replace('背面', '背')}
                  </button>
                )
              })}
            </div>
            <div className="play-bar">
              <button className="btn btn-sm" onClick={() => setPlaying((p) => !p)}>
                {playing ? '暂停' : '播放'}
              </button>
              <span className="play-frame">
                {total ? `${Math.min(frame + 1, total)} / ${total} 帧` : '无帧'}
              </span>
              <span className="play-hint">方向键/WASD 转向 · QEZC 斜向 · 空格暂停</span>
            </div>
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            {v.enabled
              ? '还没有可播放的精灵表。连好「精灵整图」后点上面的按钮。'
              : '节点已关闭：打开开关后点「刷新预览」。'}
          </div>
        )}

        {meta?.directions.length ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.directions.length} 个方向</span>
            <span className="meta-pill">{meta.fps}fps</span>
            <span className="meta-pill">×{meta.zoom}</span>
          </div>
        ) : null}

        {cur ? (
          <button className="btn btn-sm" style={{ marginTop: 6, width: '100%' }}
            onClick={() => onPreview(cur.url, `${DIR_CN[cur.dir]}精灵表`)}>
            查看当前方向精灵表
          </button>
        ) : null}
      </div>
    </div>
  )
}
