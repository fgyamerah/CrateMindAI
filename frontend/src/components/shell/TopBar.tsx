import { Menu } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'
import { fetchHealth } from '../../api/health'
import { getRuntimePreflight } from '../../api/runtime'
import { preflightStatusMeta } from '../../lib/status'
import StatusPill from '../ui/StatusPill'

interface Props {
  onOpenDrawer: () => void
}

export default function TopBar({ onOpenDrawer }: Props) {
  const health = useQuery({ queryKey: ['health'], queryFn: fetchHealth })
  const preflight = useQuery({
    queryKey: ['runtime-preflight'],
    queryFn: getRuntimePreflight,
    refetchInterval: 60_000,
  })

  const meta = preflight.isError
    ? { label: 'Status unavailable', tone: 'neutral' as const }
    : preflight.data
      ? preflightStatusMeta(preflight.data.status)
      : null

  return (
    <header className="cm-topbar">
      <button
        type="button"
        className="cm-icon-btn cm-hamburger"
        onClick={onOpenDrawer}
        aria-label="Open navigation menu"
        data-testid="open-nav-drawer-button"
      >
        <Menu size={18} />
      </button>
      <div className="cm-topbar-root" data-testid="topbar-library-root">
        {health.data ? health.data.library_root : health.isError ? 'library root unavailable' : ''}
      </div>
      <div className="cm-topbar-spacer" />
      {meta ? (
        <StatusPill tone={meta.tone} label={meta.label} testId="topbar-readiness-pill" />
      ) : (
        <div className="cm-skeleton" style={{ width: 100, height: 22, borderRadius: 999 }} />
      )}
    </header>
  )
}
