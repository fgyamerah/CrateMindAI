/**
 * Recommended-next-action engine for the command-center home.
 *
 * Pure and deterministic so it can be unit tested. The backend remains the
 * authority for whether operations are safe; this only prioritizes visible,
 * already-reported state into a single suggestion.
 */

export interface NextActionInput {
  preflightStatus: 'ready' | 'degraded' | 'unsafe' | null
  dbExists: boolean | null
  totalTracks: number | null
  repairPending: number
  sanitationPending: number
  enrichmentReviewCount: number
  bpmPending: number
  missingFiles: number
  untrackedFiles: number
  runningJobs: number
  failedJobs: number
}

export interface NextAction {
  id: string
  title: string
  detail: string
  to: string
  cta: string
}

export function computeNextAction(input: NextActionInput): NextAction {
  if (input.preflightStatus === 'unsafe') {
    return {
      id: 'fix-runtime',
      title: 'Resolve runtime issues before operating on the library',
      detail: 'Preflight reported failing checks. Write operations are blocked until resolved.',
      to: '/',
      cta: 'View readiness checks',
    }
  }

  if (input.dbExists === false) {
    return {
      id: 'scan-library',
      title: 'Scan your library to build the track database',
      detail: 'No pipeline database was found for the selected root. Run a scan job first.',
      to: '/jobs',
      cta: 'Open Jobs',
    }
  }

  if (input.failedJobs > 0) {
    return {
      id: 'failed-jobs',
      title: `Inspect ${input.failedJobs} failed ${input.failedJobs === 1 ? 'job' : 'jobs'}`,
      detail: 'Recent operations failed. Review the logs before continuing cleanup.',
      to: '/jobs',
      cta: 'Open Jobs',
    }
  }

  if (input.repairPending > 0) {
    return {
      id: 'review-repairs',
      title: `Review ${input.repairPending} pending metadata ${input.repairPending === 1 ? 'repair' : 'repairs'}`,
      detail: 'Deterministic repair proposals are waiting for approval. Nothing is applied without review.',
      to: '/metadata-repair',
      cta: 'Review repairs',
    }
  }

  if (input.sanitationPending > 0) {
    return {
      id: 'review-sanitation',
      title: `Review ${input.sanitationPending} metadata sanitation ${input.sanitationPending === 1 ? 'proposal' : 'proposals'}`,
      detail: 'Sanitation proposals remove junk tokens from tags after your approval.',
      to: '/metadata-sanitation',
      cta: 'Review sanitation',
    }
  }

  if (input.enrichmentReviewCount > 0) {
    return {
      id: 'review-enrichment',
      title: `Review ${input.enrichmentReviewCount} enrichment ${input.enrichmentReviewCount === 1 ? 'match' : 'matches'}`,
      detail: 'Online metadata candidates need a human decision before anything is written.',
      to: '/enrichment',
      cta: 'Review enrichment',
    }
  }

  if (input.bpmPending > 0) {
    return {
      id: 'review-bpm',
      title: `Review ${input.bpmPending} BPM ${input.bpmPending === 1 ? 'anomaly' : 'anomalies'}`,
      detail: 'Suspicious BPM values were detected. BPM is never overwritten automatically.',
      to: '/bpm-review',
      cta: 'Review anomalies',
    }
  }

  if (input.missingFiles > 0) {
    return {
      id: 'audit-missing',
      title: `Audit ${input.missingFiles} missing ${input.missingFiles === 1 ? 'path' : 'paths'}`,
      detail: 'Database rows point at files that no longer exist on disk.',
      to: '/audit',
      cta: 'Open audit',
    }
  }

  if (input.untrackedFiles > 0) {
    return {
      id: 'audit-untracked',
      title: `Inspect ${input.untrackedFiles} untracked ${input.untrackedFiles === 1 ? 'file' : 'files'}`,
      detail: 'Audio files exist on disk that are not represented in the database.',
      to: '/audit',
      cta: 'Open audit',
    }
  }

  return {
    id: 'all-clear',
    title: 'No pending reviews — validate export readiness',
    detail: 'The review queues are clear. Validate the library for Rekordbox export next.',
    to: '/exports',
    cta: 'Open Export',
  }
}
