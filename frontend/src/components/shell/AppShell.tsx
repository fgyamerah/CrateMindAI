import { useCallback, useEffect, useState } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import { PanelLeftClose, PanelLeftOpen, X, Disc3 } from 'lucide-react'
import SidebarNav from './SidebarNav'
import TopBar from './TopBar'

const COLLAPSE_KEY = 'cratemindai.sidebar.collapsed'

function Brand() {
  return (
    <>
      <span className="cm-brand-mark" aria-hidden="true">
        <Disc3 size={16} strokeWidth={2} />
      </span>
      <span className="cm-brand-name">
        CrateMind<span>AI</span>
      </span>
    </>
  )
}

export default function AppShell() {
  const [collapsed, setCollapsed] = useState(() => localStorage.getItem(COLLAPSE_KEY) === '1')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const location = useLocation()

  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  useEffect(() => {
    if (!drawerOpen) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setDrawerOpen(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [drawerOpen])

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      localStorage.setItem(COLLAPSE_KEY, c ? '0' : '1')
      return !c
    })
  }, [])

  return (
    <div className={`cm-shell${collapsed ? ' cm-shell--collapsed' : ''}`}>
      <a href="#cm-main-content" className="cm-skip-link">
        Skip to content
      </a>

      <nav className="cm-sidebar" aria-label="Primary navigation" data-testid="app-sidebar">
        <div className="cm-sidebar-brand">
          <Brand />
          <div className="cm-topbar-spacer" />
          <button
            type="button"
            className="cm-icon-btn"
            onClick={toggleCollapsed}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            data-testid="sidebar-collapse-button"
          >
            {collapsed ? <PanelLeftOpen size={15} /> : <PanelLeftClose size={15} />}
          </button>
        </div>
        <SidebarNav collapsed={collapsed} />
        <div className="cm-sidebar-foot">v0.2.0 · local-first</div>
      </nav>

      {drawerOpen && (
        <>
          <div
            className="cm-drawer-backdrop"
            onClick={() => setDrawerOpen(false)}
            aria-hidden="true"
          />
          <nav
            className="cm-drawer"
            aria-label="Primary navigation"
            data-testid="mobile-nav-drawer"
          >
            <div className="cm-sidebar-brand">
              <Brand />
              <div className="cm-topbar-spacer" />
              <button
                type="button"
                className="cm-icon-btn"
                onClick={() => setDrawerOpen(false)}
                aria-label="Close navigation menu"
                data-testid="close-nav-drawer-button"
              >
                <X size={16} />
              </button>
            </div>
            <SidebarNav onNavigate={() => setDrawerOpen(false)} testIdPrefix="drawer-nav" />
          </nav>
        </>
      )}

      <div className="cm-main-col">
        <TopBar onOpenDrawer={() => setDrawerOpen(true)} />
        <main id="cm-main-content" className="cm-main app-main" data-testid="app-main">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
