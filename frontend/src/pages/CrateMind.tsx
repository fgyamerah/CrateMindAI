import { useCallback, useEffect, useMemo, useState } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  CheckCircle2,
  Check,
  Database,
  Folder,
  Download,
  ListFilter,
  SquareCheck,
  Clock3,
  RefreshCw,
  Search,
  X,
} from 'lucide-react'
import { fetchHealth } from '../api/health'
import {
  approveReview,
  applyApproved,
  deferReview,
  dryRunApplyApproved,
  fetchEnrichmentQueue,
  fetchLatestAudit,
  fetchReviewState,
  fetchReviewSummary,
  rejectReview,
} from '../api/insights'
import { fetchLibraryFolders, fetchLibraryOverview } from '../api/library'
import type { LibraryFolderStat, LibraryOverview } from '../api/library'
import { fetchTrack, fetchTrackIssues, fetchTrackPage } from '../api/tracks'
import type {
  ParseConfidence,
  TrackDetail,
  TrackIssue,
  TrackIssueCounts,
  TrackListParams,
  TrackPage,
  TrackSummary,
} from '../types/track'
import { ISSUE_LABELS } from '../types/track'
import type { HealthResponse } from '../api/health'
import type {
  EnrichmentQueueItem,
  EnrichmentQueueResponse,
  ApplyApprovedResponse,
  ReviewStateResponse,
  ReviewSummaryResponse,
  ReviewStatus,
} from '../api/insights'

type Section = 'library' | 'issues' | 'enrichment' | 'audit' | 'folders'
type SortKey = 'artist' | 'title' | 'bpm' | 'filename'
type SortOrder = 'asc' | 'desc'

const LIMIT = 50
const TRACK_ROW_HEIGHT = 42
const TRACK_TABLE_HEIGHT = 420
const TRACK_OVERSCAN = 6
const UI_STATE_KEY = 'cratemind.ui.v1'

interface PersistedUiState {
  search?: string
  offset?: number
  sort?: SortKey
  order?: SortOrder
  issueFilter?: TrackIssue | ''
  selectedId?: number | null
  section?: Section
  queueActionFilter?: 'auto_candidate' | 'review' | 'ignore' | ''
  queueConfidenceFilter?: 'HIGH' | 'MEDIUM' | 'LOW' | ''
  queueReviewFilter?: ReviewStatus | 'all'
}

function loadUiState(): PersistedUiState {
  try {
    const raw = window.localStorage.getItem(UI_STATE_KEY)
    return raw ? JSON.parse(raw) as PersistedUiState : {}
  } catch {
    return {}
  }
}

function persistUiState(next: PersistedUiState) {
  try {
    window.localStorage.setItem(UI_STATE_KEY, JSON.stringify(next))
  } catch {
    // localStorage may be unavailable in restricted browser contexts.
  }
}

const ISSUE_KEYS: Array<keyof TrackIssueCounts> = [
  'missing_artist',
  'missing_title',
  'weak_filename_parse',
  'suspicious_artist',
  'suspicious_title',
]

function sectionFromPath(pathname: string): Section {
  if (pathname.includes('/issues')) return 'issues'
  if (pathname.includes('/enrichment')) return 'enrichment'
  if (pathname.includes('/audit')) return 'audit'
  if (pathname.includes('/folders')) return 'folders'
  return 'library'
}

function pct(value: number, total: number): string {
  if (!total) return '0%'
  return `${Math.round((value / total) * 100)}%`
}

function pctValue(value: number, total: number): number {
  if (!total) return 0
  return Math.max(0, Math.min(100, (value / total) * 100))
}

function displayValue(value: unknown, fallback = '—'): string {
  if (value === null || value === undefined) return fallback
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed ? trimmed : fallback
  }
  if (typeof value === 'number' && Number.isNaN(value)) return fallback
  return String(value)
}

function shortIssue(issue: string): string {
  const map: Record<string, string> = {
    missing_artist: 'artist',
    missing_title: 'title',
    weak_filename_parse: 'parse',
    suspicious_artist: 'artist?',
    suspicious_title: 'title?',
    missing_bpm: 'bpm',
    missing_key: 'key',
    low_quality: 'quality',
    error: 'error',
    needs_review: 'review',
  }
  return map[issue] ?? issue
}

function confidenceClass(value: string | null | undefined): string {
  return `conf--${(value || 'unknown').toLowerCase()}`
}

function reviewClass(value: string | null | undefined): string {
  return `review--${(value || 'pending').toLowerCase()}`
}

function reviewLabel(value: string | null | undefined): string {
  return (value || 'pending').toLowerCase()
}

function queueRowKey(item: EnrichmentQueueItem, index: number): string {
  return `${item.track_id ?? item.filepath ?? 'queue'}-${index}`
}

function candidateLabel(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') {
    const trimmed = value.trim()
    return trimmed || '—'
  }
  if (typeof value === 'object') {
    const record = value as Record<string, unknown>
    const parts = [record.artist, record.title].filter(Boolean).map((part) => String(part))
    if (parts.length) return parts.join(' - ')
    return JSON.stringify(record)
  }
  return String(value)
}

function formatTimestamp(value: string | null | undefined): string {
  if (!value) return '—'
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString()
}

