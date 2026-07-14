import { useMemo } from 'react'
import type { Job } from '../types/job'
import MetricCard from '../components/ui/MetricCard'
import NextActionBanner from './home/NextActionBanner'
import ReadinessPanel from './home/ReadinessPanel'
import RecentJobsPanel from './home/RecentJobsPanel'
import CoveragePanel from './home/CoveragePanel'
import AttentionCards from './home/AttentionCards'
import { computeNextAction } from '../lib/nextAction'
import { formatNumber, formatPercent, formatRelativeTime } from '../lib/format'
import { useHomeData, enrichmentReviewable, bpmPendingCount } from './home/useHomeData'

const UNAVAILABLE = 'Unavailable'

export default function Home() {
  const {
    preflight,
    health,
    stats,
    overview,
    repair,
    sanitation,
    enrichmentQueue,
    enrichmentReview,
    bpm,
    jobs,
  } = useHomeData()

  const coreLoading =
    preflight.isPending ||
    stats.isPending ||
    repair.isPending ||
    sanitation.isPending ||
    enrichmentQueue.isPending ||
    bpm.isPending

  const failedJobs = useMemo(
    () => (jobs.data ? jobs.data.filter((j: Job) => j.status === 'failed').length : 0),
    [jobs.data],
  )

  const nextAction = useMemo(() => {
    if (coreLoading) return null
    return computeNextAction({
      preflightStatus: preflight.data?.status ?? null,
      dbExists: health.data ? health.data.db_exists : null,
      totalTracks: stats.data?.tracks_count ?? null,
      repairPending: repair.data?.pending_count ?? 0,
      sanitationPending: sanitation.data?.pending_count ?? 0,
      enrichmentReviewCount: enrichmentReviewable(enrichmentQueue.data, enrichmentReview.data),
      bpmPending: bpmPendingCount(bpm.data),
      missingFiles: stats.data?.missing_files ?? 0,
      untrackedFiles: stats.data?.untracked_files ?? 0,
      runningJobs: jobs.data ? jobs.data.filter((j: Job) => j.status === 'running').length : 0,
      failedJobs,
    })
  }, [
    coreLoading,
    preflight.data,
    health.data,
    stats.data,
    repair.data,
    sanitation.data,
    enrichmentQueue.data,
    enrichmentReview.data,
    bpm.data,
    jobs.data,
    failedJobs,
  ])

  const pendingReviews =
    (repair.data?.pending_count ?? 0) +
    (sanitation.data?.pending_count ?? 0) +
    enrichmentReviewable(enrichmentQueue.data, enrichmentReview.data) +
    bpmPendingCount(bpm.data)
  const reviewsUnavailable = repair.isError && sanitation.isError && enrichmentQueue.isError

  const lastAudit = stats.data?.last_audit_report?.generated_at ?? null

  return (
    <div className="cm-home" data-testid="home-page">
      <div className="cm-home-header">
        <h1>Command center</h1>
        <p>
          Operational overview for the selected library
          {health.data ? (
            <>
              {' — '}
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12.5 }}>
                {health.data.library_root}
              </span>
            </>
          ) : null}
        </p>
      </div>

      <NextActionBanner action={nextAction} loading={coreLoading} />

      <div className="cm-home-grid">
        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-5)', minWidth: 0 }}>
          <section className="cm-home-section" aria-labelledby="home-metrics-heading">
            <h2 id="home-metrics-heading">Library health</h2>
            <div className="cm-metric-grid">
              <MetricCard
                label="Total tracks"
                value={formatNumber(stats.data?.tracks_count)}
                loading={stats.isPending}
                unavailable={stats.isError ? UNAVAILABLE : undefined}
                testId="metric-total-tracks"
              />
              <MetricCard
                label="Pending reviews"
                value={formatNumber(pendingReviews)}
                tone={pendingReviews > 0 ? 'warning' : 'success'}
                loading={coreLoading}
                unavailable={reviewsUnavailable ? UNAVAILABLE : undefined}
                testId="metric-pending-reviews"
              />
              <MetricCard
                label="Missing files"
                value={formatNumber(stats.data?.missing_files)}
                tone={(stats.data?.missing_files ?? 0) > 0 ? 'danger' : 'success'}
                loading={stats.isPending}
                unavailable={stats.isError ? UNAVAILABLE : undefined}
                testId="metric-missing-files"
              />
              <MetricCard
                label="Untracked files"
                value={formatNumber(stats.data?.untracked_files)}
                tone={(stats.data?.untracked_files ?? 0) > 0 ? 'warning' : 'success'}
                loading={stats.isPending}
                unavailable={stats.isError ? UNAVAILABLE : undefined}
                testId="metric-untracked-files"
              />
              <MetricCard
                label="BPM coverage"
                value={
                  overview.data
                    ? formatPercent(overview.data.tracks_with_bpm, overview.data.total_tracks)
                    : '—'
                }
                sub={
                  overview.data
                    ? `${formatNumber(overview.data.total_tracks - overview.data.tracks_with_bpm)} missing`
                    : undefined
                }
                loading={overview.isPending}
                unavailable={overview.isError ? UNAVAILABLE : undefined}
                testId="metric-bpm-coverage"
              />
              <MetricCard
                label="Key coverage"
                value={
                  overview.data
                    ? formatPercent(
                        overview.data.tracks_with_camelot_key,
                        overview.data.total_tracks,
                      )
                    : '—'
                }
                sub={
                  overview.data
                    ? `${formatNumber(overview.data.total_tracks - overview.data.tracks_with_camelot_key)} missing`
                    : undefined
                }
                loading={overview.isPending}
                unavailable={overview.isError ? UNAVAILABLE : undefined}
                testId="metric-key-coverage"
              />
              <MetricCard
                label="Last audit"
                value={
                  <span style={{ fontSize: 15 }}>{lastAudit ? formatRelativeTime(lastAudit) : 'Never run'}</span>
                }
                loading={stats.isPending}
                unavailable={stats.isError ? UNAVAILABLE : undefined}
                testId="metric-last-audit"
              />
            </div>
          </section>

          <section className="cm-home-section" aria-labelledby="home-attention-heading">
            <h2 id="home-attention-heading">Requires attention</h2>
            <AttentionCards
              loading={coreLoading}
              counts={{
                repairPending: repair.isError ? null : (repair.data?.pending_count ?? 0),
                sanitationPending: sanitation.isError ? null : (sanitation.data?.pending_count ?? 0),
                enrichmentReviewCount: enrichmentQueue.isError
                  ? null
                  : enrichmentReviewable(enrichmentQueue.data, enrichmentReview.data),
                bpmPending: bpm.isError ? null : bpmPendingCount(bpm.data),
                missingFiles: stats.isError ? null : (stats.data?.missing_files ?? 0),
                untrackedFiles: stats.isError ? null : (stats.data?.untracked_files ?? 0),
                failedJobs: jobs.isError ? null : failedJobs,
              }}
            />
          </section>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-4)', minWidth: 0 }}>
          <ReadinessPanel query={preflight} />
          <CoveragePanel query={overview} />
          <RecentJobsPanel query={jobs} />
        </div>
      </div>
    </div>
  )
}
