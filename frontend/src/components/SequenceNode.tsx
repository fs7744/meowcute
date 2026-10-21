import type { SequenceNode as SequenceNodeType } from '../types'

type Props = {
  node: SequenceNodeType
  index: number
  running?: boolean
  /** 后端推来的当前步骤文案 */
  progress?: string | null
  /** 上游行走视频节点给出了哪几个方向 */
  upstreamLabel?: string | null
  onChange: (patch: Partial<SequenceNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 序列帧节点（八方向链的第 2 步）：每方向一段行走视频 → 一小摞透明底帧。
 *
 * 做三件事：等间距抽帧（首尾都取，循环才顺）、相邻去重（同姿势的帧放两颗
 * 循环会卡）、抠图（视频带纯色背景，精灵素材要透明底）。抽出来的帧数就是
 * 最终行走循环的帧数——默认 8 帧 @ 8fps。
 */
export default function SequenceNode({
  node, index, running, progress, upstreamLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: SequenceNodeType['sequence'] = {
    enabled: true, framesPerDir: 8, fps: 8, matte: true, tolerance: 40,
  }
  const v = { ...DEFAULTS, ...node.sequence }
  const patch = (p: Partial<typeof v>) => onChange({ sequence: { ...v, ...p } })

  const meta = node.meta
  const dirs = meta?.directions ?? []

  return (
    <div className={`node sequence ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">SEQ</span>
        <span className="node-title">序列帧</span>
        <span className="node-idx">S{index + 1}</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游视频）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换序列帧节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamLabel ? <b>{upstreamLabel}</b> : '未连线（把「行走视频」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">每方向帧数</span>
            <input className="input" type="number" min={2} max={24}
              value={v.framesPerDir}
              onChange={(e) => patch({ framesPerDir: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">循环帧率</span>
            <input className="input" type="number" min={2} max={30}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}>逐帧抠图（透明底）</span>
          <button className={`switch ${v.matte ? 'on' : ''}`}
            onClick={() => patch({ matte: !v.matte })} aria-label="切换逐帧抠图">
            <i />
          </button>
        </div>
        {v.matte ? (
          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label">抠图容差</span>
            <input className="input" type="number" min={10} max={120} step={5}
              value={v.tolerance}
              onChange={(e) => patch({ tolerance: Number(e.target.value) })} />
          </label>
        ) : null}

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 拆帧中…</> : '运行拆序列帧'}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.directions.length} 个方向</span>
            <span className="meta-pill">每方向 {meta.framesPerDir} 帧</span>
            {meta.dropped ? <span className="meta-pill">去重 {meta.dropped} 帧</span> : null}
            {meta.matteErrors ? (
              <span className="meta-pill bad" title="这些帧抠图失败，已退回未抠图版本">
                抠图失败 {meta.matteErrors} 帧
              </span>
            ) : null}
            <span className="meta-pill">{meta.seconds}s</span>
          </div>
        ) : null}

        {dirs.length ? (
          <div className="walk-grid">
            {dirs.map((d) => (
              <div className="walk-cell" key={d.dir}>
                <button className="walk-strip" title={`${d.label}：${d.count} 帧（点击放大）`}
                  onClick={() => onPreview(d.strip.url, `${d.label}序列帧`)}>
                  <img src={d.strip.url} alt={`${d.label}序列帧`} />
                </button>
                <div className="walk-cell-t">
                  {d.label}
                  <span className="walk-count">{d.count} 帧</span>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有序列帧。连好「行走视频」后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
