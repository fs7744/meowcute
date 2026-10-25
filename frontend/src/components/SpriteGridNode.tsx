import type { SpriteGridNode as SpriteGridNodeType } from '../types'
import AdvancedSection from './AdvancedSection'

type Props = {
  node: SpriteGridNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  /** 上游已选帧数（没连线时为 null） */
  upstreamPicked?: number | null
  onChange: (patch: Partial<SpriteGridNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const CELL_PRESETS = [32, 48, 64, 96, 128, 192, 256]

/**
 * 精灵图节点（素材链第 3 步）：一串选中的帧 → 一张网格精灵图 + 索引 JSON。
 *
 * 参数取舍照着市面图集工具（TexturePacker / ezgif）里真正影响结果的三个：
 *
 * - **共享缩放**（默认开）：量所有帧的最大包围盒再统一缩放。逐帧各自撑满会让
 *   抬腿的帧被放大，一播就露馅。这条是精灵表最容易被一眼看出的毛病。
 * - **帧间距**（默认 0）：Unity 的 Sprite Editor「Automatic」切片靠**透明间隔**
 *   认边界，帧紧贴时会把两帧并成一张。要自动切片就给它 2px。
 * - **锚点**（默认底部居中）：循环里角色的脚要踩在同一条线上，上下漂移像在飘。
 *
 * 不做 extrude/bleed：那是紧密打包（允许旋转、MaxRects）才需要的补偿，规整
 * 网格本身有单元格内的透明留白，不会串色。
 */
export default function SpriteGridNode({
  node, index, running, progress, error, upstreamPicked,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: SpriteGridNodeType['spritegrid'] = {
    enabled: true, cell: 128, cols: 0, padding: 0, margin: 0,
    anchor: 'bottom', sharedScale: true, fps: 12,
  }
  const v = { ...DEFAULTS, ...node.spritegrid }
  const patch = (p: Partial<typeof v>) => onChange({ spritegrid: { ...v, ...p } })

  const meta = node.meta
  const sheet = node.result ?? (node.images ?? [])[0] ?? null
  const checksOk = meta?.checks?.cellsMatchFrames !== false
  const noUpstream = typeof upstreamPicked === 'number' && upstreamPicked === 0

  return (
    <div className={`node spritegrid ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 420 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">GRID</span>
        <span className="node-title">精灵图</span>
        <span className="node-idx">G{index + 1}</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换精灵图节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{typeof upstreamPicked === 'number'
            ? <b>已选 {upstreamPicked} 帧</b>
            : '未连线（把「拆解帧」连到左侧端口）'}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="每个单元格的边长（正方形）">单元格</span>
            <select className="input" value={v.cell}
              onChange={(e) => patch({ cell: Number(e.target.value) })}>
              {CELL_PRESETS.map((c) => <option key={c} value={c}>{c}px</option>)}
              {CELL_PRESETS.includes(v.cell) ? null
                : <option value={v.cell}>{v.cell}px</option>}
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label"
              title="每行放几帧；0 = 自动（排成接近正方形的网格）">每行列数</span>
            <input className="input" type="number" min={0} max={32}
              value={v.cols} onChange={(e) => patch({ cols: Number(e.target.value) })} />
          </label>
        </div>

        <AdvancedSection title="更多设置"
          hint="帧间距 · 外边距 · 锚点 · 帧率 · 共享缩放">
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="相邻单元格之间的透明间隔：给 Unity 自动切片就填 2">帧间距</span>
              <input className="input" type="number" min={0} max={32}
                value={v.padding} onChange={(e) => patch({ padding: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="整张图四周的外边距">外边距</span>
              <input className="input" type="number" min={0} max={64}
                value={v.margin} onChange={(e) => patch({ margin: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="脚踩同一条线（bottom）/ 居中 / 顶部">锚点</span>
              <select className="input" value={v.anchor}
                onChange={(e) => patch({ anchor: e.target.value as typeof v.anchor })}>
                <option value="bottom">底部</option>
                <option value="center">居中</option>
                <option value="top">顶部</option>
              </select>
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="写进索引 JSON，动画预览默认取它">帧率</span>
              <input className="input" type="number" min={1} max={60} step={1}
                value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
            </label>
          </div>

          <div className="switch-row" style={{ marginTop: 6 }}>
            <span className="t" style={{ fontSize: 12 }}
              title="所有帧用同一个缩放系数：抬腿的帧不会被放大，循环里角色尺寸恒定">
              共享缩放
            </span>
            <button className={`switch ${v.sharedScale ? 'on' : ''}`}
              onClick={() => patch({ sharedScale: !v.sharedScale })} aria-label="切换共享缩放">
              <i />
            </button>
          </div>
        </AdvancedSection>

        {/* 布局快捷方式：方形网格适合 GPU 采样，横排适合 Godot hframes /
            Unity 单行。两种都常用，给按钮比让用户自己算列数快。 */}
        <div className="grid-layouts">
          <button className={`btn btn-xs ${v.cols === 0 ? '' : 'btn-ghost'}`}
            onClick={() => patch({ cols: 0 })} title="排成接近正方形的网格">
            自动方形
          </button>
          <button className={`btn btn-xs ${typeof upstreamPicked === 'number' && upstreamPicked > 0 && v.cols === upstreamPicked ? '' : 'btn-ghost'}`}
            onClick={() => patch({ cols: Math.min(32, upstreamPicked || 8) })}
            title="所有帧排成一行（Godot hframes / Unity 单行切片）">
            横排一行
          </button>
          <span className="hint" style={{ fontSize: 10, marginLeft: 'auto' }}>
            {v.cols === 0 ? '自动' : `${v.cols} 列`}
          </span>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running || noUpstream}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 拼板中…</> : '生成精灵图'}
        </button>
        {progress ? <div className="hint walk-progress">{progress}</div> : null}
        {error ? <div className="node-error">{error}</div> : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.count} 帧</span>
            <span className="meta-pill">{meta.cols}×{meta.rows} 格</span>
            <span className="meta-pill">{meta.sheetSize[0]}×{meta.sheetSize[1]}</span>
            {meta.padding ? <span className="meta-pill">间距 {meta.padding}px</span> : null}
            <span className={`meta-pill ${checksOk ? '' : 'bad'}`}
              title={checksOk ? '每个单元格与源帧逐位一致' : (meta.checks?.reason || '自检未通过')}>
              {checksOk ? '自检通过' : '自检异常'}
            </span>
            {meta.missing ? (
              <span className="meta-pill bad">{meta.missing} 帧缺失</span>
            ) : null}
          </div>
        ) : null}

        {meta?.warnings?.length ? (
          <div className="hint warn" style={{ marginTop: 6 }}>
            {meta.warnings.slice(0, 3).map((w) => <div key={w}>· {w}</div>)}
            {meta.warnings.length > 3 ? <div>· 还有 {meta.warnings.length - 3} 条…</div> : null}
          </div>
        ) : null}

        {sheet ? (
          <>
            <button className="grid-preview" title="点击放大"
              onClick={() => onPreview(sheet.url, '精灵图')}>
              <img src={sheet.url} alt="精灵图" />
            </button>
            <div className="grid-actions">
              <a className="btn btn-xs" href={sheet.url} download>下载 PNG</a>
              {node.indexUrl ? (
                <a className="btn btn-xs btn-ghost" href={node.indexUrl} download
                  title="帧坐标表 + Phaser 3 (JSON Array) 兼容段">下载索引 JSON</a>
              ) : null}
            </div>
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有精灵图。上游挑好帧后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
