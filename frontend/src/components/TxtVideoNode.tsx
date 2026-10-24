import type { TxtVideoNode as TxtVideoNodeType } from '../types'

type Props = {
  node: TxtVideoNodeType
  index: number
  running?: boolean
  /** 后端推来的当前步骤文案（视频生成要跑几分钟，光转圈看不出进行到哪） */
  progress?: string | null
  /** 上游给了哪张图（连了就是图生视频，没连就是纯文本） */
  sourceLabel?: string | null
  onChange: (patch: Partial<TxtVideoNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const ENGINE_CN: Record<string, string> = {
  auto: '自动', wan: 'Wan2.2（文/图统一）', hy15: 'HunyuanVideo-1.5（仅图生）',
}

/**
 * 文本生视频节点：提示词 + **可选**参考图 → 一段视频（GIF）。
 *
 * 和行走视频节点的分工：行走视频必须有一张角色图（要从它推导八方向立绘），
 * 本节点没有图也能跑——走 Wan2.2 TI2V-5B 的纯文生视频路径（同一份权重的
 * 另一条管线类，显存互斥，见 provider._load_wan 的 t2v 分支）。
 * 连了上游图就自动切图生视频（首帧锚定），适合「先出立绘再让它动起来」。
 *
 * 提示词整段用户输入、程序不拼接（与行走节点同一约定）：程序拼的句子会在
 * 长文本被截断时丢掉关键条款，而截的永远是尾部。
 */
export default function TxtVideoNode({
  node, index, running, progress, sourceLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: TxtVideoNodeType['txtvideo'] = {
    enabled: true, prompt: '', negative: '',
    steps: null, seed: null, width: 832, height: 480,
    numFrames: 81, fps: 24, maxSide: 832, maxSeqLen: null,
    pixelArt: false, guidanceScale: null, engine: null,
  }
  const v = { ...DEFAULTS, ...node.txtvideo }
  const patch = (p: Partial<typeof v>) => onChange({ txtvideo: { ...v, ...p } })

  const meta = node.meta
  const hasRef = meta?.hasRef ?? (sourceLabel != null)
  const etaMin = Math.round((v.numFrames >= 61 ? 4 : 3) + (v.width * v.height > 400000 ? 1 : 0))

  return (
    <div className={`node txtvideo ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 420 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">TXV</span>
        <span className="node-title">文本生视频</span>
        <span className="node-idx">X{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port inl" title="点击完成连线（先点上游节点的输出端口）；不连也能跑"
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
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换文本生视频节点">
            <i />
          </button>
        </div>

        <div className="hint">
          参考图（可选）：{sourceLabel
            ? <b>{sourceLabel}</b>
            : '未连线——纯文本生成；连上游图则切图生视频（首帧锚定）'}
        </div>

        <label className="gen-field">
          <span className="gen-field-label">提示词（整段自己写，程序不拼接）</span>
          <textarea
            className="textarea"
            style={{ minHeight: 92 }}
            placeholder={'例：像素风小猫少女在草地上奔跑，阳光从左前方照过来，镜头缓慢跟随；'
              + '四肢持续交替迈步、身体随步伐上下颠动'}
            value={v.prompt}
            onChange={(e) => patch({ prompt: e.target.value })}
          />
        </label>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">宽度</span>
            <input className="input" type="number" min={128} max={1024} step={16}
              value={v.width} onChange={(e) => patch({ width: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">高度</span>
            <input className="input" type="number" min={128} max={1024} step={16}
              value={v.height} onChange={(e) => patch({ height: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="Wan 时序 VAE 要求 4k+1，内部会自动向下取整">帧数</span>
            <input className="input" type="number" min={5} max={193} step={4}
              value={v.numFrames} onChange={(e) => patch({ numFrames: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">帧率</span>
            <input className="input" type="number" min={8} max={30}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">步数（空=默认 30）</span>
            <input className="input" type="number" min={4} max={60}
              value={v.steps ?? ''} placeholder="30"
              onChange={(e) => patch({ steps: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">种子（空=随机）</span>
            <input className="input" type="number" min={0}
              value={v.seed ?? ''} placeholder="随机"
              onChange={(e) => patch({ seed: e.target.value ? Number(e.target.value) : null })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="CFG：默认 5.0。动作偏弱时上调加强运动，过头会出重影">引导强度</span>
            <input className="input" type="number" min={1} max={9} step={0.5}
              value={v.guidanceScale ?? ''} placeholder="5.0"
              onChange={(e) => patch({ guidanceScale: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="无参考图时只有 Wan2.2 支持纯文本；HunyuanVideo-1.5 只做了图生视频">引擎</span>
            <select className="input" value={v.engine ?? 'auto'}
              onChange={(e) => patch({ engine: e.target.value === 'auto' ? null : e.target.value })}>
              {Object.entries(ENGINE_CN).map(([id, cn]) => (
                <option key={id} value={id}>{cn}</option>
              ))}
            </select>
          </label>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="像素素材用最近邻缩放，别被视频底座糊掉；非像素素材别开">
            像素保真
          </span>
          <button className={`switch ${v.pixelArt ? 'on' : ''}`}
            onClick={() => patch({ pixelArt: !v.pixelArt })} aria-label="切换像素保真">
            <i />
          </button>
        </div>

        <label className="gen-field">
          <span className="gen-field-label">负面词（空=用全局默认）</span>
          <input className="input" value={v.negative} placeholder="模糊, 低质量, 水印…"
            onChange={(e) => patch({ negative: e.target.value })} />
        </label>

        <button className="btn btn-sm" onClick={onRun} disabled={running || !v.prompt.trim()}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 生成中…</>
            : `运行生成（约 ${etaMin} 分钟${hasRef ? '，图生视频' : '，纯文本'}）`}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill good">{meta.mode === 't2v' ? '纯文本' : '图生视频'}</span>
            <span className="meta-pill">{meta.frames ?? '?'} 帧</span>
            {meta.steps ? <span className="meta-pill">{meta.steps} 步</span> : null}
            {meta.vram?.peakGB ? <span className="meta-pill">峰值 {meta.vram.peakGB}GB</span> : null}
            {meta.seconds ? <span className="meta-pill">{Math.round(meta.seconds)}s</span> : null}
          </div>
        ) : null}

        {node.result ? (
          <div style={{ marginTop: 10 }}>
            <button className="matte-thumb wide" title="点击放大"
              onClick={() => onPreview(node.result!.url, '文本生视频')}>
              <img src={node.result.url} alt="文本生视频" />
            </button>
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有视频。写好提示词后点上面的按钮（可连一张参考图首帧锚定）。
          </div>
        )}
      </div>
    </div>
  )
}
