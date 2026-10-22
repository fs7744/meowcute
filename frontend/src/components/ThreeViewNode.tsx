import type { ResultImage, ThreeViewNode as ThreeViewNodeType } from '../types'

type Slot = {
  view: 'front' | 'side' | 'back' | 'side_r'
  label: string
  image: ResultImage | null
  /** 这个视角的全部候选（正视图抽卡 n 张 → 每个视角 n 张） */
  images?: ResultImage[]
  /** 当前选中的是第几组（pairIndex） */
  pair?: number
  /** 这张图是从哪个节点收来的（UI 上给个来源说明） */
  from?: string
  /** 之前点选的那张已不在候选里（重跑过），已按组自动重选 */
  stale?: boolean
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
    gap: 12, baseline: 0.93, cell: null, picks: {},
  }
  const t = { ...DEFAULTS, ...node.threeview }
  const patch = (p: Partial<typeof t>) => onChange({ threeview: { ...t, ...p } })

  const meta = node.result?.threeview
  // 右视图可选：缺它只提示、不阻塞；正/侧/背缺任一才阻塞
  const requiredMissing = slots.filter((s) => !s.image && s.view !== 'side_r')
  const optionalMissing = slots.filter((s) => !s.image && s.view === 'side_r')

  // 逐视角选图：点缩略图 = 把这个视角的 picks 指向那张
  const picks = t.picks || {}
  const pickImage = (s: Slot, img: ResultImage) => patch({
    picks: { ...picks, [s.view]: img.assetId },
  })
  /** 按第 k 组一键对齐：正视图第 k 张 + 各视角第 k 张（抽卡后挑顺眼的一整套）。
   *  某个视角没有这一组时退回它能给的最近一组，不把整格清空。 */
  const pickGroup = (k: number) => {
    const next = { ...picks }
    for (const s of slots) {
      const list = s.images ?? []
      if (!list.length) continue
      const hit = list.find((im, i) => (im.pairIndex ?? i) === k) ?? list[Math.min(k, list.length - 1)]
      if (hit) next[s.view] = hit.assetId
    }
    patch({ picks: next })
  }
  const maxGroups = Math.max(1, ...slots.map((s) => s.images?.length ?? 1))
  const multi = maxGroups > 1

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
            <div className="tv-slot-wrap" key={s.view}>
              <div className={`tv-slot ${s.image ? '' : 'empty'}`}
                title={s.image ? `来自：${s.from || '画布'}` : `缺少${VIEW_CN[s.view]}`}
                onClick={() => s.image
                  ? onPreview(s.image.url, `${VIEW_CN[s.view]}（第 ${(s.pair ?? 0) + 1} 组）`)
                  : undefined}>
                {s.image ? (
                  <img src={s.image.url} alt={VIEW_CN[s.view]} />
                ) : (
                  <span className="tv-slot-x">缺</span>
                )}
                <span className="tv-slot-t">
                  {VIEW_CN[s.view]}{multi && s.image ? ` · 第${(s.pair ?? 0) + 1}组` : ''}
                </span>
              </div>
              {/* 多张候选：逐张点选（抽卡时每个视角都要能挑「同一组」） */}
              {(s.images?.length ?? 0) > 1 ? (
                <div className="tv-pick-row">
                  {s.images!.map((img, i) => (
                    <button key={img.assetId}
                      className={`tv-pick ${s.image?.assetId === img.assetId ? 'on' : ''}`}
                      title={`${VIEW_CN[s.view]} · 第 ${(img.pairIndex ?? i) + 1} 组`}
                      onClick={() => pickImage(s, img)}>
                      <img src={img.url} alt="" />
                      <span>{(img.pairIndex ?? i) + 1}</span>
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
        {multi ? (
          <div className="tv-group-row">
            <span className="hint" style={{ margin: 0 }}>按组一键选齐：</span>
            {Array.from({ length: maxGroups }, (_, i) => (
              <button key={i} className="tv-group-btn" onClick={() => pickGroup(i)}
                title={`正视图第 ${i + 1} 张 + 各视角第 ${i + 1} 组`}>
                第 {i + 1} 组
              </button>
            ))}
          </div>
        ) : null}
        {multi ? (
          <div className="hint" style={{ marginTop: -2 }}>
            每个视角都抽了 {maxGroups} 张：逐格点缩略图挑，或点「第 k 组」一次性挑齐
            同一套（角色一致性看的就是这一整套）。
          </div>
        ) : null}
        {slots.some((s) => s.stale) ? (
          <div className="warn" style={{ marginTop: 2 }}>
            有视角之前挑的那张已不在候选里（重新生成过），已按「同一组」自动重选；
            点一下缩略图可以重新确认。
          </div>
        ) : null}
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
