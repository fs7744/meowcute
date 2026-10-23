import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { Template } from '../types'

/**
 * 模板编辑器：内置默认模板与自定义模板都用它。
 *
 * 内置模板的编辑**不动代码**：保存到本地覆盖补丁（只存与出厂不同的字段），
 * 所以「恢复默认」永远可用，升级代码也不会把用户的修改冲掉。
 *
 * 三个页签对应三类真正会被消费的东西：
 * - 概览：模板页上看到的文字（名称 / 描述 / 亮点 / 链路说明）；
 * - 默认参数：defaults，只影响**在该模板下新建生成卡**的预填值；
 * - 节点：模板自带的节点链，节点参数才决定「用模板新建项目」时那些卡长什么样。
 *   这个区别必须写在界面上——否则「改了默认尺寸但模板自带的生成卡没变」
 *   会被当成 bug（实际是两套值）。
 */
type Props = {
  open: boolean
  template: Template | null
  onClose: () => void
  /** 保存/恢复/建副本之后把最新模板回传，列表就地更新（不整页刷新） */
  onSaved: (t: Template, msg?: string) => void
  /** 副本创建后希望列表重新拉一次（新 id 不在原列表里） */
  onDuplicated?: (t: Template) => void
  onReset?: (t: Template) => void
}

type Tab = 'info' | 'params' | 'nodes'

const NODE_CN: Record<string, string> = {
  generate: '生成图', result: '图片结果', input: '选择图片', refine: '像素精修',
  matte: '扣除背景', viewgen: '视角生成', threeview: '四视图拼板',
  walkvideo: '行走视频', sequence: '序列帧', spritesheet: '精灵整图',
  play: '可操控预览',
}

/** defaults 里真正会被读到的键；其它键写了也不生效，保存前明确提示 */
const DEFAULT_KEYS = new Set([
  'styleId', 'width', 'height', 'n', 'background', 'maxSeqLen', 'pixel', 'matte',
])

const splitLines = (s: string) => s.split('\n').map((x) => x.trim()).filter(Boolean)

/** 链路说明一行一条：`标题 | 说明`（全角竖线也认） */
const parseSteps = (s: string) => splitLines(s).map((line) => {
  const parts = line.split(/\s*[|｜]\s*/)
  return { title: parts[0] || '', detail: parts.slice(1).join('｜') }
}).filter((x) => x.title)

const stepsToText = (steps?: { title: string; detail: string }[]) =>
  (steps || []).map((s) => (s.detail ? `${s.title} | ${s.detail}` : s.title)).join('\n')

