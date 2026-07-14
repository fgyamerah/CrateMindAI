import { NavLink } from 'react-router-dom'
import {
  LayoutDashboard,
  Library,
  BarChart3,
  FolderTree,
  AlertTriangle,
  Wrench,
  Eraser,
  Sparkles,
  Activity,
  ClipboardList,
  Music,
  Download,
  HardDrive,
  ListChecks,
  Database,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface NavItem {
  to: string
  label: string
  Icon: LucideIcon
  end?: boolean
}

interface NavSection {
  title: string
  items: NavItem[]
}

export const NAV_SECTIONS: NavSection[] = [
  {
    title: 'Overview',
    items: [{ to: '/', label: 'Home', Icon: LayoutDashboard, end: true }],
  },
  {
    title: 'Library',
    items: [
      { to: '/library', label: 'Tracks', Icon: Library },
      { to: '/quality', label: 'Quality', Icon: BarChart3 },
      { to: '/folders', label: 'Folders', Icon: FolderTree },
    ],
  },
  {
    title: 'Fix & Review',
    items: [
      { to: '/issues', label: 'Issues', Icon: AlertTriangle },
      { to: '/metadata-repair', label: 'Metadata Repair', Icon: Wrench },
      { to: '/metadata-sanitation', label: 'Sanitation', Icon: Eraser },
      { to: '/enrichment', label: 'Enrichment', Icon: Sparkles },
      { to: '/bpm-review', label: 'BPM Review', Icon: Activity },
      { to: '/audit', label: 'Audit', Icon: ClipboardList },
    ],
  },
  {
    title: 'Sets',
    items: [{ to: '/set-builder', label: 'Set Builder', Icon: Music }],
  },
  {
    title: 'Publish',
    items: [
      { to: '/exports', label: 'Export', Icon: Download },
      { to: '/sync', label: 'SSD Sync', Icon: HardDrive },
    ],
  },
  {
    title: 'Operations',
    items: [
      { to: '/jobs', label: 'Jobs', Icon: ListChecks },
      { to: '/reconciliation', label: 'Reconciliation', Icon: Database },
    ],
  },
]

interface Props {
  collapsed?: boolean
  onNavigate?: () => void
  testIdPrefix?: string
}

export default function SidebarNav({ collapsed = false, onNavigate, testIdPrefix = 'nav' }: Props) {
  return (
    <div className="cm-sidebar-sections">
      {NAV_SECTIONS.map(({ title, items }) => (
        <section key={title} aria-label={title}>
          <div className="cm-nav-section-title">{title}</div>
          <ul className="cm-nav">
            {items.map(({ to, label, Icon, end }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={end}
                  onClick={onNavigate}
                  data-testid={`${testIdPrefix}-${label.toLowerCase().replace(/\s+/g, '-')}`}
                  className={({ isActive }) =>
                    `cm-nav-link${isActive ? ' cm-nav-link--active' : ''}`
                  }
                  title={collapsed ? label : undefined}
                  aria-label={collapsed ? label : undefined}
                >
                  <Icon size={16} className="cm-nav-icon" strokeWidth={1.8} aria-hidden="true" />
                  <span className="cm-nav-label">{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
