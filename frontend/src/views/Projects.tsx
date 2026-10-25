import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import NewProjectModal from '../components/NewProjectModal'
import type { ProjectSummary, Template } from '../types'

type Props = {
  onOpenProject: (id: string) => void
}

const fmt = (ts?: number) => {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const now = Date.now()
  const diff = now - d.getTime()
  if (diff < 60_000) return '刚刚'
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)} 分钟前`
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

export default function Projects({ onOpenProject }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [templates, setTemplates] = useState<Template[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [modal, setModal] = useState(false)
  const [query, setQuery] = useState('')

  const load = () => {
    setLoading(true)
    Promise.all([api.projects(), api.templates()])
      .then(([p, t]) => { setProjects(p.projects); setTemplates(t.templates); setError(null) })
      .catch((e) => setError(String(e.message || e)))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [])

  const tplName = useMemo(() => {
    const m = new Map(templates.map((t) => [t.id, t.name]))
    // 已下线的模板：老项目还挂着这个 id，直接显示原始 id（dir_walk）会让人
    // 以为是数据坏了。给个人话名字。
    const retired: Record<string, string> = {
      dir_walk: '八方向行走（模板已下线，可继续打开）',
    }
    return (id: string) => m.get(id) || retired[id] || id
  }, [templates])

  const shown = projects.filter((p) => !query || p.name.toLowerCase().includes(query.toLowerCase()))

  const create = async (name: string, tid: string) => {
    const r = await api.createProject(name, tid)
    onOpenProject(r.project.id)
  }

  const remove = async (p: ProjectSummary) => {
    if (!confirm(`删除项目「${p.name}」？\n\n会连同该项目的图片文件一起删除，不可恢复。`)) return
    try {
      await api.deleteProject(p.id)
      load()
    } catch (e: any) {
      alert(`删除失败：${e.message || e}`)
    }
  }

  return (
    <div className="page">
      <div className="page-inner">
        <div className="page-head">
          <div>
            <div className="page-title">项目</div>
            <div className="page-sub">每个项目是一块无限画布：生成卡 + 结果卡按节点摆开，随时回到原位继续。</div>
          </div>
          <div className="grow" />
          <input className="input" style={{ width: 200 }} placeholder="搜索项目名…"
            value={query} onChange={(e) => setQuery(e.target.value)} />
          <button className="btn btn-primary" onClick={() => setModal(true)}>＋ 新建项目</button>
        </div>

        {error ? <div className="err">{error}</div> : null}

        {loading ? (
          <div className="hint">加载中…</div>
        ) : shown.length === 0 ? (
          <div className="empty">
            <span className="pixel">NO PROJECT YET</span>
            {query ? (
              <>没有匹配「{query}」的项目</>
            ) : (
              <>
                还没有项目。点右上角<b>新建项目</b>，选一个工作流模板开始——<br />
                第一个模板「像素精灵 · 文字生图」已经可用。
              </>
            )}
          </div>
        ) : (
          <div className="proj-grid">
            {shown.map((p) => (
              <div className="proj-card" key={p.id} onClick={() => onOpenProject(p.id)}>
                <div className="proj-thumb">
                  {p.thumbnail
                    ? <img src={p.thumbnail} alt={p.name} />
                    : <span className="none">NO IMAGE</span>}
                </div>
                <div className="proj-body">
                  <div className="proj-name">
                    <span className="grow">{p.name}</span>
                    {p.templateDraftOf ? (
                      <span className="tag yellow" title="这是某个模板的编辑草稿：打开画布改完，点「保存为模板」写回">
                        模板编辑中
                      </span>
                    ) : null}
                  </div>
                  <div className="proj-meta">
                    <span>{tplName(p.templateId)}</span>
                    <span>·</span>
                    <span>{p.resultCount} 次生成 / {p.imageCount} 张图</span>
                  </div>
                  <div className="proj-meta">
                    <span>更新 {fmt(p.updatedAt)}</span>
                    <span>·</span>
                    <span>{p.sizeMB} MB</span>
                  </div>
                </div>
                <div className="proj-acts" onClick={(e) => e.stopPropagation()}>
                  <button className="btn btn-sm" onClick={() => onOpenProject(p.id)}>打开画布</button>
                  <div className="grow" />
                  <button className="btn btn-sm btn-danger" onClick={() => remove(p)}>删除</button>
                </div>
              </div>
            ))}
            <button className="proj-new" onClick={() => setModal(true)}>
              <span className="plus">＋</span>
              <span>新建项目</span>
              <span className="hint" style={{ maxWidth: 200 }}>选工作流模板后创建画布</span>
            </button>
          </div>
        )}
      </div>

      <NewProjectModal
        open={modal}
        templates={templates}
        onClose={() => setModal(false)}
        onCreate={create}
      />
    </div>
  )
}
