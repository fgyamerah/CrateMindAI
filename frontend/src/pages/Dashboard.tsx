import { useState, useEffect } from 'react'
import { Link } from 'react-router-dom'
import { fetchVersion } from '../api/health'
import type { VersionResponse } from '../api/health'
import { useJobs } from '../hooks/useJobs'
import StatusBadge from '../components/StatusBadge'
import ErrorBanner from '../components/ErrorBanner'
import type { JobStatus } from '../types/job'

// ---------------------------------------------------------------------------
// Stat card
// ---------------------------------------------------------------------------

interface StatCardProps {
  label: string
  value: number
  accent?: JobStatus
}

function StatCard({ label, value, accent }: StatCardProps) {
  return (
    <div className={['stat-card', accent ? `stat-card--${accent}` : ''].filter(Boolean).join(' ')}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function Dashboard() {
  const { jobs, loading, error } = useJobs()

  const [version, setVersion] = useState<VersionResponse | null>(null)
  const [versionError, setVersionError] = useState<string | null>(null)

  useEffect(() => {
    fetchVersion()
      .then(setVersion)
      .catch((e: Error) => setVersionError(e.message))
  }, [])

  const counts = {
    total: jobs.length,
    running: jobs.filter((j) => j.status === 'running').length,
    pending: jobs.filter((j) => j.status === 'pending').length,
    succeeded: jobs.filter((j) => j.status === 'succeeded').length,
    failed: jobs.filter((j) => j.status === 'failed').length,
  }

  const recent = jobs.slice(0, 8)

  return (
    <div className="page">
      <div className="page-header">
        <h1 className="page-title">Dashboard</h1>
      </div>

      <ErrorBanner message={error} />

      {/* Stats */}
      <section className="section">
        <div className="stat-grid">
          <StatCard label="Total Jobs" value={counts.total} />
          <StatCard label="Running" value={counts.running} accent="running" />
          <StatCard label="Pending" value={counts.pending} accent="pending" />
          <StatCard label="Succeeded" value={counts.succeeded} accent="succeeded" />
          <StatCard label="Failed" value={counts.failed} accent="failed" />
        </div>
      </section>

      {/* Backend info */}
      <section className="section">
        <div className="card">
          <h2 className="card-title">Backend</h2>
          {versionError ? (
            <ErrorBanner message={versionError} />
          ) : version ? (
            <dl className="def-list">
              <dt>Backend</dt>
              <dd>v{version.backend_version}</dd>
              <dt>Toolkit</dt>
              <dd>v{version.toolkit_version}</dd>
              <dt>pipeline.py</dt>
              <dd>
                <code className="path">{version.pipeline_py}</code>
              </dd>
            </dl>
          ) : (
            <p className="muted">Connecting…</p>
          )}
        </div>
      </section>

      {/* Recent jobs */}
      <section className="section">
        <div className="card">
          <div className="card-header">
            <h2 className="card-title">Recent Jobs</h2>
            <Link to="/jobs" className="card-action">
              View all →
            </Link>
          </div>

          {loading ? (
            <p className="muted">Loading…</p>
          ) : recent.length === 0 ? (
            <p className="muted">No jobs yet. Go to Jobs to run a command.</p>
          ) : (
            <table className="table">
              <thead>
                <tr>
                  <th>Command</th>
                  <th>Args</th>
                  <th>Status</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <code>{job.command}</code>
                    </td>
                    <td>
                      <code className="muted">
                        {job.args.length > 0 ? job.args.join(' ') : '—'}
                      </code>
                    </td>
                    <td>
                      <StatusBadge status={job.status} />
                    </td>
                    <td className="muted">
                      {job.started_at
                        ? new Date(job.started_at).toLocaleString()
                        : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  )
}
