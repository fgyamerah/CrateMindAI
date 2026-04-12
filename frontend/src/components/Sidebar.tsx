import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  ListTodo,
  Music,
  AudioWaveform,
  ListMusic,
  Upload,
  HardDrive,
  Settings,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface NavItem {
  to:    string
  label: string
  Icon:  LucideIcon
  end?:  boolean
}

const NAV: NavItem[] = [
  { to: '/',            label: 'Dashboard',  Icon: LayoutDashboard, end: true },
  { to: '/jobs',        label: 'Jobs',        Icon: ListTodo },
  { to: '/tracks',      label: 'Tracks',      Icon: Music },
  { to: '/bpm-review',  label: 'BPM Review',  Icon: AudioWaveform },
  { to: '/set-builder', label: 'Set Builder', Icon: ListMusic },
  { to: '/export',      label: 'Export',      Icon: Upload },
  { to: '/ssd-sync',    label: 'SSD Sync',    Icon: HardDrive },
  { to: '/settings',    label: 'Settings',    Icon: Settings },
]

export default function Sidebar() {
  return (
    <nav className="sidebar">
      <div className="sidebar-brand">
        <span className="sidebar-brand-icon">▶</span>
        DJ Toolkit
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
            >
              <Icon size={15} className="sidebar-icon" strokeWidth={1.75} />
              {label}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="sidebar-footer">v0.1.0</div>
    </nav>
  )
}
