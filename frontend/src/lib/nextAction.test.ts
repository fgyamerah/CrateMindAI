import { describe, expect, it } from 'vitest'
import { computeNextAction, type NextActionInput } from './nextAction'

const BASE: NextActionInput = {
  preflightStatus: 'ready',
  dbExists: true,
  totalTracks: 300,
  repairPending: 0,
  sanitationPending: 0,
  enrichmentReviewCount: 0,
  bpmPending: 0,
  missingFiles: 0,
  untrackedFiles: 0,
  runningJobs: 0,
  failedJobs: 0,
}

describe('computeNextAction', () => {
  it('prioritizes unsafe runtime above everything', () => {
    const action = computeNextAction({ ...BASE, preflightStatus: 'unsafe', repairPending: 10 })
    expect(action.id).toBe('fix-runtime')
  })

  it('asks for a scan when the database is missing', () => {
    const action = computeNextAction({ ...BASE, dbExists: false, missingFiles: 4 })
    expect(action.id).toBe('scan-library')
    expect(action.to).toBe('/jobs')
  })

  it('surfaces failed jobs before review work', () => {
    const action = computeNextAction({ ...BASE, failedJobs: 2, repairPending: 5 })
    expect(action.id).toBe('failed-jobs')
  })

  it('recommends metadata repair review first among queues', () => {
    const action = computeNextAction({
      ...BASE,
      repairPending: 9,
      sanitationPending: 4,
      enrichmentReviewCount: 12,
    })
    expect(action.id).toBe('review-repairs')
    expect(action.title).toContain('9')
    expect(action.to).toBe('/metadata-repair')
  })

  it('falls through queues in order', () => {
    expect(computeNextAction({ ...BASE, sanitationPending: 3 }).id).toBe('review-sanitation')
    expect(computeNextAction({ ...BASE, enrichmentReviewCount: 2 }).id).toBe('review-enrichment')
    expect(computeNextAction({ ...BASE, bpmPending: 1 }).id).toBe('review-bpm')
    expect(computeNextAction({ ...BASE, missingFiles: 7 }).id).toBe('audit-missing')
    expect(computeNextAction({ ...BASE, untrackedFiles: 8 }).id).toBe('audit-untracked')
  })

  it('uses singular grammar for single items', () => {
    const action = computeNextAction({ ...BASE, repairPending: 1 })
    expect(action.title).toContain('1 pending metadata repair')
    expect(action.title).not.toContain('repairs')
  })

  it('suggests export validation when everything is clear', () => {
    const action = computeNextAction(BASE)
    expect(action.id).toBe('all-clear')
    expect(action.to).toBe('/exports')
  })
})
