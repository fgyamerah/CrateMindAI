import { memo } from 'react'
import type { TrackSummary } from '../../types/track'
import { ISSUE_LABELS } from '../../types/track'
import { COLUMNS } from './columns'
import { formatDuration } from '../../lib/format'

interface Props {
  track: TrackSummary
  visible: string[]
  selected: boolean
  active: boolean
  onToggleSelect: (id: number, checked: boolean) => void
  onOpen: (id: number) => void
  gridTemplate: string
}

function folderOf(filepath: string): string {
  const parts = filepath.split('/')
  return parts.slice(-3, -1).join('/')
}

function cellContent(track: TrackSummary, columnId: string) {
  switch (columnId) {
    case 'artist':
      return track.artist ?? <span className="lib-missing">missing</span>
    case 'title':
      return track.title ?? <span className="lib-missing">missing</span>
    case 'genre':
      return track.genre ?? <span className="lib-missing">—</span>
    case 'bpm':
      return track.bpm !== null ? (
        <span className="lib-mono">{track.bpm.toFixed(1)}</span>
      ) : (
        <span className="lib-missing">—</span>
      )
    case 'key':
      return track.key_camelot ? (
        <span className="lib-mono">{track.key_camelot}</span>
      ) : (
        <span className="lib-missing">—</span>
      )
    case 'duration':
      return <span className="lib-mono">{formatDuration(track.duration_sec)}</span>
    case 'bitrate':
      return track.bitrate_kbps ? (
        <span className="lib-mono">{track.bitrate_kbps}k</span>
      ) : (
        <span className="lib-missing">—</span>
      )
    case 'issues':
      return track.issues.length === 0 ? (
        <span className="lib-ok" aria-label="No issues">✓</span>
      ) : (
        <span className="lib-issue-badges">
          {track.issues.slice(0, 2).map((issue) => (
            <span key={issue} className="lib-issue-badge">
              {ISSUE_LABELS[issue] ?? issue}
            </span>
          ))}
          {track.issues.length > 2 && (
            <span className="lib-issue-badge lib-issue-badge--more">+{track.issues.length - 2}</span>
          )}
        </span>
      )
    case 'confidence':
      return track.parse_confidence ? (
        <span className={`lib-conf lib-conf--${track.parse_confidence.toLowerCase()}`}>
          {track.parse_confidence}
        </span>
      ) : (
        <span className="lib-missing">—</span>
      )
    case 'status':
      return <span className={`lib-status lib-status--${track.status}`}>{track.status.replace('_', ' ')}</span>
    case 'folder':
      return <span className="lib-mono lib-folder">{folderOf(track.filepath)}</span>
    default:
      return null
  }
}

function TrackRowInner({ track, visible, selected, active, onToggleSelect, onOpen, gridTemplate }: Props) {
  return (
    <div
      role="row"
      aria-selected={selected}
      className={`lib-row${selected ? ' lib-row--selected' : ''}${active ? ' lib-row--active' : ''}`}
      style={{ gridTemplateColumns: gridTemplate }}
      onClick={() => onOpen(track.id)}
      data-testid={`track-row-${track.id}`}
      data-rowid={track.id}
    >
      <div role="gridcell" className="lib-cell lib-cell--check" onClick={(e) => e.stopPropagation()}>
        <input
          type="checkbox"
          checked={selected}
          onChange={(e) => onToggleSelect(track.id, e.target.checked)}
          aria-label={`Select ${track.artist ?? 'unknown'} — ${track.title ?? track.filename}`}
          data-testid={`track-select-${track.id}`}
        />
      </div>
      {COLUMNS.filter((c) => visible.includes(c.id)).map((col) => (
        <div
          role="gridcell"
          key={col.id}
          className={`lib-cell${col.align === 'right' ? ' lib-cell--right' : ''}`}
        >
          {cellContent(track, col.id)}
        </div>
      ))}
    </div>
  )
}

export default memo(TrackRowInner)
