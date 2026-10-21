import { useState } from 'react'
import type { ResultImage, ResultNode as ResultNodeType, ResultMeta } from '../types'

type Props = {
  node: ResultNodeType
  index: number
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onPreview: (img: ResultImage, meta: ResultMeta) => void
  onCopyPrompt: () => void
  onFinishLink?: (id: string) => void
}

const fmtTime = (ts?: number) =>
  ts ? new Date(ts * 1000).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''

export default function ResultNode({
  node, index, onDelete, onDragStart, onPreview, onCopyPrompt, onFinishLink,
}: Props) {
  const [showOrig, setShowOrig] = useState<Record<string, boolean>>({})
  const [copied, setCopied] = useState(false)
  const m = node.meta || {}

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(node.prompt)
      setCopied(true)
      setTimeout(() => setCopied(false), 1400)
    } catch { /* 忽略 */ }
    onCopyPrompt()
  }

  return (
    <div className="node result" style={{ left: node.x, top: node.y, width: 420 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">RESULT</span>
        <span className="node-title">
          {m.model ? String(m.model).split('/').pop() : '生成结果'} · {fmtTime(node.createdAt)}
        </span>
        <span className="node-idx">R{index + 1}</span>
        <button className="node-x" title="删除这张结果卡" onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游节点的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink?.(node.id) }} />
      </div>

      <div className="node-body">
        <div className="result-grid">
          {node.images.length === 0 ? (
            <div className="hint" style={{ padding: 12 }}>
              把「生成图」或「像素精修」的输出连到左侧端口，结果会自动出现在这里。
            </div>
          ) : null}
          {node.images.map((img, i) => {
            const orig = !!showOrig[img.assetId]
            const r = img.refine
            return (
              <div className="result-item" key={img.assetId}>
                <div className="result-img-wrap" onClick={() => onPreview(img, m)}>
                  <img src={orig ? img.origUrl : img.url} alt={`结果 ${i + 1}`} />
                  {orig ? <span className="orig-badge">原图</span> : null}
                  <span className="zoom-badge">点击放大</span>
                </div>
                <div className="result-meta">
                  <span className="meta-pill good">{img.width}×{img.height}</span>
                  {r ? (
                    <>
                      <span className="meta-pill">网格 {r.grid[0]}×{r.grid[1]}{r.detected ? '（自动）' : ''}</span>
                      <span className={`meta-pill ${r.offPalette === 0 ? 'good' : 'bad'}`}>{r.colors} 色</span>
                      {r.palette !== 'auto' ? <span className="meta-pill">{r.palette}</span> : null}
                    </>
                  ) : (
                    <span className="meta-pill bad">未精修</span>
                  )}
                  {m.seed !== undefined ? <span className="meta-pill">seed {m.seed}</span> : null}
                </div>
                <div className="result-meta" style={{ borderTop: 0, paddingTop: 0 }}>
                  <button className="btn btn-sm btn-ghost" style={{ height: 26, fontSize: 12 }}
                    onClick={() => setShowOrig((s) => ({ ...s, [img.assetId]: !s[img.assetId] }))}>
                    {orig ? '看精修' : '看原图'}
                  </button>
                  {img.origSize ? (
                    <span className="meta-pill">原图 {img.origSize[0]}×{img.origSize[1]}</span>
                  ) : null}
                </div>
              </div>
            )
          })}
        </div>

        <div className="result-foot">
          {m.family ? <span className="tag gray">{m.family}</span> : null}
          {m.steps ? <span className="tag gray">{m.steps} 步</span> : null}
          {m.seconds ? <span className="tag gray">{m.seconds}s</span> : null}
          {m.vram?.peakGB ? (
            <span className="tag gray" title={`本次生成峰值显存 ${m.vram.peakGB}GB / 共 ${m.vram.totalGB}GB`}>
              峰值显存 {m.vram.peakGB}GB
            </span>
          ) : null}
          {m.autoRouted ? <span className="tag yellow">已自动路由编辑底座</span> : null}
          {m.refCount ? <span className="tag purple">{m.refCount} 参考图</span> : null}
        </div>

        {m.vramWarning ? <div className="warn">{m.vramWarning}</div> : null}

        <div className="result-foot">
          {node.prompt ? (
            <button className="btn btn-sm" onClick={copy}>{copied ? '已复制' : '复制提示词'}</button>
          ) : null}
          <div className="grow" />
          <span className="hint">点击图片放大查看</span>
        </div>
      </div>
    </div>
  )
}
