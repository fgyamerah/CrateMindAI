import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import {
  applyMetadataSanitationApproved,
  approveMetadataSanitationField,
  deferMetadataSanitationField,
  dryRunMetadataSanitationApply,
  fetchMetadataSanitationQueue,
  fetchMetadataSanitationSummary,
  rejectMetadataSanitationField,
  updateMetadataSanitationFieldProposal,
} from '../api/metadataSanitation'
import type {
  MetadataSanitationApplyResponse,
  MetadataSanitationFieldName,
  MetadataSanitationProposal,
  MetadataSanitationSummary,
} from '../types/metadataSanitation'

type FieldAction = 'approve' | 'reject' | 'defer'

const FIELDS: MetadataSanitationFieldName[] = ['artist', 'title']

function parseTrackId(search: string): number | null {
  const value = new URLSearchParams(search).get('track')
  if (!value) return null
  const parsed = Number(value)
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null
}

function statusLabel(value: string | undefined): string {
  return (value || 'PENDING').replace(/_/g, ' ')
}

function FieldEditor({
  item,
  field,
  onSave,
  onAction,
}: {
  item: MetadataSanitationProposal
  field: MetadataSanitationFieldName
  onSave: (field: MetadataSanitationFieldName, value: string) => Promise<void>
  onAction: (field: MetadataSanitationFieldName, action: FieldAction) => Promise<void>
}) {
  const fieldState = item.fields[field]
  const [editing, setEditing] = useState(false)
  const [value, setValue] = useState(fieldState.proposed || '')

  useEffect(() => {
    if (!editing) setValue(fieldState.proposed || '')
  }, [editing, fieldState.proposed])

  async function commit() {
    const cleaned = value.trim()
    setEditing(false)
    if (cleaned && cleaned !== (fieldState.proposed || '')) await onSave(field, cleaned)
  }

  function cancel() {
    setValue(fieldState.proposed || '')
    setEditing(false)
  }

  return (
    <div className="sanitation-field-card">
      <div className="field-card-header">
        <div>
          <div className="field-label">{field}</div>
          <div className="field-status">{statusLabel(fieldState.status)}</div>
        </div>
        <button type="button" className="field-edit-button" onClick={() => setEditing(true)}>
          Edit
        </button>
      </div>
      <div className="field-compare-grid">
        <div className="field-value-block">
          <div className="mini-label">Current</div>
          <div className="value-text">{fieldState.current || 'Empty'}</div>
        </div>
        <div className="arrow" aria-hidden="true">→</div>
        <div className="field-value-block">
          <div className="mini-label">Proposed</div>
          <div className="field-proposed-wrap">
            {editing ? (
              <input
                className="field-input"
                autoFocus
                value={value}
                onBlur={commit}
                onChange={(event) => setValue(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter') void commit()
                  if (event.key === 'Escape') cancel()
                }}
              />
            ) : (
              <div className="value-text value-text--interactive" onClick={() => setEditing(true)} role="button" tabIndex={0}>
                {fieldState.proposed || 'Empty'}
              </div>
            )}
            {fieldState.edited && <span className="edit-badge">Edited</span>}
          </div>
        </div>
      </div>
      <div className="field-actions-row">
        <button type="button" onClick={() => onAction(field, 'approve')}>Approve</button>
        <button type="button" onClick={() => onAction(field, 'reject')}>Reject</button>
        <button type="button" onClick={() => onAction(field, 'defer')}>Defer</button>
      </div>
    </div>
  )
}

