import { useState, useRef, useEffect, useCallback } from 'react'
import {
  ChevronRight, ChevronDown, FolderOpen, Folder,
  Columns3, Play, ChevronUp, X, Search, Wrench,
} from 'lucide-react'
import { submitJob, fetchJob, fetchJobLogs, cancelJob } from '../api/jobs'
import { fetchLibraryTree, fetchLibraryStats } from '../api/library'
import { fetchTracks, fetchTrackStats } from '../api/tracks'
import type { LibraryNode as ApiLibraryNode } from '../api/library'
import type { JobStatus } from '../types/job'
import type { TrackSummary } from '../types/track'

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type SortKey = 'artist' | 'title' | 'bpm' | 'key' | 'duration' | 'quality' | 'filename'
type SortDir = 'asc' | 'desc'
type Preset  = 'Clean' | 'Normalize' | 'Enrich' | 'Full Pass'

interface ActiveJob { id: string; status: JobStatus; command: string }

// ---------------------------------------------------------------------------
// Column definitions — scoped to real TrackSummary fields
// ---------------------------------------------------------------------------

interface ColDef { key: string; label: string; defaultVisible: boolean; sortable?: boolean }

const ALL_COLS: ColDef[] = [
  { key: 'cover',    label: '',         defaultVisible: true,  sortable: false },
  { key: 'artist',   label: 'Artist',   defaultVisible: true,  sortable: true  },
  { key: 'title',    label: 'Title',    defaultVisible: true,  sortable: true  },
  { key: 'bpm',      label: 'BPM',      defaultVisible: true,  sortable: true  },
  { key: 'key',      label: 'Key',      defaultVisible: true,  sortable: true  },
  { key: 'duration', label: 'Dur',      defaultVisible: false, sortable: true  },
  { key: 'status',   label: 'Status',   defaultVisible: true,  sortable: false },
  { key: 'quality',  label: 'Quality',  defaultVisible: true,  sortable: true  },
  { key: 'genre',    label: 'Genre',    defaultVisible: false, sortable: false },
  { key: 'bitrate',  label: 'Kbps',     defaultVisible: false, sortable: false },
  { key: 'filename', label: 'File',     defaultVisible: false, sortable: true  },
  { key: 'issues',   label: 'Issues',   defaultVisible: false, sortable: false },
]

// ---------------------------------------------------------------------------
// Directory tree
// ---------------------------------------------------------------------------

interface DirNode {
  label:      string
  path?:      string
  executable: boolean
  count?:     number
  children?:  DirNode[]
}

const FALLBACK_TREE: DirNode[] = [
  {
    label: 'KKDJ', path: '/music', executable: true, children: [
      { label: 'inbox',   path: '/music/inbox',   executable: true },
      { label: 'library', path: '/music/library', executable: true },
    ],
  },
]

function apiNodeToDirNode(n: ApiLibraryNode): DirNode {
  return {
    label:      n.label,
    path:       n.path,
    executable: n.executable,
    children:   n.children.map(apiNodeToDirNode),
  }
}

// ---------------------------------------------------------------------------
// Preset log samples (shown when no job is active)
// ---------------------------------------------------------------------------

const PRESET_LOGS: Record<Preset | 'default', string[]> = {
  default: [
    '[idle] No job running. Select a folder and press Clean to start.',
    '[tip] Use Apply toggle to write tag changes.',
    '[tip] Maintenance actions are at the bottom of the folder panel.',
  ],
  Clean: [
    '[preview] metadata-sanitize will scan the selected folder.',
    '[tip] Toggle Apply to commit tag changes to disk.',
  ],
  Normalize: ['[preview] ai-normalize would run on the selected folder.'],
  Enrich:    ['[preview] metadata-enrich-online would run on the selected folder.'],
  'Full Pass': ['[preview] Full pipeline pass would run on the selected folder.'],
}

// ---------------------------------------------------------------------------
// Sort helpers
// ---------------------------------------------------------------------------

const QUALITY_RANK: Record<string, number> = {
  LOSSLESS: 0, HIGH: 1, MEDIUM: 2, LOW: 3, UNKNOWN: 4,
}

