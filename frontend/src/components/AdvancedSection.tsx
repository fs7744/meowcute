import { useState, type ReactNode } from 'react'
import { useViewMode } from '../lib/viewMode'

type Props = {
  /** 折叠行上显示的标题（简略模式）／分组标题（专业模式） */
  title?: string
  /** 一句话说明「里面有什么」，让用户知道值不值得点开 */
  hint?: string
  /** 专业模式下也允许再折一层（极少用），默认常开 */
  collapsibleInPro?: boolean
  children: ReactNode
}

/**
 * 高级参数分组。
 *
 * 三种行为：
 * - **专业模式**：直接铺开（带分组标题，便于找），不再折叠；
 * - **简略模式 + 收起**：只留一行「更多设置 · 步数 / 种子 ▾」，点开才看见；
 * - **简略模式 + 展开**：和平时一样。
 *
 * 关键取舍是「收起时仍要露出里面有什么」（hint）：把高级项做成一个没有
 * 文字的「高级」按钮，等于把它们从界面上删掉——专家找不到就会换工具。
 * 露出摘要之后，小白知道有这回事但不被数字淹没，专家一眼就知道去哪找。
 */
export default function AdvancedSection({
  title = '更多设置',
  hint,
  collapsibleInPro = false,
  children,
}: Props) {
  const mode = useViewMode()
  const [open, setOpen] = useState(false)

  if (mode === 'pro') {
    if (!collapsibleInPro) {
      return (
        <section className="adv-section pro">
          <div className="adv-head static">
            <span className="adv-title">{title}</span>
            {hint ? <span className="adv-hint">{hint}</span> : null}
          </div>
          <div className="adv-body">{children}</div>
        </section>
      )
    }
    return (
      <section className={`adv-section pro ${open ? 'open' : ''}`}>
        <button type="button" className="adv-head" onClick={() => setOpen((o) => !o)}>
          <span className="adv-title">{title}</span>
          {hint ? <span className="adv-hint">{hint}</span> : null}
          <span className="adv-caret">{open ? '▴' : '▾'}</span>
        </button>
        {open ? <div className="adv-body">{children}</div> : null}
      </section>
    )
  }

  return (
    <section className={`adv-section ${open ? 'open' : ''}`}>
      <button type="button" className="adv-head" onClick={() => setOpen((o) => !o)}
        aria-expanded={open}>
        <span className="adv-title">{title}</span>
        {hint ? <span className="adv-hint">{hint}</span> : null}
        <span className="adv-caret">{open ? '▴' : '▾'}</span>
      </button>
      {open ? <div className="adv-body">{children}</div> : null}
    </section>
  )
}

/**
 * 分组小标题（只在专业模式显示）。
 *
 * 简略模式下用户打开「更多设置」就是想知道「还有哪些能调」，一进去先撞见
 * 三四个小标题反而更乱；专业模式需要按组扫视，标题是必需品。所以同一个
 * 分组结构在两种模式下表现不同。
 */
export function ParamGroup({ label, children }: { label: string; children: ReactNode }) {
  const mode = useViewMode()
  if (mode !== 'pro') return <>{children}</>
  return (
    <div className="pgroup">
      <div className="pgroup-title">{label}</div>
      {children}
    </div>
  )
}
