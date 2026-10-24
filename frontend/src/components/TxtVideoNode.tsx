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
  /** 上一次运行的错误（节点上要留着，不能只闪一个 alert 就没了） */
  error?: string | null
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const ENGINE_CN: Record<string, string> = {
  auto: '自动（默认 Hunyuan）', wan: 'Wan2.2（文/图统一）', hy15: 'HunyuanVideo-1.5',
}

/**
 * 文本生视频节点：提示词 + **可选**参考图 → 一段视频（GIF）。
 *
 * 和行走视频节点的分工：行走视频必须有一张角色图（要从它推导八方向立绘），
 * 本节点没有图也能跑——纯文生视频两个引擎都支持（2026-10-24 起 hy15 也有
 * T2V 管线 HunyuanVideo15Pipeline，不需要 SigLIP；Wan2.2 TI2V-5B 一直是
 * 文/图统一权重）。连了上游图就自动切图生视频（首帧锚定），适合
 * 「先出立绘再让它动起来」。
 *
 * 提示词整段用户输入、程序不拼接（与行走节点同一约定）：程序拼的句子会在
 * 长文本被截断时丢掉关键条款，而截的永远是尾部。
 */
export default function TxtVideoNode({
  node, index, running, progress, sourceLabel, onChange, onDelete, onDragStart,
  onRun, error, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: TxtVideoNodeType['txtvideo'] = {
    enabled: true, prompt: '', negative: '',
    quality: 'standard', resolution: '480p',
    steps: null, seed: null, width: 848, height: 480,
    numFrames: 21, fps: 12, maxSide: 848, maxSeqLen: null,
    pixelArt: false, guidanceScale: null,
    interpolate: false, upscale: false, engine: null, format: 'mp4',
  }
  const v = { ...DEFAULTS, ...node.txtvideo }
  const patch = (p: Partial<typeof v>) => onChange({ txtvideo: { ...v, ...p } })

  // 画质档位 → 步数/CFG 映射（按引擎）。hy15 是 step-distilled：官方 8-12 步、
  // CFG 1.0（无 CFG 单次前向）；Wan 非蒸馏：30 步 / CFG 5.0 是实测甜点。
  // 这些数字是踩坑换来的默认值，不是让用户从零手拧——选档即填入，仍可手改。
  const QUALITY_PRESETS = {
    hy15: {
      draft: { steps: 8, cfg: 1 },
      standard: { steps: 12, cfg: 1 },
      fine: { steps: 20, cfg: 1 },
    },
    wan: {
      draft: { steps: 20, cfg: 3 },
      standard: { steps: 30, cfg: 5 },
      fine: { steps: 40, cfg: 6 },
    },
  } as const
  const RES_PRESETS = {
    '480p': { w: 848, h: 480, note: 'hy15 原生训练分辨率；Wan 也快' },
    '720p': { w: 1216, h: 688, note: 'Wan 原生级；hy15 是 480p 蒸馏模型，超训练分辨率细节不升反降，建议切 Wan' },
  } as const
  const QUALITY_CN = { draft: '草稿', standard: '标准', fine: '精细' } as const

  /** 当前实际会用的引擎（自动 → hy15，权重不全才回落 wan；映射按 hy15 表给，
   *  真回落 wan 时 _run_wan 会把过低步数夹到 20，不会更差）。 */
  const effEngine = v.engine === 'wan' || v.engine === 'hy15' ? v.engine : 'hy15'
  const q = QUALITY_PRESETS[effEngine][v.quality]

  // 帧数硬上限由模型定：hy15 是 480p 蒸馏模型，序列长度只到 33 帧；Wan 到 193。
  // 超了后端会静默夹取，所以界面上必须提前说清楚「最长多少秒」。
  const FRAME_CAP = { hy15: 33, wan: 193 } as const
  const frameCap = FRAME_CAP[effEngine]
  // 时长与帧数是同一个量的两种表达：时长 = 帧数 ÷ 帧率。
  // 存的是帧数（真值），时长输入只是换个更好懂的口径去写它。
  const duration = v.numFrames / Math.max(1, v.fps)
  const setDuration = (sec: number) => {
    const n = Math.round(Math.max(0.25, Math.min(60, sec)) * v.fps)
    patch({ numFrames: Math.max(5, Math.min(193, n)) })
  }

  /** 选档位：按引擎填步数+CFG（用户已手改过也照填——选档就是显式要这套）。 */
  const applyQuality = (quality: TxtVideoNodeType['txtvideo']['quality']) => {
    const e = v.engine === 'wan' || v.engine === 'hy15' ? v.engine : 'hy15'
    const p = QUALITY_PRESETS[e][quality]
    patch({ quality, steps: p.steps, guidanceScale: p.cfg })
  }
  /** 切引擎：映射表随引擎变，按当前档位重填，免得留着另一引擎的数字。 */
  const applyEngine = (engine: string | null) => {
    const e = engine === 'wan' || engine === 'hy15' ? engine : 'hy15'
    const p = QUALITY_PRESETS[e][v.quality]
    patch({ engine, steps: p.steps, guidanceScale: p.cfg })
  }
  /** 选分辨率预设：填宽高 + maxSide（长边超 max_side 会被下游静默缩小，
   *  所以预设必须一起给 maxSide，720p 要给到 1216）。 */
  const applyResolution = (res: TxtVideoNodeType['txtvideo']['resolution']) => {
    if (res === 'custom') { patch({ resolution: res }); return }
    const p = RES_PRESETS[res]
    patch({ resolution: res, width: p.w, height: p.h, maxSide: Math.max(p.w, p.h) })
  }
  /** 手改宽高 → 自动切"自定义"（不再跟着预设跑）。 */
  const patchSize = (p: { width?: number; height?: number }) =>
    patch({ ...p, resolution: 'custom' })

  const meta = node.meta
  const hasRef = meta?.hasRef ?? (sourceLabel != null)
  const estMin = effEngine === 'hy15'
    ? Math.max(1, Math.round(v.numFrames / 21 + (v.steps ?? 12) / 12 - 1))
    : Math.round(v.numFrames / 21 + (v.steps ?? 30) / 30)
  const postNote = [
    // 插帧不改时长：帧数翻倍、fps 也翻倍（后端 _output_fps 同一规则）
    v.interpolate ? `插帧 ${v.numFrames * 2 - 1} 帧 @ ${Math.min(60, v.fps * 2)}fps` : '',
    v.upscale ? '放大 2×' : '',
  ].filter(Boolean).join(' + ')

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
            <span className="gen-field-label" title="画质档位：按引擎映射步数+引导强度。hy15 蒸馏 8/12/20 步无 CFG；Wan 20/30/40 步 + CFG 3/5/6。选档即填入，下面两项仍可手改">画质档位</span>
            <select className="input" value={v.quality}
              onChange={(e) => applyQuality(e.target.value as TxtVideoNodeType['txtvideo']['quality'])}>
              {Object.entries(QUALITY_CN).map(([id, cn]) => (
                <option key={id} value={id}>{cn}</option>
              ))}
            </select>
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="480p=hy15 原生 848×480；720p=1216×688（Wan 原生级，hy15 超训练分辨率会警告）；自定义=宽高自由">分辨率</span>
            <select className="input" value={v.resolution}
              onChange={(e) => applyResolution(e.target.value as TxtVideoNodeType['txtvideo']['resolution'])}>
              <option value="480p">480p</option>
              <option value="720p">720p</option>
              <option value="custom">自定义</option>
            </select>
          </label>
        </div>
        <div className="hint" style={{ marginTop: -4 }}>
          {v.resolution === 'custom'
            ? `自定义 ${v.width}×${v.height}`
            : RES_PRESETS[v.resolution].note}
          {v.resolution === '720p' && effEngine === 'hy15' ? '（⚠ 当前引擎是 hy15）' : ''}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">宽度</span>
            <input className="input" type="number" min={128} max={1280} step={16}
              value={v.width} onChange={(e) => patchSize({ width: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">高度</span>
            <input className="input" type="number" min={128} max={1280} step={16}
              value={v.height} onChange={(e) => patchSize({ height: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label"
              title={`时长就是帧数 ÷ 帧率。当前引擎 ${effEngine === 'hy15' ? 'Hunyuan（上限 33 帧）' : 'Wan（上限 193 帧）'}，` +
                `按 ${v.fps}fps 最长 ${(frameCap / v.fps).toFixed(2)} 秒；要更长请切 Wan 或降帧率`}>
              时长（秒）
            </span>
            <input className="input" type="number" min={0.25} max={60} step={0.25}
              value={Number(duration.toFixed(2))}
              onChange={(e) => setDuration(Number(e.target.value))} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="帧率只影响顺滑度，不改变动作快慢——改帧率会按当前时长换算出新帧数">帧率</span>
            <input className="input" type="number" min={8} max={30}
              value={v.fps}
              onChange={(e) => {
                // 锁时长、换帧数：用户心里的量是「几秒」，不该因为调帧率变长变短
                const nextFps = Math.max(1, Number(e.target.value))
                const n = Math.round(duration * nextFps)
                patch({ fps: nextFps, numFrames: Math.max(5, Math.min(193, n)) })
              }} />
          </label>
        </div>

        <div className="hint" style={{ marginTop: -2 }}>
          = {v.numFrames} 帧 @ {v.fps}fps ≈ {duration.toFixed(2)}s
          {v.numFrames > frameCap ? (
            <span style={{ color: 'var(--pink)' }}>
              {' '}⚠ {effEngine === 'hy15' ? 'Hunyuan' : 'Wan'} 上限 {frameCap} 帧
              （{(frameCap / v.fps).toFixed(2)}s），超出会被静默截断
            </span>
          ) : null}
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label" title="时序 VAE 要求 4k+1，内部会自动向下取整；改这里等于改时长">帧数</span>
            <input className="input" type="number" min={5} max={193} step={4}
              value={v.numFrames} onChange={(e) => patch({ numFrames: Number(e.target.value) })} />
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
            <span className="gen-field-label" title={`画质档位「${QUALITY_CN[v.quality]}」映射值：${effEngine} ${q.steps} 步 / CFG ${q.cfg}；清空则按档位默认`}>步数（空=按档位）</span>
            <input className="input" type="number" min={4} max={60}
              value={v.steps ?? ''} placeholder={String(q.steps)}
              onChange={(e) => patch({ steps: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="CFG：hy15 蒸馏模型官方推荐 1.0（无 CFG）；Wan 默认 5.0，动作偏弱时上调，过头出重影">引导强度</span>
            <input className="input" type="number" min={1} max={9} step={0.5}
              value={v.guidanceScale ?? ''} placeholder={String(q.cfg)}
              onChange={(e) => patch({ guidanceScale: e.target.value ? Number(e.target.value) : null })} />
          </label>
        </div>

        <label className="gen-field">
          <span className="gen-field-label" title="两个引擎都支持纯文本；Hunyuan 一致性和细节更好（帧数上限 33），Wan 帧数可到 193、像素风保留更严格">引擎</span>
          <select className="input" value={v.engine ?? 'auto'}
            onChange={(e) => applyEngine(e.target.value === 'auto' ? null : e.target.value)}>
            {Object.entries(ENGINE_CN).map(([id, cn]) => (
              <option key={id} value={id}>{cn}</option>
            ))}
          </select>
        </label>

        <label className="gen-field">
          <span className="gen-field-label" title="mp4（默认）：H.264 + yuv420p，浏览器 video 原生播，体积小无帧间压缩损失——最终成片用它。gif：256 色、img 直接播，同规格大数倍且有色带——适合当精灵管线的中间产物">输出格式</span>
          <select className="input" value={v.format ?? 'mp4'}
            onChange={(e) => patch({ format: e.target.value as 'mp4' | 'gif' })}>
            <option value="mp4">MP4（H.264，默认）</option>
            <option value="gif">GIF（256 色）</option>
          </select>
        </label>

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

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="出片后处理：OpenCV 光流双向变形，帧数翻倍（12→24fps 观感直接变顺）。小中幅运动效果好；大幅运动/硬边可能有伪影。非 AI 插帧（RIFE 类权重当前网络拉不到）">
            插帧 2×（光流）
          </span>
          <button className={`switch ${v.interpolate ? 'on' : ''}`}
            onClick={() => patch({ interpolate: !v.interpolate })} aria-label="切换插帧">
            <i />
          </button>
        </div>

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="出片后处理：2× 放大。像素图走最近邻整数放大（硬边不糊，相当于像素图的正确超分）；其余 Lanczos+USM 锐化。非 AI 超分">
            放大 2×
          </span>
          <button className={`switch ${v.upscale ? 'on' : ''}`}
            onClick={() => patch({ upscale: !v.upscale })} aria-label="切换放大">
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
            : `运行生成（约 ${estMin} 分钟${hasRef ? '，图生视频' : '，纯文本'}${postNote ? ` + ${postNote}` : ''}）`}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {!running && error ? (
          <div className="hint" style={{ color: 'var(--pink)', marginTop: 4 }} title={error}>
            失败：{error}
          </div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill good">{meta.mode === 't2v' ? '纯文本' : '图生视频'}</span>
            <span className="meta-pill">{meta.frames ?? '?'} 帧</span>
            {meta.steps ? <span className="meta-pill">{meta.steps} 步</span> : null}
            {meta.size ? <span className="meta-pill">{meta.size[0]}×{meta.size[1]}</span> : null}
            {meta.vram?.peakGB ? <span className="meta-pill">峰值 {meta.vram.peakGB}GB</span> : null}
            {meta.post?.interpolated ? <span className="meta-pill">已插帧</span> : null}
            {meta.post?.upscaled ? <span className="meta-pill">已放大</span> : null}
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
