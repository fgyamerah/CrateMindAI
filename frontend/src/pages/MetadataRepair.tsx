import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { useLocation } from 'react-router-dom'
import { Check, Clock3, Edit3, Loader2, Pencil, RefreshCw, ShieldAlert, X, Wrench } from 'lucide-react'
import { ApiError } from '../api/client'
import ManualMetadataEditor from '../components/ManualMetadataEditor'
import type { ManualMetadataApplyResponse } from '../api/manualMetadata'
import {
  approveMetadataRepairField,
  approveMetadataRepair,
  applyMetadataRepairApproved,
  deferMetadataRepairField,
  deferMetadataRepair,
  dryRunMetadataRepairApply,
  fetchMetadataRepairQueue,
  fetchMetadataRepairSummary,
  rejectMetadataRepairField,
  rejectMetadataRepair,
  updateMetadataRepairFieldProposal,
} from '../api/metadataRepair'
import ErrorBanner from '../components/ErrorBanner'
import PageHeader from '../components/PageHeader'
import type {
  MetadataRepairApplyResponse,
  MetadataRepairFieldName,
  MetadataRepairProposal,
  MetadataRepairSummary,
} from '../types/metadataRepair'

type SortMode = 'confidence_newest' | 'newest' | 'filename'
type ReviewAction = 'approve' | 'reject' | 'defer'
type FieldReviewAction = ReviewAction
type BulkAction = 'approve-selected' | 'reject-selected' | 'defer-selected' | 'approve-high' | 'approve-visible'

interface RepairUiState {
  repairType: string
  confidence: string
  status: string
  sortMode: SortMode
  denseMode: boolean
  showLow: boolean
  showApplied: boolean
}

interface ConfirmAction {
  kind: BulkAction
  label: string
  ids: number[]
}

interface ManualEditTarget {
  track_id: number
  artist: string | null
  title: string | null
  filename?: string | null
  filepath?: string | null
}

const STORAGE_KEY = 'metadata-repair.ui.v2'
const DEFAULT_UI: RepairUiState = {
  repairType: '',
  confidence: '',
  status: '',
  sortMode: 'confidence_newest',
  denseMode: true,
  showLow: false,
  showApplied: false,
}

const CONFIDENCE_ORDER: Record<string, number> = {
  HIGH: 0,
  MEDIUM: 1,
  REVIEW_REQUIRED: 2,
  LOW: 3,
}
const ALLOWED_CONFIDENCE_FILTERS = new Set(['', 'HIGH', 'MEDIUM', 'LOW', 'REVIEW_REQUIRED'])
const ALLOWED_STATUS_FILTERS = new Set(['', 'PENDING', 'APPROVED', 'PARTIAL', 'REJECTED', 'APPLIED', 'PARTIAL_APPLIED', 'NO_OP'])

const ROW_HEIGHT_DENSE = 88
const ROW_HEIGHT_COMPACT = 112
const OVERSCAN_ROWS = 8

function display(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  return String(value)
}

function parseTrackId(search: string): number | null {
  const value = new URLSearchParams(search).get('track')
  if (!value) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function confidenceKey(value: string | null | undefined): string {
  return String(value || '').trim().toUpperCase()
}

function confidenceLabel(value: string | null | undefined): string {
  const raw = confidenceKey(value)
  return raw ? raw.replace(/_/g, ' ') : 'UNKNOWN'
}

function confidenceClass(value: string | null | undefined): string {
  const tier = confidenceKey(value)
  if (tier === 'HIGH') return 'badge--succeeded'
  if (tier === 'MEDIUM') return 'badge--pending'
  if (tier === 'LOW') return 'badge--failed'
  return 'badge--info'
}

function statusClass(value: string | null | undefined): string {
  const status = String(value || '').toUpperCase()
  if (status === 'APPROVED' || status === 'APPLIED') return 'badge--succeeded'
  if (status === 'PARTIAL' || status === 'PARTIAL_APPLIED') return 'badge--pending'
  if (status === 'REJECTED') return 'badge--failed'
  if (status === 'PENDING' || status === 'DEFERRED' || status === 'NO_OP') return 'badge--info'
  return 'badge--pending'
}

function fieldStatusClass(value: string | null | undefined): string {
  return statusClass(value)
}

function uniqueValues(items: MetadataRepairProposal[], field: 'repair_type' | 'confidence' | 'status'): string[] {
  return Array.from(new Set(items.map((item) => String(item[field] || '')).filter(Boolean))).sort()
}

function isSortMode(value: unknown): value is SortMode {
  return value === 'confidence_newest' || value === 'newest' || value === 'filename'
}

function isString(value: unknown): value is string {
  return typeof value === 'string'
}

function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean'
}

function isConfidenceFilter(value: unknown): value is string {
  return isString(value) && ALLOWED_CONFIDENCE_FILTERS.has(value.toUpperCase())
}

function isStatusFilter(value: unknown): value is string {
  return isString(value) && ALLOWED_STATUS_FILTERS.has(value.trim().toUpperCase())
}

function loadUiState(): RepairUiState {
  if (typeof window === 'undefined') return DEFAULT_UI
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_UI
    const parsed = JSON.parse(raw) as Record<string, unknown>
    const parsedStatus = isString(parsed.status) ? parsed.status.trim().toUpperCase() : DEFAULT_UI.status
    return {
      repairType: isString(parsed.repairType) ? parsed.repairType : DEFAULT_UI.repairType,
      confidence: isConfidenceFilter(parsed.confidence) ? parsed.confidence : DEFAULT_UI.confidence,
      status: isStatusFilter(parsedStatus) ? parsedStatus : DEFAULT_UI.status,
      sortMode: isSortMode(parsed.sortMode) ? parsed.sortMode : DEFAULT_UI.sortMode,
      denseMode: isBoolean(parsed.denseMode) ? parsed.denseMode : DEFAULT_UI.denseMode,
      showLow: isBoolean(parsed.showLow) ? parsed.showLow : DEFAULT_UI.showLow,
      showApplied: isBoolean(parsed.showApplied) ? parsed.showApplied : DEFAULT_UI.showApplied,
    }
  } catch {
    return DEFAULT_UI
  }
}

