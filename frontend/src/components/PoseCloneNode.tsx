import { useMemo, useState } from 'react'
import AdvancedSection from './AdvancedSection'
import {
  POSECLONE_DEFAULT_INSTRUCTION, POSECLONE_DEFAULT_NEGATIVE,
} from '../lib/posecloneDefaults'
import type { PoseCloneNode as PoseCloneNodeType } from '../types'

type Props = {
  node: PoseCloneNodeType
  index: number
  running?: boolean
  progress?: string | null
  /** 已跑完并落盘的帧（后端边跑边推），跑的过程中就能看到 */
  partial?: any[] | null
  error?: string | null
  /** 上游「拆解精灵图」勾了几帧（没连线时提示用） */
  upstreamPicked?: number | null
  /** 角色参考图（上游「选择图片」）张数 */
  charRefCount?: number
  /** 上游勾了帧、但这里还没有产出的帧数（上次中途失败/取消留下的缺口） */
  missingCount?: number
  /** 重跑：传 'missing' = 补跑缺失帧；传数组 = 只重跑这几个源帧下标 */
  onRerun?: (only: number[] | 'missing') => void
  onChange: (patch: Partial<PoseCloneNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 动作复刻节点（动作复刻链第 2 步）：动作帧 + 角色四视图 → 换成自己角色的一模一样的动作。
 *
 * 用的是 Qwen-Image-Edit 的多图合成：每次喂两张（动作帧 + 角色设定板），
 * 按「图1 的动作 / 图2 的角色」编号引用。业界建议 1~3 张参考图，所以界面上
 * 推荐传一张拼好的四视图，而不是四张分开的图。
 *
 * 节点上暴露的三件事，都是踩过坑的：
 * ① **指令可编辑**（含默认值一键恢复）：成败几乎全在提示词
 * ② **剪影重合度**（每帧一个 IoU）：客观告诉你哪一帧动作被模型改写了
 * ③ **整图一次改只作草稿**：20 秒能看角色像不像，但它会重排网格，不能出最终素材
 */
export default function PoseCloneNode({
  node, index, running, progress, partial, error, upstreamPicked, charRefCount,
  missingCount, onRerun,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: PoseCloneNodeType['poseclone'] = {
    enabled: true, mode: 'per-frame', instruction: '', negative: '',
    steps: 0, size: 0, background: 'transparent', anchor: 'bottom',
    refMode: 'auto', seed: null, maxSeqLen: null,
  }
  const v = { ...DEFAULTS, ...node.poseclone }
  const patch = (p: Partial<typeof v>) => onChange({ poseclone: { ...v, ...p } })

  const frames = useMemo(() => node.frames ?? [], [node.frames])
  const meta = node.meta
  const [showInstruction, setShowInstruction] = useState(false)
  // 被点选为「待重跑」的源帧下标
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const togglePick = (i: number) => setPicked((s) => {
    const n = new Set(s); n.has(i) ? n.delete(i) : n.add(i); return n
  })
  const custom = (v.instruction || '').trim()
  const effective = custom || POSECLONE_DEFAULT_INSTRUCTION

  const hasFrames = v.mode === 'whole-sheet'
    ? true
    : (upstreamPicked ?? 0) > 0
  const noRefs = (charRefCount ?? 0) === 0

  return (
    <div className={`node poseclone ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 470 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">CLONE</span>
        <span className="node-title">动作复刻</span>
        <span className="node-idx">C{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游「拆解精灵图」的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink(node.id) }} />
        <div className="port out" title="按住拖到「精灵图」节点"
          onMouseDown={(e) => {
            e.stopPropagation()
            onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })
          }} />
      </div>

      <div className="node-body">
        <div className="switch-row">
          <span className="t" style={{ fontSize: 12 }}>
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游动作帧）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换动作复刻节点">
            <i />
          </button>
        </div>

        <div className="hint">
          动作帧：{v.mode === 'whole-sheet'
            ? '整张精灵图（草稿模式）'
            : (upstreamPicked && upstreamPicked > 0
              ? <b>已勾选 {upstreamPicked} 帧</b>
              : '未连线（把「拆解精灵图」连到左侧端口）')}
          <br />
          角色参考：{noRefs
            ? <span className="warn">缺四视图（把「选择图片」上传四视图后连到左侧端口）</span>
            : <b>{charRefCount} 张</b>}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label"
              title="逐帧复刻：每帧一次推理，动作最稳、可单帧重跑，12 帧约 3~4 分钟。整图一次改：20 秒出草稿，但模型会重排网格，只能看角色像不像。">
              复刻方式
            </span>
            <select className="input" value={v.mode}
              onChange={(e) => patch({ mode: e.target.value as typeof v.mode })}>
              <option value="per-frame">逐帧复刻（推荐）</option>
              <option value="whole-sheet">整图一次改（草稿）</option>
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label"
              title="参考图版面：长条形的四视图板会被重排成 2×2，让参考图的有效像素翻几倍（同样 256×256 的预算里，每个视角从 64px 变 128px）">
              参考图版面
            </span>
            <select className="input" value={v.refMode}
              onChange={(e) => patch({ refMode: e.target.value as typeof v.refMode })}>
              <option value="auto">自动重排（推荐）</option>
              <option value="as-is">原样使用</option>
            </select>
          </label>
        </div>

        {/* ---- 指令：默认值可见、可改、可恢复 ---- */}
        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="这条指令决定动作保真度和角色一致性，可以直接改。留空 = 用默认值。">
            复刻指令{custom ? '（已自定义）' : '（默认）'}
          </span>
          <button className="btn btn-xs btn-ghost"
            onClick={() => setShowInstruction((s) => !s)}>
            {showInstruction ? '收起' : '查看 / 编辑'}
          </button>
        </div>
        {showInstruction ? (
          <>
            <textarea className="input" rows={8} style={{ marginTop: 6, width: '100%', fontSize: 12 }}
              value={effective}
              onChange={(e) => patch({ instruction: e.target.value })} />
            <div className="fs-toolbar" style={{ marginTop: 4 }}>
              <button className="btn btn-xs btn-ghost"
                onClick={() => patch({ instruction: '' })}>恢复默认</button>
              <span className="fs-stat">
                占位符：{'{帧号} {总帧数} {背景} {动作}'}
              </span>
            </div>
          </>
        ) : null}

        <AdvancedSection title="更多设置" hint="步数 · 输出尺寸 · 对齐 · 背景 · 负面词">
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label" title="0 = 默认 40 步。步数越低越快，细节越少">步数</span>
              <input className="input" type="number" min={0} max={50} step={2}
                value={v.steps} onChange={(e) => patch({ steps: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="0 = 跟随源帧尺寸（推荐，落格尺寸才对得上）">输出边长</span>
              <input className="input" type="number" min={0} max={1536} step={16}
                value={v.size} onChange={(e) => patch({ size: Number(e.target.value) })} />
            </label>
          </div>
          <div className="param-grid" style={{ marginTop: 6 }}>
            <label className="gen-field">
              <span className="gen-field-label"
                title="对齐锚点：复刻完会把生成帧整体平移回源帧的落脚点。bottom=脚底边（推荐），center=包围盒中心。">对齐锚点</span>
              <select className="input" value={v.anchor}
                onChange={(e) => patch({ anchor: e.target.value as typeof v.anchor })}>
                <option value="bottom">脚底（推荐）</option>
                <option value="center">包围盒中心</option>
              </select>
            </label>
            <label className="gen-field">
              <span className="gen-field-label" title="输出背景：透明底最省事，直接进精灵图">背景</span>
              <select className="input" value={v.background}
                onChange={(e) => patch({ background: e.target.value as typeof v.background })}>
                <option value="transparent">透明</option>
                <option value="white">纯白</option>
                <option value="gray">中灰</option>
                <option value="black">纯黑</option>
              </select>
            </label>
          </div>
          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="留空 = 用默认负面词（多角色/四视图/网格线/动作改变…）">负面词</span>
            <input className="input" type="text" placeholder={POSECLONE_DEFAULT_NEGATIVE}
              value={v.negative}
              onChange={(e) => patch({ negative: e.target.value })} />
          </label>
        </AdvancedSection>

        <button className="btn btn-sm" onClick={onRun}
          disabled={running || !hasFrames || noRefs}
          style={{ marginTop: 8, width: '100%' }}>
          {running
            ? <><span className="spin" /> 复刻中…（每帧一次推理，可取消）</>
            : (frames.length ? '重新复刻' : '开始复刻动作')}
        </button>

        {running ? (
          <div className="txtv-progress">
            {progress ? <div className="hint walk-progress">{progress}</div> : null}
            <div className="txtv-progress-bar indet"><i /></div>
            {/* 已跑完的帧：后端每帧落盘就推一次，不用干等整批结束。
                中途取消/失败时这些帧也已经存盘了，不会白跑。 */}
            {partial && partial.length ? (
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 6 }}>
                {partial.map((f: any) => (
                  <img key={f.file} src={f.thumbUrl || f.url}
                    className="frame-thumb" alt={`第 ${(f.i ?? 0) + 1} 帧`}
                    title={`第 ${(f.i ?? 0) + 1} 帧 · 动作重合 ${Math.round((f.iou ?? 0) * 100)}%`}
                    onClick={() => onPreview?.(f.url, `第 ${(f.i ?? 0) + 1} 帧`)}
                    style={{ width: 44, height: 44, objectFit: 'contain',
                      background: '#1b1d23', borderRadius: 3, cursor: 'pointer' }} />
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {error ? <div className="node-error">{error}</div> : null}

        {meta ? (
          <>
            <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
              <span className="meta-pill">复刻 {meta.count} 帧</span>
              {meta.failed ? (
                <span className="meta-pill bad">失败 {meta.failed}</span>
              ) : null}
              {meta.iouMean != null ? (
                <span className={`meta-pill ${meta.iouMean < 0.7 ? 'bad' : ''}`}
                  title="与源帧的剪影重合度：越高说明动作姿势保真越好">
                  动作重合 {(meta.iouMean * 100).toFixed(0)}%
                </span>
              ) : null}
              {meta.refPack && meta.refPack !== 'as-is' ? (
                <span className="meta-pill" title="参考板已重排：同尺寸下有效像素更多">
                  板 {meta.refPack}
                </span>
              ) : null}
              {meta.gridKept === false ? (
                <span className="meta-pill bad" title="模型改了网格，这张不能当最终素材">
                  网格已变
                </span>
              ) : null}
              {/* 结构校验三项：只看「动作重合度」是看不出来的——
                  裁掉头顶 3px 的帧，剪影重合可能还有 0.95。
                  这三项是确定性测量，拼成精灵图后才会暴露的问题在这里提前报。 */}
              {meta.clipped ? (
                <span className="meta-pill bad"
                  title="这些帧的角色贴到了画布边、超出部分已被裁掉：精灵图里会看到头顶或手脚被切平。建议重跑这几帧，或换留白更多的动作素材">
                  {meta.clipped} 帧被裁边
                </span>
              ) : null}
              {meta.cornersDirty ? (
                <span className="meta-pill bad"
                  title="这些帧四角不透明 = 背景没抠干净，拼图后会留下实底色块">
                  {meta.cornersDirty} 帧背景残留
                </span>
              ) : null}
              {(meta.baselineStable === false && (meta.baselineRows?.length ?? 0) > 1) ? (
                <span className="meta-pill bad"
                  title={`脚底线分别在第 ${meta.baselineRows!.join(' / ')} 行：逐帧对齐有 1~2px 取整误差，循环播放时角色会轻微上下跳`}>
                  脚底线不齐
                </span>
              ) : null}
              {meta.autoMatted ? (
                <span className="meta-pill"
                  title="这些帧模型没给出透明背景，已由程序自动抠掉">
                  {meta.autoMatted} 帧自动抠底
                </span>
              ) : null}
              {meta.retried ? (
                <span className="meta-pill"
                  title="这些帧第一次生成时动作跑飞了（重合度<60%），已换种子自动重跑">
                  {meta.retried} 帧重跑过
                </span>
              ) : null}
              <span className="meta-pill">{meta.seconds}s</span>
            </div>
            {meta.warnings?.length ? (
              <div className="hint warn" style={{ marginTop: 4, lineHeight: 1.6 }}>
                {meta.warnings.slice(0, 3).map((w) => <div key={w}>· {w}</div>)}
              </div>
            ) : null}
          </>
        ) : null}

        {frames.length ? (
          <>
            <div className="fs-count" style={{ marginTop: 6 }}>
              复刻结果：
              {frames.map((f: any) => (
                f.iou != null && f.iou < 0.7 ? (
                  <span key={f.file} className="fs-stat warn"
                    title={`第 ${f.i + 1} 帧动作可能被改写（重合 ${(f.iou * 100).toFixed(0)}%）`}>
                    第 {f.i + 1} 帧 {Math.round(f.iou * 100)}%
                  </span>
                ) : null
              ))}
              {frames.every((f: any) => f.iou == null || f.iou >= 0.7) ? (
                <span className="fs-stat">每帧动作都保住了</span>
              ) : null}
            </div>
            <div className="fs-wall">
              {frames.map((f) => (
                <button key={f.file}
                  className={`fs-cell ${picked.has(f.srcIndex ?? f.i) ? '' : 'on'}`}
                  title={`第 ${f.i + 1} 帧`
                    + (f.iou != null ? ` · 动作重合 ${(f.iou * 100).toFixed(0)}%` : '')
                    + '\n单击 = 选为待重跑；双击 = 放大'}
                  onClick={() => togglePick(f.srcIndex ?? f.i)}
                  onDoubleClick={() => onPreview(f.url, `复刻第 ${f.i + 1} 帧`)}>
                  <img src={f.thumbUrl} alt={`复刻帧 ${f.i + 1}`} loading="lazy" />
                  <span className="fs-idx">{f.i + 1}</span>
                </button>
              ))}
            </div>

            {/* 补跑 / 重跑：整批重跑一次要几分钟，某一帧跑飞了不该连累其余帧 */}
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6,
              alignItems: 'center' }}>
              {missingCount ? (
                <button className="btn-mini primary" disabled={running}
                  title={`上游勾了 ${upstreamPicked ?? 0} 帧、这里只有 ${frames.length} 帧，`
                    + `缺 ${missingCount} 帧（多半是上次中途失败/取消）。只补跑这几帧。`}
                  onClick={() => onRerun?.('missing')}>
                  补跑缺失的 {missingCount} 帧
                </button>
              ) : null}
              <button className="btn-mini" disabled={running || picked.size === 0}
                title="只重跑你点选的这几帧（换种子再来一次），其余帧原样保留"
                onClick={() => onRerun?.([...picked])}>
                {picked.size ? `重跑选中的 ${picked.size} 帧` : '重跑选中帧'}
              </button>
              {picked.size ? (
                <button className="btn-mini" onClick={() => setPicked(new Set())}
                  title="清空选择">清空</button>
              ) : null}
            </div>
            <div className="hint" style={{ marginTop: 4 }}>
              点帧可以选为待重跑（换种子再跑一次），其余帧不会被动到。
              下游接「精灵图」：会把上面这些帧按网格拼成新精灵图，再接「动画预览」看循环。
            </div>
          </>
        ) : null}
      </div>
    </div>
  )
}
