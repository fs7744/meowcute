import { useEffect, useState } from 'react'
import { api } from '../api'
import type { MatteNode as MatteNodeType } from '../types'

type Props = {
  node: MatteNodeType
  index: number
  running?: boolean
  sourceLabel?: string | null
  onChange: (patch: Partial<MatteNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 扣除背景节点：接收上游图片 → 抠图 → 向下游输出透明 PNG。
 *
 * 顶部有一个使用开关：关掉时节点原样把上游图片交给下游，节点留着不动。
 * 这样「暂时不想扣底」不必删节点再重新连一遍线。
 */
export default function MatteNode({
  node, index, running, sourceLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const m = node.matte
  const patch = (p: Partial<typeof m>) => onChange({ matte: { ...m, ...p } })

  // 引擎体检：让用户知道这一趟到底走的是语义分割还是颜色键兜底
  const [engine, setEngine] = useState<{ primary: string; note: string } | null>(null)
  useEffect(() => {
    let alive = true
    api.matteStatus().then((s) => alive && setEngine({ primary: s.primary, note: s.note }))
      .catch(() => undefined)
    return () => { alive = false }
  }, [])

  const meta = node.result?.matte
  const methodLabel: Record<string, string> = {
    u2net: 'u2net 语义分割', grabcut: 'GrabCut 图割', key: '颜色键',
  }

  return (
    <div className={`node matte ${m.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 340 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">MATTE</span>
        <span className="node-title">扣除背景</span>
        <span className="node-idx">M{index + 1}</span>
        <button className="node-x" title="删除这张扣背景卡"
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
            使用此节点{m.enabled ? '' : '（已关闭，直接透传上游图片）'}
          </span>
          <button className={`switch ${m.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !m.enabled })} aria-label="切换扣背景节点">
            <i />
          </button>
        </div>

        <div className="hint">
          输入：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把图片/生成/精修连到左侧端口）'}
        </div>

        <label className="gen-field">
          <span className="gen-field-label">抠图方法</span>
          <select className="select" value={m.method}
            onChange={(e) => patch({ method: e.target.value as typeof m.method })}>
            <option value="auto">自动（语义分割 → 图割 → 颜色键）</option>
            <option value="u2net">u2net 语义分割</option>
            <option value="grabcut">GrabCut 图割</option>
            <option value="key">颜色键（纯色底）</option>
          </select>
        </label>

        {engine ? (
          <div className="hint" style={{ marginTop: -4 }}>
            当前引擎：<b>{engine.primary}</b> · {engine.note}
          </div>
        ) : null}

        <label className="gen-field">
          <span className="gen-field-label">背景容差 {m.tolerance}</span>
          <input type="range" min={10} max={200} step={5} value={m.tolerance}
            onChange={(e) => patch({ tolerance: Number(e.target.value) })} />
        </label>

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>硬边（像素素材，无半透明）</span>
          <button className={`switch ${m.hard ? 'on' : ''}`}
            onClick={() => patch({ hard: !m.hard })} aria-label="切换硬边">
            <i />
          </button>
        </div>
        {!m.hard ? (
          <label className="gen-field" style={{ marginTop: -6 }}>
            <span className="gen-field-label">羽化半径 {m.feather}px</span>
            <input type="range" min={0} max={24} step={1} value={m.feather}
              onChange={(e) => patch({ feather: Number(e.target.value) })} />
          </label>
        ) : null}

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>边缘去污（洗掉底色描边）</span>
          <button className={`switch ${m.despill ? 'on' : ''}`}
            onClick={() => patch({ despill: !m.despill })} aria-label="切换边缘去污">
            <i />
          </button>
        </div>

        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>裁到主体（去掉透明留白）</span>
          <button className={`switch ${m.trim ? 'on' : ''}`}
            onClick={() => patch({ trim: !m.trim })} aria-label="切换裁到主体">
            <i />
          </button>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 抠图中…</> : '运行扣背景'}
        </button>

        {node.result ? (
          <div style={{ marginTop: 10 }}>
            <button className="matte-thumb" title="点击放大（棋盘格=透明区域）"
              onClick={() => onPreview(node.result!.url, '扣除背景结果')}>
              <img src={node.result.url} alt="扣背景结果" />
            </button>
            <div className="result-meta" style={{ borderTop: 0 }}>
              <span className="meta-pill good">{node.result.width}×{node.result.height}</span>
              {meta ? <span className="meta-pill">{methodLabel[meta.method] || meta.method}</span> : null}
              {meta?.keptRatio != null
                ? <span className="meta-pill">保留 {Math.round(meta.keptRatio * 100)}%</span>
                : null}
            </div>
            {meta?.warning ? (
              <div className="ref-note" style={{ marginTop: 4 }}>{meta.warning}</div>
            ) : null}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有抠图结果。连好输入后点「运行扣背景」。
          </div>
        )}
      </div>
    </div>
  )
}
