import { useState, useRef } from 'react'
import { RefreshCw } from 'lucide-react'
import { useTracks } from '../hooks/useTracks'
import TrackPanel from '../components/TrackPanel'
import PageHeader from '../components/PageHeader'
import ErrorBanner from '../components/ErrorBanner'
import type { TrackListParams, TrackSummary } from '../types/track'
import { ISSUE_LABELS } from '../types/track'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function fmtBpm(bpm: number | null): string {
  if (bpm == null) return '—'
  return bpm.toFixed(1)
}

function fmtDuration(sec: number | null): string {
  if (!sec) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

type SortKey = 'artist' | 'title' | 'bpm' | 'processed_at' | 'filename'

interface SortState {
  key: SortKey
  order: 'asc' | 'desc'
}

// ---------------------------------------------------------------------------
// Stats bar
// ---------------------------------------------------------------------------

interface StatsBarProps {
  total: number
  byStatus: Record<string, number>
  missingBpm: number
  missingKey: number
}

function StatsBar({ total, byStatus, missingBpm, missingKey }: StatsBarProps) {
  return (
    <div className="track-stats-bar">
      <span className="track-stat">{total.toLocaleString()} tracks</span>
      {(byStatus['ok'] ?? 0) > 0 && (
        <span className="track-stat track-stat--ok">{byStatus['ok']} ok</span>
      )}
      {(byStatus['error'] ?? 0) > 0 && (
        <span className="track-stat track-stat--error">{byStatus['error']} errors</span>
      )}
      {missingBpm > 0 && (
        <span className="track-stat track-stat--warn">{missingBpm} missing BPM</span>
      )}
      {missingKey > 0 && (
        <span className="track-stat track-stat--warn">{missingKey} missing key</span>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Column header with sort toggle
// ---------------------------------------------------------------------------

interface ThProps {
  label: string
  sortKey?: SortKey
  sort: SortState
  onSort: (key: SortKey) => void
}

function Th({ label, sortKey, sort, onSort }: ThProps) {
  if (!sortKey) return <th>{label}</th>
  const active = sort.key === sortKey
  return (
    <th
      className={`th-sortable ${active ? 'th-sortable--active' : ''}`}
      onClick={() => onSort(sortKey)}
      title={`Sort by ${label}`}
    >
      {label}
      <span className="sort-indicator">
        {active ? (sort.order === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}
      </span>
    </th>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Tracks() {
  // --- Filter state ---
  const [rawQ, setRawQ]             = useState('')
  const [q, setQ]                   = useState('')
  const qTimer                      = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [status, setStatus]         = useState('')
  const [qualityTier, setQualityTier] = useState('')
  const [bpmMin, setBpmMin]         = useState('')
  const [bpmMax, setBpmMax]         = useState('')
  const [sort, setSort]             = useState<SortState>({ key: 'artist', order: 'asc' })
  const [offset, setOffset]         = useState(0)

  const LIMIT = 100

  // Debounce search input
  function handleQChange(val: string) {
    setRawQ(val)
    if (qTimer.current) clearTimeout(qTimer.current)
    qTimer.current = setTimeout(() => {
      setQ(val)
      setOffset(0)
    }, 300)
  }

  const params: TrackListParams = {
    q:            q || undefined,
    status:       status || undefined,
    quality_tier: qualityTier || undefined,
    bpm_min:      bpmMin ? parseFloat(bpmMin) : undefined,
    bpm_max:      bpmMax ? parseFloat(bpmMax) : undefined,
    sort:         sort.key,
    order:        sort.order,
    limit:        LIMIT,
    offset,
  }

  const { tracks, stats, loading, error, refresh } = useTracks(params)

  const [selectedTrack, setSelectedTrack] = useState<TrackSummary | null>(null)

  // --- Sort handler ---
  function handleSort(key: SortKey) {
    setSort((prev) =>
      prev.key === key
        ? { key, order: prev.order === 'asc' ? 'desc' : 'asc' }
        : { key, order: 'asc' },
    )
    setOffset(0)
  }

  // --- Filter reset ---
  function resetFilters() {
    setRawQ('')
    setQ('')
    setStatus('')
    setQualityTier('')
    setBpmMin('')
    setBpmMax('')
    setOffset(0)
  }

  const hasFilters = rawQ || status || qualityTier || bpmMin || bpmMax
  const total = stats?.total ?? 0

  return (
    <div className="page">
      <PageHeader
        title="Tracks"
        subtitle={stats ? `${total.toLocaleString()} tracks in library` : undefined}
        actions={
          <>
            {loading && <span className="muted" style={{ fontSize: 12 }}>Loading…</span>}
            <button className="btn btn--ghost btn--sm" onClick={refresh}>
              <RefreshCw size={13} />
              Refresh
            </button>
          </>
        }
      />

      <ErrorBanner message={error} />

      {/* Stats bar */}
      {stats && (
        <StatsBar
          total={total}
          byStatus={stats.by_status}
          missingBpm={stats.missing_bpm}
          missingKey={stats.missing_key}
        />
      )}

      {/* Filter bar */}
      <div className="filter-bar">
        <input
          className="filter-input filter-input--search"
          type="search"
          placeholder="Search artist, title, filename…"
          value={rawQ}
          onChange={(e) => handleQChange(e.target.value)}
          aria-label="Search tracks"
        />

        <select
          className="filter-select"
          value={status}
          onChange={(e) => { setStatus(e.target.value); setOffset(0) }}
          aria-label="Filter by status"
        >
          <option value="">All statuses</option>
          <option value="ok">ok</option>
          <option value="pending">pending</option>
          <option value="error">error</option>
          <option value="needs_review">needs_review</option>
          <option value="rejected">rejected</option>
          <option value="duplicate">duplicate</option>
          <option value="stale">stale</option>
        </select>

        <select
          className="filter-select"
          value={qualityTier}
          onChange={(e) => { setQualityTier(e.target.value); setOffset(0) }}
          aria-label="Filter by quality"
        >
          <option value="">All quality</option>
          <option value="LOSSLESS">LOSSLESS</option>
          <option value="HIGH">HIGH</option>
          <option value="MEDIUM">MEDIUM</option>
          <option value="LOW">LOW</option>
          <option value="UNKNOWN">UNKNOWN</option>
        </select>

        <div className="filter-bpm-range">
          <input
            className="filter-input filter-input--bpm"
            type="number"
            placeholder="BPM min"
            min={0}
            max={300}
            value={bpmMin}
            onChange={(e) => { setBpmMin(e.target.value); setOffset(0) }}
            aria-label="BPM minimum"
          />
          <span className="filter-bpm-sep">–</span>
          <input
            className="filter-input filter-input--bpm"
            type="number"
            placeholder="BPM max"
            min={0}
            max={300}
            value={bpmMax}
            onChange={(e) => { setBpmMax(e.target.value); setOffset(0) }}
            aria-label="BPM maximum"
          />
        </div>

        {hasFilters && (
          <button className="btn btn--ghost btn--sm" onClick={resetFilters}>
            Clear
          </button>
        )}
      </div>

      {/* Table */}
      <section className="section">
        <div className="card" style={{ padding: 0 }}>
          {!loading && tracks.length === 0 ? (
            <p className="empty-state" style={{ padding: '20px 24px' }}>
              {hasFilters
                ? 'No tracks match the current filters.'
                : 'No tracks in the library yet. Run the pipeline to populate.'}
            </p>
          ) : (
            <div className="table-wrapper">
              <table className="table table--tracks">
                <thead>
                  <tr>
                    <Th label="Artist"  sortKey="artist"   sort={sort} onSort={handleSort} />
                    <Th label="Title"   sortKey="title"    sort={sort} onSort={handleSort} />
                    <Th label="BPM"     sortKey="bpm"      sort={sort} onSort={handleSort} />
                    <th>Key</th>
                    <th>Genre</th>
                    <th>Dur</th>
                    <th>Quality</th>
                    <th>Status</th>
                    <th>Flags</th>
                  </tr>
                </thead>
                <tbody>
                  {tracks.map((track) => (
                    <tr
                      key={track.id}
                      className={[
                        'track-row',
                        track.issues.length > 0 ? 'track-row--has-issues' : '',
                        track.status === 'error' ? 'row--failed' : '',
                        selectedTrack?.id === track.id ? 'track-row--selected' : '',
                      ].filter(Boolean).join(' ')}
                      onClick={() =>
                        setSelectedTrack(selectedTrack?.id === track.id ? null : track)
                      }
                      title="Click to view detail"
                    >
                      <td className="td-artist">
                        {track.artist ?? <span className="muted">(none)</span>}
                      </td>
                      <td className="td-title">
                        {track.title ?? <span className="muted">{track.filename}</span>}
                      </td>
                      <td className="td-bpm">{fmtBpm(track.bpm)}</td>
                      <td className="td-key">
                        {track.key_camelot ?? track.key_musical ?? '—'}
                      </td>
                      <td className="muted">{track.genre ?? '—'}</td>
                      <td className="muted nowrap">{fmtDuration(track.duration_sec)}</td>
                      <td>
                        {track.quality_tier ? (
                          <span className={`quality-badge quality-badge--${track.quality_tier.toLowerCase()}`}>
                            {track.quality_tier}
                          </span>
                        ) : <span className="muted">—</span>}
                      </td>
                      <td>
                        <span className={`badge badge--track-${track.status}`}>
                          {track.status}
                        </span>
                      </td>
                      <td className="td-flags">
                        {track.issues.map((issue) => (
                          <span key={issue} className={`issue-flag issue-flag--${issue.replace(/_/g, '-')}`}
                            title={ISSUE_LABELS[issue]}>
                            {issueShort(issue)}
                          </span>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {/* Pagination */}
          {(total > LIMIT || offset > 0) && (
            <div className="pagination">
              <button
                className="btn btn--ghost btn--sm"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - LIMIT))}
              >
                ← Prev
              </button>
              <span className="pagination-info">
                {offset + 1}–{Math.min(offset + tracks.length, total)} of {total}
              </span>
              <button
                className="btn btn--ghost btn--sm"
                disabled={offset + tracks.length >= total}
                onClick={() => setOffset(offset + LIMIT)}
              >
                Next →
              </button>
            </div>
          )}
        </div>
      </section>

      {/* Detail panel */}
      {selectedTrack && (
        <TrackPanel
          track={selectedTrack}
          onClose={() => setSelectedTrack(null)}
        />
      )}
    </div>
  )
}

function issueShort(issue: string): string {
  const map: Record<string, string> = {
    missing_bpm:    '!BPM',
    missing_key:    '!KEY',
    missing_artist: '!ART',
    missing_title:  '!TTL',
    low_quality:    '!Q',
    error:          'ERR',
    needs_review:   'REV',
  }
  return map[issue] ?? issue
}