function sortTracks(tracks: TrackSummary[], key: SortKey, dir: SortDir): TrackSummary[] {
  return [...tracks].sort((a, b) => {
    let va: number | string
    let vb: number | string
    switch (key) {
      case 'artist':
        va = (a.artist  ?? '').toLowerCase()
        vb = (b.artist  ?? '').toLowerCase()
        break
      case 'title':
        va = (a.title ?? '').toLowerCase()
        vb = (b.title ?? '').toLowerCase()
        break
      case 'filename':
        va = a.filename.toLowerCase()
        vb = b.filename.toLowerCase()
        break
      case 'bpm':
        va = a.bpm ?? -1
        vb = b.bpm ?? -1
        break
      case 'duration':
        va = a.duration_sec ?? -1
        vb = b.duration_sec ?? -1
        break
      case 'key': {
        const parseKey = (k: string | null | undefined) => {
          if (!k) return 9999
          const n = parseInt(k, 10)
          const l = k.replace(/\d/g, '')
          return (isNaN(n) ? 9999 : n) * 2 + (l === 'B' ? 1 : 0)
        }
        va = parseKey(a.key_camelot ?? a.key_musical)
        vb = parseKey(b.key_camelot ?? b.key_musical)
        break
      }
      case 'quality':
        va = QUALITY_RANK[a.quality_tier ?? 'UNKNOWN'] ?? 4
        vb = QUALITY_RANK[b.quality_tier ?? 'UNKNOWN'] ?? 4
        break
      default:
        va = ''
        vb = ''
    }
    const cmp = va < vb ? -1 : va > vb ? 1 : 0
    return dir === 'asc' ? cmp : -cmp
  })
}

