import type { SpriteSheetNode as SpriteSheetNodeType } from '../types'

type Props = {
  node: SpriteSheetNodeType
  index: number
  running?: boolean
  /** 上游序列帧节点给出了哪几个方向 */
  upstreamLabel?: string | null
  onChange: (patch: Partial<SpriteSheetNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 精灵整图节点（八方向链的第 3 步）：每方向一小摞透明帧 → 一张横排精灵表。
 *
 * 共享缩放是重点：先量所有帧的最大包围盒再统一缩放，抬腿的帧不会被放大，
 * 角色在整段循环里尺寸恒定。拼板前会把透明像素的 RGB 清零（防引擎双线性
 * 采样时渲出色晕），拼完逐格自检。
 */
export default function SpriteSheetNode({
  node, index, running, upstreamLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: SpriteSheetNodeType['spritesheet'] = {
    enabled: true, size: 128, cols: 0, sharedScale: true, anchor: 'bottom',
  }
  const v = { ...DEFAULTS, ...node.spritesheet }
  const patch = (p: Partial<typeof v>) => onChange({ spritesheet: { ...v, ...p } })

  const meta = node.meta
  const dirs = meta?.directions ?? []
  const warnCount = meta?.warnings?.length ?? 0

  return (
    <div className={`node spritesheet ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">SHEET</span>
        <span className="node-title">精灵整图</span>
        <span className="node-idx">P{index + 1}</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游帧）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换精灵整图节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamLabel ? <b>{upstreamLabel}</b> : '未连线（把「序列帧」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">单元格边长</span>
            <input className="input" type="number" min={32} max={512} step={16}
              value={v.size} onChange={(e) => patch({ size: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">每行帧数（0=排完）</span>
            <input className="input" type="number" min={0} max={32}
              value={v.cols} onChange={(e) => patch({ cols: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">锚点</span>
            <select className="input" value={v.anchor}
              onChange={(e) => patch({ anchor: e.target.value as typeof v.anchor })}>
              <option value="bottom">底部对齐</option>
              <option value="center">居中对齐</option>
              <option value="top">顶部对齐</option>
            </select>
          </label>
          <div className="switch-row" style={{ marginTop: 6 }}>
            <span className="t" style={{ fontSize: 12 }}>共享缩放</span>
            <button className={`switch ${v.sharedScale ? 'on' : ''}`}
              onClick={() => patch({ sharedScale: !v.sharedScale })} aria-label="切换共享缩放">
              <i />
            </button>
          </div>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 拼板中…</> : '运行拼精灵表'}
        </button>

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.directions.length} 个方向</span>
            <span className="meta-pill">单元格 {meta.size}px</span>
            <span className={`meta-pill ${meta.checksOk ? 'good' : 'bad'}`}>
              {meta.checksOk ? '逐格自检通过' : '自检未通过'}
            </span>
            {warnCount ? <span className="meta-pill bad">{warnCount} 条警告</span> : null}
            <span className="meta-pill">{meta.seconds}s</span>
          </div>
        ) : null}

        {meta?.overview ? (
          <button className="matte-thumb wide" title="八方向总览（点击放大）"
            style={{ marginTop: 8 }}
            onClick={() => onPreview(meta.overview.url, '八方向精灵表总览')}>
            <img src={meta.overview.url} alt="八方向精灵表总览" />
          </button>
        ) : null}

        {warnCount ? (
          <div className="hint" style={{ marginTop: 6, color: '#f0b27a' }}>
            {meta!.warnings.slice(0, 4).map((w, i) => <div key={i}>· {w}</div>)}
            {warnCount > 4 ? <div>· …等 {warnCount} 条</div> : null}
          </div>
        ) : null}

        {dirs.length ? (
          <div className="walk-grid">
            {dirs.map((d) => (
              <div className="walk-cell" key={d.dir}>
                <button className="walk-sheet" title={`${d.label}精灵表（点击放大）`}
                  onClick={() => onPreview(d.sheet.url, `${d.label}精灵表`)}>
                  <img src={d.sheet.url} alt={`${d.label}精灵表`} />
                </button>
                <div className="walk-cell-t">
                  {d.label}
                  <span className="walk-count">{d.cols}×{d.rows}</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有精灵表。连好「序列帧」后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
