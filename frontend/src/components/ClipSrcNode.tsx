import { useEffect, useRef, useState } from 'react'
import { api } from '../api'
import type { ClipSrcNode as ClipSrcNodeType, ClipSrcOptions, VideoAsset } from '../types'

type Props = {
  node: ClipSrcNodeType
  index: number
  projectId: string
  onChange: (patch: Partial<ClipSrcNodeType>) => void
  onDelete: () => void
  onDragStart: (e: React.MouseEvent) => void
  onStartLink: (id: string, client?: { clientX: number; clientY: number }) => void
  onFinishLink: (id: string) => void
  onPreview: (url: string, title: string) => void
}

/**
 * 选择视频 / GIF 节点：素材链的入口。
 *
 * 为什么不是复用「选择图片」：那条链下游吃的是 `refs`（参考图），而这里要的
 * 是一段**可解码的动画**（mp4/gif/webm），存在 `images/` 里、要能拖进度条
 * 播一遍确认。两者的资产目录和选择器都不一样，混在一起会让「参考图」列表
 * 里冒出一堆视频。
 *
 * 「先播再拆」是这个节点唯一的交互价值：拆一次要跑抠图（分钟级），先看一遍
 * 确认片段对不对，比拆完发现拿错文件划算。
 */
export default function ClipsrcNode({
  node, index, projectId, onChange, onDelete, onDragStart,
  onStartLink, onFinishLink, onPreview,
}: Props) {
  const fileRef = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [videos, setVideos] = useState<VideoAsset[]>([])
  const [pickOpen, setPickOpen] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  const v: ClipSrcOptions = {
    file: node.clipsrc?.file ?? '',
    kind: node.clipsrc?.kind ?? 'video',
    durationSec: node.clipsrc?.durationSec ?? 0,
    fps: node.clipsrc?.fps ?? 0,
    width: node.clipsrc?.width ?? 0,
    height: node.clipsrc?.height ?? 0,
  }
  const url = v.file
    ? `/api/projects/${encodeURIComponent(projectId)}/images/${encodeURIComponent(v.file)}`
    : null
  const isGif = v.file.toLowerCase().endsWith('.gif')

  const loadVideos = async () => {
    try {
      const r = await api.listProjectVideos(projectId)
      setVideos(r.videos || [])
    } catch (e: any) {
      setErr(String(e?.message || e))
    }
  }

  useEffect(() => {
    if (pickOpen) loadVideos()
    // 只在下拉打开时拉一次：项目里视频不多，避免每次渲染都打接口
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pickOpen])

  const upload = async (files: FileList | null) => {
    if (!files?.length) return
    const f = files[0]
    setBusy(true)
    setErr(null)
    try {
      const r = await api.uploadProjectVideo(projectId, f)
      // 上传成功后顺手把时长/尺寸读出来：拆帧节点默认的起止时间要按它算，
      // 用户不必自己量。读失败不影响主流程（只是不显示这些数字）。
      const probe = await probeMedia(r.video.url)
      onChange({
        clipsrc: {
          ...v,
          file: r.video.file,
          kind: f.name.toLowerCase().endsWith('.gif') ? 'gif' : 'video',
          durationSec: probe.durationSec || 0,
          fps: probe.fps || 0,
          width: probe.width || 0,
          height: probe.height || 0,
        },
      })
    } catch (e: any) {
      setErr(`上传失败：${e?.message || e}`)
    } finally {
      setBusy(false)
      if (fileRef.current) fileRef.current.value = ''
    }
  }

  const choose = async (a: VideoAsset) => {
    setPickOpen(false)
    const probe = await probeMedia(a.url)
    onChange({
      clipsrc: {
        ...v,
        file: a.file,
        kind: a.file.toLowerCase().endsWith('.gif') ? 'gif' : 'video',
        durationSec: probe.durationSec || 0,
        fps: probe.fps || 0,
        width: probe.width || 0,
        height: probe.height || 0,
      },
    })
  }

  return (
    <div className="node clipsrc" style={{ left: node.x, top: node.y, width: 360 }}
      data-node-id={node.id}>
      <div className="node-head" onMouseDown={onDragStart}>
        <span className="node-kind">CLIP</span>
        <span className="node-title">选择视频 / GIF</span>
        <span className="node-idx">C{index + 1}</span>
        <button className="node-x" title="删除这张节点"
          onMouseDown={(e) => e.stopPropagation()} onClick={onDelete}>×</button>
        <div className="port out" title="按住拖到目标节点即可连线"
          onMouseDown={(e) => {
            e.stopPropagation()
            onStartLink(node.id, { clientX: e.clientX, clientY: e.clientY })
          }} />
      </div>

      <div className="node-body">
        <div className="hint">
          导入一段动作视频或 GIF（mp4 / mov / webm / gif）。先播一遍确认片段，再交给下游拆帧。
        </div>

        <div className="row-2">
          <button className="btn btn-sm" onClick={() => fileRef.current?.click()}
            disabled={busy}>
            {busy ? <><span className="spin" /> 上传中…</> : (v.file ? '换一个文件' : '上传视频')}
          </button>
          <button className="btn btn-sm btn-ghost" onClick={() => setPickOpen((x) => !x)}>
            {pickOpen ? '收起' : '从项目里选'}
          </button>
        </div>
        <input ref={fileRef} type="file" hidden
          accept="video/mp4,video/quicktime,video/webm,video/x-matroska,image/gif"
          onChange={(e) => upload(e.target.files)} />

        {pickOpen ? (
          <div className="clip-videolist">
            {videos.length ? videos.map((a) => (
              <button key={a.file} className={`clip-videoitem ${a.file === v.file ? 'on' : ''}`}
                onClick={() => choose(a)} title={a.file}>
                <span className="t">{a.file}</span>
                <span className="meta">
                  {a.origin === 'upload' ? '上传' : '生成'}
                  {a.dir ? ` · ${a.dir}` : ''}
                </span>
              </button>
            )) : (
              <div className="hint">项目里还没有视频。上面上传一个。</div>
            )}
          </div>
        ) : null}

        {err ? <div className="node-error">{err}</div> : null}

        {url ? (
          <div className="clip-preview">
            {isGif ? (
              <button className="clip-media" title="点击放大"
                onClick={() => onPreview(url, 'GIF 源')}>
                <img src={url} alt="GIF 源" />
              </button>
            ) : (
              <video className="clip-media" src={url} controls loop playsInline
                preload="metadata" />
            )}
            <div className="clip-info">
              <span className="meta-pill">{v.kind === 'gif' ? 'GIF' : '视频'}</span>
              {v.width && v.height
                ? <span className="meta-pill">{v.width}×{v.height}</span> : null}
              {v.durationSec ? <span className="meta-pill">{v.durationSec.toFixed(2)}s</span> : null}
              {v.fps ? <span className="meta-pill">{v.fps.toFixed(1)}fps</span> : null}
            </div>
            <div className="hint" style={{ fontSize: 11 }}>
              {v.file}
            </div>
          </div>
        ) : (
          <div className="hint" style={{ marginTop: 8 }}>
            还没选素材。上传一个视频，或从项目里已有片段中挑。
          </div>
        )}
      </div>
    </div>
  )
}

