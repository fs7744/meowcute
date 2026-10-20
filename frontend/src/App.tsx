import { useCallback, useEffect, useState } from 'react'
import { api } from './api'
import CatLogo from './components/CatLogo'
import Canvas from './views/Canvas'
import Projects from './views/Projects'
import Settings from './views/Settings'
import Templates from './views/Templates'

type Route = { kind: 'templates' } | { kind: 'projects' } | { kind: 'settings' } | { kind: 'canvas'; id: string }

const parseHash = (): Route => {
  const h = (location.hash || '').replace(/^#\/?/, '')
  if (h.startsWith('p/')) {
    const id = h.slice(2)
    if (id) return { kind: 'canvas', id }
  }
  if (h === 'projects') return { kind: 'projects' }
  if (h === 'settings') return { kind: 'settings' }
  if (h === 'templates') return { kind: 'templates' }
  return { kind: 'projects' }
}

const toHash = (r: Route): string => {
  switch (r.kind) {
    case 'canvas': return `#/p/${r.id}`
    case 'projects': return '#/projects'
    case 'settings': return '#/settings'
    default: return '#/templates'
  }
}

type Health = { ok: boolean; version: string; modelCount: number; families: string[]; dataDir: string }

export default function App() {
  const [route, setRoute] = useState<Route>(() => parseHash())
  const [health, setHealth] = useState<Health | null>(null)
  const [offline, setOffline] = useState(false)

  // 路由 ↔ hash 同步：刷新页面还在原页面，链接也能直接分享
  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    if (!location.hash) location.hash = toHash(route)
    return () => window.removeEventListener('hashchange', onHash)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const go = useCallback((r: Route) => {
    setRoute(r)
    const h = toHash(r)
    if (location.hash !== h) location.hash = h
  }, [])

  const ping = useCallback(() => {
    api.health()
      .then((h) => { setHealth(h); setOffline(false) })
      .catch(() => setOffline(true))
  }, [])

  useEffect(() => {
    ping()
    const t = setInterval(ping, 20_000)
    return () => clearInterval(t)
  }, [ping])

  const tab: 'templates' | 'projects' | 'settings' | 'canvas' = route.kind

  return (
    <div className="app">
      <nav className="nav">
        <div className="logo" onClick={() => go({ kind: 'templates' })} title="回到模板说明">
          <CatLogo size={32} />
          <span className="logo-name">
            <span className="logo-en">MEOWCUTE</span>
            <span className="logo-cn">喵喵叫的可爱助手</span>
          </span>
        </div>

        <div className="nav-tabs">
          <button className={`nav-tab ${tab === 'templates' ? 'on' : ''}`} onClick={() => go({ kind: 'templates' })}>
            模板
          </button>
          <button className={`nav-tab ${tab === 'projects' ? 'on' : ''}`} onClick={() => go({ kind: 'projects' })}>
            项目
          </button>
          <button className={`nav-tab ${tab === 'settings' ? 'on' : ''}`} onClick={() => go({ kind: 'settings' })}>
            设置
          </button>
        </div>

        <div className="nav-spacer" />

        <button
          className={`nav-status ${offline ? 'off' : health?.ok ? '' : 'warn'}`}
          onClick={() => go({ kind: 'settings' })}
          title={offline ? '后端未连接' : `数据目录：${health?.dataDir || '—'}`}
        >
          <span className="dot" />
          {offline
            ? '后端未连接'
            : health
              ? `v${health.version} · ${health.modelCount} 个模型`
              : '连接中…'}
        </button>

        <div className="nav-avatar" title="本地优先，图片不出机器">MEOW</div>
      </nav>

      {route.kind === 'canvas' ? (
        <Canvas projectId={route.id} onBack={() => go({ kind: 'projects' })} />
      ) : route.kind === 'templates' ? (
        <Templates
          onOpenProject={(id) => go({ kind: 'canvas', id })}
          onGoProjects={() => go({ kind: 'projects' })}
        />
      ) : route.kind === 'projects' ? (
        <Projects onOpenProject={(id) => go({ kind: 'canvas', id })} />
      ) : (
        <Settings />
      )}
    </div>
  )
}