export default function TemplateEditor({
  open, template, onClose, onSaved, onDuplicated, onReset,
}: Props) {
  const [tab, setTab] = useState<Tab>('info')
  const [name, setName] = useState('')
  const [tagline, setTagline] = useState('')
  const [description, setDescription] = useState('')
  const [highlights, setHighlights] = useState('')
  const [notes, setNotes] = useState('')
  const [stepsText, setStepsText] = useState('')
  const [defaultsText, setDefaultsText] = useState('{}')
  const [nodes, setNodes] = useState<any[]>([])
  const [edges, setEdges] = useState<{ from: string; to: string }[]>([])
  const [nodeTexts, setNodeTexts] = useState<string[]>([])
  const [nodeErrors, setNodeErrors] = useState<Record<number, string>>({})
  const [openNode, setOpenNode] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 打开时按模板当前（已合并）的值初始化
  useEffect(() => {
    if (!open || !template) return
    const g = template.graph || { nodes: [], edges: [] }
    setTab('info')
    setName(template.name || '')
    setTagline(template.tagline || '')
    setDescription(template.description || '')
    setHighlights((template.highlights || []).join('\n'))
    setNotes((template.notes || []).join('\n'))
    setStepsText(stepsToText(template.steps))
    setDefaultsText(JSON.stringify(template.defaults || {}, null, 2))
    setNodes((g.nodes || []).map((n: any) => ({ ...n })))
    setEdges((g.edges || []).map((e: any) => ({ from: String(e.from), to: String(e.to) })))
    setNodeTexts((g.nodes || []).map((n: any) => JSON.stringify(n, null, 2)))
    setNodeErrors({})
    setOpenNode(null)
    setError(null)
    setBusy(false)
  }, [open, template])

  const defaultsInfo = useMemo(() => {
    try {
      const v = JSON.parse(defaultsText)
      if (!v || typeof v !== 'object' || Array.isArray(v)) {
        return { error: '默认参数必须是 JSON 对象（{ }）', unknown: [] as string[] }
      }
      const unknown = Object.keys(v).filter((k) => !DEFAULT_KEYS.has(k))
      return { error: null, unknown }
    } catch (e: any) {
      return { error: `默认参数不是合法 JSON：${e.message}`, unknown: [] as string[] }
    }
  }, [defaultsText])

  if (!open || !template) return null

  const isBuiltin = !template.custom
  const missingStepEdge = edges.some((e) => !nodes.some((n) => n.id === e.from)
    || !nodes.some((n) => n.id === e.to))

  const patchNodeText = (idx: number, text: string) => {
    setNodeTexts((arr) => arr.map((x, i) => (i === idx ? text : x)))
    try {
      const obj = JSON.parse(text)
      if (!obj || typeof obj !== 'object' || Array.isArray(obj)) throw new Error('必须是对象')
      setNodes((arr) => arr.map((x, i) => (i === idx ? obj : x)))
      setNodeErrors((m) => { const c = { ...m }; delete c[idx]; return c })
    } catch (e: any) {
      setNodeErrors((m) => ({ ...m, [idx]: String(e.message || e) }))
    }
  }

  const removeNode = (idx: number) => {
    const nid = String(nodes[idx]?.id || '')
    if (!nid) return
    const rest = nodes.filter((_, i) => i !== idx)
    if (!rest.length) { setError('模板至少要保留一个节点'); return }
    setNodes(rest)
    setNodeTexts((arr) => arr.filter((_, i) => i !== idx))
    setNodeErrors({})
    setEdges((arr) => arr.filter((e) => e.from !== nid && e.to !== nid))
    setOpenNode(null)
    setError(null)
  }

  /** 把 defaults 里的尺寸类参数写进模板自带的生成卡（两套值一键对齐） */
  const syncDefaultsToGenNode = () => {
    if (defaultsInfo.error) { setError(defaultsInfo.error); return }
    const d = JSON.parse(defaultsText) as Record<string, any>
    const keys = ['width', 'height', 'n', 'background', 'styleId', 'maxSeqLen'] as const
    let changed = 0
    const next = nodes.map((n) => {
      if (n.type !== 'generate') return n
      const params = { ...(n.params || {}) }
      let hit = false
      for (const k of keys) {
        if (d[k] === undefined) continue
        if (k === 'styleId') {
          if (n.styleId !== d[k]) { hit = true }
        } else if (params[k] !== d[k]) {
          params[k] = d[k]
          hit = true
        }
      }
      if (d.pixel && typeof d.pixel === 'object') {
        n = { ...n, pixel: { ...(n.pixel || {}), ...d.pixel } }
        hit = true
      }
      if (!hit) return n
      changed += 1
      return { ...n, params, styleId: d.styleId ?? n.styleId }
    })
    setNodes(next)
    setNodeTexts(next.map((n) => JSON.stringify(n, null, 2)))
    setNodeErrors({})
    setError(changed ? null : '模板里没有需要更新的生成卡（参数已经一致）')
  }

  const save = async () => {
    if (!name.trim()) { setError('模板名称不能为空'); setTab('info'); return }
    if (defaultsInfo.error) { setError(defaultsInfo.error); setTab('params'); return }
    if (Object.keys(nodeErrors).length) { setError('节点 JSON 有错误，修好后再保存'); setTab('nodes'); return }
    const defaults = JSON.parse(defaultsText) as Record<string, any>
    setBusy(true)
    setError(null)
    try {
      const t = isBuiltin
        ? (await api.saveBuiltinTemplate(template.id, {
            name: name.trim(), tagline, description,
            highlights: splitLines(highlights), notes: splitLines(notes),
            steps: parseSteps(stepsText),
            defaults,
            graph: { nodes, edges },
          })).template
        : (await api.updateTemplate(template.id, {
            name: name.trim(), tagline, description,
            defaults,
            graph: { nodes, edges },
          })).template
      onSaved(t, t.overridden ? '已保存（存为本地覆盖，可随时恢复默认）' : '已保存')
      onClose()
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const reset = async () => {
    if (!confirm('恢复出厂默认？你对这个模板的修改会被丢弃（已创建的项目不受影响）。')) return
    setBusy(true)
    try {
      const t = (await api.resetBuiltinTemplate(template.id)).template
      onReset?.(t)
      onSaved(t, '已恢复出厂默认')
      onClose()
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  const duplicate = async () => {
    setBusy(true)
    try {
      const t = (await api.duplicateTemplate(template.id)).template
      onDuplicated?.(t)
      onClose()
    } catch (e: any) {
      setError(String(e.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal wide" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">编辑模板 · {template.name}</div>
            <div className="hint" style={{ marginTop: 4 }}>
              {isBuiltin
                ? '内置默认模板：修改存为本地覆盖（只记录与出厂不同的字段），代码里的出厂定义不动，随时可恢复默认。'
                : '自定义模板：修改直接写回该模板，已创建的项目不受影响。'}
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>

        <div className="view-switch" style={{ alignSelf: 'flex-start' }}>
          {([['info', '概览'], ['params', '默认参数'], ['nodes', `节点（${nodes.length}）`]] as const).map(([k, label]) => (
            <button key={k} className={`vs-btn ${tab === k ? 'on' : ''}`}
              onClick={() => setTab(k as Tab)}>{label}</button>
          ))}
        </div>

        <div className="modal-body" style={{ maxHeight: '58vh', overflowY: 'auto', paddingRight: 4 }}>
          {tab === 'info' ? (
            <>
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
                <span className="hint">
                  模板页「01 → 02 → 03」那一行字。序号自动重排，删行即删步骤。
                </span>
              </label>
            </>
          ) : null}

          {tab === 'params' ? (
            <>
              <div className="hint" style={{ lineHeight: 1.75 }}>
                这里的参数只作用于<strong>用这个模板新建项目后、再新加的生成卡</strong>的预填值；
                模板自带的那张生成卡用的是「节点」页签里的参数——两套值互相独立。
              </div>
              <label className="field">
                <span className="field-label">defaults（JSON）</span>
                <textarea className="input" rows={14} spellCheck={false}
                  style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12 }}
                  value={defaultsText} onChange={(e) => setDefaultsText(e.target.value)} />
              </label>
              {defaultsInfo.error ? (
                <div className="err">{defaultsInfo.error}</div>
              ) : defaultsInfo.unknown.length ? (
                <div className="warn">
                  这些键不会被读取（拼错或已废弃）：{defaultsInfo.unknown.join('、')}。
                  可用键：{Array.from(DEFAULT_KEYS).join(' / ')}。
                </div>
              ) : null}
              <div>
                <button className="btn btn-sm" onClick={syncDefaultsToGenNode}>
                  把尺寸 / 画风 / 背景写进模板自带的生成卡
                </button>
                <span className="hint" style={{ marginLeft: 8 }}>
                  两套值对齐用；只改生成卡的对应字段，不动提示词。
                </span>
              </div>
            </>
          ) : null}

          {tab === 'nodes' ? (
            <>
              <div className="hint" style={{ lineHeight: 1.75 }}>
                节点决定「用这个模板新建项目」时画布上的初始链路。改节点 id 会让相关连线失效
                （保存时自动丢弃悬空连线）；想加节点请先在画布上摆好再「存为模板」。
              </div>
              {missingStepEdge ? (
                <div className="warn">
                  有连线指向不存在的节点，保存时会自动丢弃这些连线。
                </div>
              ) : null}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {nodes.map((n, i) => (
                  <div key={`${n.id || 'node'}-${i}`} className="tpl-node-row">
                    <div className="row between" style={{ gap: 8 }}>
                      <button className="btn btn-sm" style={{ minWidth: 132, textAlign: 'left' }}
                        onClick={() => setOpenNode(openNode === i ? null : i)}>
                        {openNode === i ? '▾' : '▸'} {NODE_CN[n.type] || n.type}
                        <span className="hint" style={{ marginLeft: 6 }}>{String(n.id || '')}</span>
                      </button>
                      <span className="hint" style={{ flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {n.type === 'generate'
                          ? (String(n.prompt || '').trim().slice(0, 60) || '（空提示词）')
                          : (n.type === 'viewgen' ? `视角 ${n.viewgen?.view || '?'}`
                            : n.type === 'result' ? '结果卡' : '')}
                      </span>
                      <button className="btn btn-sm btn-danger" onClick={() => removeNode(i)}
                        title="从模板里删掉这个节点（相关连线一并清理）">删除</button>
                    </div>
                    {nodeErrors[i] ? <div className="err">JSON 错误：{nodeErrors[i]}</div> : null}
                    {openNode === i ? (
                      <textarea className="input" rows={12} spellCheck={false}
                        style={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', fontSize: 12, marginTop: 6 }}
                        value={nodeTexts[i] ?? ''}
                        onChange={(e) => patchNodeText(i, e.target.value)} />
                    ) : null}
                  </div>
                ))}
                {!nodes.length ? <div className="hint">这个模板还没有节点图（规划中的模板）。</div> : null}
              </div>
              {edges.length ? (
                <div className="hint">
                  连线：{edges.map((e) => `${e.from}→${e.to}`).join('，')}
                </div>
              ) : null}
            </>
          ) : null}

          {error ? <div className="err">{error}</div> : null}
        </div>

        <div className="modal-foot" style={{ justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', gap: 8 }}>
            {isBuiltin ? (
              <>
                <button className="btn btn-sm" onClick={duplicate} disabled={busy}
                  title="基于当前内容建一个自定义模板，默认模板不受影响">另存为副本</button>
                {template.overridden ? (
                  <button className="btn btn-sm btn-danger" onClick={reset} disabled={busy}>
                    恢复默认
                  </button>
                ) : null}
              </>
            ) : null}
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn" onClick={onClose} disabled={busy}>取消</button>
            <button className="btn btn-primary" onClick={save} disabled={busy}>
              {busy ? '保存中…' : '保存'}
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
