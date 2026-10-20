import { useEffect, useState } from 'react'
import type { Template } from '../types'
import { PixelIcon } from './CatLogo'

type Props = {
  open: boolean
  templates: Template[]
  defaultTemplateId?: string
  /** 从「查看参数」进来时只展开参数面板，不聚焦项目名输入框 */
  paramsOnly?: boolean
  onClose: () => void
  onCreate: (name: string, templateId: string) => Promise<void>
}

export default function NewProjectModal({ open, templates, defaultTemplateId, paramsOnly, onClose, onCreate }: Props) {
  const [name, setName] = useState('')
  const [tid, setTid] = useState(defaultTemplateId || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [showParams, setShowParams] = useState(false)

  useEffect(() => {
    if (!open) return
    const first = defaultTemplateId
      || templates.find((t) => t.status === 'available')?.id
      || ''
    setTid(first)
    setName('')
    setError(null)
    setBusy(false)
    setShowParams(!!paramsOnly)
  }, [open, defaultTemplateId, paramsOnly, templates])

  if (!open) return null

  const submit = async () => {
    if (!tid) { setError('请选择一个工作流模板'); return }
    setBusy(true)
    setError(null)
    try {
      await onCreate(name.trim() || '未命名项目', tid)
      onClose()
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const cur = templates.find((t) => t.id === tid)
  const d = (cur?.defaults || {}) as Record<string, any>

  /** 画布节点里的实际参数：defaults 没给的（如生图模板的扣背景）从 graph 里取。 */
  const fromGraph = (key: string): any => {
    const nodes = cur?.graph?.nodes || []
    if (key === 'matte') return nodes.find((n: any) => n.type === 'matte')?.matte
    if (key === 'pixel') return nodes.find((n: any) => n.type === 'refine')?.pixel
    return undefined
  }

  // 内部字段名 → 界面词，参数面板别露 camelCase
  const LABEL: Record<string, string> = {
    enabled: '启用', method: '方法', tolerance: '容差', hard: '硬边', feather: '羽化',
    despill: '边缘去污', trim: '裁到主体', edge: '边缘偏移', bottomTrim: '底部裁切',
    gridSnap: '网格吸附', colors: '色数', palette: '色板', gridSize: '网格',
    targetSize: '目标尺寸', transparentBg: '透明背景',
    styleId: '画风', width: '宽', height: '高', n: '张数', background: '背景色',
    maxSeqLen: '序列长度',
  }
  const fmt = (v: unknown): string => {
    if (v === null || v === undefined || v === '') return '—'
    if (typeof v === 'boolean') return v ? '开' : '关'
    if (Array.isArray(v)) return v.length ? `${v.length} 项` : '—'
    if (typeof v === 'object') {
      return Object.entries(v as Record<string, unknown>)
        .filter(([, val]) => val !== null && val !== undefined && val !== '')
        .map(([k, val]) => `${LABEL[k] || k} ${typeof val === 'boolean' ? (val ? '开' : '关') : val}`)
        .join(' · ') || '—'
    }
    return String(v)
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">新建项目</div>
            <div className="hint" style={{ marginTop: 4 }}>先选一个工作流模板——模板决定画布里出现哪些节点。</div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>

        <div className="modal-body">
          <div className="tpl-pick">
            {templates.map((t) => {
              const disabled = t.status !== 'available'
              return (
                <button
                  key={t.id}
                  className={`tpl-pick-item ${tid === t.id ? 'on' : ''}`}
                  disabled={disabled}
                  onClick={() => setTid(t.id)}
                >
                  <span className="tpl-icon" style={{ color: t.accent, width: 36, height: 36 }}>
                    <PixelIcon kind={t.icon} color={t.accent} />
                  </span>
                  <span className="grow">
                    <span className="tpl-pick-name">
                      {t.name}{' '}
                      {disabled ? <span className="tag gray" style={{ marginLeft: 6 }}>规划中</span> : null}
                    </span>
                    <span className="tpl-pick-desc">{t.tagline}</span>
                  </span>
                </button>
              )
            })}
          </div>

          {cur ? (
            <div className="param-panel">
              <button className="param-toggle" onClick={() => setShowParams((s) => !s)}>
                <span className="grow">模板参数 · {cur.name}</span>
                <span className="hint" style={{ margin: 0 }}>{showParams ? '收起 ▴' : '展开 ▾'}</span>
              </button>
              {showParams ? (
                <div className="param-body">
                  <div className="param-desc">{cur.description}</div>
                  {cur.inputs.map((i) => (
                    <div className="param-row" key={i.key}>
                      <div className="param-key">
                        {i.label}{i.required ? '' : '（可选）'}
                      </div>
                      <div className="param-val">{fmt(d[i.key] ?? fromGraph(i.key))}</div>
                      {i.hint ? <div className="param-hint">{i.hint}</div> : null}
                    </div>
                  ))}
                  {cur.steps.length ? (
                    <div className="param-row">
                      <div className="param-key">画布节点链</div>
                      <div className="param-val">{cur.steps.map((s) => s.title).join(' → ')}</div>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          ) : null}

          <label className="field">
            <span className="field-label">项目名称</span>
            <input className="input" placeholder="例：猫武士 · 像素精灵套图" value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submit() }} autoFocus={!paramsOnly} />
          </label>

          {error ? <div className="err">{error}</div> : null}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose}>取消</button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? <><span className="spin" /> 创建中</> : '创建并打开画布'}
          </button>
        </div>
      </div>
    </div>
  )
}
