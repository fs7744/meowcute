import type { RigWalkNode as RigWalkNodeType } from '../types'

type Props = {
  node: RigWalkNodeType
  index: number
  running?: boolean
  progress?: string | null
  /** 上游拆件结果是否就绪 */
  upstreamLabel?: string | null
  onChange: (patch: Partial<RigWalkNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 骨骼行走节点：已拆好的 6 个部件 → 8 帧行走循环。
 *
 * 与「行走视频」节点的本质区别：这里没有任何扩散采样。同一组部件 + 同一段相位，
 * 渲染结果永远一致（纯 CPU 约 0.1 秒），所以不存在闪烁、肢体变形、角色漂移。
 * 代价是动作表现力上限：刚性部件做不出膝关节，走路是「纸娃娃抬踏」。
 */
export default function RigWalkNode({
  node, index, running, progress, upstreamLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: RigWalkNodeType['rigwalk'] = {
    enabled: true, frames: 8, swing: 13, arm: 12, bob: 5, lift: 7, hold: false, fps: 8,
  }
  const v = { ...DEFAULTS, ...node.rigwalk }
  const patch = (p: Partial<typeof v>) => onChange({ rigwalk: { ...v, ...p } })

  const meta = node.meta
  const gif = meta?.gif

  return (
    <div className={`node rigwalk ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 390 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">RIG</span>
        <span className="node-title">骨骼行走</span>
        <span className="node-idx">R{index + 1}</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游部件）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换骨骼行走节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamLabel ? <b>{upstreamLabel}</b> : '未连线（把「拆件」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="循环帧数。8 帧 @ 8fps 约 1 秒一圈">帧数</span>
            <input className="input" type="number" min={4} max={24}
              value={v.frames}
              onChange={(e) => patch({ frames: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="腿摆角。12° 干净、18° 动作大但腿根会外翻">腿摆角</span>
            <input className="input" type="number" min={6} max={24} step={1}
              value={v.swing}
              onChange={(e) => patch({ swing: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="臂摆角（与腿反相）">臂摆角</span>
            <input className="input" type="number" min={0} max={30} step={1}
              value={v.arm}
              onChange={(e) => patch({ arm: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="身体上下起伏（像素）">起伏</span>
            <input className="input" type="number" min={0} max={16} step={1}
              value={v.bob}
              onChange={(e) => patch({ bob: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="前摆腿抬起高度（模拟离地）">抬脚</span>
            <input className="input" type="number" min={0} max={20} step={1}
              value={v.lift}
              onChange={(e) => patch({ lift: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">循环帧率</span>
            <input className="input" type="number" min={4} max={24}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="双手持枪/握把的角色必须开：两只手属于不同骨骼，各转各的会脱手">
            握持模式（双臂同向小摆）
          </span>
          <button className={`switch ${v.hold ? 'on' : ''}`}
            onClick={() => patch({ hold: !v.hold })} aria-label="切换握持模式">
            <i />
          </button>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 渲染中…</> : '运行骨骼行走（约 1 秒）'}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.frames} 帧 @ {v.fps}fps</span>
            <span className="meta-pill">步幅 {meta.stridePx}px</span>
            <span className="meta-pill"
              title="逐帧角色像素面积的波动。确定性渲染下只反映姿势变化，不是闪烁">
              面积 ±{meta.areaStdPct}%
            </span>
            <span className="meta-pill">{meta.renderSecs}s</span>
          </div>
        ) : null}

        {gif?.url ? (
          <div style={{ marginTop: 8 }}>
            <button className="walk-strip" title="点击放大"
              onClick={() => onPreview(gif.url, '骨骼行走循环')}>
              <img src={gif.url} alt="骨骼行走循环" />
            </button>
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有循环。先跑「拆件」，再点上面的按钮（纯 CPU，1 秒内出结果）。
          </div>
        )}
      </div>
    </div>
  )
}
