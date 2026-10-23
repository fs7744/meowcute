import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Template } from '../types'

/**
 * 模板「文案」编辑：只改模板页上看到的文字。
 *
 * 节点链、提示词、参数一律在无限画布上改（模板页「在画布中编辑」）——
 * 用 JSON 表单改节点图是上一版的做法，用户明确反馈太复杂。这里只留
 * 画布上改不了的东西：名称、简介、描述、亮点、备注、链路说明。
 */
type Props = {
  open: boolean
  template: Template | null
  onClose: () => void
  onSaved: (t: Template, msg?: string) => void
}

const splitLines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)

/** 链路说明一行一条：`标题 | 说明`（全角竖线也认） */
const parseSteps = (s: string) => splitLines(s).map((line) => {
  const parts = line.split(/\s*[|｜]\s*/)
  return { title: parts[0] || '', detail: parts.slice(1).join('｜') }
}).filter((x) => x.title)

const stepsToText = (steps?: { title: string; detail: string }[]) =>
  (steps || []).map((s) => (s.detail ? `${s.title} | ${s.detail}` : s.title)).join('\n')

export default function TemplateInfoModal({ open, template, onClose, onSaved }: Props) {
  const [name, setName] = useState('')
  const [tagline, setTagline] = useState('')
  const [description, setDescription] = useState('')
  const [highlights, setHighlights] = useState('')
  const [notes, setNotes] = useState('')
  const [stepsText, setStepsText] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!open || !template) return
    setName(template.name || '')
    setTagline(template.tagline || '')
    setDescription(template.description || '')
    setHighlights((template.highlights || []).join('\n'))
    setNotes((template.notes || []).join('\n'))
    setStepsText(stepsToText(template.steps))
    setBusy(false)
    setError(null)
  }, [open, template])

  if (!open || !template) return null

  const isBuiltin = !template.custom

  const save = async () => {
    if (!name.trim()) { setError('模板名称不能为空'); return }
    setBusy(true)
    setError(null)
    try {
      // 内置模板只提交文案字段：后端会把这次提交**合并**进已有覆盖，
      // 所以之前用画布改的节点图不会因为「改个简介」被冲掉。
      const t = isBuiltin
        ? (await api.saveBuiltinTemplate(template.id, {
            name: name.trim(), tagline, description,
            highlights: splitLines(highlights), notes: splitLines(notes),
            steps: parseSteps(stepsText),
          })).template
        : (await api.updateTemplate(template.id, {
            name: name.trim(), tagline, description,
          })).template
      onSaved(t, '文案已保存')
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
            <div className="modal-title">模板文案 · {template.name}</div>
            <div className="hint" style={{ marginTop: 4 }}>
              节点链、提示词、参数请用「在画布中编辑」——这里只改模板页上显示的文字。
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>

        <div className="modal-body">
          <div className="form-grid">
            <label className="field">
              <span className="field-label">模板名称</span>
              <input className="input" value={name} maxLength={60}
                onChange={(e) => setName(e.target.value)} />
            </label>
            <label className="field">
              <span className="field-label">一句话</span>
              <input className="input" value={tagline} maxLength={120}
                onChange={(e) => setTagline(e.target.value)} />
            </label>
          </div>

          <label className="field">
            <span className="field-label">描述</span>
            <textarea className="input" rows={4} value={description} maxLength={1200}
              onChange={(e) => setDescription(e.target.value)} />
          </label>

          {isBuiltin ? (
            <>
              <div className="form-grid">
                <label className="field">
                  <span className="field-label">亮点（每行一条）</span>
                  <textarea className="input" rows={4} value={highlights}
                    onChange={(e) => setHighlights(e.target.value)} />
                </label>
                <label className="field">
                  <span className="field-label">备注（每行一条）</span>
                  <textarea className="input" rows={4} value={notes}
                    onChange={(e) => setNotes(e.target.value)} />
                </label>
              </div>

              <label className="field">
                <span className="field-label">链路说明（每行：标题 | 说明）</span>
                <textarea className="input" rows={4} value={stepsText}
                  onChange={(e) => setStepsText(e.target.value)} />
                <span className="hint">模板页「01 → 02 → 03」那一行字，序号自动重排。</span>
              </label>
            </>
          ) : (
            <div className="hint">
              自定义模板的亮点 / 备注由节点自动生成，不在这里编辑。
            </div>
          )}

          {error ? <div className="err">{error}</div> : null}
        </div>

        <div className="modal-foot">
          <button className="btn" onClick={onClose} disabled={busy}>取消</button>
          <button className="btn btn-primary" onClick={save} disabled={busy}>
            {busy ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </div>
  )
}
