import { useEffect, useState } from 'react'
import { api } from '../api'
import type { ProjectFilesInfo } from '../types'

type Props = {
  open: boolean
  projectId: string
  onClose: () => void
}

const kb = (n: number) => (n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(2)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`)

const KIND_LABEL: Record<string, string> = {
  images: '产出',
  refs: '输入',
  other: '记录',
}

/** 项目文件夹面板：确认「输入和结果都留在项目目录下」，并支持直接打开目录。 */
export default function ProjectFiles({ open, projectId, onClose }: Props) {
  const [info, setInfo] = useState<ProjectFilesInfo | null>(null)
  const [loading, setLoading] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    setLoading(true)
    setMsg(null)
    api.projectFiles(projectId)
      .then(setInfo)
      .catch((e) => setMsg(String(e.message || e)))
      .finally(() => setLoading(false))
  }, [open, projectId])

  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  if (!open) return null

  const reveal = async () => {
    try {
      const r = await api.revealProject(projectId)
      setMsg(r.message)
      if (!r.ok) {
        try { await navigator.clipboard.writeText(r.root) } catch { /* 剪贴板不可用 */ }
      }
    } catch (e: any) {
      setMsg(String(e.message || e))
    }
  }

  const copyRoot = async () => {
    if (!info) return
    try {
      await navigator.clipboard.writeText(info.root)
      setMsg('路径已复制')
    } catch {
      setMsg('复制失败，请手动选中路径')
    }
  }

  const imgs = (info?.files || []).filter((f) => f.kind === 'images')
  const refs = (info?.files || []).filter((f) => f.kind === 'refs')
  const other = (info?.files || []).filter((f) => f.kind === 'other')

  return (
    <div className="modal-mask" onClick={onClose}>
      <div className="modal" style={{ maxWidth: 660 }} onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <div>
            <div className="modal-title">项目文件夹</div>
            <div className="hint" style={{ marginTop: 4 }}>
              输入（参考图）和产出（生成图）都在这个目录里，整个文件夹拷走就能换机器继续。
            </div>
          </div>
          <button className="btn btn-sm" onClick={onClose}>关闭</button>
        </div>

        <div className="modal-body">
          <div className="row" style={{ gap: 8 }}>
            <div className="mono grow" style={{
              fontSize: 12, color: 'var(--muted)', wordBreak: 'break-all',
              border: '1px solid var(--line)', padding: '8px 10px', background: 'rgba(0,0,0,0.35)',
            }}>{info?.root || '读取中…'}</div>
            <button className="btn btn-sm" onClick={reveal}>打开文件夹</button>
            <button className="btn btn-sm" onClick={copyRoot}>复制路径</button>
          </div>

          {msg ? <div className="ok">{msg}</div> : null}
          {loading ? <div className="hint">读取目录…</div> : null}

          {info ? (
            <>
              <div className="status-line" style={{ flexWrap: 'wrap' }}>
                <span className="tag pink">产出 {info.counts.images || 0}</span>
                <span className="tag gray">输入 {info.counts.refs || 0}</span>
                <span className="tag gray">记录 {info.counts.other || 0}</span>
                <span className="grow" />
                <span className="mono">{info.sizeMB} MB</span>
              </div>

              <div className="check-list" style={{ maxHeight: 340, overflowY: 'auto' }}>
                {[...imgs, ...refs, ...other].map((f) => (
                  <div className="status-line" key={f.path} style={{ gap: 8 }}>
                    <span className={`tag ${f.kind === 'images' ? 'pink' : 'gray'}`}>
                      {KIND_LABEL[f.kind] || f.kind}
                    </span>
                    <span className="mono grow" style={{ color: 'var(--white)', wordBreak: 'break-all' }}>
                      {f.path}
                    </span>
                    <span className="mono" style={{ color: 'var(--muted-2)' }}>{kb(f.bytes)}</span>
                  </div>
                ))}
                {info.files.length === 0 ? <div className="hint">目录还是空的。</div> : null}
              </div>

              <div className="hint" style={{ lineHeight: 1.8 }}>
                <b>project.json</b> 存画布状态：每张生成卡的描述、参考图引用、尺寸/张数/背景/画风/像素参数，
                以及每张结果的图片引用。所以关掉再打开，节点、参数、图都还在原位。
              </div>
            </>
          ) : null}
        </div>

        <div className="modal-foot">
          <div className="grow" />
          <button className="btn" onClick={onClose}>好</button>
        </div>
      </div>
    </div>
  )
}