function riskBadgeLabel(flag: string): string {
  return flag
}

function valueClass(changed: boolean, side: 'current' | 'proposed'): string {
  if (!changed) return 'metadata-repair-value'
  return side === 'current'
    ? 'metadata-repair-value metadata-repair-value--removed'
    : 'metadata-repair-value metadata-repair-value--added'
}

function fieldLabel(field: MetadataRepairFieldName): string {
  return field.charAt(0).toUpperCase() + field.slice(1)
}

function FieldActionButton({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string
  icon: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button className="icon-btn icon-btn--sm" title={label} aria-label={label} disabled={disabled} onClick={onClick}>
      {icon}
    </button>
  )
}

function EditableProposedValue({
  value,
  originalValue,
  changed,
  edited,
  compact,
  busy,
  onSave,
}: {
  value: string
  originalValue: string
  changed: boolean
  edited?: boolean
  compact?: boolean
  busy?: boolean
  onSave?: (value: string) => void | Promise<void>
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value === '—' ? '' : value)
  const cancelEditRef = useRef(false)

  useEffect(() => {
    if (!editing) setDraft(value === '—' ? '' : value)
  }, [editing, value])

  async function saveDraft() {
    const cleaned = draft.trim()
    if (!cleaned || cleaned === value || !onSave) {
      setEditing(false)
      return
    }
    await onSave(cleaned)
    setEditing(false)
  }

  if (editing && onSave) {
    return (
      <div className={`${valueClass(changed, 'proposed')} metadata-repair-value--editing`}>
        <span>proposed</span>
        <input
          className="metadata-repair-proposed-input"
          value={draft}
          autoFocus
          disabled={busy}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => {
            if (cancelEditRef.current) {
              cancelEditRef.current = false
              setDraft(value === '—' ? '' : value)
              setEditing(false)
              return
            }
            void saveDraft()
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') {
              event.preventDefault()
              void saveDraft()
            }
            if (event.key === 'Escape') {
              event.preventDefault()
              cancelEditRef.current = true
              event.currentTarget.blur()
            }
          }}
        />
      </div>
    )
  }

  return (
    <button
      type="button"
      className={`${valueClass(changed, 'proposed')} metadata-repair-value--button${compact ? ' metadata-repair-value--compact-button' : ''}`}
      onClick={() => {
        if (!busy && onSave) setEditing(true)
      }}
      disabled={busy || !onSave}
      title={onSave ? 'Edit proposed value' : undefined}
    >
      <span>
        proposed
        {edited && <em className="metadata-repair-edited-badge" title={`Original: ${originalValue}`}>EDITED</em>}
      </span>
      <strong>{value}</strong>
      {onSave && <Pencil size={12} className="metadata-repair-edit-icon" aria-hidden="true" />}
    </button>
  )
}

function MetadataFieldDiff({
  item,
  field,
  compact = false,
  onAction,
  onProposalSave,
  busy,
}: {
  item: MetadataRepairProposal
  field: MetadataRepairFieldName
  compact?: boolean
  onAction?: (field: MetadataRepairFieldName, action: FieldReviewAction) => void
  onProposalSave?: (field: MetadataRepairFieldName, value: string) => void | Promise<void>
  busy?: boolean
}) {
  const fieldState = item.fields[field]
  const currentValue = display(fieldState.current)
  const proposedValue = display(fieldState.proposed)
  const originalProposedValue = display(fieldState.original_proposed ?? fieldState.proposed)
  const changed = currentValue !== proposedValue
  return (
    <div className={`metadata-repair-field${compact ? ' metadata-repair-field--compact' : ''}`}>
      <div className="metadata-repair-field-head">
        <div className="metadata-repair-field-title">
          <span className="metadata-repair-diff-label">{fieldLabel(field)}</span>
          <span className={`badge ${fieldStatusClass(fieldState.status)} metadata-repair-field-status`}>
            {String(fieldState.status || 'pending').toUpperCase()}
          </span>
        </div>
        {onAction && (
          <div className="metadata-repair-field-actions">
            <FieldActionButton
              label={`Approve ${fieldLabel(field)}`}
              icon={<Check size={13} />}
              disabled={busy}
              onClick={() => onAction(field, 'approve')}
            />
            <FieldActionButton
              label={`Reject ${fieldLabel(field)}`}
              icon={<X size={13} />}
              disabled={busy}
              onClick={() => onAction(field, 'reject')}
            />
            <FieldActionButton
              label={`Defer ${fieldLabel(field)}`}
              icon={<Clock3 size={13} />}
              disabled={busy}
              onClick={() => onAction(field, 'defer')}
            />
          </div>
        )}
      </div>
      <div className="metadata-repair-field-grid">
        <div className={valueClass(changed, 'current')}>
          <span>current</span>
          <strong>{currentValue}</strong>
        </div>
        <div className="metadata-repair-diff-arrow" aria-hidden="true">→</div>
        <EditableProposedValue
          value={proposedValue}
          originalValue={originalProposedValue}
          changed={changed}
          edited={fieldState.edited}
          compact={compact}
          busy={busy}
          onSave={onProposalSave ? (value) => onProposalSave(field, value) : undefined}
        />
      </div>
    </div>
  )
}

