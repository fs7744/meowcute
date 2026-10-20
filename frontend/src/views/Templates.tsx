import { useEffect, useState } from 'react'
import { api } from '../api'
import { HeroCat, PixelIcon } from '../components/CatLogo'
import NewProjectModal from '../components/NewProjectModal'
import type { Template } from '../types'

type Props = {
  onOpenProject: (id: string) => void
  onGoProjects: () => void
}

const WHY = [
  {
    title: '提示词契约',
    tag: '生成侧',
    body: '像素画不是形容词而是规格：精确网格、每格单色、限定色板、无抗锯齿、硬轮廓。画风词前置定调，契约句收尾压细节——两句都实测过位置。',
  },
  {
    title: 'FFT 网格对齐',
    tag: '后处理',
    body: 'AI 图的「像素块」边界通常不齐。FFT 检测真实网格、Sobel 把网格线吸附到梯度峰、每格多数表决取主色，边界不再切在块中间。',
  },
  {
    title: '共享调色板',
    tag: '后处理',
    body: '同一批图共用一块色板（频次加权最远点采样），避免逐张各自量化导致的偏色；量化后色板外颜色数恒为 0，是验收指标不是口号。',
  },
]

export default function Templates({ onOpenProject, onGoProjects }: Props) {
  const [templates, setTemplates] = useState<Template[]>([])
  const [modal, setModal] = useState<{ open: boolean; tid?: string }>({ open: false })
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.templates().then((r) => setTemplates(r.templates)).catch((e) => setError(String(e.message || e)))
  }, [])

  const available = templates.filter((t) => t.status === 'available')
  const planned = templates.filter((t) => t.status !== 'available')

  const create = async (name: string, tid: string) => {
    const r = await api.createProject(name, tid)
    onOpenProject(r.project.id)
  }

  return (
    <div className="page">
      <div className="page-inner">
        <div className="hero">
          <div className="hero-main">
            <div className="hero-kicker">MEOW CUTE · AI SPRITE WORKBENCH</div>
            <h1 className="hero-title">
              把一句话，锻成<em>能用的像素精灵</em>
            </h1>
            <p className="hero-desc">
              本地模型出图，确定性管线收尾：网格对齐、每格单色、共享调色板。
              无限画布上每张生成卡与结果卡都是可拖动、可留档的节点——同一条提示词的不同尝试并排摆着，
              差别一眼可见。
            </p>
            <div className="hero-acts">
              <button className="btn btn-primary" onClick={() => setModal({ open: true })}>
                用模板新建项目
              </button>
              <button className="btn" onClick={onGoProjects}>查看我的项目</button>
            </div>
          </div>
          <div className="hero-cat"><HeroCat /></div>
        </div>

        {error ? <div className="err">{error}</div> : null}

        <div className="section-head">
          <div className="section-title">工作流模板</div>
          <div className="section-sub">新建项目必须先选一个模板；模板决定画布上的节点链</div>
        </div>

        <div className="tpl-grid">
          {available.map((t) => (
            <div className="tpl-card available" key={t.id}>
              <div className="tpl-card-head">
                <span className="tpl-icon" style={{ color: t.accent }}>
                  <PixelIcon kind={t.icon} color={t.accent} />
                </span>
                <div className="grow">
                  <div className="tpl-name">{t.name}</div>
                  <div className="tpl-tagline">{t.tagline}</div>
                </div>
                <span className="tag">可用</span>
              </div>

              <div className="desc">{t.description}</div>

              <ul className="tpl-highlights">
                {t.highlights.map((h) => <li key={h}>{h}</li>)}
              </ul>

              <div className="tpl-flows">
                {t.steps.map((s, i) => (
                  <span key={s.n} style={{ display: 'contents' }}>
                    <span className="flow-step" title={s.detail}><b>{String(s.n).padStart(2, '0')}</b>{s.title}</span>
                    {i < t.steps.length - 1 ? <span className="flow-arrow">→</span> : null}
                  </span>
                ))}
              </div>

              <div className="tpl-io">
                <div><b>输入</b> · {t.inputs.map((i) => i.label + (i.required ? '' : '（可选）')).join(' · ')}</div>
                <div><b>输出</b> · {t.outputs.map((o) => o.label).join(' · ')}</div>
              </div>

              <div className="tpl-actions">
                <button className="btn btn-primary btn-sm" style={{ height: 34 }}
                  onClick={() => setModal({ open: true, tid: t.id })}>
                  用此模板新建项目
                </button>
                <button className="btn btn-sm btn-ghost" onClick={() => setModal({ open: true, tid: t.id })}>
                  查看参数
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="section-head">
          <div className="section-title">像素效果为什么可控</div>
          <div className="section-sub">三步都不是「调参玄学」，每一步都有可验证的产出指标</div>
        </div>
        <div className="tpl-grid">
          {WHY.map((w) => (
            <div className="tpl-card" key={w.title} style={{ gap: 8 }}>
              <div className="row between">
                <div className="tpl-name" style={{ fontSize: 15 }}>{w.title}</div>
                <span className="tag pink">{w.tag}</span>
              </div>
              <div className="desc" style={{ fontSize: 12.5 }}>{w.body}</div>
            </div>
          ))}
        </div>

        {planned.length ? (
          <>
            <div className="section-head">
              <div className="section-title">规划中的模板</div>
              <div className="section-sub">先亮出路线，不摆点了没反应的入口</div>
            </div>
            <div className="tpl-grid">
              {planned.map((t) => (
                <div className="tpl-card planned" key={t.id}>
                  <div className="tpl-card-head">
                    <span className="tpl-icon" style={{ color: t.accent }}>
                      <PixelIcon kind={t.icon} color={t.accent} />
                    </span>
                    <div className="grow">
                      <div className="tpl-name">{t.name}</div>
                      <div className="tpl-tagline">{t.tagline}</div>
                    </div>
                    <span className="tag gray">规划中</span>
                  </div>
                  <div className="desc">{t.description}</div>
                  <div className="hint">{t.notes?.[0]}</div>
                </div>
              ))}
            </div>
          </>
        ) : null}

        <div className="section-head">
          <div className="section-title">本地优先</div>
        </div>
        <div className="hint" style={{ lineHeight: 1.9, maxWidth: 860 }}>
          生成使用项目 models/ 目录下的本地权重（Z-Image-Turbo 文生图 / Qwen-Image-Edit 参考图编辑），
          数据全部落在本机 data/ 目录，不上传任何素材。设置页可查看模型体检与显存状态；
          未来接入第三方接口时，只需替换生成端，像素精修与项目结构不变。
        </div>
      </div>

      <NewProjectModal
        open={modal.open}
        templates={templates}
        defaultTemplateId={modal.tid}
        onClose={() => setModal({ open: false })}
        onCreate={create}
      />
    </div>
  )
}
