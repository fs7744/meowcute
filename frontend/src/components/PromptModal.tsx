import { useEffect, useState } from 'react'
import type { PromptPreview } from '../types'

type Props = {
  open: boolean
  loading: boolean
  error: string | null
  data: PromptPreview | null
  onClose: () => void
}

/** 预览生成提示词：分段展示「哪一段从哪来」，避免黑盒拼提示词。 */
export default function PromptModal({ open, loading, error, data, onClose }: Props) {
  const [copied, setCopied] = useState('')

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const copy = async (text: string, tag: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(tag)
      setTimeout(() => setCopied(''), 1400)
    } catch { /* 剪贴板被拒（非 https）时静默，用户可手动选中 */ }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">生成提示词预览</div>
            <div className="hint" style={{ marginTop: 4 }}>
              实际送给模型的完整文本。画风词在最前，像素契约在最后。
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>

        {loading ? <div className="hint">组装中…</div> : null}
        {error ? <div className="err">{error}</div> : null}

        {data ? (
          <>
            <div className="seg-list">
              {data.breakdown.map((seg) => (
                <div className="seg" key={seg.label}>
                  <div className="seg-head">
                    <span className="seg-label">{seg.label}</span>
                  </div>
                  <div className="seg-text">{seg.text}</div>
                </div>
              ))}
            </div>

            <div className="row between" style={{ margin: '16px 0 6px' }}>
              <span className="label">最终提示词</span>
              <button className="btn btn-sm" onClick={() => copy(data.prompt, 'prompt')}>
                {copied === 'prompt' ? '已复制' : '复制'}
              </button>
            </div>
            <pre className="prompt-pre">{data.prompt}</pre>

            <div className="row between" style={{ margin: '12px 0 6px' }}>
              <span className="label">负面提示词</span>
              <button className="btn btn-sm" onClick={() => copy(data.negative, 'neg')}>
                {copied === 'neg' ? '已复制' : '复制'}
              </button>
            </div>
            <pre className="prompt-pre" style={{ borderLeftColor: 'var(--pink)' }}>{data.negative || '（空）'}</pre>

            <div className="hint" style={{ marginTop: 12 }}>
              契约网格 {data.grid[0]}×{data.grid[1]}
              {data.useContract ? ' · 像素契约已启用' : ' · 像素契约未启用'}
            </div>
          </>
        ) : null}

        <div className="modal-foot">
          <button className="btn" onClick={onClose}>知道了</button>
        </div>
      </div>
    </div>
  )
}
