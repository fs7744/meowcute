import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import type { SettingsPayload } from '../api'
import type { BackgroundOption, DoctorReport, PromptExample, Style } from '../types'

type Tab = 'doctor' | 'model' | 'examples' | 'styles' | 'about'

type ModelInfo = { path: string; family: string; name: string; gb: number }

const TABS: { id: Tab; icon: string; label: string; hint: string }[] = [
  { id: 'doctor', icon: '◎', label: '环境体检', hint: '15 项检查' },
  { id: 'model', icon: '▤', label: '模型与默认值', hint: '本地底座' },
  { id: 'examples', icon: '✦', label: '提示词示例库', hint: '生成卡里直接点选' },
  { id: 'styles', icon: '◈', label: '画风与背景', hint: '提示词词库' },
  { id: 'about', icon: '?', label: '关于', hint: '原理与数据' },
]

export default function Settings() {
  const [tab, setTab] = useState<Tab>('doctor')
  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-head">
          <div>
            <div className="page-title">设置</div>
            <div className="page-sub">先跑一遍体检确认能出图，再按自己的习惯调默认值与词库。</div>
          </div>
        </div>

        <div className="settings-layout">
          <div className="set-nav">
            {TABS.map((t) => (
              <button key={t.id} className={`set-tab ${tab === t.id ? 'on' : ''}`} onClick={() => setTab(t.id)}>
                <span className="mono" style={{ width: 14, textAlign: 'center' }}>{t.icon}</span>
                <span className="grow">{t.label}</span>
              </button>
            ))}
            <div className="hint" style={{ marginTop: 8, padding: '0 4px', lineHeight: 1.7 }}>
              提示词示例库就是「优秀提示词示例维护页面」——改完立刻在生成卡里生效。
            </div>
          </div>

          <div className="set-panel">
            {tab === 'doctor' ? <DoctorPanel /> : null}
            {tab === 'model' ? <ModelPanel /> : null}
            {tab === 'examples' ? <ExamplesPanel /> : null}
            {tab === 'styles' ? <StylesPanel /> : null}
            {tab === 'about' ? <AboutPanel /> : null}
          </div>
        </div>
      </div>
    </div>
  )
}

/* -------------------------------------------------------------------------- */
/* 体检                                                                        */
/* -------------------------------------------------------------------------- */

