import { useEffect, useRef, useState } from 'react'
import { api, loadPromptLimits } from '../api'
import type {
  PromptLimits, WalkVideoNode as WalkVideoNodeType, WalkDir, VideoAsset,
} from '../types' 

type Props = {
  node: WalkVideoNodeType
  index: number
  projectId: string
  running?: boolean
  /** 后端推来的当前步骤文案（八方向跑几分钟，光转圈看不出进行到哪） */
  progress?: string | null
  /** 参考的正视图来自哪个节点（链条最上游那张） */
  sourceLabel?: string | null
  onChange: (patch: Partial<WalkVideoNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onRun: () => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

const DIR_MODE_CN: Record<string, string> = {
  eight: '八方向',
  four: '四方向',
  single: '仅正面',
}

const DIR_CN: Record<WalkDir, string> = {
  'down': '正面', 'down-left': '左下', 'left': '左侧', 'up-left': '左上',
  'up': '背面', 'up-right': '右上', 'right': '右侧', 'down-right': '右下',
}

/** 四视图锚点的中文名（后端 anchorPlan 里的角色） */
const ANCHOR_CN: Record<string, string> = { front: '正面', side: '侧面', back: '背面', side_r: '右面' }

/** 每个方向模式里「真实生成」的方向（右侧三个是左侧镜像，不单独给来源）。 */
const REAL_DIRS: Record<string, WalkDir[]> = {
  eight: ['down', 'down-left', 'left', 'up-left', 'up'],
  four: ['down', 'left', 'up'],
  single: ['down'],
}

const isGif = (f: string) => f.toLowerCase().endsWith('.gif')
const shortName = (f: string) => (f.length > 22 ? `${f.slice(0, 10)}…${f.slice(-8)}` : f)

/**
 * 行走视频节点（八方向链的第 1 步）：正视图 → 各方向立绘 + Wan 行走视频。
 *
 * 两个关键设计：
 * 1. 参考永远是最上游那张正视图，和视角节点一样沿链条往前找；
 * 2. 右侧三个方向是左侧的整段帧镜像——视频生成是整条链最慢的一步，
 *    镜像省一半时间，行走循环镜像后依然合理（见 walkvideo 模块文档）。
 *
 * 视频来源（sources）：每个方向可以改用一段现成视频，跳过该方向的立绘
 * 推导 + Wan 生成。用来换参数重跑下游、替换不满意的方向、或完全用外部
 * 素材。上传的文件和历史生成的片段在同一个列表里选。
 */
export default function WalkVideoNode({
  node, index, projectId, running, progress, sourceLabel, onChange, onDelete, onDragStart,
  onRun, onStartLink, onFinishLink, onPreview,
}: Props) {
  const DEFAULTS: WalkVideoNodeType['walkvideo'] = {
    enabled: true, dirMode: 'eight', action: '行走', styleId: null,
    steps: null, seed: null, width: 384, height: 512,
    numFrames: 49, fps: 16, maxSide: 512, maxSeqLen: null,
    guidanceScale: null, motionThreshold: 5.5, retries: 1,
    sources: {},
  }
  const v = { ...DEFAULTS, ...node.walkvideo }
  const patch = (p: Partial<typeof v>) => onChange({ walkvideo: { ...v, ...p } })
  const sources = v.sources || {}
  // 动作描述会拼进 Wan 的提示词模板，模板本身已占掉大半预算
  const [limits, setLimits] = useState<PromptLimits | null>(null)
  useEffect(() => {
    let alive = true
    loadPromptLimits().then((l) => { if (alive) setLimits(l) }).catch(() => undefined)
    return () => { alive = false }
  }, [])
  const actionMax = limits?.walkAction.maxChars ?? 40

  const meta = node.meta
  const dirs = meta?.directions ?? []

  // ---- 视频来源面板状态 ----
  const realDirs = REAL_DIRS[v.dirMode] || REAL_DIRS.eight
  // 本机实测拟合（4090 / fp8 / 384×512）：单方向视频 ≈ 31 + 0.0183 × 帧数 × 步数 秒。
  // 81 帧/30 步实测 75.7s、49 帧/24 步 52.7s，两点拟合误差 < 1s。
  // 再加上每方向一次立绘推导（28 步约 18s）与序列帧/拼表（约 25s）。
  const vidSteps = v.steps ?? 30
  const perVideoSec = Math.round(31 + 0.0183 * (v.numFrames || 49) * vidSteps)
  const etaSec = Math.round(realDirs.length * (perVideoSec + 18) + 25)
  const etaText = `${Math.floor(etaSec / 60)} 分 ${etaSec % 60} 秒`
  const [videos, setVideos] = useState<VideoAsset[] | null>(null)
  const [picking, setPicking] = useState<WalkDir | null>(null)
  const [uploadFor, setUploadFor] = useState<WalkDir | null>(null)
  const [busy, setBusy] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const srcRef = useRef<HTMLDivElement>(null)

  // ---- 方向立绘自选 ----
  const [pickingImg, setPickingImg] = useState<WalkDir | null>(null)
  const [imgFiles, setImgFiles] = useState<string[] | null>(null)
  const imgRef = useRef<HTMLDivElement>(null)
  const customDirCount = Object.values(v.dirImages || {}).filter(Boolean).length
  // 两个量化指标（都在后端算，剪影判据）：
  //   motion 相邻帧剪影变化 → 低于 3 基本等于没动（真走的实测 7~20）
  //   instability 剪影面积波动 → 高于 80% 说明画面在闪/糊（正常的 28~38%）
  const motions = dirs
    .map((d) => (d.walk as any)?.motion)
    .filter((m): m is number => typeof m === 'number')
  const minMotion = motions.length ? Math.min(...motions) : null
  const instabs = dirs
    .map((d) => (d.walk as any)?.instability)
    .filter((m): m is number => typeof m === 'number')
  const maxInstab = instabs.length ? Math.max(...instabs) : null

  const refreshImages = async () => {
    try {
      const info = await api.projectFiles(projectId)
      const list = (info.files || [])
        .filter((f) => f.kind === 'images' && /\.(png|jpe?g|webp)$/i.test(f.path))
        .map((f) => f.path.split('/').pop() || f.path)
      setImgFiles(list)
    } catch {
      setImgFiles(null)      // null = 加载失败（和空列表区分开）
    }
  }
  const openImgPicker = async (d: WalkDir) => {
    setPickingImg(pickingImg === d ? null : d)
    if (pickingImg !== d) await refreshImages()
  }
  const setDirImage = (d: WalkDir, file: string | null) => {
    const next = { ...(v.dirImages || {}) }
    if (file) next[d] = file
    else delete next[d]
    patch({ dirImages: next })
  }

  // 点面板外面收起选择器：下拉开着却不消失，点别处还要再点一次很烦
  useEffect(() => {
    if (!picking && !pickingImg) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (srcRef.current && srcRef.current.contains(t)) return
      if (imgRef.current && imgRef.current.contains(t)) return
      setPicking(null)
      setPickingImg(null)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [picking, pickingImg])

  const refreshVideos = async () => {
    try {
      const r = await api.listProjectVideos(projectId)
      setVideos(r.videos)
    } catch {
      setVideos([])
    }
  }

  const openPicker = async (d: WalkDir) => {
    setPicking(picking === d ? null : d)
    // 每次打开都拉最新列表：刚跑完的方向会多出新的 walk-*.gif，
    // 缓存一次很容易让人以为「没有可选的视频」
    if (picking !== d) await refreshVideos()
  }

  const setSource = (d: WalkDir, file: string | null) => {
    const next = { ...sources }
    if (file) next[d] = file
    else delete next[d]
    patch({ sources: next })
    setPicking(null)
  }

  const upload = async (files: FileList | null) => {
    const dir = uploadFor
    if (!files?.length || !dir) return
    // 一次只能给一个方向传一段视频：以前多选时每个文件都写同一个 key，
    // 先传的会被后传的静默覆盖（白上传 + 用户以为传了两个）
    const list = Array.from(files)
    if (list.length > 1) {
      alert(`一次只给一个方向上传一段视频，本次只使用第 1 个文件（共 ${list.length} 个）。`)
    }
    setBusy(true)
    try {
      for (const f of list.slice(0, 1)) {
        const res = await api.uploadProjectVideo(projectId, f)
        setSource(dir, res.video.file)
        await refreshVideos()
      }
    } catch (e: any) {
      alert(`视频上传失败：${e.message || e}`)
    } finally {
      setBusy(false)
      setUploadFor(null)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const existingCount = realDirs.filter((d) => sources[d]).length
  const runLabel = existingCount === 0
    ? `运行生成${DIR_MODE_CN[v.dirMode] ?? ''}行走视频`
    : existingCount === realDirs.length
      ? '运行（全部用现成视频）'
      : `运行（${existingCount} 现成 + ${realDirs.length - existingCount} Wan）`

  return (
    <div className={`node walkvideo ${v.enabled ? '' : 'off'}`}
      style={{ left: node.x, top: node.y, width: 380 }} data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">WALK</span>
        <span className="node-title">行走视频</span>
        <span className="node-idx">W{index + 1}</span>
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
            使用此节点{v.enabled ? '' : '（已关闭，直接透传上游图）'}
          </span>
          <button className={`switch ${v.enabled ? 'on' : ''}`}
            onClick={() => patch({ enabled: !v.enabled })} aria-label="切换行走视频节点">
            <i />
          </button>
        </div>

        <div className="hint">
          参考：{sourceLabel ? <b>{sourceLabel}</b> : '未连线（把「生成图」或「选择图片」连到左侧端口）'}
        </div>

        <div className="hint" style={{ marginTop: -2, lineHeight: 1.6 }}>
          预计 ≈ <b>{etaText}</b>（{realDirs.length} 个真实方向 × 视频 {perVideoSec}s +
          立绘 18s + 后处理约 25s；{v.numFrames} 帧 / {vidSteps} 步，本机实测拟合）
          {v.dirMode === 'eight' ? '。选「四方向」可少跑 2 次视频（约省 40%）' : ''}
          {v.numFrames > 49 || (v.steps ?? 30) > 30 ? (
            <button className="btn btn-sm" style={{ marginLeft: 6, height: 22 }}
              onClick={() => patch({
                numFrames: 49, fps: 16, steps: null, retries: 1, motionThreshold: 5.5,
              })}>
              用推荐参数
            </button>
          ) : null}
        </div>

        <label className="gen-field">
          <span className="gen-field-label">方向模式</span>
          <select className="input" value={v.dirMode}
            onChange={(e) => patch({ dirMode: e.target.value as typeof v.dirMode })}>
            <option value="eight">八方向（5 实拍 + 3 镜像）</option>
            <option value="four">四方向（3 实拍 + 1 镜像）</option>
            <option value="single">仅正面（快速验证）</option>
          </select>
        </label>

        <label className="gen-field">
          <span className="gen-field-label">动作</span>
          <input className="input" value={v.action}
            onChange={(e) => patch({ action: e.target.value })} />
          <span className="hint" style={{ marginTop: 2,
            color: v.action.length > actionMax ? 'var(--yellow)' : undefined }}>
            {v.action.length} / 建议 {actionMax} 字
            {v.action.length > actionMax ? ' · 超出建议长度，仍会生效' : ''}
          </span>
        </label>

        {/* ---- 方向立绘：每个真实方向用哪张图当参考（可预览 / 可自选） ---- */}
        <div className="walk-src" ref={imgRef}>
          <div className="walk-src-head">
            <span>方向立绘</span>
            <span className="walk-src-count">
              {customDirCount ? `${customDirCount} 个已指定` : '自动（就近锚点）'}
            </span>
          </div>
          {realDirs.map((d) => {
            const sel = (v.dirImages || {})[d]
            const ran = dirs.find((x) => x.dir === d)
            const viewUrl = sel
              ? `/api/projects/${projectId}/images/${sel}`
              : ran?.view?.url
            const plan = (meta?.anchorPlan || {})[d]
            return (
              <div className="walk-src-row" key={d}>
                <span className="walk-src-dir">{DIR_CN[d]}</span>
                {viewUrl ? (
                  <button className="walk-src-thumb"
                    title={`${DIR_CN[d]}立绘（点击放大）`}
                    onClick={() => onPreview(viewUrl, `${DIR_CN[d]}立绘`)}>
                    <img src={viewUrl} alt={`${DIR_CN[d]}立绘`} />
                  </button>
                ) : null}
                <span className="walk-src-name" title={plan ? `从${ANCHOR_CN[plan.anchor] || plan.anchor}转 ${plan.rotation}°` : '按就近锚点推导'}>
                  {sel ? '用户指定'
                    : plan ? `${ANCHOR_CN[plan.anchor] || plan.anchor}${plan.rotation ? ` ${plan.rotation > 0 ? '+' : ''}${plan.rotation}°` : ' 直接用'}`
                      : '自动'}
                </span>
                <button className="btn btn-xs" onClick={() => openImgPicker(d)}>选图</button>
                {sel ? (
                  <button className="btn btn-xs walk-src-clear" title="改回自动推导"
                    onClick={() => setDirImage(d, null)}>×</button>
                ) : null}
                {pickingImg === d ? (
                  <div className="walk-src-picker">
                    <div className="walk-src-picker-head">
                      <span>选一张立绘（{imgFiles?.length ?? 0}）</span>
                      <button className="btn btn-xs" onClick={() => refreshImages()}>刷新</button>
                    </div>
                    {imgFiles && imgFiles.length ? imgFiles.map((f) => (
                      <button key={f} className="walk-src-item"
                        onClick={() => { setDirImage(d, f); setPickingImg(null) }}>
                        <img src={`/api/projects/${projectId}/images/${f}`} alt={f} />
                        <span>项目图片<i>{shortName(f)}</i></span>
                      </button>
                    )) : imgFiles === null ? (
                      // 拉列表失败和「真的没有图片」不能混成一句话：
                      // 前者要用户点刷新重试，后者要用户去跑上游
                      <div className="warn" style={{ margin: 0 }}>
                        图片列表加载失败（后端可能没连上）。
                        <button className="btn btn-sm" style={{ marginLeft: 6, height: 22 }}
                          onClick={() => void refreshImages()}>重试</button>
                      </div>
                    ) : (
                      <div className="hint" style={{ margin: 0 }}>
                        项目里还没有图片。先跑上游的「生成图」或上传一张。
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            )
          })}
        </div>

        {/* ---- 视频来源：每个方向可选 Wan 生成或现成视频 ---- */}
        <div className="walk-src" ref={srcRef}>
          <div className="walk-src-head">
            <span>视频来源</span>
            <span className="walk-src-count">
              {existingCount ? (
                <>
                  {existingCount}/{realDirs.length} 用现成视频
                  <button className="walk-src-reset" title="全部改回 Wan 生成"
                    onClick={() => patch({ sources: {} })}>全部改回</button>
                </>
              ) : '全部 Wan 生成'}
            </span>
          </div>
          {realDirs.map((d) => {
            const file = sources[d]
            return (
              <div className="walk-src-row" key={d}>
                <span className="walk-src-dir">{DIR_CN[d]}</span>
                {file ? (
                  <>
                    {isGif(file) ? (
                      <button className="walk-src-thumb" title={`${file}（点击放大）`}
                        onClick={() => onPreview(
                          `/api/projects/${projectId}/images/${file}`, `${DIR_CN[d]}现成视频`)}>
                        <img src={`/api/projects/${projectId}/images/${file}`} alt={file} />
                      </button>
                    ) : (
                      // 非 GIF 不做缩略图：<video> 会把整段视频拉下来当封面，
                      // 上传的 mp4 动辄几十 MB，一个面板能拉几百 MB
                      <span className="walk-src-thumb walk-src-ext" title={file}>
                        {file.split('.').pop()?.toUpperCase()}
                      </span>
                    )}
                    <span className="walk-src-name" title={file}>{shortName(file)}</span>
                    <button className="btn btn-xs" onClick={() => openPicker(d)}>换</button>
                    <button className="btn btn-xs walk-src-clear" title="改回 Wan 生成"
                      onClick={() => setSource(d, null)}>×</button>
                  </>
                ) : (
                  <>
                    <span className="walk-src-name walk-src-wan">Wan 生成</span>
                    <button className="btn btn-xs" disabled={busy}
                      onClick={() => openPicker(d)}>选视频</button>
                    <button className="btn btn-xs" disabled={busy} title="上传一段现成视频"
                      onClick={() => { setUploadFor(d); fileRef.current?.click() }}>
                      {busy && uploadFor === d ? <span className="spin" /> : '上传'}
                    </button>
                  </>
                )}
                {picking === d ? (
                  <div className="walk-src-picker">
                    <div className="walk-src-picker-head">
                      <span>选一段视频（{videos?.length ?? 0}）</span>
                      <button className="btn btn-xs" onClick={() => refreshVideos()}>刷新</button>
                    </div>
                    {videos && videos.length ? videos.map((vd) => (
                      <button key={vd.file} className="walk-src-item"
                        onClick={() => setSource(d, vd.file)}>
                        {isGif(vd.file)
                          ? <img src={vd.url} alt={vd.file} />
                          : <span className="walk-src-ext walk-src-ext-sm">
                            {vd.file.split('.').pop()?.toUpperCase()}
                          </span>}
                        <span>
                          {vd.origin === 'generated'
                            ? `生成·${DIR_CN[vd.dir as WalkDir] ?? vd.dir ?? '未知'}`
                            : '上传'}
                          <i>{shortName(vd.file)}</i>
                        </span>
                      </button>
                    )) : (
                      <div className="hint" style={{ margin: 0 }}>
                        项目里还没有视频。跑一次「行走视频」或点「上传」。
                      </div>
                    )}
                  </div>
                ) : null}
              </div>
            )
          })}
          <input ref={fileRef} type="file" hidden
            accept=".gif,.mp4,.mov,.avi,.webm,.mkv,.m4v,video/*"
            onChange={(e) => upload(e.target.files)} />
          <div className="hint" style={{ marginTop: 4 }}>
            指定了现成视频的方向跳过立绘推导和 Wan 生成，直接用文件里的帧；
            没指定的照常生成。右侧方向由左侧镜像得到。
          </div>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">Wan 帧数</span>
            <input className="input" type="number" min={5} max={193} step={4}
              value={v.numFrames}
              onChange={(e) => patch({ numFrames: Number(e.target.value) })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label">视频帧率</span>
            <input className="input" type="number" min={8} max={30}
              value={v.fps} onChange={(e) => patch({ fps: Number(e.target.value) })} />
          </label>
        </div>

        <div className="param-grid">
          <label className="gen-field">
            <span className="gen-field-label">步数（空=默认 30）</span>
            <input className="input" type="number" min={20} max={50}
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

        <div className="switch-row" style={{ marginTop: 6 }}>
          <span className="t" style={{ fontSize: 12 }}
            title="像素素材用最近邻缩放：保住硬边，不会被视频底座的重采样糊掉">
            像素保真（最近邻缩放）
          </span>
          <button className={`switch ${v.pixelArt ? 'on' : ''}`}
            onClick={() => patch({ pixelArt: !v.pixelArt })} aria-label="切换像素保真">
            <i />
          </button>
        </div>

        {/* ---- 行走动作控制（轻量闭环：不上更大模型也能逼出「真正在走」） ---- */}
        <div className="param-grid" style={{ marginTop: 6 }}>
          <label className="gen-field">
            <span className="gen-field-label" title="引导强度。默认 5.0；行走动作偏弱时上调（封顶 9.0）可逼出更大动作幅度">
              引导 CFG（空=5.0）
            </span>
            <input className="input" type="number" min={1} max={9} step={0.5}
              value={v.guidanceScale ?? ''} placeholder="5.0"
              onChange={(e) => patch({ guidanceScale: e.target.value ? Number(e.target.value) : null })} />
          </label>
          <label className="gen-field">
            <span className="gen-field-label" title="动作幅度合格线：剪影 1-IoU，真实行走约 5~20，定格 <3。低于此值自动加压重跑">
              动作合格线
            </span>
            <input className="input" type="number" min={1} max={30} step={1}
              value={v.motionThreshold}
              onChange={(e) => patch({ motionThreshold: Number(e.target.value) })} />
          </label>
        </div>
        <div className="param-grid" style={{ marginTop: 4 }}>
          <label className="gen-field" style={{ gridColumn: '1 / -1' }}>
            <span className="gen-field-label" title="动作不达标时自动加压（每次 CFG +1.5、步数 +5）重跑的次数；0 = 不重试">
              不达标自动重试次数：{v.retries}
            </span>
            <input className="input" type="range" min={0} max={5} step={1}
              value={v.retries}
              onChange={(e) => patch({ retries: Number(e.target.value) })} />
          </label>
        </div>

        <button className="btn btn-sm" onClick={onRun} disabled={running}
          style={{ marginTop: 8, width: '100%' }}>
          {running ? <><span className="spin" /> 生成中…</> : runLabel}
        </button>

        {running && progress ? (
          <div className="hint walk-progress" title="当前步骤">{progress}</div>
        ) : null}

        {meta ? (
          <div className="result-meta" style={{ borderTop: 0, marginTop: 8 }}>
            <span className="meta-pill">{meta.dirModeLabel}</span>
            <span className="meta-pill">实拍 {meta.realDirections}</span>
            {meta.mirroredDirections ? (
              <span className="meta-pill">镜像 {meta.mirroredDirections}</span>
            ) : null}
            {meta.existingDirections ? (
              <span className="meta-pill walk-pill-src">现成 {meta.existingDirections}</span>
            ) : null}
            <span className="meta-pill">{meta.numFrames} 帧 @ {meta.fps}fps</span>
            {meta.vramPeakGB ? (
              <span className="meta-pill" title="一段视频的峰值显存（卸载底座前的分配器峰值）">
                峰值显存 {meta.vramPeakGB}GB
              </span>
            ) : null}
            {meta.vramAfterGB != null ? (
              <span className="meta-pill good" title="出完片并卸载视频底座后的显存占用">
                已释放至 {meta.vramAfterGB}GB
              </span>
            ) : null}
            <span className="meta-pill">{meta.seconds}s</span>
          </div>
        ) : null}

        {minMotion != null && minMotion < 3 ? (
          <div className="walk-fails">
            <b>有方向几乎没动</b>
            <div>剪影动作幅度最低 {minMotion}（真在走在 7 以上）。这段视频大概没走起来：
              把「引导 CFG」调到 6.5~8、或「不达标自动重试次数」调到 2~3 再跑，
              闭环会把动作幅度逼到合格线以上；也可以换个种子重跑。</div>
          </div>
        ) : null}

        {maxInstab != null && maxInstab > 80 ? (
          <div className="walk-fails">
            <b>画面不稳（有糊帧 / 闪烁）</b>
            <div>剪影面积波动 {maxInstab}%（正常 28~38%）。视频底座中途把画面冲淡或
              重影了，这类帧进精灵表就是糊斑；重跑一次通常能好，也可以在序列帧里靠
              「剔糊帧」兜住。</div>
          </div>
        ) : null}

        {meta?.failures?.length ? (
          <div className="walk-fails">
            <b>{meta.failures.length} 个方向失败</b>
            {meta.failures.slice(0, 4).map((f) => (
              <div key={`${f.dir}-${f.stage}`} title={f.error}>
                {f.label}（{f.stage === 'derive' ? '立绘' : f.stage === 'walk' ? '视频'
                  : f.stage === 'existing' ? '现成视频' : '镜像'}）：{f.error}
              </div>
            ))}
            {meta.failures.length > 4
              ? <div>……还有 {meta.failures.length - 4} 个</div> : null}
            <div className="walk-fails-tip">已成功的方向已保留，可只重跑失败的部分</div>
          </div>
        ) : null}

        {dirs.length ? (
          <div className="walk-grid">
            {dirs.map((d) => (
              <div className="walk-cell" key={d.dir}>
                <button className="walk-gif" title={`${d.label}（点击放大）`}
                  onClick={() => onPreview(d.gif.url, `${d.label}行走视频`)}>
                  <img src={d.gif.url} alt={`${d.label}行走视频`} />
                </button>
                <div className="walk-cell-t">
                  {d.label}
                  {d.mirrored ? <span className="walk-mirror" title={`镜像自${d.mirroredFrom}`}>镜</span> : null}
                  {d.walk?.mode === 'existing'
                    ? <span className="walk-mirror walk-src-badge" title={`现成视频：${d.walk.file}`}>现成</span>
                    : null}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没有行走视频。连好正视图后点上面的按钮——每个方向会生成一段原地
            行走的动画（Wan 图生视频），右侧方向由左侧镜像得到；也可以在
            「视频来源」里直接选现成视频。
          </div>
        )}
      </div>
    </div>
  )
}
