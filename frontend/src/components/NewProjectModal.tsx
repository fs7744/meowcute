import { useEffect, useState } from 'react'
import type { Template } from '../types'
import { PixelIcon } from './CatLogo'

type Props = {
  open: boolean
  templates: Template[]
  defaultTemplateId?: string
  onClose: () => void
  onCreate: (name: string, templateId: string) => Promise<void>
}

export default function NewProjectModal({ open, templates, defaultTemplateId, onClose, onCreate }: Props) {
  const [name, setName] = useState('')
  const [tid, setTid] = useState(defaultTemplateId || '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    const first = defaultTemplateId
      || templates.find((t) => t.status === 'available')?.id
      || ''
    setTid(first)
    setName('')
    setError(null)
    setBusy(false)
  }, [open, defaultTemplateId, templates])

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

          <label className="field">
            <span className="field-label">项目名称</span>
            <input className="input" placeholder="例：猫武士 · 像素精灵套图" value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') submit() }} autoFocus />
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
