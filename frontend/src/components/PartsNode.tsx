import type { PartsNode as PartsNodeType } from '../types'

type Props = {
  node: PartsNodeType
  index: number
  running?: boolean
  progress?: string | null
  /** 上游给了哪张角色图 */
  sourceLabel?: string | null
  onChange: (patch: Partial<PartsNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const ROLE_ORDER = ['head', 'torso', 'arm_l', 'arm_r', 'leg_l', 'leg_r'] as const
const ROLE_CN: Record<string, string> = {
  head: '头', torso: '躯干', arm_l: '左臂', arm_r: '右臂', leg_l: '左腿', leg_r: '右腿',
}

/**
 * 拆件节点：角色立绘 → 6 个骨骼部件 + 武器。
 *
 * 这是「确定性交付」路线的起点：拆完之后不再有任何扩散采样，行走帧由
 * 骨骼渲染器算出来，帧间一致性和耗时都可预测。混合路线的原因是实测的——
 * see-through 拆躯干/四肢/道具极准（道具单独成 objects 层，不会丢），但半写实
 * 角色的头部会崩成鬼影；爆炸图的头部是准的，两个拼起来面积稳定性 ±5.4% → ±2%。
 */
export default function PartsNode({
  node, index, running, progress, sourceLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: PartsNodeType['parts'] = {
    enabled: true, resolution: 1280, withHead: true, seed: 7,
  }
  const v = { ...DEFAULTS, ...node.parts }
  const patch = (p: Partial<typeof v>) => onChange({ parts: { ...v, ...p } })

  const meta = node.meta
  const parts = meta?.parts || {}
  const weapon = meta?.weapon || null
  const got = ROLE_ORDER.filter((r) => parts[r])
  const etaMin = Math.round(((v.resolution >= 1200 ? 180 : 110) + (v.withHead ? 50 : 0)) / 60)

  return (
    <div className={`node parts ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 400 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">PART</span>
        <span className="node-title">拆件 · 骨骼部件</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游图）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换拆件节点">
            <i />
          </button>
        </div>

        <div className="hint">
          角色图：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把「选择图片」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="see-through 的生成分辨率。1280 约 3 分钟；768 更快但手臂完整度会掉">
              分辨率
            </span>
            <select className="input" value={v.resolution}
              onChange={(e) => patch({ resolution: Number(e.target.value) })}>
              <option value={1024}>1024（快）</option>
              <option value={1280}>1280（推荐）</option>
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="爆炸图抽卡种子：头部不对就换一个">
              头部种子
            </span>
            <input className="input" type="number" min={0} value={v.seed}
              onChange={(e) => patch({ seed: Number(e.target.value) })} />
          </label>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="半写实角色的头部 see-through 会崩成鬼影，用爆炸图补一个头（慢约 45s）">
            用爆炸图补头部
          </span>
          <button className={`switch ${v.withHead ? 'on' : ''}`}
            onClick={() => patch({ withHead: !v.withHead })} aria-label="切换爆炸图补头">
            <i />
          </button>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 拆件中…</> : `运行拆件（约 ${etaMin} 分钟）`}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{got.length}/6 件</span>
            {weapon ? <span className="meta-pill good">含武器</span> : null}
            <span className="meta-pill">{meta.source === 'hybrid' ? '混合' : 'see-through'}</span>
            {meta.seconds ? <span className="meta-pill">{Math.round(meta.seconds)}s</span> : null}
          </div>
        ) : null}

        {got.length ? (
          <div className="walk-grid">
            {ROLE_ORDER.filter((r) => parts[r]).map((r) => (
              <div className="walk-cell" key={r}>
                <button className="walk-strip" title={`${ROLE_CN[r]}（点击放大）`}
                  onClick={() => onPreview(parts[r]!.url, ROLE_CN[r])}>
                  <img src={parts[r]!.url} alt={ROLE_CN[r]} />
                </button>
                <div className="walk-cell-t">{ROLE_CN[r]}</div>
              </div>
            ))}
            {weapon ? (
              <div className="walk-cell">
                <button className="walk-strip" title="武器/道具（点击放大）"
                  onClick={() => onPreview(weapon.url, '武器/道具')}>
                  <img src={weapon.url} alt="武器" />
                </button>
                <div className="walk-cell-t">武器</div>
              </div>
            ) : null}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有部件。连好角色图后点上面的按钮（首次约 3 分钟）。
          </div>
        )}
      </div>
    </div>
  )
}
