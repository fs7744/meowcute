import { useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  Layer25DMeta, Layer25DNode as Layer25DNodeType, Layer25DStatus, ResultImage,
} from '../types'

type Props = {
  node: Layer25DNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  /** 上游给的那张图（要分层的就是它） */
  upstream?: { url: string; file: string; width: number; height: number; kind: 'images' | 'refs' } | null
  onChange: (patch: Partial<Layer25DNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const RESOLUTIONS = [
  { v: 768, label: '768 · 快' },
  { v: 1024, label: '1024 · 推荐' },
  { v: 1280, label: '1280 · 官方推荐（慢）' },
  { v: 1536, label: '1536 · 很慢' },
]

/** 层名 → 中文说明（Live2D 语义层，界面上给个可读的提示） */
const LAYER_CN: Record<string, string> = {
  'back hair': '后发', 'front hair': '前发', hair: '头发',
  face: '脸', nose: '鼻', mouth: '嘴', neck: '脖子',
  eyewhite: '眼白', irides: '瞳孔', eyelash: '睫毛', eyebrow: '眉毛',
  eyes: '眼睛', headwear: '头饰', helmet: '头盔', eyewear: '眼镜',
  earwear: '耳饰', ears: '耳朵', neckwear: '颈饰',
  topwear: '上衣', bottomwear: '下装', innerwear: '内衣',
  legwear: '腿袜', footwear: '鞋', shoes: '鞋', socks: '袜',
  objects: '手持物', weapon: '武器', accessory: '配件', accessories: '配件',
  handwear: '手部', armwear: '臂饰', tail: '尾巴', wings: '翅膀',
}

const layerCn = (n: string) => LAYER_CN[n] || n

export default function Layer25DNode({
  node, index, running, progress, error, upstream,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const v = node.layer25d
  const meta = node.meta ?? null
  const [status, setStatus] = useState<Layer25DStatus | null>(null)

  // 环境体检：see-through 需要独立 venv + 权重，缺了就早点说清
  useEffect(() => {
    let alive = true
    api.layer25dStatus()
      .then((s) => { if (alive) setStatus(s) })
      .catch(() => { if (alive) setStatus({ ok: false, dir: '', python: '', missing: ['体检接口没响应'] }) })
    return () => { alive = false }
  }, [])

  const patch = (p: Partial<typeof v>) => onChange({ layer25d: { ...v, ...p } })

  return (
    <div className="node l25d">
      <div className="node-head">
        <span className="node-title">2.5D 分层模型</span>
        <span className="node-sub">See-Through</span>
        <span className="node-badge">{meta ? `${meta.layers.length} 层` : '未分层'}</span>
        <button className="node-x" onClick={onDelete} title="删除这张节点">×</button>
      </div>

      <div className="node-body">
        {/* ---- 源图 ---- */}
        {upstream ? (
          <button className="l25d-src" onClick={() => onPreview(upstream.url, '源图')}
            title="放大看源图">
            <img src={upstream.url} alt="源图" />
            <span>源图 {upstream.width}×{upstream.height}</span>
          </button>
        ) : (
          <div className="hint warn">
            没有源图：把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到它的输入端口。
            输入图越干净（纯色底、单人、轮廓清楚），分层越准。
          </div>
        )}

        {/* ---- 环境体检 ---- */}
        {status && !status.ok ? (
          <div className="hint warn" style={{ marginTop: 6 }}>
            see-through 不可用：{status.missing.join('；')}
            <br />
            它需要独立 venv（torch 2.8+cu128）与约 17GB 权重，装好后可用
            <code> MEOWCUTE_SEETHROUGH_DIR </code> 指定仓库位置。
          </div>
        ) : null}

        {/* ---- 参数 ---- */}
        <div className="l25d-params">
          <label className="gen-field">
            <span className="gen-field-label" title="see-through 的工作分辨率：越大越慢，1024 是折中">
              分辨率
            </span>
            <select className="input" value={v.resolution}
              onChange={(e) => patch({ resolution: Number(e.target.value) })}>
              {RESOLUTIONS.map((r) => (
                <option key={r.v} value={r.v}>{r.label}</option>
              ))}
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="采样步数：越高质量越好也越慢">步数</span>
            <input className="input" type="number" min={4} max={60} value={v.steps}
              onChange={(e) => patch({ steps: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="同一个种子 = 同一份分层">种子</span>
            <input className="input" type="number" min={0} value={v.seed}
              onChange={(e) => patch({ seed: Number(e.target.value) })} />
          </label>
          <label className="gen-field l25d-switch">
            <span className="gen-field-label" title="眼睛/耳朵/手饰/头发拆成左、右两层——做眨眼和侧摆的前提">
              左右拆分
            </span>
            <button className={`switch ${v.lrsplit ? 'on' : ''}`} onClick={() => patch({ lrsplit: !v.lrsplit })}
              aria-label="切换左右拆分"><i /></button>
          </label>
        </div>
        <div className="hint">
          一次要 2~3 分钟（LayerDiff 分层 + Marigold 深度）。同样的图 + 同样的种子，
          出来的是同一份分层。
        </div>

        <button className="btn btn-sm" onClick={onRun}
          disabled={running || !upstream || (status ? !status.ok : false)}>
          {running ? `分层中… ${progress || ''}` : '开始分层'}
        </button>
        {error ? <div className="hint warn" style={{ marginTop: 4 }}>{error}</div> : null}

        {/* ---- 产出 ---- */}
        {meta ? <Layer25DView meta={meta} onPreview={onPreview} /> : null}
      </div>

      <div className="node-ports">
        <span className="port port-in" onMouseDown={(e) => onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })}
          onMouseUp={() => onFinishLink(node.id)} title="输入" />
        <span className="port port-out" onMouseDown={(e) => onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })}
          onMouseUp={() => onFinishLink(node.id)} title="输出" />
      </div>
    </div>
  )
}

/* ------------------------------------------------------------------ */
/* 2.5D 预览 + 层列表                                                  */
/* ------------------------------------------------------------------ */

function Layer25DView({ meta, onPreview }: {
  meta: Layer25DMeta
  onPreview: (url: string, title: string) => void
}) {
  const [parallax, setParallax] = useState(55)   // 视差强度 %
  const [breath, setBreath] = useState(true)     // 呼吸动画
  const [hover, setHover] = useState<{ x: number; y: number } | null>(null)
  const [t, setT] = useState(0)
  const wrapRef = useRef<HTMLDivElement>(null)

  // 呼吸动画：只在开着时跑 rAF，省电
  useEffect(() => {
    if (!breath) return
    let raf = 0
    const loop = () => {
      setT((v) => v + 0.016)
      raf = requestAnimationFrame(loop)
    }
    raf = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(raf)
  }, [breath])

  const [fw, fh] = meta.frameSize || [1, 1]
  // 预览缩放：按画布尺寸算，保证整张立绘都装得进 300px 高的预览框
  const scale = Math.min(1, 264 / Math.max(1, fh))
  // 层按绘制顺序排：order 大的先放（底层），order 小的后放（顶层）
  const ordered = useMemo(
    () => [...(meta.layers || [])].sort((a, b) => b.order - a.order),
    [meta.layers],
  )

  const onMove = (e: React.MouseEvent) => {
    const r = wrapRef.current?.getBoundingClientRect()
    if (!r) return
    setHover({ x: (e.clientX - r.left) / r.width - 0.5, y: (e.clientY - r.top) / r.height - 0.5 })
  }

  return (
    <div className="l25d-out">
      {/* ---- 实时 2.5D 预览 ---- */}
      <div className="l25d-stage" ref={wrapRef}
        onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        <div className="l25d-canvas"
          style={{ width: fw, height: fh, transform: `translate(-50%, -50%) scale(${scale})` }}>
          {ordered.map((l) => {
            // 深度 → z：depth 越小越靠前 = z 越大（位移更明显）
            const z = l.depth == null ? 0.5 : Math.max(0, Math.min(1, 1 - l.depth))
            // 位移按画布像素算，再被预览缩放压下去：768 画布 + 0.34 缩放时
            // 系数给小了屏幕只剩 2~3px，肉眼看不出「层在错位」——这里给到
            // 满档约 11px（屏幕），跟 Live2D 里头部相对身体的位移量级接近。
            const amp = (parallax / 100) * z * (fw * 0.12)
            const dx = (hover?.x || 0) * -amp
            const dy = (hover?.y || 0) * -amp
            // 呼吸：摆动幅度建议越大、越靠前，晃得越明显
            const sw = l.swing * z
            const bx = Math.sin(t * 1.6 + l.order) * sw * (fw * 0.022)
            const by = Math.sin(t * 2.1 + l.order * 0.7) * sw * (fw * 0.014)
            const rot = Math.sin(t * 1.2 + l.order * 0.5) * sw * 1.6
            return (
              <img key={l.name} src={l.url} alt={l.name} className="l25d-layer"
                style={{
                  left: l.x, top: l.y, width: l.width, height: l.height,
                  transform: `translate(${dx + bx}px, ${dy + by}px) rotate(${rot}deg)`,
                }} />
            )
          })}
        </div>
        <div className="l25d-stage-tip">
          鼠标在图上移动 = 转头看（各层按深度错位）
        </div>
      </div>

      <div className="l25d-ctl">
        <label className="l25d-slider">
          视差
          <input type="range" min={0} max={100} value={parallax}
            onChange={(e) => setParallax(Number(e.target.value))} />
          <span>{parallax}%</span>
        </label>
        <button className={`switch ${breath ? 'on' : ''}`} onClick={() => setBreath(!breath)}
          aria-label="切换呼吸动画" title="呼吸动画：看各层怎么错位">
          <i />
        </button>
        <span className="hint" style={{ margin: 0 }}>
          {meta.layers.length} 层 · {fw}×{fh} · {meta.seconds}s
        </span>
      </div>

      {/* ---- 层列表 ---- */}
      <div className="l25d-layers">
        <div className="l25d-layers-head">
          图层（上层 = 最靠前）
          <span className="hint" style={{ margin: 0 }}>深度条：左=前 右=后</span>
        </div>
        {(meta.layers || []).map((l) => {
          const d = l.depth == null ? 0.5 : l.depth
          return (
            <div key={l.name} className="l25d-layer-row">
              <img src={l.url} alt={l.name} onClick={() => onPreview(l.url, layerCn(l.name))}
                title={`${l.name} · ${l.width}×${l.height}`} />
              <div className="l25d-layer-info">
                <span className="l25d-layer-name">{layerCn(l.name)}</span>
                <span className="l25d-layer-en">{l.name}</span>
                <div className="l25d-depthbar">
                  <i style={{ width: `${Math.round(d * 100)}%` }} />
                </div>
              </div>
              <span className="l25d-layer-order">#{l.order + 1}</span>
            </div>
          )
        })}
      </div>

      {/* ---- 下载 ---- */}
      <div className="grid-actions wrap">
        <a className="btn btn-xs" href={meta.psdUrl} download title="分层 PSD：Live2D 语义层，层序 = 深度">
          下载分层 PSD
        </a>
        {meta.depthPsdUrl ? (
          <a className="btn btn-xs btn-ghost" href={meta.depthPsdUrl} download>深度 PSD</a>
        ) : null}
        {meta.lrsplitPsdUrl ? (
          <a className="btn btn-xs btn-ghost" href={meta.lrsplitPsdUrl} download>左右拆分 PSD</a>
        ) : null}
        <a className="btn btn-xs btn-ghost" href={meta.psdJsonUrl} download>深度 JSON</a>
        <a className="btn btn-xs btn-ghost" href={meta.manifestUrl} download>manifest</a>
      </div>
      <div className="hint" style={{ fontSize: 11 }}>
        每层透明 PNG 在项目的 exports/layer25d/layers/ 里，按层名分文件；
        manifest.json 里有每层的画布坐标与深度，可以直接喂给自己的引擎。
      </div>
    </div>
  )
}
