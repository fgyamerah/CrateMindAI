import type { TrackSummary } from '../../types/track'
import { formatDuration } from '../../lib/format'

interface Props {
  tracks: TrackSummary[]
  onOpen: (id: number) => void
  selection: Set<number>
  onToggleSelect: (id: number, checked: boolean) => void
}

/** Compact card list used on phone-width viewports instead of the full table. */
export default function TrackCardList({ tracks, onOpen, selection, onToggleSelect }: Props) {
  return (
    <div className="lib-cards" data-testid="track-card-list">
      {tracks.map((t) => (
        <div key={t.id} className="lib-card" data-testid={`track-card-${t.id}`}>
          <input
            type="checkbox"
            className="lib-card-check"
            checked={selection.has(t.id)}
            onChange={(e) => onToggleSelect(t.id, e.target.checked)}
            aria-label={`Select ${t.artist ?? 'unknown'} — ${t.title ?? t.filename}`}
          />
          <button type="button" className="lib-card-body" onClick={() => onOpen(t.id)}>
            <span className="lib-card-title">{t.title ?? <span className="lib-missing">missing title</span>}</span>
            <span className="lib-card-artist">{t.artist ?? <span className="lib-missing">missing artist</span>}</span>
            <span className="lib-card-meta">
              <span className="lib-mono">{t.bpm !== null ? `${t.bpm.toFixed(1)} BPM` : 'no BPM'}</span>
              <span className="lib-mono">{t.key_camelot ?? 'no key'}</span>
              <span className="lib-mono">{formatDuration(t.duration_sec)}</span>
              {t.issues.length > 0 && (
                <span className="lib-issue-badge">{t.issues.length} issue{t.issues.length > 1 ? 's' : ''}</span>
              )}
            </span>
          </button>
        </div>
      ))}
    </div>
  )
}