function MetadataDiff({
  item,
  compact = false,
  onAction,
  onProposalSave,
  busy,
}: {
  item: MetadataRepairProposal
  compact?: boolean
  onAction?: (field: MetadataRepairFieldName, action: FieldReviewAction) => void
  onProposalSave?: (field: MetadataRepairFieldName, value: string) => void | Promise<void>
  busy?: boolean
}) {
  return (
    <div className={`metadata-repair-diff metadata-repair-diff--stacked${compact ? ' metadata-repair-diff--compact' : ''}`}>
      <MetadataFieldDiff item={item} field="artist" compact={compact} onAction={onAction} onProposalSave={onProposalSave} busy={busy} />
      <MetadataFieldDiff item={item} field="title" compact={compact} onAction={onAction} onProposalSave={onProposalSave} busy={busy} />
    </div>
  )
}

function MetadataRiskBadges({ flags }: { flags: string[] }) {
  if (!flags.length) return <span className="metadata-repair-muted">clean</span>
  return (
    <div className="metadata-repair-risk-row">
      {flags.map((flag) => (
        <span key={flag} className="metadata-repair-risk-badge" title={flag}>
          {riskBadgeLabel(flag)}
        </span>
      ))}
    </div>
  )
}

function ReviewButton({
  label,
  icon,
  onClick,
  disabled,
}: {
  label: string
  icon: ReactNode
  onClick: () => void
  disabled?: boolean
}) {
  return (
    <button className="icon-btn" title={label} aria-label={label} disabled={disabled} onClick={onClick}>
      {icon}
    </button>
  )
}

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  const tag = target.tagName.toLowerCase()
  return target.isContentEditable || tag === 'input' || tag === 'textarea' || tag === 'select'
}

