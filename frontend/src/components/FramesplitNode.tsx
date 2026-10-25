import { useMemo, useState } from 'react'
import { applyFilters, diffStats, loopSeam } from '../lib/frameSelect'
import AdvancedSection from './AdvancedSection'
import type { FrameSplitNode as FrameSplitNodeType } from '../types'

type Props = {
  node: FrameSplitNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  /** 上游「选择视频」给了哪个文件（没连线时提示用） */
  upstreamFile?: string | null
  onChange: (patch: Partial<FrameSplitNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 拆解帧节点（素材链第 2 步）：一段视频 → 一摞可勾选的帧。
 *
 * 这个节点的核心不是「拆」而是「筛」：200 帧里真正有用的可能只有 12 帧，
 * 让用户从 200 张缩略图里手点 12 张是不现实的。所以给两把筛子——
 *
 * - **跳帧步长**：每 N 帧取 1 帧。适合匀速动作，一眼压掉 3/4。
 * - **近似度过滤**：和「上一张保留帧」比，差异低于阈值就丢掉。适合视频里有
 *   起步/停步/定格段落的情况（步长处理不了这种不均匀的重复）。
 *
 * 两把筛子都是**即时生效**的（指纹在前端，见 lib/frameSelect.ts），但**不会
 * 自动改用户已经勾好的帧**——筛选只改变「显示哪些」，勾选是显式状态。改完
 * 滑杆点「应用筛选结果」才算采纳。这样不会出现「调了下滑杆，辛苦挑的 12 帧
 * 被悄悄换掉」。
 */
export default function FramesplitNode({
  node, index, running, progress, error, upstreamFile,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: FrameSplitNodeType['framesplit'] = {
    enabled: true, fpsCap: 12, maxFrames: 120, start: 0, end: null,
    matte: true, tolerance: 40, maxSide: 384, step: 1, simPct: 0, picks: [],
  }
  const v = { ...DEFAULTS, ...node.framesplit }
  const patch = (p: Partial<typeof v>) => onChange({ framesplit: { ...v, ...p } })

  const frames = useMemo(() => node.frames ?? [], [node.frames])
  const meta = node.meta
  const [showAll, setShowAll] = useState(false)

  const filtered = useMemo(
    () => applyFilters(frames, { step: v.step, simPct: v.simPct }),
    [frames, v.step, v.simPct],
  )
  const shown = showAll ? frames : filtered

  const picked = useMemo(() => new Set(v.picks ?? []), [v.picks])
  /** 按帧序排列的已选帧（生成精灵图要的是稳定顺序，不按点击顺序） */
  const pickedInOrder = useMemo(
    () => frames.filter((f) => picked.has(f.file)),
    [frames, picked],
  )
  const stats = useMemo(() => diffStats(pickedInOrder), [pickedInOrder])
  const seam = useMemo(() => loopSeam(pickedInOrder), [pickedInOrder])

  const toggle = (file: string) => {
    const cur = v.picks ?? []
    patch({ picks: cur.includes(file) ? cur.filter((x) => x !== file) : [...cur, file] })
  }

  const applyFiltered = () => patch({ picks: filtered.map((f) => f.file) })
  const clearAll = () => patch({ picks: [] })
  const invert = () => {
    const cur = new Set(v.picks ?? [])
    patch({ picks: frames.filter((f) => !cur.has(f.file)).map((f) => f.file) })
  }

  const src = node.source
  const noSource = !upstreamFile && !src?.file

  return (
    <div className={`node framesplit ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 460 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">SPLIT</span>
        <span className="node-title">拆解帧 · 挑帧</span>
        <span className="node-idx">F{index + 1}</span>
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
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换拆帧节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamFile
            ? <b>{upstreamFile}</b>
            : (src?.file ? <b>{src.file}</b>
              : '未连线（把「选择视频」连到左侧端口）')}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="抽稀后的帧率：24fps 的视频设 12 就是每 2 帧取 1">帧率上限</span>
            <input className="input" type="number" min={1} max={60} step={1}
              value={v.fpsCap}
              onChange={(e) => patch({ fpsCap: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="单次最多拆多少帧：帧数越多抠图越久">最多帧数</span>
            <input className="input" type="number" min={2} max={600} step={1}
              value={v.maxFrames}
              onChange={(e) => patch({ maxFrames: Number(e.target.value) })} />
          </label>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}>逐帧抠图（透明底）</span>
          <button className={`switch ${v.matte ? 'on' : ''}`}
            onClick={() => patch({ matte: !v.matte })} aria-label="切换逐帧抠图">
            <i />
          </button>
        </div>

        <AdvancedSection title="更多设置"
          hint="起止时间 · 抠图容差 · 解码长边">
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label">起始（秒）</span>
              <input className="input" type="number" min={0} step={0.1}
                value={v.start}
                onChange={(e) => patch({ start: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label">结束（秒，空=到结尾）</span>
              <input className="input" type="number" min={0} step={0.1}
                value={v.end ?? ''}
                onChange={(e) => patch({ end: e.target.value === '' ? null : Number(e.target.value) })} />
            </label>
          </div>
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="抠图容差：底色与主体太接近时调大，误吃主体时调小">抠图容差</span>
              <input className="input" type="number" min={10} max={120} step={5}
                value={v.tolerance}
                onChange={(e) => patch({ tolerance: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="解码时长边上限：精灵单元格一般 64~256，没必要按原始 1080p 解码">解码长边</span>
              <input className="input" type="number" min={64} max={1024} step={64}
                value={v.maxSide}
                onChange={(e) => patch({ maxSide: Number(e.target.value) })} />
            </label>
          </div>
        </AdvancedSection>

        <button className="btn btn-sm" onClick={onRun} disabled={running || noSource}
          style={{ marginTop: 8, width: '100%' }}>
          {running
            ? <><span className="spin" /> 拆帧中…（抠图较慢，可取消）</>
            : (frames.length ? '重新拆解' : '拆解帧')}
        </button>

        {running ? (
          <div className="txtv-progress">
            {progress ? <div className="hint walk-progress">{progress}</div> : null}
            <div className="txtv-progress-bar indet"><i /></div>
          </div>
        ) : null}
        {error ? <div className="node-error">{error}</div> : null}

        {frames.length ? (
          <>
            <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
              <span className="meta-pill">拆出 {frames.length} 帧</span>
              {meta ? <span className="meta-pill">{meta.effFps}fps</span> : null}
              {meta?.thinned ? <span className="meta-pill">已按上限抽稀</span> : null}
              {meta?.matteErrors ? (
                <span className="meta-pill bad" title="这些帧抠图失败，已退回未抠图版本">
                  抠图失败 {meta.matteErrors}
                </span>
              ) : null}
              {meta ? <span className="meta-pill">{meta.seconds}s</span> : null}
            </div>

            {/* ---- 两道筛子：即时生效，但只改「显示」，不动已勾选 ---- */}
            <div className="fs-filters">
              <label className="fs-slider">
                <span>跳帧步长 <b>每 {v.step} 帧取 1</b></span>
                <input type="range" min={1} max={10} step={1} value={v.step}
                  onChange={(e) => patch({ step: Number(e.target.value) })} />
              </label>
              <label className="fs-slider">
                <span>
                  近似度过滤 <b>{v.simPct.toFixed(1)}%</b>
                  <em title="与上一张保留帧的差异低于这个值就当作重复帧">
                    低于此视为重复
                  </em>
                </span>
                <input type="range" min={0} max={20} step={0.5} value={v.simPct}
                  onChange={(e) => patch({ simPct: Number(e.target.value) })} />
              </label>
            </div>

            <div className="fs-toolbar">
              <button className="btn btn-xs" onClick={applyFiltered}
                title="把当前筛选结果设为已选">应用筛选（{filtered.length}）</button>
              <button className="btn btn-xs btn-ghost" onClick={invert}>反选</button>
              <button className="btn btn-xs btn-ghost" onClick={clearAll}>清空</button>
              <label className="fs-showall" title="显示被筛掉的帧（灰显）">
                <input type="checkbox" checked={showAll}
                  onChange={(e) => setShowAll(e.target.checked)} />
                显示全部
              </label>
            </div>

            <div className="fs-count">
              已选 <b>{pickedInOrder.length}</b> / 共 {frames.length} 帧
              {pickedInOrder.length >= 2 ? (
                <>
                  <span className="fs-stat" title="相邻已选帧的差异中位数：太低说明还有重复姿势">
                    间距中位 {stats.median.toFixed(1)}%
                  </span>
                  <span className={`fs-stat ${seam < 1.5 ? 'warn' : ''}`}
                    title="末帧与首帧的差异：太小循环会卡一下（末帧≈首帧）">
                    首尾差 {seam.toFixed(1)}%
                  </span>
                  {stats.nearDup ? (
                    <span className="fs-stat warn" title="相邻两帧几乎一样，循环里会停一拍">
                      近重复 {stats.nearDup} 对
                    </span>
                  ) : null}
                </>
              ) : null}
            </div>

            <div className="fs-wall">
              {shown.map((f) => {
                const on = picked.has(f.file)
                return (
                  <button key={f.file}
                    className={`fs-cell ${on ? 'on' : ''}`}
                    title={`第 ${f.i + 1} 帧 · ${f.t.toFixed(2)}s · 与前帧差 ${f.diff.toFixed(1)}%`
                      + `\n点击${on ? '取消选择' : '选中'}；双击放大`}
                    onClick={() => toggle(f.file)}
                    onDoubleClick={() => onPreview(f.url, `第 ${f.i + 1} 帧`)}>
                    <img src={f.thumbUrl} alt={`帧 ${f.i + 1}`} loading="lazy" />
                    <span className="fs-idx">{f.i + 1}</span>
                    {on ? <span className="fs-order">
                      {pickedInOrder.findIndex((x) => x.file === f.file) + 1}
                    </span> : null}
                  </button>
                )
              })}
            </div>
            {!shown.length ? (
              <div className="hint" style={{ marginTop: 6 }}>
                当前筛选把全部帧都滤掉了：把「近似度过滤」调低，或打开「显示全部」手动挑。
              </div>
            ) : null}
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            {noSource
              ? '先连一段视频：左侧端口接「选择视频 / GIF」。'
              : '还没有帧。点上面的「拆解帧」。'}
          </div>
        )}
      </div>
    </div>
  )
}