function formatDuration(sec: number | null | undefined): string {
  if (sec == null) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

function jobStatusColor(s: JobStatus): string {
  if (s === 'succeeded') return 'var(--status-succeeded)'
  if (s === 'failed')    return 'var(--status-failed)'
  if (s === 'cancelled') return 'var(--status-cancelled)'
  return 'var(--status-running)'
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function DirTreeNode({
  node, depth = 0, activePath, onSelectDir,
}: {
  node:        DirNode
  depth?:      number
  activePath:  string | null
  onSelectDir: (label: string, path: string | undefined, executable: boolean) => void
}) {
  const [open, setOpen] = useState(depth < 1)
  const hasChildren = !!node.children?.length
  const isActive = node.path != null && node.path === activePath

  function handleClick() {
    onSelectDir(node.label, node.path, node.executable)
    if (hasChildren) setOpen((o) => !o)
  }

  return (
    <div>
      <div
        className={[
          'dir-tree-row',
          depth === 0 ? 'dir-tree-row--root' : '',
          isActive ? 'dir-tree-row--active' : '',
        ].filter(Boolean).join(' ')}
        style={{ paddingLeft: depth * 12 + 8 }}
        onClick={handleClick}
        title={node.path ?? node.label}
      >
        {hasChildren
          ? (open ? <ChevronDown size={11} /> : <ChevronRight size={11} />)
          : <span style={{ width: 11, display: 'inline-block' }} />}
        {hasChildren
          ? <FolderOpen size={12} className="dir-icon" />
          : <Folder size={12} className="dir-icon" />}
        <span className={`dir-label${!node.executable ? ' dir-label--no-exec' : ''}`}>
          {node.label}
        </span>
        {node.count != null && (
          <span className="dir-count">{node.count.toLocaleString()}</span>
        )}
      </div>
      {open && node.children?.map((child) => (
        <DirTreeNode
          key={child.path ?? child.label}
          node={child}
          depth={depth + 1}
          activePath={activePath}
          onSelectDir={onSelectDir}
        />
      ))}
    </div>
  )
}

function CamelotWheel({ activeKey }: { activeKey: string | null }) {
  return (
    <div className="camelot-wheel">
      {Array.from({ length: 12 }, (_, i) => i + 1).map((pos) => {
        const aKey = `${pos}A`
        const bKey = `${pos}B`
        return (
          <div key={pos} className="camelot-segment">
            <div className={`camelot-cell camelot-cell--a${activeKey === aKey ? ' camelot-cell--active' : ''}`}>{aKey}</div>
            <div className={`camelot-cell camelot-cell--b${activeKey === bKey ? ' camelot-cell--active' : ''}`}>{bKey}</div>
          </div>
        )
      })}
    </div>
  )
}

function SortTh({
  col, sortKey, sortDir, onSort,
}: {
  col:     ColDef
  sortKey: SortKey | null
  sortDir: SortDir
  onSort:  (k: SortKey) => void
}) {
  if (!col.sortable) return <th>{col.label}</th>
  const active = sortKey === col.key
  return (
    <th
      className={`th-sortable${active ? ' th-sortable--active' : ''}`}
      onClick={() => onSort(col.key as SortKey)}
    >
      {col.label}
      <span className="sort-indicator">
        {active ? (sortDir === 'asc' ? ' ▲' : ' ▼') : ' ⇅'}
      </span>
    </th>
  )
}

function ColPicker({
  cols, visible, onChange, onClose,
}: {
  cols:    ColDef[]
  visible: Set<string>
  onChange: (key: string, on: boolean) => void
  onClose: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    function handle(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handle)
    return () => document.removeEventListener('mousedown', handle)
  }, [onClose])
  return (
    <div className="col-picker" ref={ref}>
      <div className="col-picker-title">Columns</div>
      {cols.map((c) => (
        <label key={c.key} className="col-picker-row">
          <input
            type="checkbox"
            checked={visible.has(c.key)}
            onChange={(e) => onChange(c.key, e.target.checked)}
          />
          {c.label || c.key}
        </label>
      ))}
    </div>
  )
}

function Inspector({ track, onClose }: { track: TrackSummary; onClose: () => void }) {
  const ext  = track.filename.split('.').pop()?.toUpperCase() ?? '?'
  const key  = track.key_camelot ?? track.key_musical ?? null
  const qual = (track.quality_tier ?? 'unknown').toLowerCase()

  return (
    <div className="collection-inspector">
      <div className="inspector-header">
        <span className="inspector-title">Inspector</span>
        <button className="btn btn--ghost btn--xs" onClick={onClose}><X size={12} /></button>
      </div>

      <div className="inspector-cover">
        <div className="inspector-cover-art">
          {(track.artist ?? '?').slice(0, 2).toUpperCase()}
        </div>
        <div className="inspector-track-name">
          <div className="inspector-artist">{track.artist ?? <span className="muted">—</span>}</div>
          <div className="inspector-track-title">{track.title ?? <span className="muted">—</span>}</div>
          <div className="inspector-version muted">{ext}</div>
        </div>
      </div>

      <div className="inspector-section">
        <div className="inspector-section-label">Analysis</div>
        <dl className="def-list">
          <dt>BPM</dt>
          <dd className="td-mono">{track.bpm != null ? track.bpm.toFixed(1) : <span className="muted">—</span>}</dd>
          <dt>Key</dt>
          <dd className="td-mono">{key ?? <span className="muted">—</span>}</dd>
          <dt>Duration</dt>
          <dd className="td-mono">{formatDuration(track.duration_sec)}</dd>
        </dl>
      </div>

      <div className="inspector-section">
        <div className="inspector-section-label">File</div>
        <dl className="def-list">
          <dt>Format</dt>
          <dd className="td-mono">{ext}</dd>
          <dt>Bitrate</dt>
          <dd className="td-mono">{track.bitrate_kbps != null ? `${track.bitrate_kbps} kbps` : <span className="muted">—</span>}</dd>
          <dt>Quality</dt>
          <dd><span className={`quality-badge quality-badge--${qual}`}>{track.quality_tier ?? '—'}</span></dd>
          <dt>Status</dt>
          <dd><span className={`badge badge--track-${track.status}`}>{track.status}</span></dd>
        </dl>
      </div>

      {track.genre && (
        <div className="inspector-section">
          <div className="inspector-section-label">Genre</div>
          <p style={{ fontSize: 12, padding: '2px 0' }}>{track.genre}</p>
        </div>
      )}

      {track.issues.length > 0 && (
        <div className="inspector-section">
          <div className="inspector-section-label">Issues</div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
            {track.issues.map((issue) => (
              <span key={issue} className="badge badge--track-error" style={{ fontSize: 10, padding: '1px 5px' }}>
                {issue.replace(/_/g, ' ')}
              </span>
            ))}
          </div>
        </div>
      )}

      <div className="inspector-section">
        <div className="inspector-section-label">Path</div>
        <p className="inspector-note" style={{ wordBreak: 'break-all' }}>{track.filepath}</p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Maintenance panel
// ---------------------------------------------------------------------------

const MAINT_ACTIONS: { key: string; label: string; command: string; args: string[]; desc: string }[] = [
  {
    key: 'dedupe', label: 'Scan Duplicates',
    command: 'dedupe', args: [],
    desc: 'Detect and quarantine duplicate files (review-first)',
  },
  {
    key: 'prune', label: 'Prune Orphans',
    command: 'db-prune-stale', args: [],
    desc: 'Remove DB records for files no longer on disk',
  },
  {
    key: 'audit', label: 'Audit Library',
    command: 'audit-quality', args: ['--dry-run'],
    desc: 'Scan codec/bitrate quality — dry run, no writes',
  },
]

function MaintenancePanel({
  activeJob,
  onRun,
}: {
  activeJob: ActiveJob | null
  onRun: (command: string, args: string[]) => void
}) {
  const [open, setOpen] = useState(false)
  const running = activeJob?.status === 'pending' || activeJob?.status === 'running'

  return (
    <div className="maintenance-panel">
      <div className="maintenance-header" onClick={() => setOpen(!open)}>
        <Wrench size={11} style={{ opacity: 0.6 }} />
        <span>Maintenance</span>
        {open ? <ChevronUp size={11} /> : <ChevronDown size={11} />}
      </div>
      {open && (
        <div className="maintenance-actions">
          {MAINT_ACTIONS.map((a) => {
            const isThis = activeJob?.command === a.command
            return (
              <button
                key={a.key}
                className={`btn btn--ghost btn--xs maintenance-btn${isThis && running ? ' btn--running' : ''}`}
                onClick={() => onRun(a.command, a.args)}
                disabled={running}
                title={a.desc}
              >
                {isThis && running && <span className="btn-spinner" />}
                {a.label}
              </button>
            )
          })}
          {activeJob && (
            <div className="maintenance-status muted">
              Last: {activeJob.command} —{' '}
              <span style={{ color: jobStatusColor(activeJob.status) }}>{activeJob.status}</span>
              {(activeJob.status === 'succeeded' || activeJob.status === 'failed') && (
                <> · See <a href="/jobs" style={{ color: 'var(--accent)' }}>Jobs</a></>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const PRESETS: Preset[] = ['Clean', 'Normalize', 'Enrich', 'Full Pass']

export default function Collection() {
  // ── Tree / selection ────────────────────────────────────────────────────
  const [treeNodes, setTreeNodes]       = useState<DirNode[]>(FALLBACK_TREE)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [activeDir, setActiveDir]       = useState<string>('KKDJ')
  const [selectedExecutable, setSelectedExecutable] = useState<boolean>(true)

  // ── Track data ───────────────────────────────────────────────────────────
  const [tracks, setTracks]             = useState<TrackSummary[]>([])
  const [tracksLoading, setTracksLoading] = useState(false)
  const [globalCount, setGlobalCount]   = useState<number | null>(null)
  const [folderCount, setFolderCount]   = useState<number | null>(null)

  // ── Table state ──────────────────────────────────────────────────────────
  const [selectedTrack, setSelectedTrack] = useState<TrackSummary | null>(null)
  const [sortKey, setSortKey]             = useState<SortKey | null>('artist')
  const [sortDir, setSortDir]             = useState<SortDir>('asc')
  const [searchQ, setSearchQ]             = useState('')
  const [activePreset, setActivePreset]   = useState<Preset | null>(null)
  const [visibleCols, setVisibleCols]     = useState<Set<string>>(
    () => new Set(ALL_COLS.filter((c) => c.defaultVisible).map((c) => c.key))
  )
  const [colPickerOpen, setColPickerOpen] = useState(false)

  // ── Sanitize job ──────────────────────────────────────────────────────────
  const [sanitizeJob, setSanitizeJob] = useState<ActiveJob | null>(null)
  const [applyMode, setApplyMode]     = useState(false)
  const [liveLogs, setLiveLogs]       = useState<string[]>([])
  const [jobError, setJobError]       = useState<string | null>(null)
  const [jobSummary, setJobSummary]   = useState<string | null>(null)

  // ── Maintenance job ───────────────────────────────────────────────────────
  const [maintJob, setMaintJob] = useState<ActiveJob | null>(null)

  // ── Log panel ─────────────────────────────────────────────────────────────
  const [logOpen, setLogOpen]     = useState(true)
  const [userScrolled, setUserScrolled] = useState(false)
  const logBodyRef = useRef<HTMLDivElement>(null)

  // ── Load global count once ───────────────────────────────────────────────
  useEffect(() => {
    fetchTrackStats()
      .then((s) => setGlobalCount(s.total))
      .catch(() => {})
  }, [])

  // ── Load directory tree on mount ─────────────────────────────────────────
  useEffect(() => {
    fetchLibraryTree(3)
      .then((res) => setTreeNodes([apiNodeToDirNode(res.root)]))
      .catch(() => { /* keep fallback */ })
  }, [])

  // ── Load tracks + folder count when path changes ─────────────────────────
  useEffect(() => {
    setTracksLoading(true)
    setTracks([])
    setSelectedTrack(null)

    const fetchParams = { path: selectedPath ?? undefined, limit: 500 }
    Promise.all([
      fetchTracks(fetchParams),
      fetchLibraryStats(selectedPath),
    ])
      .then(([rows, stats]) => {
        setTracks(rows)
        setFolderCount(stats.folder_count)
        if (globalCount === null) setGlobalCount(stats.global_count)
      })
      .catch(() => {
        setTracks([])
        setFolderCount(0)
      })
      .finally(() => setTracksLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPath])

  // ── Auto-scroll log ───────────────────────────────────────────────────────
  useEffect(() => {
    if (userScrolled || !logBodyRef.current) return
    logBodyRef.current.scrollTop = logBodyRef.current.scrollHeight
  }, [liveLogs, userScrolled])

  // ── Reset scroll on new job ───────────────────────────────────────────────
  useEffect(() => {
    setUserScrolled(false)
    if (logBodyRef.current) logBodyRef.current.scrollTop = 0
  }, [sanitizeJob?.id])

  // ── Poll sanitize job ─────────────────────────────────────────────────────
  useEffect(() => {
    if (!sanitizeJob) return
    if (sanitizeJob.status !== 'pending' && sanitizeJob.status !== 'running') return

    let stopped = false

    async function poll() {
      if (stopped) return
      try {
        const [job, logText] = await Promise.all([
          fetchJob(sanitizeJob!.id),
          fetchJobLogs(sanitizeJob!.id, 100),
        ])
        if (stopped) return
        const lines = logText.split('\n').filter((l) => l.trim())
        setLiveLogs(lines)
        const summaryLine = lines.slice().reverse().find((l) =>
          /metadata-sanitize.*(scanned|DRY-RUN|APPLY)/i.test(l)
        )
        if (summaryLine) setJobSummary(summaryLine)
        setSanitizeJob((prev) =>
          prev && prev.id === job.id && prev.status !== job.status
            ? { ...prev, status: job.status }
            : prev
        )
      } catch { /* network blip */ }
      if (!stopped) setTimeout(poll, 2000)
    }

    poll()
    return () => { stopped = true }
  }, [sanitizeJob?.id, sanitizeJob?.status])

  // ── Poll maintenance job status ───────────────────────────────────────────
  useEffect(() => {
    if (!maintJob) return
    if (maintJob.status !== 'pending' && maintJob.status !== 'running') return

    let stopped = false
    async function poll() {
      if (stopped) return
      try {
        const job = await fetchJob(maintJob!.id)
        if (stopped) return
        if (job.status !== maintJob!.status) {
          setMaintJob((prev) => prev ? { ...prev, status: job.status } : prev)
        }
      } catch { /* ignore */ }
      if (!stopped) setTimeout(poll, 3000)
    }
    poll()
    return () => { stopped = true }
  }, [maintJob?.id, maintJob?.status])

  // ── Handlers ──────────────────────────────────────────────────────────────

  const handleRunClean = useCallback(async () => {
    if (sanitizeJob?.status === 'pending' || sanitizeJob?.status === 'running') return
    if (!selectedPath || !selectedExecutable) {
      setJobError(`"${activeDir}" is not a real library path. Select a folder from the tree.`)
      return
    }
    setJobError(null)
    setJobSummary(null)
    setLiveLogs([])
    try {
      const args: string[] = ['--input', selectedPath]
      if (applyMode) args.push('--apply')
      const job = await submitJob({ command: 'metadata-sanitize', args })
      setSanitizeJob({ id: job.id, status: job.status, command: 'metadata-sanitize' })
      setActivePreset('Clean')
    } catch (err) {
      setJobError(err instanceof Error ? err.message : 'Failed to start job')
    }
  }, [sanitizeJob, applyMode, selectedPath, selectedExecutable, activeDir])

  const handleCancelClean = useCallback(async () => {
    if (!sanitizeJob) return
    try { await cancelJob(sanitizeJob.id) } catch { /* ignore */ }
  }, [sanitizeJob])

  async function handleMaintenance(command: string, args: string[]) {
    if (maintJob?.status === 'pending' || maintJob?.status === 'running') return
    try {
      const job = await submitJob({ command, args })
      setMaintJob({ id: job.id, status: job.status, command })
      setLiveLogs([`[${new Date().toLocaleTimeString()}] Submitted: ${command} (job ${job.id})`])
      setLogOpen(true)
    } catch (err) {
      setLiveLogs([`Error submitting ${command}: ${err instanceof Error ? err.message : String(err)}`])
      setLogOpen(true)
    }
  }

  function handleSelectDir(label: string, path: string | undefined, executable: boolean) {
    setActiveDir(label)
    setSelectedPath(path ?? null)
    setSelectedExecutable(executable)
    setSearchQ('')
  }

  function handleSort(key: SortKey) {
    if (sortKey === key) setSortDir((d) => (d === 'asc' ? 'desc' : 'asc'))
    else { setSortKey(key); setSortDir('asc') }
  }

  function toggleCol(key: string, on: boolean) {
    setVisibleCols((prev) => {
      const next = new Set(prev)
      on ? next.add(key) : next.delete(key)
      return next
    })
  }

  // ── Derived ───────────────────────────────────────────────────────────────

  let displayTracks = tracks
  if (searchQ.trim()) {
    const q = searchQ.toLowerCase()
    displayTracks = displayTracks.filter((t) =>
      (t.artist  ?? '').toLowerCase().includes(q) ||
      (t.title   ?? '').toLowerCase().includes(q) ||
      t.filename.toLowerCase().includes(q)
    )
  }
  if (sortKey) displayTracks = sortTracks(displayTracks, sortKey, sortDir)

  const visibleDefs = ALL_COLS.filter((c) => visibleCols.has(c.key))

  // Logs: real job logs when active, else preset hint
  const activeLogs: string[] = (sanitizeJob && liveLogs.length > 0)
    ? liveLogs
    : liveLogs.length > 0
      ? liveLogs
      : (activePreset ? PRESET_LOGS[activePreset] : PRESET_LOGS.default)

  // Active key for the Camelot wheel
  const activeKey = selectedTrack?.key_camelot ?? selectedTrack?.key_musical ?? null

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="collection-ws">

      {/* ── Toolbar ──────────────────────────────────────────────────── */}
      <div className="collection-toolbar">
        <div className="toolbar-left">
          <span className="toolbar-brand">Collection</span>
          <span className="toolbar-count muted">
            {tracksLoading ? (
              'Loading…'
            ) : (
              <>
                <span title="Tracks in selected folder">
                  {displayTracks.length.toLocaleString()}
                  {searchQ && ` of ${tracks.length.toLocaleString()}`}
                  {' in folder'}
                </span>
                {globalCount != null && (
                  <> · <span title="Total tracks across whole library">{globalCount.toLocaleString()} total</span></>
                )}
                {activeDir !== 'KKDJ' && (
                  <> · <span style={{ color: 'var(--accent)' }}>{activeDir}</span></>
                )}
              </>
            )}
          </span>
        </div>

        <div className="toolbar-jobs">
          <span className="toolbar-jobs-label">Run:</span>

          {(sanitizeJob?.status === 'pending' || sanitizeJob?.status === 'running') ? (
            <>
              <button className="btn btn--ghost btn--sm job-preset-btn btn--running" disabled>
                <span className="btn-spinner" />
                Clean
              </button>
              <button
                className="btn btn--ghost btn--sm"
                onClick={handleCancelClean}
                style={{ fontSize: 11 }}
              >
                ✕ Cancel
              </button>
            </>
          ) : (
            <button
              className={[
                'btn btn--ghost btn--sm job-preset-btn',
                sanitizeJob?.status === 'succeeded' ? 'btn--succeeded' : '',
                sanitizeJob?.status === 'failed'    ? 'btn--failed'    : '',
                activePreset === 'Clean' && !sanitizeJob ? 'btn--active' : '',
              ].filter(Boolean).join(' ')}
              onClick={handleRunClean}
              title={applyMode
                ? 'Run metadata-sanitize --apply (writes tags)'
                : 'Run metadata-sanitize preview (no writes)'}
            >
              <Play size={10} />
              Clean
            </button>
          )}

          {(['Normalize', 'Enrich', 'Full Pass'] as const).map((label) => (
            <button
              key={label}
              className={`btn btn--ghost btn--sm job-preset-btn${activePreset === label ? ' btn--active' : ''}`}
              onClick={() => setActivePreset((p) => p === label ? null : label)}
              title="Not wired yet — preview only"
            >
              <Play size={10} />
              {label}
            </button>
          ))}

          <label
            className={`apply-toggle${applyMode ? ' apply-toggle--on' : ''}`}
            title={applyMode ? 'Apply mode ON — tag changes will be written' : 'Preview mode — no writes'}
          >
            <input
              type="checkbox"
              checked={applyMode}
              onChange={(e) => setApplyMode(e.target.checked)}
              disabled={sanitizeJob?.status === 'pending' || sanitizeJob?.status === 'running'}
            />
            Apply
          </label>
        </div>

        <div className="toolbar-right">
          <div className="toolbar-search">
            <Search size={12} className="toolbar-search-icon" />
            <input
              className="toolbar-search-input"
              type="search"
              placeholder="Search…"
              value={searchQ}
              onChange={(e) => setSearchQ(e.target.value)}
              aria-label="Search tracks"
            />
          </div>
          <div style={{ position: 'relative' }}>
            <button
              className={`btn btn--ghost btn--sm${colPickerOpen ? ' btn--active' : ''}`}
              onClick={() => setColPickerOpen(!colPickerOpen)}
              title="Choose visible columns"
            >
              <Columns3 size={13} />
              Columns
            </button>
            {colPickerOpen && (
              <ColPicker
                cols={ALL_COLS}
                visible={visibleCols}
                onChange={toggleCol}
                onClose={() => setColPickerOpen(false)}
              />
            )}
          </div>
        </div>
      </div>

      {/* ── Body ─────────────────────────────────────────────────────── */}
      <div className="collection-body">

        {/* Left: directory tree */}
        <div className="collection-tree">
          <div className="tree-header">Library</div>
          {treeNodes.map((node) => (
            <DirTreeNode
              key={node.path ?? node.label}
              node={node}
              depth={0}
              activePath={selectedPath}
              onSelectDir={handleSelectDir}
            />
          ))}
          {selectedPath && (
            <div className="tree-path-hint" title={selectedPath}>
              {selectedPath}
            </div>
          )}

          <MaintenancePanel activeJob={maintJob} onRun={handleMaintenance} />
        </div>

        {/* Center: analysis bar + table */}
        <div className="collection-main">
          <div className="collection-analysis-bar">
            <div className="analysis-widget">
              <div className="analysis-widget-label">Camelot</div>
              <CamelotWheel activeKey={activeKey} />
            </div>
            <div className="analysis-stats">
              {[
                { value: selectedTrack?.bpm != null ? selectedTrack.bpm.toFixed(1) : '—', label: 'BPM', mono: true },
                { value: activeKey ?? '—',                                                 label: 'Key', mono: true },
                { value: formatDuration(selectedTrack?.duration_sec),                      label: 'Dur', mono: true },
              ].map(({ value, label, mono }) => (
                <div key={label} className="analysis-stat">
                  <span className={`analysis-stat-value${mono ? ' td-mono' : ''}`}>{value}</span>
                  <span className="analysis-stat-label">{label}</span>
                </div>
              ))}
            </div>
            {selectedTrack && (
              <div className="analysis-track-label">
                <span className="analysis-track-artist">{selectedTrack.artist}</span>
                <span className="analysis-track-title muted"> — {selectedTrack.title}</span>
              </div>
            )}
          </div>

          {/* Table */}
          <div className="collection-table-scroll">
            {tracksLoading ? (
              <p className="empty-state" style={{ padding: '20px 24px' }}>Loading tracks…</p>
            ) : displayTracks.length === 0 ? (
              <p className="empty-state" style={{ padding: '20px 24px' }}>
                {searchQ
                  ? 'No tracks match that search.'
                  : selectedPath
                    ? 'No tracks in this folder (or pipeline DB has no records for it).'
                    : 'Select a folder to view tracks.'}
              </p>
            ) : (
              <table className="table collection-table">
                <thead>
                  <tr>
                    {visibleDefs.map((col) => (
                      <SortTh
                        key={col.key}
                        col={col}
                        sortKey={sortKey}
                        sortDir={sortDir}
                        onSort={handleSort}
                      />
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {displayTracks.map((track) => {
                    const isSelected = selectedTrack?.id === track.id
                    const key  = track.key_camelot ?? track.key_musical ?? '—'
                    const qual = (track.quality_tier ?? 'unknown').toLowerCase()
                    return (
                      <tr
                        key={track.id}
                        className={[
                          'track-row',
                          track.status === 'error'        ? 'row--failed'            : '',
                          track.status === 'needs_review' ? 'track-row--has-issues'  : '',
                          isSelected                      ? 'track-row--selected'    : '',
                        ].filter(Boolean).join(' ')}
                        onClick={() => setSelectedTrack(isSelected ? null : track)}
                      >
                        {visibleCols.has('cover') && (
                          <td><div className="track-cover-art">{(track.artist ?? '?').slice(0, 1)}</div></td>
                        )}
                        {visibleCols.has('artist') && (
                          <td className="td-artist td-bold">{track.artist ?? '—'}</td>
                        )}
                        {visibleCols.has('title') && (
                          <td className="td-title">{track.title ?? '—'}</td>
                        )}
                        {visibleCols.has('bpm') && (
                          <td className="td-mono">{track.bpm != null ? track.bpm.toFixed(1) : '—'}</td>
                        )}
                        {visibleCols.has('key') && (
                          <td className="td-mono">{key}</td>
                        )}
                        {visibleCols.has('duration') && (
                          <td className="td-mono">{formatDuration(track.duration_sec)}</td>
                        )}
                        {visibleCols.has('status') && (
                          <td><span className={`badge badge--track-${track.status}`}>{track.status}</span></td>
                        )}
                        {visibleCols.has('quality') && (
                          <td><span className={`quality-badge quality-badge--${qual}`}>{track.quality_tier ?? '—'}</span></td>
                        )}
                        {visibleCols.has('genre') && (
                          <td className="muted" style={{ fontSize: 12 }}>{track.genre ?? '—'}</td>
                        )}
                        {visibleCols.has('bitrate') && (
                          <td className="td-mono" style={{ fontSize: 11 }}>{track.bitrate_kbps ?? '—'}</td>
                        )}
                        {visibleCols.has('filename') && (
                          <td className="muted" style={{ fontSize: 11 }}>{track.filename}</td>
                        )}
                        {visibleCols.has('issues') && (
                          <td>
                            {track.issues.length > 0
                              ? <span className="badge badge--track-error">{track.issues.length}</span>
                              : <span className="muted">—</span>}
                          </td>
                        )}
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>

        {/* Right: inspector */}
        {selectedTrack
          ? <Inspector track={selectedTrack} onClose={() => setSelectedTrack(null)} />
          : (
            <div className="collection-inspector collection-inspector--empty">
              <span className="muted" style={{ fontSize: 12 }}>Select a track to inspect</span>
            </div>
          )
        }
      </div>

      {/* ── Log panel ────────────────────────────────────────────────── */}
      <div className={`collection-log${logOpen ? '' : ' collection-log--collapsed'}`}>
        <div className="log-panel-header" onClick={() => setLogOpen(!logOpen)}>
          <span className="log-panel-title">Log</span>
          <span className="muted" style={{ fontSize: 11 }}>
            {activeLogs.length} entries
            {sanitizeJob && (
              <> · <span style={{ color: jobStatusColor(sanitizeJob.status) }}>
                {sanitizeJob.status}
              </span></>
            )}
            {jobSummary && !sanitizeJob && <> · {jobSummary}</>}
            {!sanitizeJob && activePreset && (
              <> · <span style={{ color: 'var(--accent)' }}>{activePreset}</span></>
            )}
          </span>
          {logOpen ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </div>
        {logOpen && (
          <div
            className="log-panel-body"
            ref={logBodyRef}
            onScroll={(e) => {
              const el = e.currentTarget
              const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 30
              setUserScrolled(!atBottom)
            }}
          >
            {jobError && (
              <div className="log-line log-line--error">Error: {jobError}</div>
            )}
            {activeLogs.map((line, i) => (
              <div
                key={i}
                className={`log-line${
                  /ERROR|failed|FAIL/i.test(line) ? ' log-line--error' :
                  /skipped|SKIP|warn/i.test(line) ? ' log-line--warn'  : ''
                }`}
              >
                {line}
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  )
}
