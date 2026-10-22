import { useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type {
  BackgroundOption, GenNode as GenNodeType, PixelOptions, PromptExample, RefAsset, Style,
  TaskProgress,
} from '../types'

type Props = {
  node: GenNodeType
  index: number
  styles: Style[]
  backgrounds: BackgroundOption[]
  examples: PromptExample[]
  categories: string[]
  projectId: string
  task: { progress: TaskProgress | null; error: string | null } | null
  onChange: (patch: Partial<GenNodeType>) => void
  onGenerate: () => void
  onPreview: () => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onCancel: () => void
  onFinishLink?: (id: string) => void
  onStartLink?: (id: string, client?: { clientX: number; clientY: number }) => void
}

const SIZES: [number, number][] = [
  [512, 512], [512, 768], [768, 512], [768, 768], [1024, 1024],
]

export default function GenNode({
  node, index, styles, backgrounds, examples, categories, projectId, task,
  onChange, onGenerate, onPreview, onDelete, onDragStart, onCancel, onFinishLink,
  onStartLink,
}: Props) {
  const [exCat, setExCat] = useState(categories[0] || '角色描述')
  const [uploading, setUploading] = useState(false)
  const [advOpen, setAdvOpen] = useState(false)
  const taRef = useRef<HTMLTextAreaElement>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const style = useMemo(() => styles.find((s) => s.id === node.styleId) || null, [styles, node.styleId])
  // 像素精修只对像素类画风开放：写实/卡通/水彩走了量化会把渐变和细节压成色块
  const isPixel = !!style?.pixelClass
  const running = !!task?.progress && !task.progress.doneAt
  const p = task?.progress

  const grouped = useMemo(() => {
    const g: Record<string, Style[]> = {}
    for (const s of styles) {
      (g[s.group] ||= []).push(s)
    }
    return g
  }, [styles])

  const examplesOfCat = useMemo(
    () => examples.filter((e) => e.category === exCat),
    [examples, exCat],
  )

  const insertExample = (text: string) => {
    const ta = taRef.current
    const cur = node.prompt || ''
    if (!ta) {
      onChange({ prompt: cur ? `${cur}，${text}` : text })
      return
    }
    const start = ta.selectionStart ?? cur.length
    const end = ta.selectionEnd ?? cur.length
    const before = cur.slice(0, start)
    const after = cur.slice(end)
    const sep = before && !/[，,。\s]$/.test(before) ? '，' : ''
    const next = `${before}${sep}${text}${after}`
    onChange({ prompt: next })
    requestAnimationFrame(() => {
      ta.focus()
      const pos = before.length + sep.length + text.length
      ta.setSelectionRange(pos, pos)
    })
  }

  const uploadRefs = async (files: FileList | null) => {
    if (!files || !files.length) return
    setUploading(true)
    try {
      const room = Math.max(0, 4 - node.refs.length)
      const picked = Array.from(files).slice(0, room)
      const added: RefAsset[] = []
      for (const f of picked) {
        const res = await api.uploadRef(projectId, f)
        added.push(res.ref)
      }
      if (added.length) onChange({ refs: [...node.refs, ...added] })
    } catch (e: any) {
      alert(`参考图上传失败：${e.message || e}`)
    } finally {
      setUploading(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const patchParams = (patch: Partial<GenNodeType['params']>) => onChange({ params: { ...node.params, ...patch } })
  const patchPixel = (patch: Partial<PixelOptions>) => onChange({ pixel: { ...node.pixel, ...patch } })

  return (
    <div className="node gen" style={{ left: node.x, top: node.y, width: 404 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">GENERATE</span>
        <span className="node-title">{style ? `${style.name} · 文字生图` : '文字生图'}</span>
        <span className="node-idx">#{index + 1}</span>
        <button className="node-x" title="删除这张生成卡" onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（把「选择图片」的输出接进来作为参考图）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink?.(node.id) }} />
        <div className="port out" title="按住拖到目标节点即可连线"
          onMouseDown={(e) => {
            e.stopPropagation()
            onStartLink?.(node.id, { clientX: e.clientX, clientY: e.clientY })
          }} />
      </div>

      <div className="node-body" style={{ maxHeight: 'min(70vh, 760px)', overflowY: 'auto' }}>
        {/* 1. 提示词 */}
        <div className="gen-field">
          <div className="gen-field-head">
            <span className="gen-field-label"><span className="idx">01</span>文字描述</span>
            <span className="hint">{node.prompt.length} 字</span>
          </div>
          <textarea
            ref={taRef}
            className="textarea"
            style={{ minHeight: 92 }}
            placeholder="例：灰白英短猫武士，红色短披风，腰挎武士刀，三头身比例，正面站立"
            value={node.prompt}
            onChange={(e) => onChange({ prompt: e.target.value })}
          />
          <div className="ex-cat">
            {categories.map((c) => (
              <button key={c} className={exCat === c ? 'on' : ''} onClick={() => setExCat(c)}>{c}</button>
            ))}
          </div>
          <div className="ex-chips">
            {examplesOfCat.map((e) => (
              <button key={e.id} className="ex-chip" title={`${e.content}\n\n${e.note || ''}`}
                onClick={() => insertExample(e.content)}>
                {e.title}
              </button>
            ))}
            {examplesOfCat.length === 0 ? <span className="hint">该分类暂无示例，可在设置页添加</span> : null}
          </div>
        </div>

        {/* 2. 参考图 */}
        <div className="gen-field">
          <div className="gen-field-head">
            <span className="gen-field-label"><span className="idx">02</span>参考图（可选 0-4 张）</span>
          </div>
          <div className="ref-strip">
            {node.refs.map((r) => (
              <div className="ref-thumb" key={r.id} title={r.name}>
                <img src={r.url} alt={r.name} />
                <button className="kill" title="移除"
                  onClick={() => onChange({ refs: node.refs.filter((x) => x.id !== r.id) })}>×</button>
              </div>
            ))}
            {node.refs.length < 4 ? (
              <button className="ref-add" title="添加参考图" disabled={uploading}
                onClick={() => fileRef.current?.click()}>
                {uploading ? <span className="spin" /> : '+'}
              </button>
            ) : null}
          </div>
          <input ref={fileRef} type="file" accept="image/*" multiple hidden
            onChange={(e) => uploadRefs(e.target.files)} />
          {node.refs.length > 0 ? (
            <div className="ref-note">已选参考图：自动改用编辑底座（Qwen-Image-Edit），以「保持角色外观不变」为主指令</div>
          ) : (
            <div className="hint">加入参考图可保持已有角色的外观一致（换姿势 / 换道具 / 出同套素材）</div>
          )}
        </div>

        {/* 3. 格式参数 */}
        <div className="gen-field">
          <div className="gen-field-head">
            <span className="gen-field-label"><span className="idx">03</span>图片格式</span>
          </div>
          <div className="size-chips">
            {SIZES.map(([w, h]) => (
              <button key={`${w}x${h}`}
                className={`size-chip ${node.params.width === w && node.params.height === h ? 'on' : ''}`}
                onClick={() => patchParams({ width: w, height: h })}>{w}×{h}</button>
            ))}
          </div>
          <div className="hint" style={{ marginTop: 4 }}>
            512 最快（默认）；768 平衡；1024 细节最好但像素量是 512 的 4 倍，
            显存不足时会明显变慢。
          </div>
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label">宽 / 高（16 对齐）</span>
              <div className="row">
                <input className="input input-sm" type="number" min={128} max={1536} step={64}
                  value={node.params.width}
                  onChange={(e) => patchParams({ width: Number(e.target.value) || 512 })} />
                <input className="input input-sm" type="number" min={128} max={1536} step={64}
                  value={node.params.height}
                  onChange={(e) => patchParams({ height: Number(e.target.value) || 512 })} />
              </div>
            </label>
            <label className="gen-field">
              <span className="gen-field-label">生成张数</span>
              <div className="chips">
                {[1, 2, 3, 4].map((n) => (
                  <button key={n} className={`chip ${node.params.n === n ? 'on' : ''}`}
                    onClick={() => patchParams({ n })}>{n} 张</button>
                ))}
              </div>
            </label>
            <label className="gen-field">
              <span className="gen-field-label">种子（空 = 随机）</span>
              <input className="input input-sm" type="number" placeholder="随机"
                value={node.params.seed ?? ''}
                onChange={(e) => patchParams({ seed: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label">步数（空 = 自动）</span>
              <input className="input input-sm" type="number" placeholder="文生图 9 / 编辑 40"
                value={node.params.steps ?? ''}
                onChange={(e) => patchParams({ steps: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label">提示词质量</span>
              <select className="select" value={node.params.maxSeqLen ?? ''}
                onChange={(e) => patchParams({
                  maxSeqLen: e.target.value === '' ? null : Number(e.target.value),
                })}>
                <option value="">自动（推荐）</option>
                <option value={512}>快速</option>
                <option value={768}>标准</option>
                <option value={1024}>高质量</option>
              </select>
            </label>
          </div>
          <div className="hint" style={{ marginTop: 4 }}>
            质量越高，长提示词（含像素契约）越不容易被截断；只影响文本编码，
            对出图速度影响很小。自动会按提示词长度选档。
          </div>
        </div>

        {/* 4. 背景 */}
        <div className="gen-field">
          <div className="gen-field-head">
            <span className="gen-field-label"><span className="idx">04</span>背景</span>
          </div>
          <div className="chips">
            {backgrounds.map((b) => (
              <button key={b.id} className={`chip ${node.params.background === b.id ? 'on' : ''}`}
                title={b.hint} onClick={() => patchParams({ background: b.id as any })}>
                <i style={{
                  display: 'inline-block', width: 9, height: 9, marginRight: 5,
                  background: b.swatch === 'transparent'
                    ? 'repeating-conic-gradient(#999 0% 25%, #444 0% 50%) 0 0 / 6px 6px'
                    : b.swatch,
                  boxShadow: '0 0 0 1px rgba(255,255,255,0.3)',
                }} />
                {b.label}
              </button>
            ))}
          </div>
          <div className="hint">{backgrounds.find((b) => b.id === node.params.background)?.hint}</div>
        </div>

        {/* 5. 画风 */}
        <div className="gen-field">
          <div className="gen-field-head">
            <span className="gen-field-label"><span className="idx">05</span>游戏画风</span>
            {style?.pixelClass ? <span className="tag pink">像素类</span> : null}
          </div>
          <div className="style-scroll">
            {Object.entries(grouped).map(([group, list]) => (
              <div key={group}>
                <div className="style-group-name">{group}</div>
                <div className="style-chips">
                  {list.map((s) => (
                    <button key={s.id} className={`style-chip ${node.styleId === s.id ? 'on' : ''}`}
                      title={s.hint}
                      onClick={() => onChange({
                        styleId: s.id,
                        // 像素类画风 → 自动开启精修并套用建议色数；
                        // 非像素类 → 强制关闭，避免量化毁掉渐变和细节
                        pixel: {
                          ...node.pixel,
                          enabled: !!s.pixelClass,
                          ...(s.pixelize ? { colors: s.pixelize.colors } : {}),
                        },
                      })}>
                      {s.pixelClass ? <i className="px" /> : null}
                      {s.name}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
          {style ? <div className="style-words-line">前置风格词：{style.words}</div> : null}
        </div>

        {/* 6. 像素精修（仅像素类画风可用） */}
        <div className={`pixel-box ${isPixel && node.pixel.enabled ? 'on' : ''}`}>
          <div className="switch-row">
            <span className="t">像素精修
              <span className="tag gray" style={{ marginLeft: 4 }}>网格对齐 + 调色板</span>
            </span>
            <button className={`switch ${isPixel && node.pixel.enabled ? 'on' : ''}`}
              disabled={!isPixel}
              onClick={() => { if (isPixel) patchPixel({ enabled: !node.pixel.enabled }) }}
              aria-label="切换像素精修">
              <i />
            </button>
          </div>
          {!isPixel ? (
            <div className="hint">
              像素精修只对像素类画风生效。当前{style ? `「${style.name}」` : '未选画风'}
              不是像素画风，出图会保留原始细节与渐变。
            </div>
          ) : node.pixel.enabled ? (
            <>
              <div className="switch-row">
                <span className="t" style={{ fontSize: 12 }}>网格对齐（FFT 检测 + 邻近吸附）</span>
                <button className={`switch ${node.pixel.gridSnap ? 'on' : ''}`}
                  onClick={() => patchPixel({ gridSnap: !node.pixel.gridSnap })} aria-label="切换网格对齐">
                  <i />
                </button>
              </div>
              <div className="param-grid">
                <label className="gen-field">
                  <span className="gen-field-label">调色板色数</span>
                  <select className="select" value={node.pixel.colors}
                    onChange={(e) => patchPixel({ colors: Number(e.target.value) })}>
                    {[4, 8, 12, 16, 24, 32, 48].map((c) => <option key={c} value={c}>{c} 色</option>)}
                  </select>
                </label>
                <label className="gen-field">
                  <span className="gen-field-label">固定色板</span>
                  <select className="select" value={node.pixel.palette}
                    onChange={(e) => patchPixel({ palette: e.target.value })}>
                    <option value="auto">自动提取（推荐）</option>
                    <option value="gameboy">GameBoy 4 色</option>
                    <option value="nes">NES 28 色</option>
                  </select>
                </label>
              </div>
              <div className="switch-row">
                <span className="t" style={{ fontSize: 12 }}>背景透明（抠掉纯色底）</span>
                <button className={`switch ${node.pixel.transparentBg ? 'on' : ''}`}
                  onClick={() => patchPixel({ transparentBg: !node.pixel.transparentBg })}
                  aria-label="切换背景透明">
                  <i />
                </button>
              </div>
              <details className="pixel-advanced" open={advOpen}
                onToggle={(e) => setAdvOpen((e.target as HTMLDetailsElement).open)}>
                <summary>高级：网格 / 输出尺寸 / 采样</summary>
                <div className="pixel-adv-grid">
                  <label className="gen-field">
                    <span className="gen-field-label">输出像素尺寸</span>
                    <div className="row">
                      <input className="input input-sm" type="number" placeholder="自动" min={8} max={256}
                        value={node.pixel.targetSize?.[0] ?? ''}
                        onChange={(e) => {
                          const v = e.target.value
                          patchPixel({
                            targetSize: v === '' ? null
                              : [Number(v), Number(v)],
                          })
                        }} />
                      <span className="hint">方形</span>
                    </div>
                  </label>
                  <label className="gen-field">
                    <span className="gen-field-label">手动网格</span>
                    <input className="input input-sm" type="number" placeholder="自动检测" min={8} max={256}
                      value={node.pixel.gridSize?.[0] ?? ''}
                      onChange={(e) => {
                        const v = e.target.value
                        patchPixel({ gridSize: v === '' ? null : [Number(v), Number(v)] })
                      }} />
                  </label>
                </div>
                <div className="hint" style={{ marginTop: 6 }}>
                  输出尺寸 = 最终素材边长（如 32/48/64）；手动网格会覆盖自动检测，适合固定规格的素材集。
                </div>
              </details>
            </>
          ) : (
            <div className="hint">关闭后直接保存 AI 原图（带像素质感，但每格颜色不唯一）</div>
          )}
        </div>
      </div>

      <div className="node-foot">
        <button className="btn btn-sm" onClick={onPreview} disabled={running}>预览提示词</button>
        <div className="grow" />
        {running ? (
          <button className="btn btn-sm btn-danger" onClick={onCancel}>取消</button>
        ) : null}
        <button className="btn btn-primary btn-sm" style={{ height: 34 }}
          onClick={onGenerate} disabled={running || !node.prompt.trim()}>
          {running ? <><span className="spin" /> 生成中</> : `生成 ${node.params.n} 张`}
        </button>
      </div>

      {p ? (
        <div className="node-foot" style={{ borderTop: 0, paddingTop: 0 }}>
          <div className="task-bar" style={{ flex: 1 }}>
            <div className="line">
              {p.total > 0 ? (
                <i style={{ width: `${Math.min(100, (p.done / p.total) * 100)}%` }} />
              ) : (
                <i className="ind" style={{ width: '100%' }} />
              )}
            </div>
            <div className="meta">
              <span>{p.doneAt ? (p.error ? '失败' : '完成') : (p.label || '准备中…')}</span>
              <span className="elapsed">{p.elapsed.toFixed(1)}s</span>
            </div>
          </div>
        </div>
      ) : null}

      {task?.error ? <div className="err" style={{ margin: '0 12px 12px' }}>{task.error}</div> : null}
    </div>
  )
}
