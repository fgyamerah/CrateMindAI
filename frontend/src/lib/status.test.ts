import { describe, expect, it } from 'vitest'
import {
  jobStatusMeta,
  preflightStatusMeta,
  reviewStatusMeta,
  REVIEW_STATUS_META,
} from './status'

describe('status model', () => {
  it('never labels an approved item as applied', () => {
    expect(REVIEW_STATUS_META.approved.label).toBe('Approved for apply')
    expect(REVIEW_STATUS_META.approved.label).not.toContain('Applied')
    expect(REVIEW_STATUS_META.applied.label).toBe('Applied')
  })

  it('maps every review status to a label and tone', () => {
    for (const key of Object.keys(REVIEW_STATUS_META)) {
      const meta = reviewStatusMeta(key)
      expect(meta.label.length).toBeGreaterThan(0)
      expect(meta.tone.length).toBeGreaterThan(0)
    }
  })

  it('degrades gracefully for unknown statuses', () => {
    expect(reviewStatusMeta('mystery').tone).toBe('neutral')
    expect(jobStatusMeta(undefined).label).toBe('Unknown')
    expect(preflightStatusMeta('nope').label).toBe('Unknown')
  })

  it('maps job lifecycle statuses', () => {
    expect(jobStatusMeta('running').tone).toBe('info')
    expect(jobStatusMeta('failed').tone).toBe('danger')
    expect(jobStatusMeta('succeeded').tone).toBe('success')
  })
})
