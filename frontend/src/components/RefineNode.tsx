import type { RefineNode as RefineNodeType } from '../types'

type Props = {
  node: RefineNodeType
  index: number
  running?: boolean
  sourceLabel?: string | null
  onChange: (patch: Partial<RefineNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string) => void
  onFinishLink: (id: string) => void
}

/** 像素精修节点：接收上游图片 → 网格对齐 + 调色板量化 → 输出精修图。 */
export default function RefineNode({
  node, index, running, sourceLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink,
}: Props) {
  const px = node.pixel
  const patchPixel = (patch: Partial<typeof px>) =>
    onChange({ pixel: { ...px, ...patch } })

  return (
    <div className="node refine" style={{ left: node.x, top: node.y, width: 360 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">REFINE</span>
        <span className="node-title">像素精修</span>
        <span className="node-idx">P{index + 1}</span>
        <button className="node-x" title="删除这张精修卡"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游节点的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink(node.id) }} />
        <div className="port out" title="点击后，再点目标节点的输入端口连线"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onStartLink(node.id) }} />
      </div>

      <div className="node-body">
        <div className="hint">
          输入：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把图片/生成节点连到左侧端口）'}
        </div>

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>网格对齐（FFT 检测 + 邻近吸附）</span>
          <button className={`switch ${px.gridSnap ? 'on' : ''}`}
            onClick={() => patchPixel({ gridSnap: !px.gridSnap })} aria-label="切换网格对齐">
            <i />
          </button>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">调色板色数</span>
            <select className="select" value={px.colors}
              onChange={(e) => patchPixel({ colors: Number(e.target.value) })}>
              {[4, 8, 12, 16, 24, 32, 48].map((c) => <option key={c} value={c}>{c} 色</option>)}
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label">固定色板</span>
            <select className="select" value={px.palette}
              onChange={(e) => patchPixel({ palette: e.target.value })}>
              <option value="auto">自动提取（推荐）</option>
              <option value="gameboy">GameBoy 4 色</option>
              <option value="nes">NES 28 色</option>
            </select>
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">输出像素尺寸</span>
            <input className="input input-sm" type="number" placeholder="自动" min={8} max={256}
              value={px.targetSize?.[0] ?? ''}
              onChange={(e) => {
                const v = e.target.value
                patchPixel({ targetSize: v === '' ? null : [Number(v), Number(v)] })
              }} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">手动网格</span>
            <input className="input input-sm" type="number" placeholder="自动检测" min={8} max={256}
              value={px.gridSize?.[0] ?? ''}
              onChange={(e) => {
                const v = e.target.value
                patchPixel({ gridSize: v === '' ? null : [Number(v), Number(v)] })
              }} />
          </label>
        </div>

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>背景透明（抠掉纯色底）</span>
          <button className={`switch ${px.transparentBg ? 'on' : ''}`}
            onClick={() => patchPixel({ transparentBg: !px.transparentBg })}
            aria-label="切换背景透明">
            <i />
          </button>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 精修中…</> : '运行精修'}
        </button>

        {node.result ? (
          <div style={{ marginTop: 10 }}>
            <div className="ref-thumb" style={{ width: 96, height: 96 }}>
              <img src={node.result.url} alt="精修结果" />
            </div>
            <div className="result-meta" style={{ borderTop: 0 }}>
              <span className="meta-pill good">{node.result.width}×{node.result.height}</span>
              {node.result.refine ? (
                <>
                  <span className="meta-pill">网格 {node.result.refine.grid[0]}×{node.result.refine.grid[1]}</span>
                  <span className="meta-pill">{node.result.refine.colors} 色</span>
                </>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>还没有精修结果。连好输入后点「运行精修」。</div>
        )}
      </div>
    </div>
  )
}
