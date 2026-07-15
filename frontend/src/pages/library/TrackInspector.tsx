import { useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { X, ExternalLink } from 'lucide-react'
import { useTrackDetail } from './useLibraryTracks'
import { ISSUE_LABELS } from '../../types/track'
import { formatDuration, formatFileSize } from '../../lib/format'
import { ErrorState, LoadingState } from '../../components/ui/StatePanel'

interface Props {
  trackId: number
  onClose: () => void
  asDrawer: boolean
}

interface EnrichmentMatch {
  artist?: string
  title?: string
  album?: string
  year?: number
  provider?: string
}

function Field({ label, value, mono = false }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <div className="lib-insp-field">
      <dt>{label}</dt>
      <dd className={mono ? 'lib-mono' : undefined}>{value ?? <span className="lib-missing">—</span>}</dd>
    </div>
  )
}

export default function TrackInspector({ trackId, onClose, asDrawer }: Props) {
  const { data, isPending, isError, refetch } = useTrackDetail(trackId)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (asDrawer) panelRef.current?.focus()
  }, [asDrawer, trackId])

  const queueItem = data?.enrichment_queue_item as
    | { best_match?: EnrichmentMatch; confidence?: string; provider?: string; action_suggestion?: string }
    | null
    | undefined

  const body = (
    <>
      <div className="lib-insp-head">
        <div>
          <h2 className="lib-insp-title" data-testid="inspector-title">
            {data?.title ?? (isPending ? '…' : 'Unknown title')}
          </h2>
          <div className="lib-insp-artist">{data?.artist ?? ''}</div>
        </div>
        <button
          type="button"
          className="cm-icon-btn"
          onClick={onClose}
          aria-label="Close track inspector"
          data-testid="inspector-close-button"
        >
          <X size={16} />
        </button>
      </div>

      {isPending && <LoadingState label="Loading track…" testId="inspector-loading" />}
      {isError && (
        <ErrorState detail="Track details could not be loaded." onRetry={() => refetch()} testId="inspector-error" />
      )}

      {data && (
        <div className="lib-insp-body">
          <section aria-labelledby="insp-meta">
            <h3 id="insp-meta">Metadata</h3>
            <dl>
              <Field label="Artist" value={data.artist} />
              <Field label="Title" value={data.title} />
              <Field label="Genre" value={data.genre} />
              <Field label="BPM" value={data.bpm !== null ? data.bpm.toFixed(1) : null} mono />
              <Field
                label="Key"
                value={data.key_camelot ? `${data.key_camelot}${data.key_musical ? ` (${data.key_musical})` : ''}` : null}
                mono
              />
            </dl>
          </section>

          <section aria-labelledby="insp-file">
            <h3 id="insp-file">File</h3>
            <dl>
              <Field label="Duration" value={formatDuration(data.duration_sec)} mono />
              <Field label="Bitrate" value={data.bitrate_kbps ? `${data.bitrate_kbps} kbps` : null} mono />
              <Field label="Size" value={formatFileSize(data.filesize_bytes)} mono />
              <Field label="Format" value={data.filename.split('.').pop()?.toUpperCase()} mono />
              <Field label="Path" value={<span className="lib-path">{data.filepath}</span>} mono />
            </dl>
          </section>

          <section aria-labelledby="insp-state">
            <h3 id="insp-state">Processing state</h3>
            <dl>
              <Field label="Status" value={data.status.replace('_', ' ')} />
              <Field label="Parse confidence" value={data.parse_confidence} />
              <Field label="Quality tier" value={data.quality_tier} />
              <Field label="Processed" value={data.processed_at ?? 'never'} mono />
              <Field label="Pipeline version" value={data.pipeline_ver} mono />
              {data.error_msg && <Field label="Error" value={data.error_msg} />}
            </dl>
          </section>

          <section aria-labelledby="insp-issues">
            <h3 id="insp-issues">Issues</h3>
            {data.issues.length === 0 ? (
              <p className="lib-insp-ok">No issues detected.</p>
            ) : (
              <>
                <div className="lib-issue-badges">
                  {data.issues.map((i) => (
                    <span key={i} className="lib-issue-badge">
                      {ISSUE_LABELS[i] ?? i}
                    </span>
                  ))}
                </div>
                {data.recommended_route && (
                  <p className="lib-insp-route">
                    Recommended route:{' '}
                    <Link to={data.recommended_route} data-testid="inspector-recommended-route">
                      {data.recommended_route} <ExternalLink size={11} aria-hidden="true" />
                    </Link>
                  </p>
                )}
              </>
            )}
          </section>

          {queueItem?.best_match && (
            <section aria-labelledby="insp-proposal">
              <h3 id="insp-proposal">Enrichment proposal — pending review</h3>
              <div className="lib-diff" data-testid="inspector-proposal">
                <div className="lib-diff-col">
                  <h4>Current</h4>
                  <dl>
                    <Field label="Artist" value={data.artist} />
                    <Field label="Title" value={data.title} />
                  </dl>
                </div>
                <div className="lib-diff-col lib-diff-col--proposed">
                  <h4>Proposed ({queueItem.provider ?? 'unknown source'})</h4>
                  <dl>
                    <Field label="Artist" value={queueItem.best_match.artist} />
                    <Field label="Title" value={queueItem.best_match.title} />
                    <Field label="Album" value={queueItem.best_match.album} />
                  </dl>
                </div>
              </div>
              <p className="lib-insp-route">
                Nothing is applied without review. <Link to="/enrichment">Open enrichment review</Link>
              </p>
            </section>
          )}
        </div>
      )}
    </>
  )

  if (asDrawer) {
    return (
      <>
        <div className="cm-drawer-backdrop" onClick={onClose} aria-hidden="true" />
        <div
          className="lib-insp lib-insp--drawer"
          role="dialog"
          aria-modal="true"
          aria-label="Track inspector"
          tabIndex={-1}
          ref={panelRef}
          onKeyDown={(e) => e.key === 'Escape' && onClose()}
          data-testid="track-inspector"
        >
          {body}
        </div>
      </>
    )
  }

  return (
    <aside className="lib-insp" aria-label="Track inspector" data-testid="track-inspector">
      {body}
    </aside>
  )
}
