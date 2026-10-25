import { useEffect, useState } from 'react'
import { api } from '../api'
import type {
  EraseMeta, EraseMethod, EraseMethodInfo, EraseNode as EraseNodeType,
} from '../types'
import AdvancedSection from './AdvancedSection'

type Props = {
  node: EraseNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  /** 后端给的四种补法（含适用场景）：知识放在后端，卡片只负责显示 */
  methods: EraseMethodInfo[]
  /** 上游源图（没连 / 没产出时为 null） */
  upstream: { url: string; width: number; height: number; label: string } | null
  onChange: (patch: Partial<EraseNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onOpenMask: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string, orig?: string) => void
}

/**
 * 去除局部节点：涂掉水印 / 杂物，用周围内容补上。
 *
 * 和「局部重绘」最大的区别是**没有必填的提示词**——用户要的是「这块的东西消失」，
 * 而不是「变成什么」。所以卡片的主体是「选补法」，每种补法自带一句「什么时候用」：
 *
 * - 扩散修复：纯色/渐变背景、水印、划痕（毫秒级，不跑模型）
 * - 纹理平移：重复纹理（砖、木纹、金属板、条纹）
 * - 镜像：对称 / 大色块
 * - AI 补全：周围没东西可借（人物、大物体）
 *
 * 这个选择顺序是刻意的：默认不跑模型。小改动本来只需要参考周围区域，
 * 让人等 40 秒去看一张「其实毫秒级就能做对」的图，是产品设计上的错。
 */