function TrackSortHeader({
  label,
  sortKey,
  sort,
  order,
  onSort,
}: {
  label: string
  sortKey?: SortKey
  sort: SortKey
  order: SortOrder
  onSort: (key: SortKey) => void
}) {
  if (!sortKey) return <th>{label}</th>
  const active = sort === sortKey
  return (
    <th className={`th-sortable${active ? ' th-sortable--active' : ''}`} onClick={() => onSort(sortKey)}>
      {label}
      <span className="sort-indicator">{active ? (order === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}</span>
    </th>
  )
}

function OverviewCards({ overview }: { overview: LibraryOverview | null }) {
  const total = overview?.total_tracks ?? 0
  const bpm = overview?.tracks_with_bpm ?? 0
  const camelot = overview?.tracks_with_camelot_key ?? 0
  const missingArtist = overview?.tracks_missing_artist ?? 0
  const missingTitle = overview?.tracks_missing_title ?? 0
  const parse = overview?.parse_confidence_breakdown ?? {}
  return (
    <div className="crate-card-grid">
      <div className="crate-metric">
        <span className="crate-metric-label">Total tracks</span>
        <strong>{total.toLocaleString()}</strong>
        <span className="crate-metric-sub">Read-only DB snapshot</span>
      </div>
      <div className="crate-metric">
        <span className="crate-metric-label">BPM coverage</span>
        <strong>{pct(bpm, total)}</strong>
        <div className="crate-meter"><span style={{ width: `${pctValue(bpm, total)}%` }} /></div>
        <span className="crate-metric-sub">{bpm.toLocaleString()} tracks with BPM</span>
      </div>
      <div className="crate-metric">
        <span className="crate-metric-label">Camelot coverage</span>
        <strong>{pct(camelot, total)}</strong>
        <div className="crate-meter"><span style={{ width: `${pctValue(camelot, total)}%` }} /></div>
        <span className="crate-metric-sub">{camelot.toLocaleString()} tracks with key</span>
      </div>
      <div className="crate-metric">
        <span className="crate-metric-label">Missing artist/title</span>
        <strong>{missingArtist + missingTitle}</strong>
        <div className="crate-meter crate-meter--warn">
          <span style={{ width: `${pctValue(missingArtist + missingTitle, total)}%` }} />
        </div>
        <span className="crate-metric-sub">{missingArtist} artist, {missingTitle} title</span>
      </div>
      <div className="crate-metric crate-metric--wide">
        <span className="crate-metric-label">Parse confidence</span>
        <div className="crate-breakdown">
          {(['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'] as ParseConfidence[]).map((key) => (
            <span key={key} className={`conf-chip ${confidenceClass(key)}`}>
              {key} {parse?.[key] ?? 0}
            </span>
          ))}
        </div>
        <div className="crate-meter-stack">
          {(['HIGH', 'MEDIUM', 'LOW', 'UNKNOWN'] as ParseConfidence[]).map((key) => (
            <span
              key={key}
              className={`crate-meter-segment crate-meter-segment--${key.toLowerCase()}`}
              style={{ width: `${pctValue(parse[key] ?? 0, total)}%` }}
              title={`${key}: ${parse[key] ?? 0}`}
            />
          ))}
        </div>
      </div>
    </div>
  )
}

function Inspector({
  track,
  loading,
  queueItem,
}: {
  track: TrackDetail | null
  loading: boolean
  queueItem: EnrichmentQueueItem | null
}) {
  if (loading && !queueItem) {
    return <aside className="crate-inspector"><p className="muted">Loading track detail...</p></aside>
  }
  if (!track && !queueItem) {
    return (
      <aside className="crate-inspector crate-inspector--empty">
        <Database size={22} />
        <span>Select a track</span>
      </aside>
    )
  }

  const bestMatch = queueItem?.best_match as Record<string, unknown> | null | undefined
  const query = queueItem?.query as Record<string, unknown> | null | undefined
  const scoreBreakdown =
    (queueItem?.score_breakdown as Record<string, unknown> | null | undefined) ||
    (queueItem?.scores as Record<string, unknown> | null | undefined) ||
    (queueItem?.candidate_scores as Record<string, unknown> | null | undefined) ||
    (queueItem?.match_breakdown as Record<string, unknown> | null | undefined)
  const rawCandidates = (queueItem as { candidates?: unknown } | null | undefined)?.candidates
  const candidateList = Array.isArray(rawCandidates) ? rawCandidates.slice(0, 3) : []

  return (
    <aside className="crate-inspector">
      {track && (
        <div className="crate-inspector-head">
          <span className="crate-kicker">Inspector</span>
          <strong>{track.artist || '(no artist)'}</strong>
          <span>{track.title || track.filename}</span>
        </div>
      )}

      {queueItem && (
        <section className="crate-inspector-section">
          <h3>Review</h3>
          <div className="crate-badge-row">
            <span className={`review-chip ${reviewClass(queueItem.review_status)}`}>
              {reviewLabel(queueItem.review_status)}
            </span>
            <span className={`conf-chip ${confidenceClass(String(queueItem.confidence ?? 'UNKNOWN'))}`}>
              {String(queueItem.confidence ?? 'UNKNOWN')}
            </span>
            <span className="crate-provider-chip">{displayValue(queueItem.provider, 'unknown')}</span>
          </div>
        </section>
      )}

      {track && (
        <>
          <section className="crate-inspector-section">
            <h3>File</h3>
            <code>{displayValue(track.filesystem_path || track.filepath)}</code>
          </section>

          <section className="crate-inspector-section">
            <h3>Metadata</h3>
            <dl className="crate-defs">
              <dt>Artist</dt><dd>{displayValue(track.artist)}</dd>
              <dt>Title</dt><dd>{displayValue(track.title)}</dd>
              <dt>BPM</dt><dd>{displayValue(track.bpm)}</dd>
              <dt>Camelot</dt><dd>{displayValue(track.key_camelot)}</dd>
              <dt>Musical key</dt><dd>{displayValue(track.key_musical)}</dd>
              <dt>Genre</dt><dd>{displayValue(track.genre)}</dd>
              <dt>Bitrate</dt><dd>{track.bitrate_kbps ? `${track.bitrate_kbps} kbps` : '—'}</dd>
            </dl>
          </section>

          <section className="crate-inspector-section">
            <h3>Parse Confidence</h3>
            <span className={`conf-chip ${confidenceClass(track.parse_confidence)}`}>
              {track.parse_confidence || 'UNKNOWN'}
            </span>
          </section>

          <section className="crate-inspector-section">
            <h3>Issues</h3>
            <div className="crate-badge-row">
              {track.issues.length ? track.issues.map((issue) => (
                <span key={issue} className="crate-issue-badge">{ISSUE_LABELS[issue] ?? issue}</span>
              )) : <span className="muted">No current issue flags</span>}
            </div>
          </section>
        </>
      )}

      {queueItem && (
        <section className="crate-inspector-section">
          <h3>Candidate Metadata</h3>
          <dl className="crate-defs crate-defs--queue">
            <dt>Track ID</dt><dd>{displayValue(queueItem.track_id)}</dd>
            <dt>File</dt><dd>{displayValue(queueItem.filepath)}</dd>
            <dt>Score</dt><dd>{displayValue(queueItem.score)}</dd>
            <dt>Suggestion</dt><dd>{displayValue(queueItem.action_suggestion)}</dd>
            <dt>Query</dt><dd>{candidateLabel(query)}</dd>
            <dt>Best match</dt><dd>{candidateLabel(bestMatch)}</dd>
            <dt>Reviewed at</dt><dd>{displayValue(queueItem.review_updated_at)}</dd>
          </dl>
          <div className="crate-score-breakdown">
            {scoreBreakdown
              ? Object.entries(scoreBreakdown)
                  .slice(0, 6)
                  .map(([key, value]) => (
                    <div key={key} className="crate-score-row">
                      <span>{key.replace(/_/g, ' ')}</span>
                      <strong>{typeof value === 'number' ? value.toFixed(3) : displayValue(value)}</strong>
                    </div>
                  ))
              : <span className="muted">No score breakdown available</span>}
          </div>
          {candidateList.length > 0 && (
            <div className="crate-candidate-list">
              {candidateList.map((candidate, index) => (
                <div key={index} className="crate-candidate-row">
                  <span>{candidateLabel(candidate)}</span>
                </div>
              ))}
            </div>
          )}
        </section>
      )}
    </aside>
  )
}

export default function CrateMind() {
  const location = useLocation()
  const navigate = useNavigate()
  const section = sectionFromPath(location.pathname)
  const persistedUi = useMemo(loadUiState, [])

  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [overview, setOverview] = useState<LibraryOverview | null>(null)
  const [issues, setIssues] = useState<TrackIssueCounts | null>(null)
  const [folders, setFolders] = useState<LibraryFolderStat[]>([])
  const [queue, setQueue] = useState<EnrichmentQueueResponse | null>(null)
  const [reviewState, setReviewState] = useState<ReviewStateResponse | null>(null)
  const [reviewSummary, setReviewSummary] = useState<ReviewSummaryResponse | null>(null)
  const [audit, setAudit] = useState<Record<string, unknown> | null>(null)
  const [trackPage, setTrackPage] = useState<TrackPage | null>(null)
  const [selectedId, setSelectedId] = useState<number | null>(persistedUi.selectedId ?? null)
  const [selectedDetail, setSelectedDetail] = useState<TrackDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [loading, setLoading] = useState(true)
  const [queueLoading, setQueueLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [queueError, setQueueError] = useState<string | null>(null)

  const [searchDraft, setSearchDraft] = useState(persistedUi.search ?? '')
  const [search, setSearch] = useState(persistedUi.search ?? '')
  const [offset, setOffset] = useState(persistedUi.offset ?? 0)
  const [sort, setSort] = useState<SortKey>(persistedUi.sort ?? 'artist')
  const [order, setOrder] = useState<SortOrder>(persistedUi.order ?? 'asc')
  const [issueFilter, setIssueFilter] = useState<TrackIssue | ''>(persistedUi.issueFilter ?? '')
  const [queueActionFilter, setQueueActionFilter] = useState<'auto_candidate' | 'review' | 'ignore' | ''>(persistedUi.queueActionFilter ?? '')
  const [queueConfidenceFilter, setQueueConfidenceFilter] = useState<'HIGH' | 'MEDIUM' | 'LOW' | ''>(persistedUi.queueConfidenceFilter ?? '')
  const [queueReviewFilter, setQueueReviewFilter] = useState<ReviewStatus | 'all'>(persistedUi.queueReviewFilter ?? 'all')
  const [selectedQueueIds, setSelectedQueueIds] = useState<number[]>([])
  const [trackScrollTop, setTrackScrollTop] = useState(0)
  const [actionBusy, setActionBusy] = useState(false)
  const [applyBusy, setApplyBusy] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [applyPreview, setApplyPreview] = useState<ApplyApprovedResponse | null>(null)

  useEffect(() => {
    const id = window.setTimeout(() => {
      setSearch(searchDraft)
      setOffset(0)
    }, 350)
    return () => window.clearTimeout(id)
  }, [searchDraft])

  useEffect(() => {
    persistUiState({
      search: searchDraft,
      offset,
      sort,
      order,
      issueFilter,
      selectedId,
      section,
      queueActionFilter,
      queueConfidenceFilter,
      queueReviewFilter,
    })
  }, [
    searchDraft,
    offset,
    sort,
    order,
    issueFilter,
    selectedId,
    section,
    queueActionFilter,
    queueConfidenceFilter,
    queueReviewFilter,
  ])

  const params: TrackListParams = useMemo(() => ({
    search: search || undefined,
    issue: issueFilter || undefined,
    sort,
    order,
    limit: LIMIT,
    offset,
  }), [search, issueFilter, sort, order, offset])

  const queueParams = useMemo(() => ({
    action: queueActionFilter || undefined,
    confidence: queueConfidenceFilter || undefined,
    limit: 200,
    offset: 0,
  }), [queueActionFilter, queueConfidenceFilter])

  const loadMain = useCallback(async () => {
    setLoading(true)
    try {
      const [healthData, overviewData, issueData, folderData, auditData, pageData] = await Promise.all([
        fetchHealth(),
        fetchLibraryOverview(),
        fetchTrackIssues(),
        fetchLibraryFolders(),
        fetchLatestAudit(),
        fetchTrackPage(params),
      ])
      setHealth(healthData)
      setOverview(overviewData)
      setIssues(issueData)
      setFolders(folderData)
      setAudit(auditData)
      setTrackPage(pageData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load dashboard data')
    } finally {
      setLoading(false)
    }
  }, [params])

  useEffect(() => {
    loadMain()
  }, [loadMain])

  useEffect(() => {
    setTrackScrollTop(0)
  }, [search, issueFilter, sort, order, offset])

  const loadQueue = useCallback(async () => {
    setQueueLoading(true)
    setQueueError(null)
    try {
      const [queueData, reviewData, summaryData] = await Promise.all([
        fetchEnrichmentQueue(queueParams),
        fetchReviewState(),
        fetchReviewSummary(),
      ])
      setQueue(queueData)
      setReviewState(reviewData)
      setReviewSummary(summaryData)
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : 'Failed to load enrichment queue')
    } finally {
      setQueueLoading(false)
    }
  }, [queueParams])

  useEffect(() => {
    loadQueue()
  }, [loadQueue])

  useEffect(() => {
    if (!selectedId) {
      setSelectedDetail(null)
      return
    }
    setDetailLoading(true)
    fetchTrack(selectedId)
      .then(setSelectedDetail)
      .catch((e: Error) => setError(e.message))
      .finally(() => setDetailLoading(false))
  }, [selectedId])

  function refresh() {
    loadMain()
    loadQueue()
  }

  function handleSort(next: SortKey) {
    if (sort === next) {
      setOrder((value) => value === 'asc' ? 'desc' : 'asc')
    } else {
      setSort(next)
      setOrder('asc')
    }
    setOffset(0)
  }

  function applyIssue(issue: keyof TrackIssueCounts) {
    setIssueFilter(issue as TrackIssue)
    setOffset(0)
    navigate('/issues')
  }

  function reviewStatusForItem(item: EnrichmentQueueItem): ReviewStatus {
    if (item.review_status && item.review_status !== 'pending') return item.review_status
    if (item.track_id == null) return 'pending'
    return reviewState?.items[String(item.track_id)]?.review_status ?? 'pending'
  }

  const queueItems = useMemo(() => queue?.items ?? [], [queue])
  const visibleQueueItems = useMemo(() => queueItems.filter((item) => {
    const status = reviewStatusForItem(item)
    return queueReviewFilter === 'all' || queueReviewFilter === status
  }), [queueItems, queueReviewFilter, reviewState])
  const selectedQueueIdSet = useMemo(() => new Set(selectedQueueIds), [selectedQueueIds])
  const selectedQueueItems = useMemo(() => visibleQueueItems.filter((item) => {
    const id = item.track_id
    return id != null && selectedQueueIdSet.has(id)
  }), [visibleQueueItems, selectedQueueIdSet])
  const selectedQueueItem = selectedQueueItems.length === 1 ? selectedQueueItems[0] : null
  const allVisibleSelected = visibleQueueItems.length > 0 && visibleQueueItems.every((item) => item.track_id != null && selectedQueueIdSet.has(item.track_id))

  const items = trackPage?.items ?? []
  const total = trackPage?.total ?? 0
  const rootLabel = health?.library_root || 'Library root unavailable'
  const virtualStart = Math.max(0, Math.floor(trackScrollTop / TRACK_ROW_HEIGHT) - TRACK_OVERSCAN)
  const visibleRowCount = Math.ceil(TRACK_TABLE_HEIGHT / TRACK_ROW_HEIGHT) + TRACK_OVERSCAN * 2
  const virtualEnd = Math.min(items.length, virtualStart + visibleRowCount)
  const virtualRows = items.slice(virtualStart, virtualEnd)
  const virtualTopPad = virtualStart * TRACK_ROW_HEIGHT
  const virtualBottomPad = Math.max(0, (items.length - virtualEnd) * TRACK_ROW_HEIGHT)

  useEffect(() => {
    setSelectedQueueIds((current) => {
      const visibleIds = new Set(visibleQueueItems.map((item) => item.track_id).filter((id): id is number => id != null))
      const next = current.filter((id) => visibleIds.has(id))
      if (next.length === current.length && next.every((id, index) => id === current[index])) {
        return current
      }
      return next
    })
  }, [visibleQueueItems])

  async function submitReview(trackId: number, action: ReviewStatus) {
    setActionBusy(true)
    try {
      if (action === 'approved') {
        await approveReview(trackId)
      } else if (action === 'rejected') {
        await rejectReview(trackId)
      } else {
        await deferReview(trackId)
      }
      await loadQueue()
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : 'Failed to update review state')
    } finally {
      setActionBusy(false)
    }
  }

  async function bulkDeferSelection() {
    const ids = selectedQueueItems.map((item) => item.track_id).filter((id): id is number => id != null)
    if (!ids.length) return
    setActionBusy(true)
    try {
      for (const id of ids) {
        // Sequential writes keep the state file stable and deterministic.
        await deferReview(id)
      }
      setSelectedQueueIds([])
      await loadQueue()
    } catch (e) {
      setQueueError(e instanceof Error ? e.message : 'Failed to defer selected items')
    } finally {
      setActionBusy(false)
    }
  }

  function exportReviewState() {
    window.open('/api/enrichment/review/export', '_blank', 'noopener,noreferrer')
  }

  async function runDryRunApprovedApply() {
    setApplyBusy(true)
    setApplyError(null)
    try {
      setApplyPreview(await dryRunApplyApproved())
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : 'Failed to dry-run approved enrichment updates')
    } finally {
      setApplyBusy(false)
    }
  }

  async function commitApprovedApply() {
    if (!applyPreview?.proposed_count) return
    setApplyBusy(true)
    setApplyError(null)
    try {
      setApplyPreview(await applyApproved(true))
      await loadMain()
      await loadQueue()
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : 'Failed to apply approved enrichment updates')
    } finally {
      setApplyBusy(false)
    }
  }

  function toggleQueueSelection(trackId: number | null | undefined) {
    if (trackId == null) return
    setSelectedQueueIds((current) =>
      current.includes(trackId)
        ? current.filter((id) => id !== trackId)
        : [...current, trackId],
    )
  }

  function toggleVisibleSelection() {
    if (!visibleQueueItems.length) return
    setSelectedQueueIds((current) => {
      if (allVisibleSelected) {
        const visibleIds = new Set(visibleQueueItems.map((item) => item.track_id).filter((id): id is number => id != null))
        return current.filter((id) => !visibleIds.has(id))
      }
      const next = new Set(current)
      visibleQueueItems.forEach((item) => {
        if (item.track_id != null) next.add(item.track_id)
      })
      return Array.from(next)
    })
  }

  return (
    <div className="crate-workspace">
      <header className="crate-topbar">
        <div className="crate-status" title={health?.db_path}>
          {health?.ok && health.db_exists ? <CheckCircle2 size={15} /> : <AlertTriangle size={15} />}
          <span>{health?.ok && health.db_exists ? 'API online' : 'API unavailable'}</span>
        </div>
        <div className="crate-root" title={rootLabel}>{rootLabel}</div>
        <label className="crate-search">
          <Search size={14} />
          <input
            value={searchDraft}
            onChange={(event) => setSearchDraft(event.target.value)}
            placeholder="Search artist, title, filename"
            type="search"
          />
        </label>
        {(loading || queueLoading || detailLoading) && <span className="crate-spinner" aria-label="Loading" />}
        <button className="btn btn--ghost btn--sm" onClick={refresh}>
          <RefreshCw size={13} />
          Refresh
        </button>
      </header>

      <div className="crate-body">
        <main className="crate-main">
          {error && <div className="error-banner">{error}</div>}

          {section === 'library' && <OverviewCards overview={overview} />}

          {section === 'issues' && (
      <section className="crate-panel">
        <div className="crate-panel-head">
          <h2>Issue Counts</h2>
          {issueFilter && <button className="btn btn--ghost btn--sm" onClick={() => setIssueFilter('')}>Clear filter</button>}
        </div>
        <div className="crate-issue-grid">
          {ISSUE_KEYS.map((key) => (
            <button
              key={key}
              className={`crate-issue-count${issueFilter === key ? ' crate-issue-count--active' : ''}`}
              onClick={() => applyIssue(key)}
            >
              <span>{ISSUE_LABELS[key as TrackIssue] ?? key}</span>
              <strong>{issues?.[key] ?? 0}</strong>
            </button>
          ))}
        </div>
      </section>
          )}

          {section === 'folders' && (
            <section className="crate-panel">
              <div className="crate-panel-head">
                <h2>Folders</h2>
                <span className="muted">{folders.length.toLocaleString()} DB folders</span>
              </div>
              <div className="crate-folder-list">
                {folders.map((folder) => (
                  <div key={folder.folder} className="crate-folder-row">
                    <Folder size={14} />
                    <code>{folder.folder}</code>
                    <span>{folder.track_count} tracks</span>
                    <span className={folder.issue_count ? 'text--error' : 'muted'}>{folder.issue_count} issues</span>
                  </div>
                ))}
                {!folders.length && <div className="crate-empty">No folder rows available from the database.</div>}
              </div>
            </section>
          )}

          {section === 'enrichment' && (
            <section className="crate-panel crate-enrichment-panel">
              <div className="crate-panel-head">
                <div>
                  <h2>Enrichment Queue</h2>
                  <span className="muted">
                    {queueLoading ? 'Loading queue...' : `${visibleQueueItems.length.toLocaleString()} visible / ${queue?.total ?? 0} total`}
                  </span>
                </div>
                <div className="crate-panel-actions">
                  <button className="btn btn--ghost btn--sm" onClick={runDryRunApprovedApply} disabled={applyBusy}>
                    Dry-run apply approved
                  </button>
                  <button
                    className="btn btn--ghost btn--sm"
                    onClick={commitApprovedApply}
                    disabled={applyBusy || !applyPreview || applyPreview.proposed_count === 0}
                    title={applyPreview ? 'Applies only to tracks with approved HIGH-confidence review items' : 'Run a dry-run first'}
                  >
                    Apply approved
                  </button>
                  <button className="btn btn--ghost btn--sm" onClick={exportReviewState}>
                    <Download size={13} />
                    Export Review State
                  </button>
                  <button className="btn btn--ghost btn--sm" onClick={toggleVisibleSelection} disabled={!visibleQueueItems.length}>
                    <SquareCheck size={13} />
                    {allVisibleSelected ? 'Clear visible' : 'Select visible'}
                  </button>
                  <button className="btn btn--ghost btn--sm" onClick={bulkDeferSelection} disabled={actionBusy || !selectedQueueItems.length}>
                    <Clock3 size={13} />
                    Defer selected
                  </button>
                </div>
              </div>
              <div className="crate-apply-warning">
                DB only. No tag writes. No audio file changes. Dry-run first, then confirm approved rows against the tracks table.
              </div>
              {applyError && <div className="error-banner">{applyError}</div>}
              {applyPreview && (
                <section className="crate-apply-preview">
                  <div className="crate-panel-head">
                    <div>
                      <h2>Proposed Changes</h2>
                      <span className="muted">
                        {applyPreview.proposed_count.toLocaleString()} proposed / {applyPreview.skipped_count.toLocaleString()} skipped
                      </span>
                    </div>
                    <span className="muted">{formatTimestamp(reviewSummary?.last_updated ?? reviewState?.updated_at)}</span>
                  </div>
                  <div className="crate-table-scroll crate-apply-scroll">
                    <table className="table crate-table crate-table--apply">
                      <thead>
                        <tr>
                          <th>Track</th>
                          <th>Fields</th>
                          <th>Before</th>
                          <th>After</th>
                          <th>Confidence</th>
                          <th>Provider</th>
                        </tr>
                      </thead>
                      <tbody>
                        {applyPreview.changes.map((change) => (
                          <tr key={`${change.track_id}-${change.filepath}`}>
                            <td>
                              <div className="crate-apply-track">
                                <strong>{change.track_id}</strong>
                                <span>{change.filepath}</span>
                              </div>
                            </td>
                            <td>{change.fields.join(', ')}</td>
                            <td><code>{JSON.stringify(change.before)}</code></td>
                            <td><code>{JSON.stringify(change.after)}</code></td>
                            <td>
                              <span className={`conf-chip ${confidenceClass(change.confidence)}`}>{change.confidence}</span>
                            </td>
                            <td>{displayValue(change.provider)}</td>
                          </tr>
                        ))}
                        {!applyPreview.changes.length && (
                          <tr>
                            <td colSpan={6} className="crate-empty">No eligible approved enrichment rows found.</td>
                          </tr>
                        )}
                      </tbody>
                    </table>
                  </div>
                </section>
              )}
              <div className="crate-review-summary">
                {[
                  { label: 'Pending', value: reviewSummary?.pending_count ?? 0 },
                  { label: 'Approved', value: reviewSummary?.approved_count ?? 0 },
                  { label: 'Rejected', value: reviewSummary?.rejected_count ?? 0 },
                  { label: 'Deferred', value: reviewSummary?.deferred_count ?? 0 },
                  { label: 'Approved HIGH', value: reviewSummary?.approved_high_count ?? 0 },
                  { label: 'Approved MEDIUM', value: reviewSummary?.approved_medium_count ?? 0 },
                ].map((stat) => (
                  <div key={stat.label} className="crate-review-card">
                    <span>{stat.label}</span>
                    <strong>{stat.value.toLocaleString()}</strong>
                  </div>
                ))}
                <div className="crate-review-card crate-review-card--wide">
                  <span>Last updated</span>
                  <strong>{formatTimestamp(reviewSummary?.last_updated ?? reviewState?.updated_at)}</strong>
                </div>
              </div>
              <div className="crate-queue-filters">
                <div className="crate-pill-group">
                  {([
                    ['', 'All actions'],
                    ['auto_candidate', 'Auto'],
                    ['review', 'Review'],
                    ['ignore', 'Ignore'],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value || 'all-actions'}
                      className={`crate-pill${queueActionFilter === value ? ' crate-pill--active' : ''}`}
                      onClick={() => setQueueActionFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="crate-pill-group">
                  {([
                    ['', 'All confidence'],
                    ['HIGH', 'High'],
                    ['MEDIUM', 'Medium'],
                    ['LOW', 'Low'],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value || 'all-confidence'}
                      className={`crate-pill${queueConfidenceFilter === value ? ' crate-pill--active' : ''}`}
                      onClick={() => setQueueConfidenceFilter(value)}
                    >
                      {label}
                    </button>
                  ))}
                </div>
                <div className="crate-pill-group">
                  {([
                    ['all', 'All states'],
                    ['pending', 'Pending'],
                    ['approved', 'Approved'],
                    ['rejected', 'Rejected'],
                    ['deferred', 'Deferred'],
                  ] as const).map(([value, label]) => (
                    <button
                      key={value}
                      className={`crate-pill${queueReviewFilter === value ? ' crate-pill--active' : ''}`}
                      onClick={() => setQueueReviewFilter(value as ReviewStatus | 'all')}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
              {queueError && <div className="error-banner">{queueError}</div>}
              <div className="crate-queue-summary">
                <span className="muted">{selectedQueueItems.length.toLocaleString()} selected</span>
                <span className="muted">
                  {queue?.counts?.by_action
                    ? Object.entries(queue.counts.by_action).map(([key, value]) => `${key}: ${value}`).join(' · ')
                    : ''}
                </span>
              </div>
              <div className="crate-table-scroll crate-queue-scroll">
                <table className="table crate-table crate-table--queue">
                  <thead>
                    <tr>
                      <th className="crate-check-col">
                        <button
                          className="crate-select-all"
                          onClick={toggleVisibleSelection}
                          disabled={!visibleQueueItems.length}
                          title="Select all visible rows"
                        >
                          <SquareCheck size={14} />
                        </button>
                      </th>
                      <th>Track</th>
                      <th>Provider</th>
                      <th>Score</th>
                      <th>Confidence</th>
                      <th>Suggestion</th>
                      <th>Review</th>
                      <th>Actions</th>
                    </tr>
                  </thead>
                  <tbody>
                    {queueLoading && visibleQueueItems.length === 0 && (
                      <>
                        {Array.from({ length: 5 }).map((_, idx) => (
                          <tr key={`queue-skeleton-${idx}`} className="crate-row-skeleton">
                            <td colSpan={8}><span /></td>
                          </tr>
                        ))}
                      </>
                    )}
                    {visibleQueueItems.map((item, index) => {
                      const reviewStatus = reviewStatusForItem(item)
                      const isSelected = item.track_id != null && selectedQueueIds.includes(item.track_id)
                      return (
                        <tr
                          key={queueRowKey(item, index)}
                          className={isSelected ? 'crate-row-selected' : 'crate-row-clickable'}
                          onClick={() => {
                            toggleQueueSelection(item.track_id)
                            if (item.track_id != null) setSelectedId(item.track_id)
                          }}
                        >
                          <td className="crate-check-col">
                            <input
                              type="checkbox"
                              checked={isSelected}
                              onChange={() => toggleQueueSelection(item.track_id)}
                              onClick={(event) => event.stopPropagation()}
                            />
                          </td>
                          <td className="td-title" title={displayValue(item.filepath)}>
                            <div className="crate-queue-track">
                              <strong>{displayValue(item.track_id, '—')}</strong>
                              <span>{displayValue(item.filepath, 'unknown')}</span>
                            </div>
                          </td>
                          <td>
                            <span className="crate-provider-chip">{displayValue(item.provider, 'unknown')}</span>
                          </td>
                          <td className="td-mono">{displayValue(item.score)}</td>
                          <td>
                            <span className={`conf-chip ${confidenceClass(String(item.confidence ?? 'UNKNOWN'))}`}>
                              {String(item.confidence ?? 'UNKNOWN')}
                            </span>
                          </td>
                          <td>{displayValue(item.action_suggestion)}</td>
                          <td>
                            <span className={`review-chip ${reviewClass(reviewStatus)}`}>
                              {reviewLabel(reviewStatus)}
                            </span>
                          </td>
                          <td>
                            <div className="crate-row-actions">
                              <button
                                className="icon-btn icon-btn--approve"
                                title="Approve"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  if (item.track_id != null) submitReview(item.track_id, 'approved')
                                }}
                                disabled={actionBusy || item.track_id == null}
                              >
                                <Check size={13} />
                              </button>
                              <button
                                className="icon-btn icon-btn--reject"
                                title="Reject"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  if (item.track_id != null) submitReview(item.track_id, 'rejected')
                                }}
                                disabled={actionBusy || item.track_id == null}
                              >
                                <X size={13} />
                              </button>
                              <button
                                className="icon-btn icon-btn--defer"
                                title="Defer"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  if (item.track_id != null) submitReview(item.track_id, 'deferred')
                                }}
                                disabled={actionBusy || item.track_id == null}
                              >
                                <Clock3 size={13} />
                              </button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                    {!queueLoading && !visibleQueueItems.length && (
                      <tr>
                        <td colSpan={8} className="crate-empty">No enrichment queue items match the current filters.</td>
                      </tr>
                    )}
                  </tbody>
                </table>
              </div>
            </section>
          )}

          {section === 'audit' && (
            <section className="crate-panel">
              <div className="crate-panel-head">
                <h2>Latest Audit</h2>
                <span className="muted">{audit && audit.available === false ? 'No audit JSON found' : 'Read-only JSON'}</span>
              </div>
              <pre className="crate-json crate-json--large">{JSON.stringify(audit ?? {}, null, 2)}</pre>
            </section>
          )}

          <section className="crate-table-panel">
            <div className="crate-panel-head">
              <div>
                <h2>Tracks</h2>
                <span className="muted">
                  {loading ? 'Loading...' : `${total.toLocaleString()} matching tracks`}
                  {issueFilter ? ` / ${ISSUE_LABELS[issueFilter] ?? issueFilter}` : ''}
                </span>
              </div>
              <div className="crate-table-tools">
                <ListFilter size={14} />
                <span>Read-only</span>
              </div>
            </div>
            <div
              className="crate-table-scroll crate-track-virtual-scroll"
              onScroll={(event) => setTrackScrollTop(event.currentTarget.scrollTop)}
            >
              <table className="table crate-table">
                <thead>
                  <tr>
                    <TrackSortHeader label="Artist" sortKey="artist" sort={sort} order={order} onSort={handleSort} />
                    <TrackSortHeader label="Title" sortKey="title" sort={sort} order={order} onSort={handleSort} />
                    <TrackSortHeader label="BPM" sortKey="bpm" sort={sort} order={order} onSort={handleSort} />
                    <th>Camelot</th>
                    <th>Genre</th>
                    <th>Parse confidence</th>
                    <th>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {loading && items.length === 0 && (
                    <>
                      {Array.from({ length: 6 }).map((_, idx) => (
                        <tr key={`skeleton-${idx}`} className="crate-row-skeleton">
                          <td colSpan={7}>
                            <span />
                          </td>
                        </tr>
                      ))}
                    </>
                  )}
                  {virtualTopPad > 0 && (
                    <tr className="crate-virtual-spacer" aria-hidden="true">
                      <td colSpan={7} style={{ height: virtualTopPad }} />
                    </tr>
                  )}
                  {virtualRows.map((track: TrackSummary) => (
                    <tr
                      key={track.id}
                      className={selectedId === track.id ? 'track-row--selected crate-row-selected' : 'crate-row-clickable'}
                      onClick={() => setSelectedId(track.id)}
                    >
                      <td className="td-artist">{displayValue(track.artist, '—')}</td>
                      <td className="td-title" title={track.filename}>{displayValue(track.title, track.filename)}</td>
                      <td className="td-mono">{displayValue(track.bpm)}</td>
                      <td className="td-mono">{displayValue(track.key_camelot || track.key_musical)}</td>
                      <td>{displayValue(track.genre)}</td>
                      <td>
                        <span className={`conf-chip ${confidenceClass(track.parse_confidence)}`}>
                          {track.parse_confidence ?? 'UNKNOWN'}
                        </span>
                      </td>
                      <td>
                        <div className="crate-badge-row">
                          {track.issues.slice(0, 4).map((issue) => (
                            <span key={issue} className="crate-issue-badge" title={ISSUE_LABELS[issue] ?? issue}>
                              {shortIssue(issue)}
                            </span>
                          ))}
                          {track.issues.length === 0 && <span className="muted">clean</span>}
                          {track.issues.length > 4 && <span className="muted">+{track.issues.length - 4}</span>}
                        </div>
                      </td>
                    </tr>
                  ))}
                  {virtualBottomPad > 0 && (
                    <tr className="crate-virtual-spacer" aria-hidden="true">
                      <td colSpan={7} style={{ height: virtualBottomPad }} />
                    </tr>
                  )}
                  {!loading && items.length === 0 && (
                    <tr>
                      <td colSpan={7} className="crate-empty">No tracks match the current filters.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <div className="crate-pagination">
              <span className="muted">{selectedId ? '1 track selected' : 'No track selected'}</span>
              <button className="btn btn--ghost btn--sm" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - LIMIT))}>
                Prev
              </button>
              <span>{total ? `${offset + 1}-${Math.min(offset + items.length, total)} of ${total}` : '0 tracks'}</span>
              <button className="btn btn--ghost btn--sm" disabled={offset + items.length >= total} onClick={() => setOffset(offset + LIMIT)}>
                Next
              </button>
            </div>
          </section>
        </main>

        <Inspector track={selectedDetail} loading={detailLoading} queueItem={selectedQueueItem} />
      </div>
    </div>
  )
}
