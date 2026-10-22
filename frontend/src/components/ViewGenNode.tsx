import type { ViewGenNode as ViewGenNodeType } from '../types'

type Props = {
  node: ViewGenNodeType
  index: number
  running?: boolean
  /** 参考的正视图来自哪个节点（链条最上游那张） */
  sourceLabel?: string | null
  /** 复用的正视图参数摘要（画风/尺寸/步数/种子/背景）——有它就不用在
   *  本节点重复填写；null = 正视图不是生成卡（如选择图片），回退节点自身参数 */
  inherit?: {
    styleName: string | null
    width: number
    height: number
    steps: number | null
    seed: number | null
    maxSeqLen: number | null
    background: string | null
  } | null
  onChange: (patch: Partial<ViewGenNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const VIEW_CN = { side: '侧视图', side_r: '右视图', back: '背视图' } as const

/**
 * 视角生成节点（四视图的第 2/3/4 步）：以「正视图」为参考，用编辑底座指令接力
 * 生成侧视图、右视图或背视图。
 *
 * 三个关键设计：
 * 1. 参考永远是最上游那张正视图——链条中间隔着别的视角节点也会自动往前找，
 *    所以「生成图 → 侧视图 → 背视图」这条直线里，背视图不会拿侧视图当参考。
 * 2. 参数全部继承正视图（画风 / 尺寸 / 步数 / 种子 / 背景）：用户只在生成卡上
 *    填一次，侧/背/右自动复用——各张图底色一致，拼板抠底才不会咬出异色边。
 * 3. 指令只描述「视角怎么变」，不点名任何服装/配件/颜色：点名会直接变成
 *    「给角色加一件」的生成指令（归档项目踩过的坑）。
 */
export default function ViewGenNode({
  node, index, running, sourceLabel, inherit, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  // 老项目 / 旧模板里可能没有后加的字段，合并默认值避免控件拿到 undefined
  const DEFAULTS: ViewGenNodeType['viewgen'] = {
    enabled: true, view: 'side', styleId: null,
    steps: null, seed: null, width: 512, height: 768, maxSeqLen: null,
  }
  const v = { ...DEFAULTS, ...node.viewgen }
  const patch = (p: Partial<typeof v>) => onChange({ viewgen: { ...v, ...p } })

  const meta = node.result?.viewMeta
  const isBack = v.view === 'back'
  const isRight = v.view === 'side_r'

  return (
    <div className={`node viewgen ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 360 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">VIEW</span>
        <span className="node-title">生成{VIEW_CN[v.view]}</span>
        <span className="node-idx">V{index + 1}</span>
        <button className="node-x" title="删除这张视角卡"
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
            使用此节点{v.enabled ? '' : `（已关闭，直接透传正视图）`}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换视角生成节点">
            <i />
          </button>
        </div>

        <div className="hint">
          参考：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把「生成图」或「选择图片」连到左侧端口）'}
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          画风 / 尺寸 / 步数 / 种子 / 背景全部复用正视图，无需重复填写。
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          {isBack
            ? '角色旋转 180° 背对观众：脸部不可见，不新增任何衣物配件。'
            : isRight
              ? '角色整体转向画面右侧：展现完整侧面轮廓，造型以正视图为准。'
              : '角色整体转向画面左侧：展现完整侧面轮廓，造型以正视图为准。'}
        </div>

        {inherit ? (
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label">参数（复用正视图，不在此节点修改）</span>
              <div className="hint" style={{ lineHeight: 1.7 }}>
                画风：{inherit.styleName ?? '未选'} · {inherit.width}×{inherit.height}
                <br />
                步数：{inherit.steps ?? '自动'} · 种子：{inherit.seed ?? '随机'}
                <br />
                背景：{inherit.background ?? '中灰'} · 质量：
                {inherit.maxSeqLen ? { 512: '快速', 768: '标准', 1024: '高质量' }[inherit.maxSeqLen as 512 | 768 | 1024] ?? '自定义' : '自动'}
              </div>
            </label>
          </div>
        ) : (
          <div className="hint">
            上游不是「生成图」生成卡：本节点没有可继承的参数，执行时按默认值跑
            （尺寸跟随参考图、步数自动、种子随机、中灰底、画风不指定）。
            要让四视图参数统一，把「生成图」接到链条最上游，改它的参数即可。
          </div>
        )}

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 生成中…</> : `运行生成${VIEW_CN[v.view]}`}
        </button>

        {node.result ? (
          <div style={{ marginTop: 10 }}>
            <button className="matte-thumb" title="点击放大"
              onClick={() => onPreview(node.result!.url, `生成${VIEW_CN[v.view]}`)}>
              <img src={node.result.url} alt={VIEW_CN[v.view]} />
            </button>
            <div className="result-meta" style={{ borderTop: 0 }}>
              <span className="meta-pill good">{node.result.width}×{node.result.height}</span>
              {meta?.mode ? <span className="meta-pill">编辑接力</span> : null}
              {meta?.steps ? <span className="meta-pill">{meta.steps} 步</span> : null}
              {meta?.seed != null ? <span className="meta-pill">种子 {meta.seed}</span> : null}
            </div>
            {meta?.autoRouted ? (
              <div className="hint" style={{ marginTop: 4 }}>
                已自动路由到编辑底座（Qwen-Image-Edit）。
              </div>
            ) : null}
            {(meta as any)?.bgWarning ? (
              <div className="warn" style={{ marginTop: 4 }}>{(meta as any).bgWarning}</div>
            ) : null}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有{VIEW_CN[v.view]}。连好正视图后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