function DoctorPanel() {
  const [report, setReport] = useState<DoctorReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const run = () => {
    setLoading(true)
    api.doctor()
      .then((r) => { setReport(r); setError(null) })
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { run() }, [])

  if (loading && !report) return <div className="hint">正在体检…</div>
  if (error) return <div className="err">{error}</div>
  if (!report) return null

  const levelClass = (lv: string) => (lv === 'ok' ? 'ok' : lv === 'warn' ? 'warn-item' : 'bad')

  return (
    <>
      <div className="card card-pad">
        <div className="row" style={{ gap: 12, alignItems: 'flex-start' }}>
          <div className="grow">
            <div className="section-title" style={{ marginBottom: 6 }}>
              {report.canGenerate ? '可以出图' : '还不能出图'}
            </div>
            <div className="hint" style={{ lineHeight: 1.7 }}>{report.summary}</div>
          </div>
          <button className="btn btn-sm" onClick={run} disabled={loading}>
            {loading ? <><span className="spin" /> 体检中</> : '重新体检'}
          </button>
        </div>

        <div className="status-line" style={{ marginTop: 14, flexWrap: 'wrap' }}>
          <span className="meta-pill">GPU {report.gpu || '未检测到'}</span>
          <span className="meta-pill">总显存 {report.vramGB} GB</span>
          <span className="meta-pill">空闲 {report.freeVRAMGB} GB</span>
          {report.warnings ? <span className="tag gray">{report.warnings} 项提醒</span> : null}
          {report.errors ? <span className="tag pink">{report.errors} 项错误</span> : null}
        </div>
      </div>

      <div className="check-list">
        {report.items.map((it) => (
          <div className={`check-item ${levelClass(it.level)}`} key={it.key}>
            <span className="mark">{it.level === 'ok' ? '✓' : it.level === 'warn' ? '!' : '×'}</span>
            <span className="txt grow">
              <b>{it.label}</b>
              <span style={{ color: 'var(--muted)' }}> — {it.detail}</span>
              {it.level !== 'ok' && it.fix ? <div className="fix">修复：{it.fix}</div> : null}
            </span>
          </div>
        ))}
      </div>

      {report.models.length ? (
        <div className="card card-pad">
          <div className="section-title" style={{ marginBottom: 8 }}>已发现的本地模型</div>
          <div className="check-list">
            {report.models.map((m) => (
              <div className="status-line" key={m.path}>
                <span className="mono" style={{ color: 'var(--white)' }}>{m.name}</span>
                <span className="tag gray">{m.family}</span>
                <span className="grow" />
                <span className="mono">{m.gb} GB</span>
                <span className="mono" style={{ color: 'var(--muted-2)' }}>{m.path}</span>
              </div>
            ))}
          </div>
        </div>
      ) : null}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* 模型与默认值                                                                */
/* -------------------------------------------------------------------------- */

function ModelPanel() {
  const [settings, setSettings] = useState<any>(null)
  const [models, setModels] = useState<ModelInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const [testing, setTesting] = useState(false)

  useEffect(() => {
    api.settings()
      .then((r) => { setSettings(r.settings); setModels(r.models) })
      .catch((e) => setMsg({ kind: 'err', text: String(e.message || e) }))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <div className="hint">加载中…</div>
  if (!settings) return <div className="err">{msg?.text || '读取设置失败'}</div>

  const diff = settings.providers?.diffusers || {}
  const defs = settings.defaults || {}
  const pixel = settings.pixel || {}

  const patch = (obj: any) => setSettings({ ...settings, ...obj })

  const patchDiff = (k: string, v: any) =>
    patch({ providers: { ...settings.providers, diffusers: { ...diff, [k]: v } } })
  const patchDefs = (k: string, v: any) =>
    patch({ defaults: { ...defs, [k]: v } })
  const patchPixel = (k: string, v: any) =>
    patch({ pixel: { ...pixel, [k]: v } })

  const save = async () => {
    setSaving(true)
    setMsg(null)
    try {
      const payload: Partial<SettingsPayload> = {
        providers: { diffusers: diff },
        defaults: defs,
        pixel,
        aiLabel: settings.aiLabel,
      }
      await api.saveSettings(payload)
      setMsg({ kind: 'ok', text: '已保存。新设置对下一次生成生效。' })
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    } finally {
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setMsg(null)
    try {
      const r = await api.testModel()
      setMsg({ kind: r.ok ? 'ok' : 'err', text: r.message })
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    } finally {
      setTesting(false)
    }
  }

  const unload = async () => {
    try {
      const r = await api.unloadModel()
      setMsg({ kind: 'ok', text: r.message })
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    }
  }

  // 图片底座只列图片族（qwen21），视频底座只列视频族（wan）：混列会让用户
  // 把 Wan 选成生图模型，然后在 _run 里才报「暂不支持的模型族」。
  const imageModels = models.filter((m) => m.family === 'qwen21')
  const videoModels = models.filter((m) => m.family === 'wan')
  const imagePaths = imageModels.map((m) => m.path)
  const videoPaths = videoModels.map((m) => m.path)

  return (
    <>
      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 4 }}>本地底座</div>
        <div className="hint" style={{ lineHeight: 1.75, marginBottom: 14 }}>
          <b>Qwen-Image-2.1 一个底座同时管三件事</b>：文生图、带参考图的指令改图、原生 RGBA 透明输出
          （带不带参考图由节点自己决定，模型不用切）。视频底座 Wan2.2 TI2V-5B 按需换入换出，
          同一时间只占一份显存，跑完即释放。
        </div>

        <div className="form-grid">
          <label className="field">
            <span className="field-label">图片底座（文生图 / 改图通用）</span>
            <select className="select" value={diff.model || ''} onChange={(e) => {
              // 改图复用同一个模型：两个字段一起改，别让它们漂移后出现
              // 「文生图是新底座、改图是旧底座」的混合状态
              const v = e.target.value
              patch({ providers: { ...settings.providers, diffusers: { ...diff, model: v, editModel: v } } })
            }}>
              {imagePaths.length === 0 ? <option value={diff.model || ''}>{diff.model || '未发现图片底座'}</option> : null}
              {imagePaths.map((p) => <option key={p} value={p}>{p}</option>)}
              {diff.model && !imagePaths.includes(diff.model) ? <option value={diff.model}>{diff.model}（缺失）</option> : null}
            </select>
          </label>

          <label className="field">
            <span className="field-label">视频底座（行走视频用）</span>
            <select className="select" value={diff.videoModel || ''} onChange={(e) => patchDiff('videoModel', e.target.value)}>
              {videoPaths.length === 0 ? <option value={diff.videoModel || ''}>{diff.videoModel || '未发现视频底座'}</option> : null}
              {videoPaths.map((p) => <option key={p} value={p}>{p}</option>)}
              {diff.videoModel && !videoPaths.includes(diff.videoModel) ? <option value={diff.videoModel}>{diff.videoModel}（缺失）</option> : null}
            </select>
          </label>

          <label className="field" style={{ gridColumn: '1 / -1' }}>
            <span className="field-label">
              全局负面词
              <span className="hint">像素风最怕渐变、抗锯齿、噪点 —— 这些词默认就带着</span>
            </span>
            <textarea className="textarea" rows={2} value={diff.negativePrompt || ''}
              onChange={(e) => patchDiff('negativePrompt', e.target.value)} />
          </label>
        </div>

        <div className="row" style={{ marginTop: 14, gap: 8 }}>
          <button className="btn btn-sm" onClick={test} disabled={testing}>
            {testing ? <><span className="spin" /> 探测中</> : '测试模型可用性'}
          </button>
          <button className="btn btn-sm" onClick={unload}>卸载模型释放显存</button>
          <div className="grow" />
          <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>
            {saving ? <><span className="spin" /> 保存中</> : '保存设置'}
          </button>
        </div>
        {msg ? <div className={msg.kind === 'ok' ? 'ok' : 'err'} style={{ marginTop: 10 }}>{msg.text}</div> : null}
      </div>

      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 4 }}>新建节点时的默认值</div>
        <div className="hint" style={{ marginBottom: 14 }}>
          只影响之后新建的生成卡（在画布上点「生成图」时预填），已有节点不受影响。
          步数不在这里设：Qwen-Image-2.1 是非蒸馏底座，固定按 40 步跑（官方推荐值）。
        </div>

        <div className="form-grid">
          <label className="field">
            <span className="field-label">默认张数</span>
            <input className="input" type="number" min={1} max={4} value={defs.n ?? 1}
              onChange={(e) => patchDefs('n', Math.min(4, Math.max(1, Number(e.target.value))))} />
          </label>
          <label className="field">
            <span className="field-label">默认宽</span>
            <input className="input" type="number" step={64} min={256} max={1536} value={defs.width ?? 512}
              onChange={(e) => patchDefs('width', Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field-label">默认高</span>
            <input className="input" type="number" step={64} min={256} max={1536} value={defs.height ?? 512}
              onChange={(e) => patchDefs('height', Number(e.target.value))} />
          </label>
        </div>
      </div>

      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 4 }}>像素精修默认值</div>
        <div className="hint" style={{ marginBottom: 14 }}>
          精修是纯 CPU 的确定性后处理：FFT 找网格 → 每格采样 → 收敛到共享色板。不开模型，几秒钟出结果。
        </div>

        <div className="form-grid">
          <label className="field">
            <span className="field-label">色数上限</span>
            <input className="input" type="number" min={2} max={64} value={pixel.colors ?? 16}
              onChange={(e) => patchPixel('colors', Number(e.target.value))} />
          </label>
          <label className="field">
            <span className="field-label">固定色板</span>
            <select className="select" value={pixel.palette || 'auto'}
              onChange={(e) => patchPixel('palette', e.target.value)}>
              <option value="auto">自动（从画面提取）</option>
              <option value="gameboy">Game Boy（4 色）</option>
              <option value="nes">NES（28 色）</option>
            </select>
          </label>
        </div>

        <div className="row" style={{ marginTop: 12 }}>
          <button className="btn btn-primary btn-sm" onClick={save} disabled={saving}>保存设置</button>
        </div>
      </div>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* 提示词示例库                                                                */
/* -------------------------------------------------------------------------- */

type Draft = { id?: string; category: string; title: string; content: string; note: string }

const EMPTY_DRAFT: Draft = { category: '', title: '', content: '', note: '' }

function ExamplesPanel() {
  const [categories, setCategories] = useState<string[]>([])
  const [examples, setExamples] = useState<PromptExample[]>([])
  const [loading, setLoading] = useState(true)
  const [active, setActive] = useState<string>('全部')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const load = () => {
    setLoading(true)
    api.promptExamples()
      .then((r) => { setCategories(r.categories); setExamples(r.examples) })
      .catch((e) => setMsg({ kind: 'err', text: String(e.message || e) }))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const shown = useMemo(
    () => (active === '全部' ? examples : examples.filter((e) => e.category === active)),
    [examples, active],
  )

  const startNew = () => setDraft({ ...EMPTY_DRAFT, category: active === '全部' ? (categories[0] || '角色描述') : active })

  const save = async () => {
    if (!draft) return
    if (!draft.title.trim()) { setMsg({ kind: 'err', text: '标题不能为空' }); return }
    if (!draft.content.trim()) { setMsg({ kind: 'err', text: '提示词内容不能为空' }); return }
    setBusy(true)
    setMsg(null)
    try {
      await api.upsertExample({
        id: draft.id,
        category: draft.category,
        title: draft.title.trim(),
        content: draft.content.trim(),
        note: draft.note.trim(),
      })
      setDraft(null)
      load()
      setMsg({ kind: 'ok', text: '已保存，生成卡里马上能点到。' })
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    } finally {
      setBusy(false)
    }
  }

  const remove = async (ex: PromptExample) => {
    if (!confirm(`删除示例「${ex.title}」？`)) return
    try {
      await api.deleteExample(ex.id)
      load()
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    }
  }

  const reset = async () => {
    if (!confirm('恢复内置示例？\n\n自建的示例不会被删除，内置示例的改动会被还原。')) return
    try {
      await api.resetExamples()
      load()
      setMsg({ kind: 'ok', text: '内置示例已还原。' })
    } catch (e: any) {
      setMsg({ kind: 'err', text: String(e.message || e) })
    }
  }

  return (
    <>
      <div className="card card-pad">
        <div className="row" style={{ alignItems: 'flex-start', gap: 12 }}>
          <div className="grow">
            <div className="section-title" style={{ marginBottom: 4 }}>优秀提示词示例库</div>
            <div className="hint" style={{ lineHeight: 1.75 }}>
              这里就是生成卡的「示例」来源。一条好示例应该写清主体、外观、动作、镜头，
              并把像素风细节（轮廓、色数、网格）交给画风和契约去补 —— 示例只管画什么，不管画多细。
            </div>
          </div>
          <button className="btn btn-primary btn-sm" onClick={startNew}>＋ 新增示例</button>
        </div>

        <div className="cat-tabs" style={{ marginTop: 14 }}>
          <button className={active === '全部' ? 'on' : ''} onClick={() => setActive('全部')}>全部 {examples.length}</button>
          {categories.map((c) => {
            const n = examples.filter((e) => e.category === c).length
            return (
              <button key={c} className={active === c ? 'on' : ''} onClick={() => setActive(c)}>{c} {n}</button>
            )
          })}
        </div>
        {msg ? <div className={msg.kind === 'ok' ? 'ok' : 'err'} style={{ marginTop: 10 }}>{msg.text}</div> : null}
      </div>

      {draft ? (
        <div className="ex-editor">
          <div className="row">
            <div className="section-title">{draft.id ? '编辑示例' : '新增示例'}</div>
            <div className="grow" />
            <button className="btn btn-sm" onClick={() => setDraft(null)}>取消</button>
          </div>

          <div className="form-grid">
            <label className="field">
              <span className="field-label">分类</span>
              <input className="input" list="ex-cats" value={draft.category}
                onChange={(e) => setDraft({ ...draft, category: e.target.value })} placeholder="角色描述" />
              <datalist id="ex-cats">
                {categories.map((c) => <option key={c} value={c} />)}
              </datalist>
            </label>
            <label className="field">
              <span className="field-label">标题</span>
              <input className="input" value={draft.title}
                onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="例：猫武士 · 持剑站姿" />
            </label>
          </div>

          <label className="field">
            <span className="field-label">
              提示词正文
              <span className="hint">写主体 + 外观 + 动作 + 镜头；不要写「像素风」「模糊」这类词</span>
            </span>
            <textarea className="textarea" rows={4} value={draft.content}
              onChange={(e) => setDraft({ ...draft, content: e.target.value })}
              placeholder="例：一只橘白相间的猫武士，穿着深蓝色布甲，右手握短剑，正面站姿，表情严肃，全身像" />
          </label>

          <label className="field">
            <span className="field-label">备注<span className="hint">为什么这条示例好？点选时能看到</span></span>
            <input className="input" value={draft.note}
              onChange={(e) => setDraft({ ...draft, note: e.target.value })}
              placeholder="例：主体明确、颜色少、动作单一，最适合像素化" />
          </label>

          <div className="row">
            <div className="grow" />
            <button className="btn btn-primary btn-sm" onClick={save} disabled={busy}>
              {busy ? <><span className="spin" /> 保存中</> : '保存示例'}
            </button>
          </div>
        </div>
      ) : null}

      {loading ? (
        <div className="hint">加载中…</div>
      ) : shown.length === 0 ? (
        <div className="empty">
          <span className="pixel">NO EXAMPLE</span>
          「{active}」下还没有示例，点右上角新增一条。
        </div>
      ) : (
        <div className="ex-list">
          {shown.map((ex) => (
            <div className="ex-item" key={ex.id}>
              <div className="ex-item-head">
                <span className="ex-title">{ex.title}</span>
                <span className="tag gray">{ex.category}</span>
                {ex.builtin === false ? <span className="tag pink">自建</span> : null}
                <button className="btn btn-sm" onClick={() => setDraft({
                  id: ex.id, category: ex.category, title: ex.title, content: ex.content, note: ex.note,
                })}>编辑</button>
                <button className="btn btn-sm btn-danger" onClick={() => remove(ex)}>删除</button>
              </div>
              <div className="ex-content">{ex.content}</div>
              {ex.note ? <div className="ex-note">↳ {ex.note}</div> : null}
            </div>
          ))}
        </div>
      )}

      <div className="row">
        <div className="hint">内置示例被改坏了不要紧，随时能还原。</div>
        <div className="grow" />
        <button className="btn btn-sm" onClick={reset}>还原内置示例</button>
      </div>
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* 画风与背景                                                                  */
/* -------------------------------------------------------------------------- */

function StylesPanel() {
  const [styles, setStyles] = useState<Style[]>([])
  const [backgrounds, setBackgrounds] = useState<BackgroundOption[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    api.styles()
      .then((r) => { setStyles(r.styles); setBackgrounds(r.backgrounds) })
      .catch(() => { /* 静默：这里只读展示 */ })
      .finally(() => setLoading(false))
  }, [])

  const groups = useMemo(() => {
    const m = new Map<string, Style[]>()
    for (const s of styles) {
      if (!m.has(s.group)) m.set(s.group, [])
      m.get(s.group)!.push(s)
    }
    return Array.from(m.entries())
  }, [styles])

  if (loading) return <div className="hint">加载中…</div>

  return (
    <>
      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 4 }}>背景预设</div>
        <div className="hint" style={{ lineHeight: 1.75, marginBottom: 14 }}>
          默认是中灰而不是绿幕 —— 实测本地模型画不出干净的纯绿，绿色会渗进轮廓，
          抠图反而更难受。中性灰不偏色、不抢高光，后续按亮度做 alpha 最稳。
        </div>
        <div className="style-lib">
          {backgrounds.map((b) => (
            <div className="style-lib-item" key={b.id}>
              <div className="style-lib-name">
                <span style={{
                  width: 14, height: 14, display: 'inline-block',
                  background: b.swatch, border: '1px solid var(--line-strong)',
                }} />
                {b.label}
                {b.default ? <span className="tag pink">默认</span> : null}
              </div>
              <div className="style-lib-words">{b.hint}</div>
              <div className="ex-note mono" style={{ wordBreak: 'break-all' }}>{b.phrase || '（不写入提示词）'}</div>
            </div>
          ))}
        </div>
      </div>

      {groups.map(([g, list]) => (
        <div className="card card-pad" key={g}>
          <div className="section-title" style={{ marginBottom: 10 }}>
            {g} <span className="hint" style={{ marginLeft: 6 }}>{list.length} 种</span>
          </div>
          <div className="style-lib">
            {list.map((s) => (
              <div className="style-lib-item" key={s.id}>
                <div className="style-lib-name">
                  {s.name}
                  {s.pixelClass ? <span className="tag pink">像素类</span> : null}
                </div>
                <div className="style-lib-words">{s.hint}</div>
                <div className="ex-note mono" style={{ wordBreak: 'break-all' }}>{s.words}</div>
                {s.pixelize ? (
                  <div className="ex-note">
                    精修建议：{s.pixelize.grid ? `目标网格 ${s.pixelize.grid}×${s.pixelize.grid}` : `块 ${s.pixelize.block}px`} / {s.pixelize.colors} 色
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ))}
    </>
  )
}

/* -------------------------------------------------------------------------- */
/* 关于                                                                        */
/* -------------------------------------------------------------------------- */

function AboutPanel() {
  const [health, setHealth] = useState<any>(null)
  useEffect(() => {
    api.health().then(setHealth).catch(() => { /* 忽略 */ })
  }, [])

  return (
    <>
      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 6 }}>像素风为什么能干净</div>
        <div className="hint" style={{ lineHeight: 1.9 }}>
          光靠一句「pixel art」是出不了像素图的 —— 模型会给你一张「看起来像像素」的模糊图。
          这个工具把生成拆成三道互相兜底的关卡：
          <ol style={{ margin: '10px 0 0 18px', padding: 0, lineHeight: 1.9 }}>
            <li><b>提示词契约</b>：写清网格尺寸、每格单色、限定色板、无抗锯齿、硬轮廓，让模型先往网格上画。</li>
            <li><b>FFT 网格对齐</b>：用快速傅里叶变换找出画面里真实的像素块大小，再用 Sobel 梯度峰吸附，
              把采样点强行落到格子中心 —— 不靠猜。</li>
            <li><b>共享调色板</b>：按频次加权做最远点采样，压到 16 色内；背景不会靠面积把色位占满。</li>
          </ol>
          前两步决定「画得下」，第三步决定「收得住」。
        </div>
      </div>

      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 8 }}>运行信息</div>
        <div className="check-list">
          <div className="status-line">
            <span className="grow">服务状态</span>
            <span className={health?.ok ? 'ok' : 'err'}>{health?.ok ? `正常 · v${health.version}` : '未连接'}</span>
          </div>
          <div className="status-line">
            <span className="grow">数据目录</span>
            <span className="mono" style={{ color: 'var(--muted)' }}>{health?.dataDir || '—'}</span>
          </div>
          <div className="status-line">
            <span className="grow">已发现模型</span>
            <span className="mono">{health?.modelCount ?? '—'} 个</span>
          </div>
          <div className="status-line">
            <span className="grow">模型家族</span>
            <span className="mono">{(health?.families || []).join(' / ') || '—'}</span>
          </div>
        </div>
      </div>

      <div className="card card-pad">
        <div className="section-title" style={{ marginBottom: 6 }}>快捷键</div>
        <div className="hint" style={{ lineHeight: 1.9 }}>
          <b>滚轮</b> 以光标为中心缩放 · <b>空白处拖拽</b> 平移画布 · <b>卡头拖拽</b> 移动节点 ·{' '}
          <b>Delete</b> 删除选中节点 · <b>0</b> 复位缩放 · <b>Esc</b> 关闭预览
        </div>
      </div>
    </>
  )
}
