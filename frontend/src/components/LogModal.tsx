import { useState, useEffect, useRef, useCallback } from 'react'
import { fetchJobLogs } from '../api/jobs'
import type { Job } from '../types/job'
import StatusBadge from './StatusBadge'

interface Props {
  job: Job
  onClose: () => void
}

const TAIL_LINES = 500
const POLL_MS = 2_000

function isActive(job: Job): boolean {
  return job.status === 'pending' || job.status === 'running'
}

export default function LogModal({ job, onClose }: Props) {
  const [logs, setLogs] = useState<string>('')
  const [loadError, setLoadError] = useState<string | null>(null)
  const [fetching, setFetching] = useState(true)
  const preRef = useRef<HTMLPreElement>(null)
  const shouldAutoScroll = useRef(true)

  const load = useCallback(async () => {
    try {
      const text = await fetchJobLogs(job.id, TAIL_LINES)
      setLogs(text)
      setLoadError(null)
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : 'Failed to fetch logs')
    } finally {
      setFetching(false)
    }
  }, [job.id])

  // Auto-scroll to bottom when logs update (only if user hasn't scrolled up)
  useEffect(() => {
    if (shouldAutoScroll.current && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [logs])

  useEffect(() => {
    load()

    // Only keep polling while the job is still active
    if (!isActive(job)) return

    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [load, job])

  // Close on Escape
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [onClose])

  function handleScroll() {
    if (!preRef.current) return
    const { scrollTop, scrollHeight, clientHeight } = preRef.current
    shouldAutoScroll.current = scrollHeight - scrollTop - clientHeight < 40
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div
        className="modal"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-label={`Logs for job ${job.id}`}
      >
        {/* Header */}
        <div className="modal-header">
          <div className="modal-title">
            <code className="modal-job-id">{job.id.slice(0, 8)}…</code>
            <code className="modal-command">{job.command}</code>
            {job.args.length > 0 && (
              <code className="modal-args">{job.args.join(' ')}</code>
            )}
            <StatusBadge status={job.status} />
          </div>
          <div className="modal-actions">
            <button
              className="btn btn--ghost btn--sm"
              onClick={load}
              title="Refresh logs"
            >
              Refresh
            </button>
            <button
              className="btn btn--ghost btn--sm"
              onClick={onClose}
              aria-label="Close"
            >
              ✕
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="modal-body">
          {fetching && logs === '' ? (
            <p className="modal-loading">Loading…</p>
          ) : loadError ? (
            <p className="modal-error">{loadError}</p>
          ) : (
            <pre
              ref={preRef}
              className="log-output"
              onScroll={handleScroll}
            >
              {logs || '(no output)'}
            </pre>
          )}
        </div>

        {/* Footer */}
        {job.exit_code !== null && (
          <div className="modal-footer">
            Exit code: <code>{job.exit_code}</code>
            {job.finished_at && (
              <span> · Finished {new Date(job.finished_at).toLocaleString()}</span>
            )}
          </div>
        )}
        {isActive(job) && (
          <div className="modal-footer modal-footer--live">
            Live — polling every {POLL_MS / 1000}s
          </div>
        )}
      </div>
    </div>
  )
}