export default function MetadataSanitation() {
  const location = useLocation()
  const requestedTrackId = useMemo(() => parseTrackId(location.search), [location.search])
  const noticeFromRoute = (location.state as { notice?: string } | null | undefined)?.notice ?? null
  const [summary, setSummary] = useState<MetadataSanitationSummary | null>(null)
  const [items, setItems] = useState<MetadataSanitationProposal[]>([])
  const [selectedId, setSelectedId] = useState<number | null>(requestedTrackId)
  const [includeApplied, setIncludeApplied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(noticeFromRoute)
  const [applyPreview, setApplyPreview] = useState<MetadataSanitationApplyResponse | null>(null)
  const queueViewportRef = useRef<HTMLDivElement | null>(null)
  const rowRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const deepLinkScrollPendingRef = useRef(requestedTrackId != null)

  const selected = useMemo(
    () => items.find((item) => item.track_id === selectedId) || items[0] || null,
    [items, selectedId],
  )

  async function refresh(nextIncludeApplied = includeApplied) {
    setLoading(true)
    setError(null)
    try {
      const [nextSummary, queue] = await Promise.all([
        fetchMetadataSanitationSummary(),
        fetchMetadataSanitationQueue({ include_applied: nextIncludeApplied }),
      ])
      setSummary(nextSummary)
      setItems(queue.items)
      setSelectedId((current) => {
        if (current && queue.items.some((item) => item.track_id === current)) return current
        return queue.items[0]?.track_id || null
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load metadata sanitation queue')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    void refresh(includeApplied)
  }, [includeApplied])

  useEffect(() => {
    if (noticeFromRoute) setNotice(noticeFromRoute)
  }, [noticeFromRoute])

  useEffect(() => {
    setSelectedId((current) => {
      if (requestedTrackId && items.some((item) => item.track_id === requestedTrackId)) return requestedTrackId
      if (current && items.some((item) => item.track_id === current)) return current
      return items[0]?.track_id || null
    })
    deepLinkScrollPendingRef.current = requestedTrackId != null
  }, [items, requestedTrackId])

  useEffect(() => {
    if (!selectedId) return
    const node = rowRefs.current[selectedId]
    if (!node || !queueViewportRef.current) return
    if (deepLinkScrollPendingRef.current || document.activeElement !== node) {
      node.scrollIntoView({ block: 'center' })
      deepLinkScrollPendingRef.current = false
    }
  }, [selectedId])

  async function saveProposal(trackId: number, field: MetadataSanitationFieldName, proposed: string) {
    await updateMetadataSanitationFieldProposal(trackId, field, proposed)
    setNotice(`Saved edited ${field} proposal`)
    await refresh()
  }

  async function reviewField(trackId: number, field: MetadataSanitationFieldName, action: FieldAction) {
    if (action === 'approve') await approveMetadataSanitationField(trackId, field)
    if (action === 'reject') await rejectMetadataSanitationField(trackId, field)
    if (action === 'defer') await deferMetadataSanitationField(trackId, field)
    setNotice(`${field} ${action}d`)
    await refresh()
  }

  async function previewApply() {
    setApplyPreview(await dryRunMetadataSanitationApply())
  }

  async function applyApproved() {
    const result = await applyMetadataSanitationApproved()
    setNotice(`Applied ${result.applied_field_count || 0} field updates`)
    setApplyPreview(null)
    await refresh()
  }

  return (
    <main className="metadata-repair-page">
      <header className="metadata-repair-header">
        <div>
          <p className="metadata-repair-eyebrow">Phase 10</p>
          <h1>Metadata Sanitation</h1>
          <p>Review deterministic artist/title cleanup proposals from suspicious metadata contamination.</p>
        </div>
        <div className="metadata-repair-header-actions">
          <label className="metadata-repair-toggle">
            <input
              type="checkbox"
              checked={includeApplied}
              onChange={(event) => setIncludeApplied(event.target.checked)}
            />
            Show applied/no-op
          </label>
          <button type="button" onClick={() => void refresh()}>Refresh</button>
          <button type="button" onClick={previewApply}>Preview apply</button>
          <button type="button" className="metadata-repair-primary" onClick={applyApproved}>Apply approved</button>
        </div>
      </header>

      {error && <div className="metadata-repair-error">{error}</div>}
      {notice && <div className="metadata-repair-success">{notice}</div>}

      <section className="metadata-repair-metrics">
        <div><span>Queue</span><strong>{summary?.queue_total ?? 0}</strong></div>
        <div><span>Pending</span><strong>{summary?.pending_count ?? 0}</strong></div>
        <div><span>Approved</span><strong>{summary?.approved_count ?? 0}</strong></div>
        <div><span>Applied</span><strong>{summary?.applied_count ?? 0}</strong></div>
        <div><span>No-op</span><strong>{summary?.no_op_count ?? 0}</strong></div>
      </section>

      {applyPreview && (
        <section className="metadata-repair-apply-preview">
          <strong>{applyPreview.proposed_count}</strong> tracks ready,{' '}
          <strong>{applyPreview.skipped_count}</strong> skipped.
        </section>
      )}

      <section className="metadata-repair-shell">
        <div className="metadata-repair-queue-panel">
          <div className="metadata-repair-table-head">
            <span>Track</span>
            <span>Confidence</span>
            <span>Status</span>
          </div>
          <div ref={queueViewportRef} className="metadata-repair-queue-viewport">
            {loading && <div className="metadata-repair-empty">Loading sanitation queue...</div>}
            {!loading && items.length === 0 && (
              <div className="metadata-repair-empty">No active metadata sanitation proposals.</div>
            )}
            {!loading && items.map((item) => (
              <button
                type="button"
                key={item.track_id}
                ref={(node) => {
                  rowRefs.current[item.track_id] = node
                }}
                className={`metadata-repair-row${selected?.track_id === item.track_id ? ' metadata-repair-row--selected' : ''}`}
                onClick={() => setSelectedId(item.track_id)}
              >
                <span>
                  <strong>{item.filename}</strong>
                  <small>{item.risk_flags.join(', ') || item.confidence_reason}</small>
                </span>
                <span className={`metadata-repair-confidence metadata-repair-confidence--${String(item.confidence).toLowerCase()}`}>
                  {item.confidence}
                </span>
                <span>{statusLabel(item.status)}</span>
              </button>
            ))}
          </div>
        </div>

        <aside className="metadata-repair-inspector">
          {!selected && <div className="metadata-repair-empty">Select a proposal to inspect.</div>}
          {selected && (
            <>
              <div className="metadata-repair-inspector-title">
                <span>Track #{selected.track_id}</span>
                <h2>{selected.filename}</h2>
                <p>{selected.filepath}</p>
              </div>
              <div className="metadata-repair-inspector-block">
                <span className="metadata-repair-kicker">Classification</span>
                <p>{selected.confidence_reason}</p>
                <div className="metadata-repair-risk-list">
                  {selected.risk_flags.map((flag) => <span key={flag}>{flag}</span>)}
                </div>
              </div>
              {FIELDS.map((field) => (
                <FieldEditor
                  key={field}
                  item={selected}
                  field={field}
                  onSave={(nextField, value) => saveProposal(selected.track_id, nextField, value)}
                  onAction={(nextField, action) => reviewField(selected.track_id, nextField, action)}
                />
              ))}
              <div className="metadata-repair-inspector-block">
                <span className="metadata-repair-kicker">Apply safety</span>
                <p>Approved sanitation updates write only tracks.artist and tracks.title in the DB.</p>
              </div>
            </>
          )}
        </aside>
      </section>
    </main>
  )
}