/** 读媒体元信息（时长 / 尺寸 / 粗略帧率）。
 *
 *  fps 只能估：`<video>` 不暴露帧率，唯一能读的是 `requestVideoFrameCallback`
 *  的媒体时间，实测要等几帧才稳。这里用「播 0.4 秒数回调次数」粗估——它只
 *  用来给拆分节点的「帧率上限」当默认提示，不准不影响正确性（后端解码时
 *  会拿到真实 fps）。
 */
async function probeMedia(url: string): Promise<{
  durationSec?: number; width?: number; height?: number; fps?: number
}> {
  if (url.toLowerCase().split('?')[0].endsWith('.gif')) {
    try {
      const img = new Image()
      img.src = url
      await img.decode()
      return { width: img.naturalWidth, height: img.naturalHeight }
    } catch {
      return {}
    }
  }
  return new Promise((resolve) => {
    const v = document.createElement('video')
    v.preload = 'metadata'
    v.muted = true
    let settled = false
    const done = (r: any) => {
      if (settled) return
      settled = true
      v.removeAttribute('src')
      v.load()
      resolve(r)
    }
    v.onloadedmetadata = () => {
      const base = {
        durationSec: Number.isFinite(v.duration) ? v.duration : 0,
        width: v.videoWidth,
        height: v.videoHeight,
      }
      // 有 rVFC 就粗估帧率，1.2 秒内不出结果就直接返回元信息
      const anyV = v as any
      if (typeof anyV.requestVideoFrameCallback !== 'function') return done(base)
      let n = 0
      let t0 = 0
      const tick = (_now: number, meta: any) => {
        if (!n) t0 = meta.mediaTime
        n++
        if (meta.mediaTime - t0 >= 0.4) {
          done({ ...base, fps: n / Math.max(0.001, meta.mediaTime - t0) })
        } else {
          anyV.requestVideoFrameCallback(tick)
        }
      }
      anyV.requestVideoFrameCallback(tick)
      setTimeout(() => done(base), 1200)
      v.play().catch(() => done(base))
    }
    v.onerror = () => done({})
    setTimeout(() => done({}), 4000)
    v.src = url
  })
}