export default function EraseNode({
  node, index, running, progress, error, methods, upstream,
  onChange, onDelete, onDragStart, onRun, onOpenMask,
  onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: EraseNodeType['erase'] = {
    enabled: true, method: 'telea', expand: 6, feather: 2, hard: false,
    matchColor: true, matchStrength: 0.6, palette: 'none', paletteColors: 48,
    instruction: '', constraint: '', negative: '', aiMode: 'crop', context: 0.35,
    steps: null, seed: null, maxSeqLen: null, maskFile: '', mask: null,
  }
  const v = { ...DEFAULTS, ...node.erase }
  const patch = (p: Partial<typeof v>) => onChange({ erase: { ...v, ...p } })

  const [showPrompt, setShowPrompt] = useState(false)
  const [promptInfo, setPromptInfo] = useState<any>(null)
  const [promptBusy, setPromptBusy] = useState(false)
  // AI 方式下负面词留空 = 用程序默认（残留轮廓 / 水印 / 文字 / 鬼影…）。
  // 默认是哪几个词必须看得见：以前只有展开「查看最终提示词」才显示。
  const [defaultNeg, setDefaultNeg] = useState<string | null>(null)
  useEffect(() => {
    let alive = true
    api.eraseInstruction({})
      .then((info) => { if (alive) setDefaultNeg(info.negative || '') })
      .catch(() => { if (alive) setDefaultNeg('') })
    return () => { alive = false }
  }, [])

  const meta: EraseMeta | null = node.meta ?? null
  const out = node.result ?? (node.images ?? [])[0] ?? null
  const isAI = v.method === 'ai'

  const maskStale = !!v.mask && !!upstream
    && (v.mask.width !== upstream.width || v.mask.height !== upstream.height)
  const ready = !!upstream && !!v.maskFile && !maskStale && !running
  const coverage = v.mask?.coverage ?? 0
  const current = methods.find((m) => m.id === v.method)

  const loadPrompt = async () => {
    setPromptBusy(true)
    try {
      setPromptInfo(await api.eraseInstruction({
        instruction: v.instruction, constraint: v.constraint,
      }))
      setShowPrompt(true)
    } catch (e: any) {
      alert(`提示词预览失败：${e.message || e}`)
    } finally {
      setPromptBusy(false)
    }
  }

  return (
    <div className={`node erase ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 400 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">ERASE</span>
        <span className="node-title">去除局部</span>
        <span className="node-idx">D{index + 1}</span>
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
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换去除局部">
            <i />
          </button>
        </div>

        <div className="hint">
          源图：{upstream
            ? <b>{upstream.label} · {upstream.width}×{upstream.height}</b>
            : '未连线（把「选择图片 / 生成图 / 像素精修 / 扣除背景」连到左侧端口）'}
        </div>
        {upstream ? (
          <button className="ip-src" title="点击放大查看"
            onClick={() => onPreview(upstream.url, '待处理的源图')}>
            <img src={upstream.url} alt="源图" />
          </button>
        ) : null}

        <div className="ip-mask-row">
          <button className="btn btn-sm" onClick={onOpenMask} disabled={!upstream}>
            {v.maskFile ? '重新涂抹' : '涂抹要去掉的东西'}
          </button>
          {v.mask ? (
            <span className={`meta-pill ${maskStale ? 'bad' : 'good'}`}
              title={maskStale ? '蒙版尺寸和当前源图不一致：换图后要重新涂抹'
                : '涂到的地方（含外扩）会被擦掉并用周围内容补上'}>
              涂抹 {Math.round(coverage * 100)}%
            </span>
          ) : (
            <span className="hint" style={{ fontSize: 11 }}>还没涂：先圈出水印/杂物</span>
          )}
          {maskStale ? <span className="meta-pill bad">蒙版尺寸不符</span> : null}
        </div>

        {/* 补法：四种，逐个带适用场景 */}
        <div className="er-methods">
          {(methods.length ? methods : [
            { id: 'telea' as EraseMethod, label: '扩散修复', hint: '水印/划痕/平滑底', needsModel: false },
            { id: 'offset' as EraseMethod, label: '纹理平移', hint: '重复纹理', needsModel: false },
            { id: 'mirror' as EraseMethod, label: '镜像', hint: '对称/大色块', needsModel: false },
            { id: 'ai' as EraseMethod, label: 'AI 补全', hint: '大物体/复杂结构', needsModel: true },
          ]).map((m) => (
            <button key={m.id}
              className={`er-m ${v.method === m.id ? 'on' : ''}`}
              title={m.hint}
              onClick={() => patch({ method: m.id })}>
              {m.label}
              {m.needsModel ? <span className="er-m-tag">慢</span> : null}
            </button>
          ))}
        </div>
        {current ? (
          <div className="hint" style={{ fontSize: 11, marginTop: 4 }}>
            {current.label}：{current.hint}
          </div>
        ) : null}

        {/* AI 方式的提示词（可选补充 + 可见可改的默认条款） */}
        {isAI ? (
          <>
            <label className="gen-field" style={{ marginTop: 8 }}>
              <span className="gen-field-label"
                title="可选：补充一句这块到底是什么（例如「这是木地板」），不填也能跑。流程是「先自动补平 → 再让模型把这块画自然」，不是让模型去擦东西">
                补充说明（可选）
              </span>
              <textarea className="input ip-prompt-in" rows={2}
                placeholder="例：这是一片草地，补成草地就行"
                value={v.instruction}
                onChange={(e) => patch({ instruction: e.target.value })} />
            </label>
            <div className="ip-prompt-actions">
              <button className="btn btn-xs btn-ghost" onClick={loadPrompt}
                disabled={promptBusy}>
                {promptBusy ? '拼装中…' : '查看最终提示词'}
              </button>
            </div>
            {showPrompt && promptInfo ? (
              <div className="ip-prompt-view">
                <div className="ip-prompt-sec">
                  <b>我写的</b>
                  <div>{promptInfo.body || '（空，只用下面的默认条款）'}</div>
                </div>
                <div className="ip-prompt-sec">
                  <b>程序追加{(!v.constraint.trim()) ? '（默认）' : '（已改）'}</b>
                  <div>{promptInfo.constraint}</div>
                </div>
                <div className="ip-prompt-sec">
                  <b>负面词</b>
                  <div>{promptInfo.negative}</div>
                </div>
                <div className="hint" style={{ fontSize: 10 }}>
                  合计 {promptInfo.chars} 字 · 上限约 {promptInfo.maxChars} 字
                </div>
              </div>
            ) : null}
          </>
        ) : null}

        <AdvancedSection title="更多设置"
          hint={`外扩 ${v.expand}px · 羽化 ${v.feather}px · ${v.hard ? '硬边' : `接缝校正 ${v.matchStrength}`} · 色板 ${v.palette === 'source' ? '吸附' : '不吸附'}`}>
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="蒙版向外扩几像素。水印边缘的抗锯齿比笔画宽 1~3px，不外扩会留一圈淡影">
                外扩
              </span>
              <input className="input" type="number" min={0} max={64} step={1}
                value={v.expand} onChange={(e) => patch({ expand: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="贴回边界的柔和程度（只向内，不会推到选区外）">羽化</span>
              <input className="input" type="number" min={0} max={32} step={1}
                value={v.feather} onChange={(e) => patch({ feather: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="把补出来的颜色拉回周围色调；改大面积/换色时调低">接缝色彩校正</span>
              <input className="input" type="number" min={0} max={1} step={0.05}
                disabled={v.hard || !v.matchColor}
                value={v.matchStrength}
                onChange={(e) => patch({ matchStrength: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="把补出来的杂色吸回源图色板（像素素材用）">色板吸附</span>
              <select className="input" value={v.palette}
                onChange={(e) => patch({ palette: e.target.value as typeof v.palette })}>
                <option value="none">不吸附</option>
                <option value="source">对齐源图</option>
              </select>
            </label>
          </div>

          <div className="switch-row" style={{ marginTop: 6 }}>
            <span className="t" style={{ fontSize: 12 }}
              title="像素素材用：边界不做羽化、不做色彩校正">硬边（像素风）</span>
            <button className={`switch ${v.hard ? 'on' : ''}`}
              onClick={() => patch({ hard: !v.hard })} aria-label="切换硬边"><i /></button>
          </div>

          {isAI ? (
            <>
              <div className="param-grid" style={{ marginTop: 6 }}>
                <label className="gen-field">
                  <span className="gen-field-label"
                    title="局部：只把「选区+上下文」送去重画；整图：整张重画后按蒙版贴回">
                    AI 范围
                  </span>
                  <select className="input" value={v.aiMode}
                    onChange={(e) => patch({ aiMode: e.target.value as typeof v.aiMode })}>
                    <option value="crop">局部（推荐）</option>
                    <option value="full">整图</option>
                  </select>
                </label>
                <label className="gen-field">
                  <span className="gen-field-label" title="局部范围外带的上下文比例">上下文</span>
                  <input className="input" type="number" min={0} max={1} step={0.05}
                    value={v.context}
                    onChange={(e) => patch({ context: Number(e.target.value) })} />
                </label>
                <label className="gen-field">
                  <span className="gen-field-label" title="模型采样步数，默认 40">步数</span>
                  <input className="input" type="number" min={4} max={60} step={1}
                    placeholder="自动 40" value={v.steps ?? ''}
                    onChange={(e) => patch({ steps: e.target.value ? Number(e.target.value) : null })} />
                </label>
                <label className="gen-field">
                  <span className="gen-field-label" title="留空 = 随机；同种子可复现">种子</span>
                  <input className="input" type="number" min={0} step={1}
                    placeholder="随机" value={v.seed ?? ''}
                    onChange={(e) => patch({ seed: e.target.value ? Number(e.target.value) : null })} />
                </label>
              </div>
              <label className="gen-field" style={{ marginTop: 6 }}>
                <span className="gen-field-label"
                  title="程序自动追加的约束（「去掉原来的东西、补成周围背景」那段）。留空=默认，非空整段替换">
                  约束条款（可改）
                </span>
                <textarea className="input ip-prompt-in" rows={3}
                  placeholder="留空 = 用程序默认的「去掉原物 + 补成周围背景」那段"
                  value={v.constraint}
                  onChange={(e) => patch({ constraint: e.target.value })} />
              </label>
              <label className="gen-field" style={{ marginTop: 6 }}>
                <span className="gen-field-label" title="留空=程序默认（残留轮廓/水印/文字/鬼影…）">
                  负面词（可改）
                </span>
                <input className="input" placeholder="留空 = 程序默认"
                  value={v.negative}
                  onChange={(e) => patch({ negative: e.target.value })} />
              </label>
              {/* 默认内容直接显示：留空的人要知道自己默认在挡什么 */}
              <div className="hint" style={{ marginTop: 2, lineHeight: 1.6 }}>
                {v.negative.trim() ? (
                  <>已固定成你写的这一段，不再用程序默认。</>
                ) : defaultNeg === null ? (
                  <>读取程序默认中…</>
                ) : defaultNeg ? (
                  <>留空 = 程序默认（{defaultNeg}）</>
                ) : (
                  <>留空 = 不带负面词。</>
                )}
              </div>
              <button className="btn btn-xs btn-ghost" style={{ marginTop: 6 }}
                onClick={() => patch({ constraint: '', negative: '' })}
                title="把约束条款和负面词恢复成程序默认值">恢复默认条款</button>
            </>
          ) : null}
        </AdvancedSection>

        <button className="btn btn-sm" onClick={onRun} disabled={!ready}
          style={{ marginTop: 8, width: '100%' }}
          title={!v.maskFile ? '先涂抹要去掉的东西'
            : maskStale ? '蒙版与源图尺寸不符：重新涂抹' : undefined}>
          {running ? <><span className="spin" /> {isAI ? '模型补全中…' : '处理中…'}</>
            : (isAI ? 'AI 补全' : '擦除并补上')}
        </button>
        {progress ? <div className="hint walk-progress">{progress}</div> : null}
        {error ? <div className="node-error">{error}</div> : null}

        {out ? (
          <>
            <div className="ip-compare">
              {upstream ? (
                <button className="ip-thumb" title="点开对比原图"
                  onClick={() => onPreview(out.url, '处理结果', upstream.url)}>
                  <img src={upstream.url} alt="原图" />
                  <span>原图</span>
                </button>
              ) : null}
              <button className="ip-thumb" title="点开看大图"
                onClick={() => onPreview(out.url, '处理结果', upstream?.url)}>
                <img src={out.url} alt="处理结果" />
                <span>已去除</span>
              </button>
            </div>
            <div className="result-meta" style={{ borderTop: 0, marginTop: 6 }}>
              <span className="meta-pill">{meta?.methodLabel || '扩散修复'}</span>
              {meta?.offset ? (
                <span className="meta-pill">平移 {meta.offset[0]},{meta.offset[1]}</span>
              ) : null}
              {meta ? <span className="meta-pill">
                {meta.ai?.targetSize
                  ? `送模型 ${meta.ai.targetSize[0]}×${meta.ai.targetSize[1]}`
                  : `${Math.round((meta.coverage || 0) * 1000) / 10}% 面积`}
              </span> : null}
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
            还没有处理结果。涂掉要去掉的东西，选一种补法后点上面的按钮。
          </div>
        )}
      </div>
    </div>
  )
}
