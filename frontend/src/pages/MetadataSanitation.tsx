import { useEffect, useMemo, useRef, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { Edit3 } from 'lucide-react'
import ManualMetadataEditor from '../components/ManualMetadataEditor'
import type { ManualMetadataApplyResponse } from '../api/manualMetadata'
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
import { fetchTrack } from '../api/tracks'
import type {
  MetadataSanitationApplyResponse,
  MetadataSanitationFieldName,
  MetadataSanitationProposal,
  MetadataSanitationSummary,
} from '../types/metadataSanitation'
import type { TrackDetail } from '../types/track'

type FieldAction = 'approve' | 'reject' | 'defer'

const FIELDS: MetadataSanitationFieldName[] = ['artist', 'title']

interface ManualEditTarget {
  track_id: number
  artist: string | null
  title: string | null
  filename?: string | null
  filepath?: string | null
}

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
  const [selectedTrack, setSelectedTrack] = useState<TrackDetail | null>(null)
  const [includeApplied, setIncludeApplied] = useState(false)
  const [loading, setLoading] = useState(true)
  const [detailLoading, setDetailLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(noticeFromRoute)
  const [noProposalReason, setNoProposalReason] = useState<string | null>(null)
  const [applyPreview, setApplyPreview] = useState<MetadataSanitationApplyResponse | null>(null)
  const [manualEditTarget, setManualEditTarget] = useState<ManualEditTarget | null>(null)
  const queueViewportRef = useRef<HTMLDivElement | null>(null)
  const rowRefs = useRef<Record<number, HTMLButtonElement | null>>({})
  const deepLinkScrollPendingRef = useRef(requestedTrackId != null)

  const selectedProposal = useMemo(
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
    if (!selectedProposal && noticeFromRoute) {
      setNoProposalReason(noticeFromRoute)
    } else if (selectedProposal) {
      setNoProposalReason(null)
    }
  }, [noticeFromRoute, selectedProposal])

  useEffect(() => {
    setSelectedId((current) => {
      if (requestedTrackId && items.some((item) => item.track_id === requestedTrackId)) return requestedTrackId
      if (current && items.some((item) => item.track_id === current)) return current
      return items[0]?.track_id || null
    })
    deepLinkScrollPendingRef.current = requestedTrackId != null
  }, [items, requestedTrackId])

  useEffect(() => {
    const activeTrackId = selectedId ?? requestedTrackId
    if (!activeTrackId) {
      setSelectedTrack(null)
      return
    }
    setDetailLoading(true)
    fetchTrack(activeTrackId)
      .then((detail) => {
        setSelectedTrack(detail)
      })
      .catch(() => {
        setSelectedTrack(null)
      })
      .finally(() => setDetailLoading(false))
  }, [requestedTrackId, selectedId])

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

  async function handleManualMetadataApplied(result: ManualMetadataApplyResponse) {
    setNotice(`Applied manual metadata edit: ${result.applied_fields.join(', ') || 'no changes'}`)
    setApplyPreview(null)
    await refresh(includeApplied)
    const activeTrackId = selectedId ?? requestedTrackId
    if (activeTrackId) {
      try {
        setSelectedTrack(await fetchTrack(activeTrackId))
      } catch {
        setSelectedTrack(null)
      }
    }
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
                className={`metadata-repair-row${selectedProposal?.track_id === item.track_id ? ' metadata-repair-row--selected' : ''}`}
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
          {!selectedProposal && !selectedTrack && <div className="metadata-repair-empty">Select a proposal to inspect.</div>}
          {(selectedProposal || selectedTrack) && (
            <>
              {selectedProposal ? (
                <>
                  <div className="metadata-repair-inspector-title">
                    <span>Track #{selectedProposal.track_id}</span>
                    <h2>{selectedProposal.filename}</h2>
                    <p>{selectedProposal.filepath}</p>
                    <button
                      className="btn btn--ghost btn--sm"
                      type="button"
                      onClick={() => setManualEditTarget({
                        track_id: selectedProposal.track_id,
                        artist: selectedProposal.current.artist,
                        title: selectedProposal.current.title,
                        filename: selectedProposal.filename,
                        filepath: selectedProposal.filepath,
                      })}
                    >
                      <Edit3 size={13} />
                      Manual Edit
                    </button>
                  </div>
                  <div className="metadata-repair-inspector-block">
                    <span className="metadata-repair-kicker">Classification</span>
                    <p>{selectedProposal.confidence_reason}</p>
                    <div className="metadata-repair-risk-list">
                      {selectedProposal.risk_flags.map((flag) => <span key={flag}>{flag}</span>)}
                    </div>
                  </div>
                  {FIELDS.map((field) => (
                    <FieldEditor
                      key={field}
                      item={selectedProposal}
                      field={field}
                      onSave={(nextField, value) => saveProposal(selectedProposal.track_id, nextField, value)}
                      onAction={(nextField, action) => reviewField(selectedProposal.track_id, nextField, action)}
                    />
                  ))}
                  <div className="metadata-repair-inspector-block">
                    <span className="metadata-repair-kicker">Apply safety</span>
                    <p>Approved sanitation updates write only tracks.artist and tracks.title in the DB.</p>
                  </div>
                </>
              ) : (
                <div className="metadata-repair-empty-state">
                  <div className="metadata-repair-empty">
                    {detailLoading ? 'Loading track context...' : 'No deterministic sanitation proposal was generated for this track.'}
                  </div>
                  {selectedTrack && (
                    <div className="metadata-repair-inspector-block">
                      <span className="metadata-repair-kicker">Track context</span>
                      <dl className="crate-defs metadata-repair-inspector-list metadata-repair-inspector-list--compact">
                        <dt>Artist</dt><dd>{selectedTrack.artist || 'Empty'}</dd>
                        <dt>Title</dt><dd>{selectedTrack.title || 'Empty'}</dd>
                        <dt>Filename</dt><dd>{selectedTrack.filename}</dd>
                        <dt>Path</dt><dd className="td-mono metadata-repair-path">{selectedTrack.filesystem_path}</dd>
                        <dt>Issues</dt><dd>{selectedTrack.issues.join(', ') || 'clean'}</dd>
                      </dl>
                      {noProposalReason && <p className="metadata-repair-empty-note">{noProposalReason}</p>}
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => setManualEditTarget({
                          track_id: selectedTrack.id,
                          artist: selectedTrack.artist,
                          title: selectedTrack.title,
                          filename: selectedTrack.filename,
                          filepath: selectedTrack.filesystem_path || selectedTrack.filepath,
                        })}
                      >
                        <Edit3 size={13} />
                        Manual Edit
                      </button>
                      {selectedTrack.issues.includes('suspicious_title') && (
                        <button type="button" className="btn btn--ghost btn--sm" disabled title="Manual sanitation proposal is not implemented yet">
                          Create manual sanitation proposal
                        </button>
                      )}
                    </div>
                  )}
                  <div className="metadata-repair-inspector-block">
                    <span className="metadata-repair-kicker">Next step</span>
                    <p>You can use Manual Edit later, or adjust sanitation heuristics.</p>
                  </div>
                </div>
              )}
            </>
          )}
        </aside>
      </section>
      {manualEditTarget && (
        <ManualMetadataEditor
          target={manualEditTarget}
          onClose={() => setManualEditTarget(null)}
          onApplied={handleManualMetadataApplied}
        />
      )}
    </main>
  )
}
