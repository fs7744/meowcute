import { useMemo } from 'react'
import AdvancedSection from './AdvancedSection'
import type { SpriteSplitNode as SpriteSplitNodeType } from '../types'

type Props = {
  node: SpriteSplitNodeType
  index: number
  running?: boolean
  progress?: string | null
  error?: string | null
  /** 上游「选择图片」传进来的文件名（没连线时提示用） */
  upstreamFile?: string | null
  onChange: (patch: Partial<SpriteSplitNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 拆解精灵图节点（动作复刻链第 1 步）：一张序列帧精灵图 → 一摞可勾选的帧。
 *
 * 和「拆解帧（视频）」的区别：那边要处理「视频帧太多」，靠跳帧步长 + 近似度两把
 * 筛子；这边帧数由网格定死（4×4 就只有 16 帧），真正的问题是**网格认错**——
 * 认成 8×2 就会把每个角色切成上下两半。所以这个节点的重点是：
 *   ① 把识别结论直接摊在界面上（几列几行、怎么认出来的）
 *   ② 允许一键改成手动网格，改完重拆
 *   ③ 拆错了能一眼看出来（缩略图 + 空格提示）
 *
 * 拆出来的帧**保留格子原尺寸**，不裁到内容包围盒：那样会把「帧与帧之间的
 * 相对位置」提前扔掉，下游统一落格也补不回来（上一轮抖动排查的结论）。
 */
export default function SpriteSplitNode({
  node, index, running, progress, error, upstreamFile,
  onChange, onDelete, onDragStart, onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: SpriteSplitNodeType['spritesplit'] = {
    enabled: true, cols: 0, rows: 0, cell: 0, count: 0,
    matte: false, tolerance: 40, picks: [],
  }
  const v = { ...DEFAULTS, ...node.spritesplit }
  const patch = (p: Partial<typeof v>) => onChange({ spritesplit: { ...v, ...p } })

  const frames = useMemo(() => node.frames ?? [], [node.frames])
  const meta = node.meta
  const picked = useMemo(() => new Set(v.picks ?? []), [v.picks])
  const pickedInOrder = useMemo(
    () => frames.filter((f) => picked.has(f.file)), [frames, picked])

  const toggle = (file: string) => {
    const cur = v.picks ?? []
    patch({ picks: cur.includes(file) ? cur.filter((x) => x !== file) : [...cur, file] })
  }
  const selectAll = () => patch({ picks: frames.map((f) => f.file) })
  const clearAll = () => patch({ picks: [] })
  const invert = () => {
    const cur = new Set(v.picks ?? [])
    patch({ picks: frames.filter((f) => !cur.has(f.file)).map((f) => f.file) })
  }

  const noSource = !upstreamFile && !node.source?.file
  const methodText: Record<string, string> = {
    auto: '自动识别',
    manual: '手动指定',
    cell: '按边长推算',
    single: '只有一帧',
    fallback: '未能识别',
    empty: '空图',
  }

  return (
    <div className={`node spritesplit ${v.enabled ? '' : 'off'} ${running ? 'busy' : ''}`}
      style={{ left: node.x, top: node.y, width: 460 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">CUT</span>
        <span className="node-title">拆解精灵图</span>
        <span className="node-idx">S{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游「选择图片」的输出端口）"
          onMouseDown={(e) => e.stopPropagation()}
          onClick={(e) => { e.stopPropagation(); onFinishLink(node.id) }} />
        <div className="port out" title="按住拖到「动作复刻」节点"
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
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换拆解精灵图节点">
            <i />
          </button>
        </div>

        <div className="hint">
          上游：{upstreamFile
            ? <b>{upstreamFile}</b>
            : (node.source?.file ? <b>{node.source.file}</b>
              : '未连线（把「选择图片」上传精灵图后连到左侧端口）')}
        </div>

        {/* 网格：0 = 自动。三个参数只要填了任何一个，就以填的为准 */}
        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label"
              title="列数。0 = 自动识别。识别错了就手动填：4×4 的精灵图填 4 和 4">列数</span>
            <input className="input" type="number" min={0} max={64} step={1}
              value={v.cols} onChange={(e) => patch({ cols: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="行数。0 = 自动识别">行数</span>
            <input className="input" type="number" min={0} max={64} step={1}
              value={v.rows} onChange={(e) => patch({ rows: Number(e.target.value) })} />
          </label>
        </div>

        <AdvancedSection title="更多设置" hint="单元格边长 · 最多帧数 · 去底容差">
          <div className="param-grid">
            <label className="gen-field">
              <span className="gen-field-label"
                title="单元格边长。填了就不猜行列数了（64 / 128 / 256 常见）。0 = 自动">单元格边长</span>
              <input className="input" type="number" min={0} max={1024} step={8}
                value={v.cell} onChange={(e) => patch({ cell: Number(e.target.value) })} />
            </label>
            <label className="gen-field">
              <span className="gen-field-label"
                title="只拆前 N 格。0 = 全部格子都拆">最多帧数</span>
              <input className="input" type="number" min={0} max={256} step={1}
                value={v.count} onChange={(e) => patch({ count: Number(e.target.value) })} />
            </label>
          </div>
          <label className="gen-field" style={{ marginTop: 6 }}>
            <span className="gen-field-label"
              title="逐帧抠图容差：底色与角色太接近时调大，误吃角色时调小">去底容差</span>
            <input className="input" type="number" min={10} max={120} step={5}
              value={v.tolerance}
              onChange={(e) => patch({ tolerance: Number(e.target.value) })} />
          </label>
        </AdvancedSection>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="精灵图是白底/纯色底而不是透明底时打开：逐帧抠掉背景。透明底的精灵图不用开（开了只是白等几百毫秒/帧）。">
            逐帧去底（白底精灵图才需要）
          </span>
          <button className={`switch ${v.matte ? 'on' : ''}`}
            onClick={() => patch({ matte: !v.matte })} aria-label="切换逐帧去底">
            <i />
          </button>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running || noSource}
          style={{ marginTop: 8, width: '100%' }}>
          {running
            ? <><span className="spin" /> 拆解中…{v.matte ? '（去底较慢）' : ''}</>
            : (frames.length ? '重新拆解' : '拆解精灵图')}
        </button>

        {running ? (
          <div className="txtv-progress">
            {progress ? <div className="hint walk-progress">{progress}</div> : null}
            <div className="txtv-progress-bar indet"><i /></div>
          </div>
        ) : null}
        {error ? <div className="node-error">{error}</div> : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">
              {meta.cols} × {meta.rows} 格
            </span>
            <span className="meta-pill">每格 {meta.cellW}×{meta.cellH}</span>
            <span className="meta-pill">{methodText[meta.gridMethod] ?? meta.gridMethod}</span>
            <span className="meta-pill">{meta.seconds}s</span>
          </div>
        ) : null}

        {meta?.warnings?.length ? (
          <div className="hint warn" style={{ marginTop: 4, lineHeight: 1.6 }}>
            {meta.warnings.slice(0, 3).map((w) => <div key={w}>· {w}</div>)}
          </div>
        ) : null}

        {frames.length ? (
          <>
            <div className="fs-toolbar">
              <button className="btn btn-xs" onClick={selectAll}>全选（{frames.length}）</button>
              <button className="btn btn-xs btn-ghost" onClick={invert}>反选</button>
              <button className="btn btn-xs btn-ghost" onClick={clearAll}>清空</button>
            </div>
            <div className="fs-count">
              已选 <b>{pickedInOrder.length}</b> / 共 {frames.length} 帧
              {meta ? (
                <span className="fs-stat" title="相邻帧差异中位数">
                  间距中位 {meta.diffMedian}%
                </span>
              ) : null}
            </div>
            <div className="fs-wall">
              {frames.map((f) => {
                const on = picked.has(f.file)
                return (
                  <button key={f.file}
                    className={`fs-cell ${on ? 'on' : ''}`}
                    title={`第 ${f.i + 1} 帧（第 ${Math.floor(f.i / (meta?.cols || 1)) + 1} 行`
                      + ` 第 ${(f.i % (meta?.cols || 1)) + 1} 列）`
                      + `\n角色占格 ${((f as any).alphaRatio ? (f as any).alphaRatio * 100 : 0).toFixed(1)}%`
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
            <div className="hint" style={{ marginTop: 6 }}>
              拆出来的帧勾选后会送进「动作复刻」：<b>勾几帧就复刻几帧</b>，
              帧数越多越慢（每帧一次推理）。
            </div>
          </>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            {noSource
              ? '先上传精灵图：点「选择图片」卡片传一张序列帧精灵图，再连到本节点。'
              : '还没有帧。点上面的「拆解精灵图」。'}
          </div>
        )}
      </div>
    </div>
  )
}
