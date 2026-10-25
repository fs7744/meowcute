import { useMemo, useRef, useState } from 'react'
import { api } from '../api'
import type { InpaintMeta, InpaintNode as InpaintNodeType, RefAsset } from '../types'
import AdvancedSection from './AdvancedSection'

type Props = {
  node: InpaintNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  projectId: string
  /** 上游源图（没连 / 没产出时为 null） */
  upstream: { url: string; width: number; height: number; label: string } | null
  onChange: (patch: Partial<InpaintNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onOpenMask: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string, orig?: string) => void
}

/**
 * 局部重绘节点（第 4 类后处理里唯一「跑模型」的那个）。
 *
 * 卡片只放三件必填的事：**哪张图**（上游）、**涂哪儿**（蒙版）、**变成什么**
 * （提示词）。其余参数全在「更多设置」里，因为它们的默认值对 90% 的用法都对：
 * 局部范围、羽化 4px、外扩 8px、接缝色彩校正。
 *
 * 「预览最终提示词」是个刻意做重的功能：约束条款是程序加的（「只重画标记区域」
 * 那段），如果用户看不到它，就等于被偷偷改了输入——这个项目一直不允许这种事。
 */
export default function InpaintNode({
  node, index, running, progress, error, projectId, upstream,
  onChange, onDelete, onDragStart, onRun, onOpenMask,
  onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: InpaintNodeType['inpaint'] = {
    enabled: true, instruction: '', constraint: '', negative: '', refs: [],
    mode: 'crop', context: 0.35, feather: 4, expand: 8, hard: false,
    matchColor: true, matchStrength: 0.45, palette: 'none', paletteColors: 48,
    steps: null, seed: null, maxSeqLen: null, maskFile: '', mask: null,
  }
  const v = { ...DEFAULTS, ...node.inpaint }
  const patch = (p: Partial<typeof v>) => onChange({ inpaint: { ...v, ...p } })

  const fileRef = useRef<HTMLInputElement>(null)
  const [busyRef, setBusyRef] = useState(false)
  const [showPrompt, setShowPrompt] = useState(false)
  const [promptInfo, setPromptInfo] = useState<any>(null)
  const [promptBusy, setPromptBusy] = useState(false)

  const meta: InpaintMeta | null = node.meta ?? null
  const out = node.result ?? (node.images ?? [])[0] ?? null

  /** 蒙版是否还配得上当前源图：换图后旧蒙版必须重涂（后端也会拦） */
  const maskStale = useMemo(() => {
    if (!v.mask || !upstream) return false
    return v.mask.width !== upstream.width || v.mask.height !== upstream.height
  }, [v.mask, upstream])

  const ready = !!upstream && !!v.maskFile && !!v.instruction.trim() && !maskStale && !running
  const coverage = v.mask?.coverage ?? 0

  const uploadRef = async (files: FileList | null) => {
    if (!files?.length) return
    setBusyRef(true)
    try {
      const room = Math.max(0, 2 - v.refs.length)
      const added: RefAsset[] = []
      for (const f of Array.from(files).slice(0, room)) {
        const r = await api.uploadRef(projectId, f)
        added.push(r.ref as RefAsset)
      }
      if (added.length) patch({ refs: [...v.refs, ...added] })
    } catch (e: any) {
      alert(`参考图上传失败：${e.message || e}`)
    } finally {
      setBusyRef(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const loadPrompt = async () => {
    setPromptBusy(true)
    try {
      const info = await api.inpaintInstruction({
        instruction: v.instruction, constraint: v.constraint,
        negative: v.negative, refCount: v.refs.length,
      })
      setPromptInfo(info)
      setShowPrompt(true)
    } catch (e: any) {
      alert(`提示词预览失败：${e.message || e}`)
    } finally {
      setPromptBusy(false)
    }
  }

  return (
    <div className={`node inpaint ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 400 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">INPAINT</span>
        <span className="node-title">局部重绘</span>
        <span className="node-idx">R{index + 1}</span>
        <button className="node-x" title="删除这张节点"
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换局部重绘">
            <i />
          </button>
        </div>

        {/* 1. 源图 */}
        <div className="hint">
          源图：{upstream
            ? <b>{upstream.label} · {upstream.width}×{upstream.height}</b>
            : '未连线（把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到左侧端口）'}
        </div>
        {upstream ? (
          <button className="ip-src" title="点击放大查看"
            onClick={() => onPreview(upstream.url, '待重绘的源图')}>
            <img src={upstream.url} alt="源图" />
          </button>
        ) : null}

        {/* 2. 涂抹 */}
        <div className="ip-mask-row">
          <button className="btn btn-sm" onClick={onOpenMask} disabled={!upstream}>
            {v.maskFile ? '重新涂抹' : '涂抹区域'}
          </button>
          {v.mask ? (
            <span className={`meta-pill ${maskStale ? 'bad' : 'good'}`}
              title={maskStale
                ? '蒙版尺寸和当前源图不一致：换图后要重新涂抹'
                : '白色区域会被重画，区域外像素保持不变'}>
              涂抹 {Math.round(coverage * 100)}%
            </span>
          ) : (
            <span className="hint" style={{ fontSize: 11 }}>
              还没涂：先圈出要重画的地方
            </span>
          )}
          {maskStale ? (
            <span className="meta-pill bad">蒙版尺寸不符</span>
          ) : null}
        </div>

        {/* 3. 提示词 */}
        <label className="gen-field" style={{ marginTop: 8 }}>
          <span className="gen-field-label">这块要变成什么</span>
          <textarea className="input ip-prompt-in" rows={3}
            placeholder="例：换成锈迹斑斑的铁皮护胸，边缘有铆钉，破损处露出暗红色内衬"
            value={v.instruction}
            onChange={(e) => patch({ instruction: e.target.value })} />
        </label>
        <div className="hint" style={{ fontSize: 11, marginTop: -2 }}>
          {v.instruction.trim().length} 字 · 写「这一块」的内容，不用重复描述整张图
        </div>

        {/* 4. 可选参考图 */}
        <div className="ip-refs">
          <span className="gen-field-label" title="只借造型 / 材质 / 配色，不照抄构图">
            参考图（可选 · {v.refs.length}/2）
          </span>
          <div className="ref-strip">
            {v.refs.map((r) => (
              <div className="ref-thumb" key={r.id} title={r.name}>
                <img src={r.url} alt={r.name} />
                <button className="kill" title="移除"
                  onClick={() => patch({ refs: v.refs.filter((x) => x.id !== r.id) })}>×</button>
              </div>
            ))}
            {v.refs.length < 2 ? (
              <button className="ref-add" title="添加参考图" disabled={busyRef}
                onClick={() => fileRef.current?.click()}>
                {busyRef ? <span className="spin" /> : '+'}
              </button>
            ) : null}
          </div>
          <input ref={fileRef} type="file" accept="image/*" multiple hidden
            onChange={(e) => uploadRef(e.target.files)} />
        </div>

        {/* 5. 参数 */}
        <AdvancedSection title="更多设置"
          hint={`${v.mode === 'crop' ? '局部' : '整图'} · 羽化 ${v.feather}px · 外扩 ${v.expand}px · ${v.hard ? '硬边' : '接缝校正'} · 色板 ${v.palette === 'source' ? '吸附' : '不吸附'}`}>
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="局部：只把「选区+上下文」送去重画（细节更好）；整图：整张重画后按蒙版贴回（适合改光照/氛围）">
                重绘范围
              </span>
              <select className="input" value={v.mode}
                onChange={(e) => patch({ mode: e.target.value as typeof v.mode })}>
                <option value="crop">局部（推荐）</option>
                <option value="full">整图</option>
              </select>
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="选区向外多带多少上下文（占选区边长的比例）：越大模型越了解周围，但越容易改到邻居">
                上下文
              </span>
              <input className="input" type="number" min={0} max={1} step={0.05}
                value={v.context} onChange={(e) => patch({ context: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="贴回边界的柔和程度（只向内羽化：选区外像素一个都不变）">
                羽化
              </span>
              <input className="input" type="number" min={0} max={32} step={1}
                value={v.feather} onChange={(e) => patch({ feather: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="蒙版向外扩几像素，给模型留出「往哪长」的余地">
                外扩
              </span>
              <input className="input" type="number" min={0} max={64} step={1}
                value={v.expand} onChange={(e) => patch({ expand: Number(e.target.value) })} />
            </label>
          </div>

          <div className="switch-row" style={{ marginTop: 6 }}>
            <span className="t" style={{ fontSize: 12 }}
              title="像素素材用：边界不做羽化、不做色彩校正，保持硬边">
              硬边（像素风）
            </span>
            <button className={`switch ${v.hard ? 'on' : ''}`}
              onClick={() => patch({ hard: !v.hard })} aria-label="切换硬边">
              <i />
            </button>
          </div>

          <div className="param-grid" style={{ marginTop: 6 }}>
            <label className="gen-field">
              <span className="gen-field-label"
                title="把新内容拉回周围的色调。改「完全不同的颜色」时调低或关掉">
                接缝色彩校正
              </span>
              <button className={`switch ${v.matchColor && !v.hard ? 'on' : ''}`}
                disabled={v.hard}
                onClick={() => patch({ matchColor: !v.matchColor })}
                aria-label="切换色彩校正"><i /></button>
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="0=关，1=完全匹配周围色调">强度</span>
              <input className="input" type="number" min={0} max={1} step={0.05}
                disabled={!v.matchColor || v.hard}
                value={v.matchStrength}
                onChange={(e) => patch({ matchStrength: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="把重绘区里「接近源图已有颜色」的杂色吸回色板；差太远的新颜色会保留">
                色板吸附
              </span>
              <select className="input" value={v.palette}
                onChange={(e) => patch({ palette: e.target.value as typeof v.palette })}>
                <option value="none">不吸附</option>
                <option value="source">对齐源图</option>
              </select>
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="模型采样步数：默认 40，越低越快越糊">步数</span>
              <input className="input" type="number" min={4} max={60} step={1}
                placeholder="自动 40" value={v.steps ?? ''}
                onChange={(e) => patch({ steps: e.target.value ? Number(e.target.value) : null })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="留空 = 随机；同种子同参数可复现">种子</span>
              <input className="input" type="number" min={0} step={1}
                placeholder="随机" value={v.seed ?? ''}
                onChange={(e) => patch({ seed: e.target.value ? Number(e.target.value) : null })} />
            </label>
          </div>

          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="程序会自动追加的约束条款（「只重画标记区域」那段）。留空=用默认值，非空整段替换">
              约束条款（可改）
            </span>
            <textarea className="input ip-prompt-in" rows={3}
              placeholder="留空 = 用程序默认的「只重画标记区域，其余像素保持原样」那段"
              value={v.constraint}
              onChange={(e) => patch({ constraint: e.target.value })} />
          </label>
          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="留空=用程序默认负面词（接缝 / 亮边 / 拼接感 / 重影…）">负面词（可改）</span>
            <input className="input" placeholder="留空 = 程序默认"
              value={v.negative}
              onChange={(e) => patch({ negative: e.target.value })} />
          </label>

          <div className="ip-prompt-actions">
            <button className="btn btn-xs btn-ghost" onClick={loadPrompt} disabled={promptBusy}>
              {promptBusy ? '拼装中…' : '查看最终提示词'}
            </button>
            <button className="btn btn-xs btn-ghost"
              onClick={() => patch({ constraint: '', negative: '' })}
              title="把约束条款和负面词恢复成程序默认值">恢复默认条款</button>
          </div>
          {showPrompt && promptInfo ? (
            <div className="ip-prompt-view">
              <div className="ip-prompt-sec">
                <b>我写的</b>
                <div>{promptInfo.body || '（空）'}</div>
              </div>
              <div className="ip-prompt-sec">
                <b>程序追加{(!v.constraint.trim()) ? '（默认）' : '（已改）'}</b>
                <div>{promptInfo.constraint}</div>
              </div>
              {promptInfo.refClause ? (
                <div className="ip-prompt-sec">
                  <b>参考图声明</b>
                  <div>{promptInfo.refClause}</div>
                </div>
              ) : null}
              <div className="ip-prompt-sec">
                <b>负面词</b>
                <div>{promptInfo.negative}</div>
              </div>
              <div className="hint" style={{ fontSize: 10 }}>
                合计 {promptInfo.chars} 字 · 上限约 {promptInfo.maxChars} 字
              </div>
            </div>
          ) : null}
        </AdvancedSection>

        <button className="btn btn-sm" onClick={onRun} disabled={!ready}
          style={{ marginTop: 8, width: '100%' }}
          title={!v.instruction.trim() ? '先写提示词'
            : !v.maskFile ? '先涂抹区域'
              : maskStale ? '蒙版与源图尺寸不符：重新涂抹' : undefined}>
          {running ? <><span className="spin" /> 重绘中…</> : '局部重绘'}
        </button>
        {progress ? <div className="hint walk-progress">{progress}</div> : null}
        {error ? <div className="node-error">{error}</div> : null}

        {out ? (
          <>
            <div className="ip-compare">
              {upstream ? (
                <button className="ip-thumb" title="点开对比原图"
                  onClick={() => onPreview(out.url, '重绘结果', upstream.url)}>
                  <img src={upstream.url} alt="原图" />
                  <span>原图</span>
                </button>
              ) : null}
              <button className="ip-thumb" title="点开看大图"
                onClick={() => onPreview(out.url, '重绘结果', upstream?.url)}>
                <img src={out.url} alt="重绘结果" />
                <span>重绘</span>
              </button>
            </div>
            <div className="result-meta" style={{ borderTop: 0, marginTop: 6 }}>
              <span className="meta-pill">{meta?.mode === 'full' ? '整图' : '局部'}</span>
              {meta ? <span className="meta-pill">送模型 {meta.targetSize[0]}×{meta.targetSize[1]}</span> : null}
              {meta?.matchColor ? <span className="meta-pill">接缝校正 {meta.matchStrength}</span> : null}
              {meta?.palette === 'source'
                ? <span className="meta-pill">色板吸附 {Math.round((meta.paletteSnapped || 0) * 100)}%</span>
                : null}
              {meta?.seconds ? <span className="meta-pill">{meta.seconds}s</span> : null}
            </div>
            <div className="grid-actions wrap">
              <a className="btn btn-xs" href={out.url} download>下载 PNG</a>
              {v.mask?.url ? (
                <a className="btn btn-xs btn-ghost" href={v.mask.url} download>下载蒙版</a>
              ) : null}
            </div>
            {meta?.warnings?.length ? (
              <div className="hint warn" style={{ marginTop: 6 }}>
                {meta.warnings.slice(0, 3).map((w) => <div key={w}>· {w}</div>)}
              </div>
            ) : null}
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有重绘结果。涂好区域、写好提示词后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
