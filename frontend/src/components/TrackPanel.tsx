import { useState, useEffect, useCallback } from 'react'
import { fetchTrack } from '../api/tracks'
import type { TrackDetail } from '../types/track'
import { ISSUE_LABELS } from '../types/track'
import type { TrackSummary } from '../types/track'

interface Props {
  track: TrackSummary
  onClose: () => void
}

function fmt(n: number | null | undefined, decimals = 0): string {
  if (n == null) return '—'
  return n.toFixed(decimals)
}

function fmtSize(bytes: number | null): string {
  if (!bytes) return '—'
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`
  return `${(bytes / 1024).toFixed(0)} KB`
}

function fmtDuration(sec: number | null): string {
  if (!sec) return '—'
  const m = Math.floor(sec / 60)
  const s = Math.floor(sec % 60)
  return `${m}:${s.toString().padStart(2, '0')}`
}

export default function TrackPanel({ track: summary, onClose }: Props) {
  const [detail, setDetail]   = useState<TrackDetail | null>(null)
  const [loadErr, setLoadErr] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const d = await fetchTrack(summary.id)
      setDetail(d)
    } catch (e) {
      setLoadErr(e instanceof Error ? e.message : 'Failed to load track detail')
    }
  }, [summary.id])

  useEffect(() => {
    load()
  }, [load])

  // Close on Escape
  useEffect(() => {
    const h = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  const t = detail ?? summary

  return (
    <div className="track-panel-backdrop" onClick={onClose}>
      <aside
        className="track-panel"
        onClick={(e) => e.stopPropagation()}
        role="complementary"
        aria-label="Track detail"
      >
        {/* Header */}
        <div className="track-panel-header">
          <div className="track-panel-title">
            <span className="track-panel-artist">{t.artist ?? '(no artist)'}</span>
            <span className="track-panel-track-title">{t.title ?? t.filename}</span>
          </div>
          <button className="btn btn--ghost btn--sm" onClick={onClose} aria-label="Close">✕</button>
        </div>

        {/* Issues */}
        {summary.issues.length > 0 && (
          <div className="track-panel-issues">
            {summary.issues.map((issue) => (
              <span key={issue} className={`issue-badge issue-badge--${issue.replace('_', '-')}`}>
                {ISSUE_LABELS[issue]}
              </span>
            ))}
          </div>
        )}

        {loadErr && <p className="track-panel-error">{loadErr}</p>}

        {/* Core metadata */}
        <section className="track-panel-section">
          <h3 className="track-panel-section-title">Analysis</h3>
          <dl className="def-list">
            <dt>BPM</dt>
            <dd>{fmt(t.bpm, 1)}</dd>
            <dt>Key (Camelot)</dt>
            <dd>{t.key_camelot ?? '—'}</dd>
            <dt>Key (Musical)</dt>
            <dd>{t.key_musical ?? '—'}</dd>
            <dt>Genre</dt>
            <dd>{t.genre ?? '—'}</dd>
            <dt>Duration</dt>
            <dd>{fmtDuration(t.duration_sec)}</dd>
          </dl>
        </section>

        {/* Quality */}
        <section className="track-panel-section">
          <h3 className="track-panel-section-title">Quality</h3>
          <dl className="def-list">
            <dt>Tier</dt>
            <dd>
              {t.quality_tier ? (
                <span className={`quality-badge quality-badge--${t.quality_tier.toLowerCase()}`}>
                  {t.quality_tier}
                </span>
              ) : '—'}
            </dd>
            <dt>Bitrate</dt>
            <dd>{t.bitrate_kbps ? `${t.bitrate_kbps} kbps` : '—'}</dd>
            {detail && (
              <>
                <dt>File size</dt>
                <dd>{fmtSize(detail.filesize_bytes)}</dd>
              </>
            )}
          </dl>
        </section>

        {/* Pipeline status */}
        <section className="track-panel-section">
          <h3 className="track-panel-section-title">Pipeline</h3>
          <dl className="def-list">
            <dt>Status</dt>
            <dd>
              <span className={`badge badge--track-${t.status}`}>{t.status}</span>
            </dd>
            {detail?.error_msg && (
              <>
                <dt>Error</dt>
                <dd className="text--error">{detail.error_msg}</dd>
              </>
            )}
            {detail?.processed_at && (
              <>
                <dt>Processed</dt>
                <dd>{new Date(detail.processed_at).toLocaleString()}</dd>
              </>
            )}
            {detail?.pipeline_ver && (
              <>
                <dt>Pipeline ver</dt>
                <dd>{detail.pipeline_ver}</dd>
              </>
            )}
          </dl>
        </section>

        {/* File path */}
        <section className="track-panel-section">
          <h3 className="track-panel-section-title">File</h3>
          <p className="track-panel-path">{t.filepath}</p>
        </section>
      </aside>
    </div>
  )
}