export default function MetadataRepair() {
  const location = useLocation()
  const requestedTrackId = useMemo(() => parseTrackId(location.search), [location.search])
  const noticeFromRoute = (location.state as { notice?: string } | null | undefined)?.notice ?? null
  const [summary, setSummary] = useState<MetadataRepairSummary | null>(null)
  const [items, setItems] = useState<MetadataRepairProposal[]>([])
  const [ui, setUi] = useState<RepairUiState>(() => loadUiState())
  const [selectedId, setSelectedId] = useState<number | null>(requestedTrackId)
  const [selectedIds, setSelectedIds] = useState<number[]>([])
  const [confirmAction, setConfirmAction] = useState<ConfirmAction | null>(null)
  const [applyPreview, setApplyPreview] = useState<MetadataRepairApplyResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [rowBusyId, setRowBusyId] = useState<number | null>(null)
  const [bulkBusy, setBulkBusy] = useState(false)
  const [applyBusy, setApplyBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(noticeFromRoute)
  const [manualEditTarget, setManualEditTarget] = useState<ManualEditTarget | null>(null)
  const [scrollTop, setScrollTop] = useState(0)
  const [viewportHeight, setViewportHeight] = useState(560)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const selectAllRef = useRef<HTMLInputElement | null>(null)
  const scrollTopRef = useRef(0)
  const scrollFrameRef = useRef<number | null>(null)
  const scrollSelectedOnNextChangeRef = useRef(false)
  const requestedTrackScrollRef = useRef<number | null>(requestedTrackId)
  const deepLinkScrollPendingRef = useRef(false)

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(ui))
    } catch {
      // ignore storage failures
    }
  }, [ui])

  async function loadQueue() {
    setLoading(true)
    setError(null)
    try {
      const [summaryData, queueData] = await Promise.all([
        fetchMetadataRepairSummary(),
        fetchMetadataRepairQueue({ include_applied: ui.showApplied }),
      ])
      setSummary(summaryData)
      setItems(queueData.items)
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void loadQueue()
  }, [])

  useEffect(() => {
    void loadQueue()
  }, [ui.showApplied])

  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const update = () => setViewportHeight(el.clientHeight || 560)
    update()
    if (typeof ResizeObserver !== 'undefined') {
      const observer = new ResizeObserver(update)
      observer.observe(el)
      return () => observer.disconnect()
    }
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [ui.denseMode])

  useEffect(() => {
    requestedTrackScrollRef.current = requestedTrackId
    deepLinkScrollPendingRef.current = requestedTrackId != null
    if (requestedTrackId) {
      setSelectedId(requestedTrackId)
    }
  }, [requestedTrackId])

  useEffect(() => {
    if (noticeFromRoute) setSuccess(noticeFromRoute)
  }, [noticeFromRoute])

  const contextItems = useMemo(() => {
    const repairType = ui.repairType.trim()
    const status = ui.status.trim().toUpperCase()
    return items.filter((item) => {
      if (repairType && item.repair_type !== repairType) return false
      if (status && String(item.status || '').toUpperCase() !== status) return false
      return true
    })
  }, [items, ui.repairType, ui.confidence, ui.status])

  const visibleItems = useMemo(() => {
    const confidence = confidenceKey(ui.confidence)
    const showLow = ui.showLow || confidence === 'LOW'
    const filtered = contextItems.filter((item) => {
      const itemConfidence = confidenceKey(item.confidence)
      if (!showLow && itemConfidence === 'LOW') return false
      if (confidence && itemConfidence !== confidence) return false
      return true
    })
    const copy = [...filtered]
    copy.sort((left, right) => {
      if (ui.sortMode === 'filename') {
        const byName = left.filename.localeCompare(right.filename, undefined, { sensitivity: 'base' })
        return byName || right.track_id - left.track_id
      }
      if (ui.sortMode === 'newest') {
        const byDate = String(right.created_at || '').localeCompare(String(left.created_at || ''))
        return byDate || right.track_id - left.track_id
      }
      const leftRank = CONFIDENCE_ORDER[confidenceKey(left.confidence)] ?? 99
      const rightRank = CONFIDENCE_ORDER[confidenceKey(right.confidence)] ?? 99
      if (leftRank !== rightRank) return leftRank - rightRank
      const byDate = String(right.created_at || '').localeCompare(String(left.created_at || ''))
      if (byDate !== 0) return byDate
      const byName = left.filename.localeCompare(right.filename, undefined, { sensitivity: 'base' })
      return byName || right.track_id - left.track_id
    })
    return copy
  }, [contextItems, ui.confidence, ui.showLow, ui.sortMode])

  const visibleIds = useMemo(() => visibleItems.map((item) => item.track_id), [visibleItems])
  const visibleIdSet = useMemo(() => new Set(visibleIds), [visibleIds])
  const selectedSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const visibleSelectedIds = useMemo(() => visibleIds.filter((id) => selectedSet.has(id)), [visibleIds, selectedSet])
  const selected = useMemo(
    () => visibleItems.find((item) => item.track_id === selectedId) ?? null,
    [visibleItems, selectedId],
  )
  const repairTypes = useMemo(() => uniqueValues(items, 'repair_type'), [items])
  const confidences = useMemo(() => uniqueValues(items, 'confidence'), [items])
  const statuses = useMemo(() => uniqueValues(items, 'status'), [items])
  const selectedCount = visibleSelectedIds.length
  const visibleHighCount = useMemo(
    () => contextItems.filter((item) => confidenceKey(item.confidence) === 'HIGH').length,
    [contextItems],
  )
  const rowHeight = ui.denseMode ? ROW_HEIGHT_DENSE : ROW_HEIGHT_COMPACT
  const overscan = OVERSCAN_ROWS
  const virtualStart = Math.max(0, Math.floor(scrollTop / rowHeight) - overscan)
  const visibleRowCount = Math.ceil(viewportHeight / rowHeight) + overscan * 2
  const virtualEnd = Math.min(visibleItems.length, virtualStart + visibleRowCount)
  const virtualRows = visibleItems.slice(virtualStart, virtualEnd)
  const virtualTopPad = virtualStart * rowHeight
  const virtualBottomPad = Math.max(0, (visibleItems.length - virtualEnd) * rowHeight)
  const filterSortKey = useMemo(
    () => [
      ui.repairType.trim(),
      ui.confidence.trim().toUpperCase(),
      ui.status.trim().toUpperCase(),
      ui.sortMode,
      ui.showLow ? '1' : '0',
      ui.showApplied ? '1' : '0',
    ].join('|'),
    [ui.confidence, ui.repairType, ui.showApplied, ui.showLow, ui.sortMode, ui.status],
  )
  const prevFilterSortKeyRef = useRef(filterSortKey)

  useEffect(() => {
    if (prevFilterSortKeyRef.current === filterSortKey) return
    prevFilterSortKeyRef.current = filterSortKey
    scrollTopRef.current = 0
    setScrollTop(0)
    if (scrollRef.current) {
      scrollRef.current.scrollTop = 0
    }
  }, [filterSortKey])

  useEffect(() => {
    return () => {
      if (scrollFrameRef.current !== null) {
        window.cancelAnimationFrame(scrollFrameRef.current)
      }
    }
  }, [])

  useEffect(() => {
    if (!visibleItems.length) {
      setSelectedId(null)
      return
    }
    setSelectedId((current) => {
      if (requestedTrackScrollRef.current && visibleIdSet.has(requestedTrackScrollRef.current)) {
        return requestedTrackScrollRef.current
      }
      if (current && visibleIdSet.has(current)) return current
      return visibleItems[0]?.track_id ?? null
    })
  }, [visibleIdSet, visibleItems])

  useEffect(() => {
    if (
      deepLinkScrollPendingRef.current
      && requestedTrackScrollRef.current
      && selectedId === requestedTrackScrollRef.current
      && scrollRef.current
    ) {
      scrollSelectedOnNextChangeRef.current = true
      deepLinkScrollPendingRef.current = false
    }
  }, [selectedId])

  useEffect(() => {
    setSelectedIds((current) => current.filter((id) => visibleIdSet.has(id)))
  }, [visibleIdSet])

  useEffect(() => {
    const checkbox = selectAllRef.current
    if (!checkbox) return
    checkbox.indeterminate = selectedCount > 0 && selectedCount < visibleIds.length
  }, [selectedCount, visibleIds.length])

  useEffect(() => {
    if (!scrollSelectedOnNextChangeRef.current || !selectedId || !scrollRef.current) return
    const index = visibleItems.findIndex((item) => item.track_id === selectedId)
    if (index < 0) return
    const rowTop = index * rowHeight
    const rowBottom = rowTop + rowHeight
    const viewTop = scrollRef.current.scrollTop
    const viewBottom = viewTop + viewportHeight
    if (rowTop < viewTop) {
      scrollRef.current.scrollTop = rowTop
    } else if (rowBottom > viewBottom) {
      scrollRef.current.scrollTop = Math.max(0, rowBottom - viewportHeight)
    }
    scrollSelectedOnNextChangeRef.current = false
    requestedTrackScrollRef.current = null
    deepLinkScrollPendingRef.current = false
  }, [rowHeight, selectedId, visibleItems, viewportHeight])

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (confirmAction || isTypingTarget(event.target) || bulkBusy || applyBusy || rowBusyId !== null) return
      const key = event.key.toLowerCase()
      if (!visibleItems.length) return
      if (key === 'j' || key === 'k') {
        event.preventDefault()
        const currentIndex = Math.max(0, visibleItems.findIndex((item) => item.track_id === selectedId))
        const nextIndex = key === 'j'
          ? (currentIndex + 1) % visibleItems.length
          : (currentIndex - 1 + visibleItems.length) % visibleItems.length
        scrollSelectedOnNextChangeRef.current = true
        setSelectedId(visibleItems[nextIndex].track_id)
        return
      }
      if (key === 'a') {
        event.preventDefault()
        if (selectedId != null) {
          void reviewTrack(selectedId, 'approve')
        }
        return
      }
      if (key === 'r') {
        event.preventDefault()
        if (selectedId != null) {
          void reviewTrack(selectedId, 'reject')
        }
        return
      }
      if (key === 'd') {
        event.preventDefault()
        if (selectedId != null) {
          void reviewTrack(selectedId, 'defer')
        }
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [applyBusy, bulkBusy, confirmAction, rowBusyId, selectedId, visibleItems])

  useEffect(() => {
    if (!confirmAction) return
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [confirmAction])

  async function reviewTrack(trackId: number, action: ReviewAction) {
    setRowBusyId(trackId)
    setError(null)
    try {
      if (action === 'approve') await approveMetadataRepair(trackId)
      if (action === 'reject') await rejectMetadataRepair(trackId)
      if (action === 'defer') await deferMetadataRepair(trackId)
      setSelectedIds([])
      setApplyPreview(null)
      await loadQueue()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setRowBusyId(null)
    }
  }

  async function reviewField(trackId: number, field: MetadataRepairFieldName, action: FieldReviewAction) {
    setRowBusyId(trackId)
    setError(null)
    try {
      if (action === 'approve') await approveMetadataRepairField(trackId, field)
      if (action === 'reject') await rejectMetadataRepairField(trackId, field)
      if (action === 'defer') await deferMetadataRepairField(trackId, field)
      setSelectedIds([])
      setApplyPreview(null)
      await loadQueue()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setRowBusyId(null)
    }
  }

  async function saveFieldProposal(trackId: number, field: MetadataRepairFieldName, proposed: string) {
    const cleaned = proposed.trim()
    if (!cleaned) {
      setError('Proposed metadata value cannot be empty.')
      return
    }
    setRowBusyId(trackId)
    setError(null)
    try {
      await updateMetadataRepairFieldProposal(trackId, field, cleaned)
      setApplyPreview(null)
      await loadQueue()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setRowBusyId(null)
    }
  }

  async function runBulkReview(ids: number[], action: ReviewAction) {
    if (!ids.length) return
    setBulkBusy(true)
    setError(null)
    try {
      for (const id of ids) {
        if (action === 'approve') await approveMetadataRepair(id)
        if (action === 'reject') await rejectMetadataRepair(id)
        if (action === 'defer') await deferMetadataRepair(id)
      }
      setSelectedIds([])
      setApplyPreview(null)
      await loadQueue()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setBulkBusy(false)
    }
  }

  function requestBulkAction(kind: BulkAction) {
    if (bulkBusy) return
    const ids =
      kind === 'approve-selected' || kind === 'reject-selected' || kind === 'defer-selected'
        ? visibleSelectedIds
        : kind === 'approve-high'
          ? contextItems.filter((item) => confidenceKey(item.confidence) === 'HIGH').map((item) => item.track_id)
          : visibleIds
    const labelMap: Record<BulkAction, string> = {
      'approve-selected': `Approve ${visibleSelectedIds.length} selected`,
      'reject-selected': `Reject ${visibleSelectedIds.length} selected`,
      'defer-selected': `Defer ${visibleSelectedIds.length} selected`,
      'approve-high': `Approve all HIGH (${ids.length})`,
      'approve-visible': `Approve visible (${ids.length})`,
    }
    if (kind === 'approve-high' || kind === 'approve-visible') {
      setConfirmAction({ kind, label: labelMap[kind], ids })
      return
    }
    if (kind === 'approve-selected') void runBulkReview(ids, 'approve')
    if (kind === 'reject-selected') void runBulkReview(ids, 'reject')
    if (kind === 'defer-selected') void runBulkReview(ids, 'defer')
  }

  async function confirmBulkAction() {
    if (!confirmAction) return
    const ids = confirmAction.ids
    const kind = confirmAction.kind
    setConfirmAction(null)
    if (kind === 'approve-high' || kind === 'approve-visible') {
      await runBulkReview(ids, 'approve')
    }
  }

  async function dryRunApply() {
    setApplyBusy(true)
    setError(null)
    try {
      setApplyPreview(await dryRunMetadataRepairApply())
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setApplyBusy(false)
    }
  }

  async function applyApproved() {
    if (!applyPreview?.proposed_count) return
    setApplyBusy(true)
    setError(null)
    setSuccess(null)
    try {
      const result = await applyMetadataRepairApproved()
      const fieldCount = result.applied_field_count ?? result.changes.reduce((total, change) => {
        const fields = Array.isArray(change.changed_fields) ? change.changed_fields.length : 0
        return total + fields
      }, 0)
      setApplyPreview(result)
      setSelectedIds([])
      setSelectedId(null)
      setSuccess(`Applied ${fieldCount.toLocaleString()} field update${fieldCount === 1 ? '' : 's'}`)
      await loadQueue()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : String(err)
      setError(msg)
    } finally {
      setApplyBusy(false)
    }
  }

  async function handleManualMetadataApplied(result: ManualMetadataApplyResponse) {
    setSuccess(`Applied manual metadata edit: ${result.applied_fields.join(', ') || 'no changes'}`)
    setApplyPreview(null)
    await loadQueue()
  }

  function toggleSelectAllVisible() {
    setSelectedIds((current) => {
      const currentSet = new Set(current)
      if (visibleIds.every((id) => currentSet.has(id))) {
        return current.filter((id) => !visibleIdSet.has(id))
      }
      const merged = new Set(currentSet)
      visibleIds.forEach((id) => merged.add(id))
      return Array.from(merged)
    })
  }

  function toggleSelectedId(id: number) {
    setSelectedIds((current) => (
      current.includes(id)
        ? current.filter((value) => value !== id)
        : [...current, id]
    ))
  }

  return (
    <div className="page metadata-repair-page">
      <PageHeader
        title="Metadata Repair"
        subtitle="Deterministic artist/title repair proposals for the tracks database."
        actions={(
          <div className="metadata-repair-actions">
            <button className="btn btn--ghost btn--sm" onClick={() => void loadQueue()} disabled={loading || bulkBusy || applyBusy}>
              {loading ? <Loader2 size={13} className="spin" /> : <RefreshCw size={13} />}
              Refresh
            </button>
          </div>
        )}
      />

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}
      {success && <div className="metadata-repair-success-banner">{success}</div>}
      <div className="crate-apply-warning">
        <ShieldAlert size={15} />
        DB only. No tag writes. No audio file changes. Review the queue, bulk-select the safe rows, and keep risky items visible.
      </div>

      <div className="metadata-repair-topbar">
        <section className="metadata-repair-summary-strip">
          {[
            { label: 'Queue', value: summary?.queue_total ?? 0 },
            { label: 'Visible', value: visibleItems.length },
            { label: 'Selected', value: selectedCount },
            { label: 'Approved', value: summary?.approved_count ?? 0 },
            { label: 'Partial', value: summary?.partial_count ?? 0 },
            { label: 'Rejected', value: summary?.rejected_count ?? 0 },
            { label: 'Applied', value: summary?.applied_count ?? 0 },
            { label: 'No-op', value: summary?.no_op_count ?? 0 },
            { label: 'Pending', value: summary?.pending_count ?? 0 },
          ].map((stat) => (
            <div key={stat.label} className="metadata-repair-stat">
              <span>{stat.label}</span>
              <strong>{stat.value.toLocaleString()}</strong>
            </div>
          ))}
        </section>

        <section className="metadata-repair-controls">
          <div className="metadata-repair-toolbar-group metadata-repair-toolbar-group--filters">
            <select className="form-select" value={ui.repairType} onChange={(event) => setUi((current) => ({ ...current, repairType: event.target.value }))}>
              <option value="">All repair types</option>
              {repairTypes.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select className="form-select" value={ui.confidence} onChange={(event) => setUi((current) => ({ ...current, confidence: event.target.value }))}>
              <option value="">All confidence</option>
              {confidences.map((value) => <option key={value} value={value}>{confidenceLabel(value)}</option>)}
            </select>
            <select className="form-select" value={ui.status} onChange={(event) => setUi((current) => ({ ...current, status: event.target.value }))}>
              <option value="">All status</option>
              {statuses.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <select className="form-select" value={ui.sortMode} onChange={(event) => setUi((current) => ({ ...current, sortMode: event.target.value as SortMode }))}>
              <option value="confidence_newest">Confidence, newest</option>
              <option value="newest">Newest</option>
              <option value="filename">Filename</option>
            </select>
            <label className="form-checkbox metadata-repair-toggle">
              <input
                type="checkbox"
                checked={ui.showLow}
                onChange={(event) => setUi((current) => ({ ...current, showLow: event.target.checked }))}
              />
              <span>Show LOW</span>
            </label>
            <label className="form-checkbox metadata-repair-toggle">
              <input
                type="checkbox"
                checked={ui.denseMode}
                onChange={(event) => setUi((current) => ({ ...current, denseMode: event.target.checked }))}
              />
              <span>Dense mode</span>
            </label>
            <label className="form-checkbox metadata-repair-toggle">
              <input
                type="checkbox"
                checked={ui.showApplied}
                onChange={(event) => setUi((current) => ({ ...current, showApplied: event.target.checked }))}
              />
              <span>Show applied/no-op</span>
            </label>
          </div>

          <div className="metadata-repair-toolbar-group metadata-repair-toolbar-group--actions">
            <button className="btn btn--ghost btn--sm" onClick={() => void dryRunApply()} disabled={applyBusy || bulkBusy}>
              {applyBusy ? <Loader2 size={13} className="spin" /> : <Wrench size={13} />}
              Dry-run apply approved
            </button>
            <button
              className="btn btn--primary btn--sm"
              onClick={() => void applyApproved()}
              disabled={applyBusy || !applyPreview || applyPreview.proposed_count === 0}
              title={applyPreview ? 'Apply approved HIGH/MEDIUM proposals to DB only' : 'Run dry-run first'}
            >
              Apply approved
            </button>
          </div>
        </section>

        <section className="metadata-repair-bulkbar">
          <div className="metadata-repair-bulkbar-left">
            <button className="btn btn--ghost btn--sm" onClick={() => requestBulkAction('approve-selected')} disabled={!selectedCount || bulkBusy}>
              <Check size={13} />
              Approve selected
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => requestBulkAction('reject-selected')} disabled={!selectedCount || bulkBusy}>
              <X size={13} />
              Reject selected
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => requestBulkAction('defer-selected')} disabled={!selectedCount || bulkBusy}>
              <Clock3 size={13} />
              Defer selected
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => requestBulkAction('approve-high')} disabled={!visibleHighCount || bulkBusy}>
              <Check size={13} />
              Approve all HIGH
            </button>
            <button className="btn btn--ghost btn--sm" onClick={() => requestBulkAction('approve-visible')} disabled={!visibleItems.length || bulkBusy}>
              <Check size={13} />
              Approve visible
            </button>
          </div>
          <div className="metadata-repair-bulkbar-right">
            <span className="muted">A approve selected, R reject, D defer, J/K next-prev</span>
          </div>
        </section>

        {applyPreview && (
          <section className="metadata-repair-preview metadata-repair-preview--inline">
            <div className="crate-apply-preview-header">
              <strong>{applyPreview.dry_run ? 'Dry-run result' : 'Apply result'}</strong>
              <span>{applyPreview.proposed_count} proposed / {applyPreview.applied_count} applied / {applyPreview.skipped_count} skipped</span>
            </div>
          </section>
        )}
      </div>

      <section className="metadata-repair-workspace">
        <div className="card metadata-repair-table-card">
          <div className="card-header metadata-repair-section-header">
            <h2 className="card-title">Queue <span className="card-title-count">{visibleItems.length}</span></h2>
            <span className="metadata-repair-section-meta">
              {visibleSelectedIds.length ? `${visibleSelectedIds.length} selected` : 'Keyboard-driven review'}
            </span>
          </div>
          {loading && !items.length ? (
            <p className="empty-state">Loading repair queue…</p>
          ) : (
            <div
              ref={scrollRef}
              className={`table-wrapper metadata-repair-scroll${ui.denseMode ? ' metadata-repair-scroll--dense' : ''}`}
              onScroll={(event) => {
                const nextScrollTop = event.currentTarget.scrollTop
                scrollTopRef.current = nextScrollTop
                if (scrollFrameRef.current !== null) return
                scrollFrameRef.current = window.requestAnimationFrame(() => {
                  scrollFrameRef.current = null
                  setScrollTop(scrollTopRef.current)
                })
              }}
            >
              <table className={`table metadata-repair-table${ui.denseMode ? ' metadata-repair-table--dense' : ''}`}>
                <thead>
                  <tr>
                    <th className="metadata-repair-select-col">
                      <input
                        ref={selectAllRef}
                        type="checkbox"
                        checked={visibleIds.length > 0 && visibleSelectedIds.length === visibleIds.length}
                        onChange={toggleSelectAllVisible}
                        onClick={(event) => event.stopPropagation()}
                        aria-label="Select all visible rows"
                      />
                    </th>
                    <th className="metadata-repair-col-track">Track</th>
                    <th className="metadata-repair-col-repair">Repair type</th>
                    <th className="metadata-repair-col-confidence">Confidence</th>
                    <th className="metadata-repair-col-diff">CURRENT → PROPOSED</th>
                    <th className="metadata-repair-col-status">Status</th>
                    <th className="metadata-repair-col-actions">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {virtualTopPad > 0 && (
                    <tr className="metadata-repair-spacer" aria-hidden="true">
                      <td colSpan={7} style={{ height: virtualTopPad }} />
                    </tr>
                  )}
                  {virtualRows.map((item) => {
                    const rowSelected = item.track_id === selectedId || selectedSet.has(item.track_id)
                    const confidence = confidenceKey(item.confidence)
                    const rowClass = [
                      'metadata-repair-row',
                      'row--clickable',
                      rowSelected ? 'row--selected metadata-repair-row--selected' : '',
                      confidence === 'LOW' ? 'metadata-repair-row--low' : '',
                      confidence === 'REVIEW_REQUIRED' ? 'metadata-repair-row--review' : '',
                      String(item.status || '').toUpperCase() === 'PARTIAL' ? 'metadata-repair-row--partial' : '',
                    ].join(' ').trim()
                    return (
                      <tr
                        key={item.track_id}
                        className={rowClass}
                        onClick={() => setSelectedId(item.track_id)}
                      >
                        <td className="metadata-repair-select-col">
                          <input
                            type="checkbox"
                            checked={selectedSet.has(item.track_id)}
                            onClick={(event) => event.stopPropagation()}
                            onChange={() => toggleSelectedId(item.track_id)}
                            aria-label={`Select track ${item.track_id}`}
                            disabled={bulkBusy || applyBusy}
                          />
                        </td>
                        <td className="metadata-repair-track-cell">
                          <strong title={item.filename}>{item.filename}</strong>
                          <span className="metadata-repair-track-id">{item.track_id}</span>
                        </td>
                        <td className="metadata-repair-td-tight">{item.repair_type}</td>
                        <td>
                          <span className={`badge ${confidenceClass(item.confidence)} metadata-repair-confidence-badge`}>
                            {confidenceLabel(item.confidence)}
                          </span>
                        </td>
                        <td className="metadata-repair-diff-cell">
                          <MetadataDiff
                            item={item}
                            compact
                            onProposalSave={(field, value) => void saveFieldProposal(item.track_id, field, value)}
                            busy={rowBusyId === item.track_id || bulkBusy || applyBusy}
                          />
                        </td>
                        <td>
                          <span className={`badge ${statusClass(item.status)} metadata-repair-status-badge`}>
                            {display(item.status)}
                          </span>
                        </td>
                        <td>
                          <div className="metadata-repair-review-buttons">
                            <ReviewButton
                              label="Approve"
                              icon={<Check size={14} />}
                              disabled={rowBusyId === item.track_id || bulkBusy || applyBusy}
                              onClick={() => void reviewTrack(item.track_id, 'approve')}
                            />
                            <ReviewButton
                              label="Reject"
                              icon={<X size={14} />}
                              disabled={rowBusyId === item.track_id || bulkBusy || applyBusy}
                              onClick={() => void reviewTrack(item.track_id, 'reject')}
                            />
                            <ReviewButton
                              label="Defer"
                              icon={<Clock3 size={14} />}
                              disabled={rowBusyId === item.track_id || bulkBusy || applyBusy}
                              onClick={() => void reviewTrack(item.track_id, 'defer')}
                            />
                          </div>
                        </td>
                      </tr>
                    )
                  })}
                  {virtualBottomPad > 0 && (
                    <tr className="metadata-repair-spacer" aria-hidden="true">
                      <td colSpan={7} style={{ height: virtualBottomPad }} />
                    </tr>
                  )}
                  {!loading && !visibleItems.length && (
                    <tr>
                      <td colSpan={7} className="empty-state">No metadata repair proposals match the current filters.</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <aside className="card metadata-repair-detail-card">
          <div className="card-header metadata-repair-section-header">
            <h2 className="card-title">Inspector</h2>
            <span className="metadata-repair-section-meta">Selected row details</span>
          </div>
          <div className="metadata-repair-inspector-scroll">
            {selected ? (
              <div className="metadata-repair-detail">
                <div className="metadata-repair-inspector-actions">
                  <button
                    className="btn btn--ghost btn--sm"
                    type="button"
                    disabled={rowBusyId === selected.track_id || bulkBusy || applyBusy}
                    onClick={() => setManualEditTarget({
                      track_id: selected.track_id,
                      artist: selected.current.artist,
                      title: selected.current.title,
                      filename: selected.filename,
                      filepath: selected.filepath,
                    })}
                  >
                    <Edit3 size={13} />
                    Manual Edit
                  </button>
                  <ReviewButton
                    label="Approve selected"
                    icon={<Check size={14} />}
                    disabled={rowBusyId === selected.track_id || bulkBusy || applyBusy}
                    onClick={() => void reviewTrack(selected.track_id, 'approve')}
                  />
                  <ReviewButton
                    label="Reject selected"
                    icon={<X size={14} />}
                    disabled={rowBusyId === selected.track_id || bulkBusy || applyBusy}
                    onClick={() => void reviewTrack(selected.track_id, 'reject')}
                  />
                  <ReviewButton
                    label="Defer selected"
                    icon={<Clock3 size={14} />}
                    disabled={rowBusyId === selected.track_id || bulkBusy || applyBusy}
                    onClick={() => void reviewTrack(selected.track_id, 'defer')}
                  />
                </div>

                <section className="metadata-repair-inspector-hero">
                  <div className="inspector-cover-art metadata-repair-inspector-mark">
                    {(selected.current.artist || selected.filename || '?').slice(0, 1).toUpperCase()}
                  </div>
                  <div className="metadata-repair-inspector-title">
                    <strong>{display(selected.current.artist)}</strong>
                    <span>{display(selected.current.title)}</span>
                    <code>{selected.track_id}</code>
                  </div>
                </section>

                <section className="metadata-repair-inspector-group">
                  <div className="metadata-repair-inspector-group-title">Source</div>
                  <dl className="def-list metadata-repair-inspector-list metadata-repair-inspector-list--compact">
                    <dt>Filename</dt><dd>{selected.filename}</dd>
                    <dt>Path</dt><dd className="td-mono metadata-repair-path">{selected.filepath}</dd>
                  </dl>
                </section>

                <section className="metadata-repair-inspector-group">
                  <div className="metadata-repair-inspector-group-title">Classification</div>
                  <dl className="def-list metadata-repair-inspector-list metadata-repair-inspector-list--compact">
                    <dt>Repair type</dt><dd>{selected.repair_type}</dd>
                    <dt>Overall status</dt><dd><span className={`badge ${statusClass(selected.status)}`}>{display(selected.status)}</span></dd>
                    <dt>Confidence</dt><dd><span className={`badge ${confidenceClass(selected.confidence)}`}>{confidenceLabel(selected.confidence)}</span></dd>
                    <dt>Parse confidence</dt><dd>{display(selected.current.parse_confidence)}</dd>
                    <dt>Confidence reason</dt><dd>{display(selected.confidence_reason)}</dd>
                    <dt>Risk flags</dt><dd><MetadataRiskBadges flags={selected.risk_flags || []} /></dd>
                  </dl>
                </section>

                <section className="metadata-repair-inspector-group">
                  <div className="metadata-repair-inspector-group-title">Before / After</div>
                  <MetadataDiff
                    item={selected}
                    onAction={(field, action) => void reviewField(selected.track_id, field, action)}
                    onProposalSave={(field, value) => void saveFieldProposal(selected.track_id, field, value)}
                    busy={rowBusyId === selected.track_id || bulkBusy || applyBusy}
                  />
                </section>

                <section className="metadata-repair-inspector-group">
                  <div className="metadata-repair-inspector-group-title">Shortcuts</div>
                  <div className="metadata-repair-shortcuts">
                    <span><strong>A</strong> approve</span>
                    <span><strong>R</strong> reject</span>
                    <span><strong>D</strong> defer</span>
                    <span><strong>J/K</strong> next-prev</span>
                  </div>
                </section>
              </div>
            ) : (
              <p className="empty-state">No proposal selected.</p>
            )}
          </div>
        </aside>
      </section>

      {confirmAction && (
        <div className="modal-backdrop" onClick={() => setConfirmAction(null)}>
          <div
            className="modal metadata-repair-confirm"
            role="dialog"
            aria-modal="true"
            aria-label={confirmAction.label}
            onClick={(event) => event.stopPropagation()}
          >
            <div className="modal-header">
              <div className="modal-title">
                <ShieldAlert size={16} />
                <span>{confirmAction.label}</span>
              </div>
            </div>
            <div className="modal-body metadata-repair-confirm-body">
              <p className="metadata-repair-confirm-copy">
                This will apply the current review decision to {confirmAction.ids.length.toLocaleString()} row(s) in the queue.
              </p>
              <p className="metadata-repair-confirm-note">
                Review-only changes stay in the database. No tags, files, BPM, key, or cue data are written.
              </p>
            </div>
            <div className="modal-footer">
              <div className="modal-actions">
                <button className="btn btn--ghost btn--sm" onClick={() => setConfirmAction(null)}>
                  Cancel
                </button>
                <button className="btn btn--primary btn--sm" onClick={() => void confirmBulkAction()} disabled={bulkBusy}>
                  Confirm
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
      {manualEditTarget && (
        <ManualMetadataEditor
          target={manualEditTarget}
          onClose={() => setManualEditTarget(null)}
          onApplied={handleManualMetadataApplied}
        />
      )}
    </div>
  )
}
