import { describe, expect, it } from 'vitest'
import {
  parseLibraryParams,
  serializeLibraryParams,
  toApiQuery,
  activeFilterCount,
  DEFAULT_PARAMS,
} from './libraryParams'

const parse = (qs: string) => parseLibraryParams(new URLSearchParams(qs))

describe('parseLibraryParams', () => {
  it('returns defaults for an empty URL', () => {
    expect(parse('')).toEqual(DEFAULT_PARAMS)
  })

  it('round-trips valid params', () => {
    const p = parse('q=night&page=3&sort=bpm&order=desc&issue=missing_artist&confidence=HIGH&missing=bpm&key=8A&track=42')
    expect(p.q).toBe('night')
    expect(p.page).toBe(3)
    expect(p.sort).toBe('bpm')
    expect(p.order).toBe('desc')
    expect(p.issue).toBe('missing_artist')
    expect(p.confidence).toBe('HIGH')
    expect(p.missing).toBe('bpm')
    expect(p.key).toBe('8A')
    expect(p.track).toBe(42)
    const qs = serializeLibraryParams(p)
    expect(parseLibraryParams(qs)).toEqual(p)
  })

  it('sanitizes invalid values instead of passing them through', () => {
    const p = parse('sort=;DROP TABLE;&order=up&page=-4&issue=bogus&confidence=SUPER&key=99Z&bpm_min=abc&bpm_max=99999&track=x&missing=cues')
    expect(p.sort).toBe('artist')
    expect(p.order).toBe('asc')
    expect(p.page).toBe(1)
    expect(p.issue).toBe('')
    expect(p.confidence).toBe('')
    expect(p.key).toBe('')
    expect(p.bpmMin).toBeNull()
    expect(p.bpmMax).toBeNull()
    expect(p.track).toBeNull()
    expect(p.missing).toBe('')
  })

  it('accepts case-insensitive enum values', () => {
    const p = parse('confidence=high&key=8a&issue=MISSING_TITLE')
    expect(p.confidence).toBe('HIGH')
    expect(p.key).toBe('8A')
    expect(p.issue).toBe('missing_title')
  })

  it('caps overlong strings', () => {
    const p = parse(`q=${'x'.repeat(500)}`)
    expect(p.q.length).toBe(200)
  })
})

describe('serializeLibraryParams', () => {
  it('omits defaults for clean URLs', () => {
    expect(serializeLibraryParams(DEFAULT_PARAMS).toString()).toBe('')
  })
})

describe('toApiQuery', () => {
  it('maps missing filters to has_bpm/has_key', () => {
    const qs = toApiQuery({ ...DEFAULT_PARAMS, missing: 'bpm' })
    expect(qs).toContain('has_bpm=false')
    const qs2 = toApiQuery({ ...DEFAULT_PARAMS, missing: 'key' })
    expect(qs2).toContain('has_key=false')
  })

  it('computes offset from page', () => {
    const qs = toApiQuery({ ...DEFAULT_PARAMS, page: 3 })
    expect(qs).toContain('offset=200')
    expect(qs).toContain('limit=100')
  })

  it('never emits the raw track/view params to the API', () => {
    const qs = toApiQuery({ ...DEFAULT_PARAMS, track: 12, view: 'all' })
    expect(qs).not.toContain('track')
    expect(qs).not.toContain('view')
  })
})

describe('activeFilterCount', () => {
  it('counts only active filters', () => {
    expect(activeFilterCount(DEFAULT_PARAMS)).toBe(0)
    expect(
      activeFilterCount({ ...DEFAULT_PARAMS, issue: 'missing_artist', bpmMin: 100, missing: 'key' }),
    ).toBe(3)
  })
})
