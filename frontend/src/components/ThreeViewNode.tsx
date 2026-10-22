import type { ResultImage, ThreeViewNode as ThreeViewNodeType } from '../types'

type Slot = {
  view: 'front' | 'side' | 'back' | 'side_r'
  label: string
  image: ResultImage | null
  /** 这张图是从哪个节点收来的（UI 上给个来源说明） */
  from?: string
}

type Props = {
  node: ThreeViewNodeType
  index: number
  running?: boolean
  /** 正/侧/背/右四路输入；缺哪路就显示哪路未找到（右视图缺失不阻塞） */
  slots: Slot[]
  onChange: (patch: Partial<ThreeViewNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const VIEW_CN = {
  front: '正视图', side: '侧视图', back: '背视图', side_r: '右视图',
} as const

/**
 * 四视图拼板节点：收齐正/侧/背/右四张 → 逐张抠底 → 统一底板 → 按共同基线拼成横板。
 * 右视图可选：缺了按三视图拼板（向后兼容旧项目），UI 上给提示不阻塞运行。
 *
 * 四路输入怎么来：先看连到本节点的入边，缺的再沿链条往上回溯（视角节点、
 * 扣除背景/精修这类透传节点都会穿过去），所以「生成图 → 侧视图 → 背视图 →
 * 拼板」这条直线不用额外连线也能收齐三张。
 */
export default function ThreeViewNode({
  node, index, running, slots, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: ThreeViewNodeType['threeview'] = {
    enabled: true, bg: '1B4332', transparentBg: false,
    gap: 12, baseline: 0.93, cell: null,
  }
  const t = { ...DEFAULTS, ...node.threeview }
  const patch = (p: Partial<typeof t>) => onChange({ threeview: { ...t, ...p } })

  const meta = node.result?.threeview
  // 右视图可选：缺它只提示、不阻塞；正/侧/背缺任一才阻塞
  const requiredMissing = slots.filter((s) => !s.image && s.view !== 'side_r')
  const optionalMissing = slots.filter((s) => !s.image && s.view === 'side_r')

  return (
    <div className={`node threeview ${t.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">4V</span>
        <span className="node-title">四视图拼板</span>
        <span className="node-idx">T{index + 1}</span>
        <button className="node-x" title="删除这张拼板卡"
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
            使用此节点{t.enabled ? '' : '（已关闭，直接透传上游图片）'}
          </span>
          <button className={`switch ${t.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !t.enabled })} aria-label="切换四视图拼板节点">
            <i />
          </button>
        </div>

        <div className="tv-slots">
          {slots.map((s) => (
            <div className={`tv-slot ${s.image ? '' : 'empty'}`} key={s.view}
              title={s.image ? `来自：${s.from || '画布'}` : `缺少${VIEW_CN[s.view]}`}>
              {s.image ? (
                <img src={s.image.url} alt={VIEW_CN[s.view]} />
              ) : (
                <span className="tv-slot-x">缺</span>
              )}
              <span className="tv-slot-t">{VIEW_CN[s.view]}</span>
            </div>
          ))}
        </div>
        {requiredMissing.length ? (
          <div className="ref-note" style={{ marginTop: -4 }}>
            还缺：{requiredMissing.map((s) => VIEW_CN[s.view]).join('、')}。
            把对应节点的输出连到本节点左侧，或保证链条上能回溯到它。
          </div>
        ) : optionalMissing.length ? (
          <div className="hint" style={{ marginTop: -4 }}>
            缺右视图：将按三视图拼板；补上「生成右视图」节点即可升级四视图。
          </div>
        ) : null}

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}
            title="开：不贴底色，拼板直接输出 RGBA（各视图 alpha 原样带出）">
            透明底板{t.transparentBg ? '（输出 RGBA）' : ''}
          </span>
          <button className={`switch ${t.transparentBg ? 'on' : ''}`}
            onClick={() => patch({ transparentBg: !t.transparentBg })}
            aria-label="切换透明底板">
            <i />
          </button>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">底板色</span>
            <div className="tv-color"
              style={t.transparentBg ? { opacity: 0.45 } : undefined}>
              <input type="color" value={`#${t.bg}`} disabled={!!t.transparentBg}
                onChange={(e) => patch({ bg: e.target.value.replace('#', '').toUpperCase() })} />
              <input className="input" value={t.bg} maxLength={6}
                disabled={!!t.transparentBg}
                onChange={(e) => patch({
                  bg: e.target.value.replace(/[^0-9A-Fa-f]/g, '').toUpperCase().slice(0, 6),
                })} />
            </div>
          </label>
          <label className="gen-field">
            <span className="gen-field-label">间距 {t.gap}px</span>
            <input type="range" min={0} max={48} step={2} value={t.gap}
              onChange={(e) => patch({ gap: Number(e.target.value) })} />
          </label>
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          {t.transparentBg ? (
            <>透明底板：输出 RGBA，底板全透明、主体 alpha 原样保留，不需要再抠一次底。
              各视角若也用「透明」背景，alpha 会直接透传（跳过抠图）；用灰/绿底则各张抠完再叠到透明板上。
              底板色此时不生效。</>
          ) : (
            <>底板色必须是不透明纯色（6 位十六进制）：拼板会把每张图抠底后贴到这块底色上。
              要透明输出，打开上面的「透明底板」。</>
          )}
        </div>

        <label className="gen-field">
          <span className="gen-field-label" title="主体底部对齐到画布高度的比例；调它让各张图的脚落在同一水平线上">
            基线 {Math.round(t.baseline * 100)}%
          </span>
          <input type="range" min={0.6} max={0.98} step={0.01} value={t.baseline}
            onChange={(e) => patch({ baseline: Number(e.target.value) })} />
        </label>

        <div className="hint" style={{ marginTop: -4 }}>
          拼板前每张会先抠底、补封闭孔洞、洗边缘溢色——各张图底色不一致时，
          接缝处不会留下一圈异色描边。
        </div>

        <button className="btn btn-sm" onClick={onRun}
          disabled={running || !!requiredMissing.length}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 拼板中…</> : '运行四视图拼板'}
        </button>

        {node.result ? (
          <div style={{ marginTop: 10 }}>
            <button className="matte-thumb wide" title="点击放大"
              onClick={() => onPreview(node.result!.url, '四视图拼板')}>
              <img src={node.result.url} alt="四视图拼板" />
            </button>
            <div className="result-meta" style={{ borderTop: 0 }}>
              <span className="meta-pill good">{node.result.width}×{node.result.height}</span>
              {meta ? <span className="meta-pill">单格 {meta.cell[0]}×{meta.cell[1]}</span> : null}
              {meta ? <span className="meta-pill">间距 {meta.gap}px</span> : null}
              {meta?.alpha
                ? <span className="meta-pill good">透明底板 RGBA</span>
                : (meta ? <span className="meta-pill">底板 #{meta.bg.replace('#', '')}</span> : null)}
            </div>
            {meta ? (
              <div className="hint" style={{ marginTop: 4 }}>
                抠图路径：
                {meta.views.map((v) => `${VIEW_CN[v.view]} ${v.matte}`).join(' · ')}
                {meta.views.some((v) => v.matte === 'raw')
                  ? '（有图没抠掉底色，已按原图贴入）' : ''}
              </div>
            ) : null}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有拼板结果。正/侧/背齐了之后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
