import { NavLink } from 'react-router-dom'
import {
  Library,
  AlertTriangle,
  Sparkles,
  ClipboardList,
  FolderTree,
  ChevronLeft,
  ChevronRight,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface NavItem {
  to:    string
  label: string
  Icon:  LucideIcon
  end?:  boolean
}

const NAV: NavItem[] = [
  { to: '/',             label: 'Library',          Icon: Library,       end: true },
  { to: '/issues',       label: 'Issues',           Icon: AlertTriangle },
  { to: '/enrichment',   label: 'Enrichment Queue', Icon: Sparkles },
  { to: '/audit',        label: 'Audit',            Icon: ClipboardList },
  { to: '/folders',      label: 'Folders',          Icon: FolderTree },
]

interface Props {
  collapsed: boolean
  onToggle:  () => void
}

export default function Sidebar({ collapsed, onToggle }: Props) {
  return (
    <nav className={`sidebar${collapsed ? ' sidebar--collapsed' : ''}`}>
      <div className="sidebar-brand">
        {!collapsed && (
          <>
            <span className="sidebar-brand-icon">▶</span>
            <span className="sidebar-brand-name">CrateMindAI</span>
          </>
        )}
        <button
          className="sidebar-collapse-btn"
          onClick={onToggle}
          title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
        </button>
      </div>

      <ul className="sidebar-nav">
        {NAV.map(({ to, label, Icon, end }) => (
          <li key={to}>
            <NavLink
              to={to}
              end={end}
              className={({ isActive }) =>
                ['sidebar-link', isActive ? 'sidebar-link--active' : ''].join(' ').trim()
              }
              title={collapsed ? label : undefined}
            >
              <Icon size={15} className="sidebar-icon" strokeWidth={1.75} />
              {!collapsed && <span className="sidebar-link-label">{label}</span>}
            </NavLink>
          </li>
        ))}
      </ul>

      {!collapsed && <div className="sidebar-footer">v0.1.0</div>}
    </nav>
  )
}
