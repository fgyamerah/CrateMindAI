/**
 * URL-backed Library workspace state.
 *
 * All values parsed from the URL are sanitized against allowlists so a
 * malformed or hostile URL can never produce an invalid API request.
 */

export const SORT_KEYS = [
  'artist', 'title', 'bpm', 'processed_at', 'filename',
  'genre', 'key', 'duration', 'bitrate', 'status',
] as const
export type SortKey = (typeof SORT_KEYS)[number]

export const ISSUE_FILTERS = [
  'missing_artist', 'missing_title', 'weak_filename_parse',
  'suspicious_artist', 'suspicious_title',
] as const
export type IssueFilter = (typeof ISSUE_FILTERS)[number]

export const CONFIDENCE_FILTERS = ['HIGH', 'MEDIUM', 'LOW'] as const
export const STATUS_FILTERS = ['ok', 'needs_review', 'error', 'pending'] as const
export const MISSING_FILTERS = ['bpm', 'key'] as const
export const CAMELOT_KEYS = Array.from({ length: 12 }, (_, i) => i + 1)
  .flatMap((n) => [`${n}A`, `${n}B`])

export const PAGE_SIZE = 100

export interface LibraryParams {
  q: string
  page: number
  sort: SortKey
  order: 'asc' | 'desc'
  issue: IssueFilter | ''
  confidence: '' | 'HIGH' | 'MEDIUM' | 'LOW'
  status: '' | 'ok' | 'needs_review' | 'error' | 'pending'
  genre: string
  key: string
  bpmMin: number | null
  bpmMax: number | null
  missing: '' | 'bpm' | 'key'
  track: number | null
  view: string
}

export const DEFAULT_PARAMS: LibraryParams = {
  q: '',
  page: 1,
  sort: 'artist',
  order: 'asc',
  issue: '',
  confidence: '',
  status: '',
  genre: '',
  key: '',
  bpmMin: null,
  bpmMax: null,
  missing: '',
  track: null,
  view: '',
}

function oneOf<T extends string>(value: string | null, allowed: readonly T[]): T | '' {
  if (!value) return ''
  const hit = allowed.find((a) => a.toLowerCase() === value.toLowerCase())
  return hit ?? ''
}

function positiveInt(value: string | null, fallback: number): number {
  const n = Number(value)
  if (!Number.isInteger(n) || n < 1 || n > 100_000) return fallback
  return n
}

function bpmValue(value: string | null): number | null {
  if (!value) return null
  const n = Number(value)
  if (!Number.isFinite(n) || n < 0 || n > 999) return null
  return n
}

export function parseLibraryParams(search: URLSearchParams): LibraryParams {
  const sort = oneOf(search.get('sort'), SORT_KEYS)
  const order = search.get('order') === 'desc' ? 'desc' : 'asc'
  const trackRaw = Number(search.get('track'))
  return {
    q: (search.get('q') ?? '').slice(0, 200),
    page: positiveInt(search.get('page'), 1),
    sort: (sort || 'artist') as SortKey,
    order,
    issue: oneOf(search.get('issue'), ISSUE_FILTERS),
    confidence: oneOf(search.get('confidence'), CONFIDENCE_FILTERS),
    status: oneOf(search.get('status'), STATUS_FILTERS),
    genre: (search.get('genre') ?? '').slice(0, 64),
    key: oneOf(search.get('key'), CAMELOT_KEYS),
    bpmMin: bpmValue(search.get('bpm_min')),
    bpmMax: bpmValue(search.get('bpm_max')),
    missing: oneOf(search.get('missing'), MISSING_FILTERS),
    track: Number.isInteger(trackRaw) && trackRaw > 0 ? trackRaw : null,
    view: (search.get('view') ?? '').slice(0, 64),
  }
}

/** Serialize params back to URL search params, omitting defaults. */
export function serializeLibraryParams(p: LibraryParams): URLSearchParams {
  const out = new URLSearchParams()
  if (p.q) out.set('q', p.q)
  if (p.page > 1) out.set('page', String(p.page))
  if (p.sort !== 'artist') out.set('sort', p.sort)
  if (p.order !== 'asc') out.set('order', p.order)
  if (p.issue) out.set('issue', p.issue)
  if (p.confidence) out.set('confidence', p.confidence)
  if (p.status) out.set('status', p.status)
  if (p.genre) out.set('genre', p.genre)
  if (p.key) out.set('key', p.key)
  if (p.bpmMin !== null) out.set('bpm_min', String(p.bpmMin))
  if (p.bpmMax !== null) out.set('bpm_max', String(p.bpmMax))
  if (p.missing) out.set('missing', p.missing)
  if (p.track !== null) out.set('track', String(p.track))
  if (p.view) out.set('view', p.view)
  return out
}

/** Build the /api/tracks query string for the given params. */
export function toApiQuery(p: LibraryParams): string {
  const qs = new URLSearchParams()
  if (p.q) qs.set('search', p.q)
  if (p.issue) qs.set('issue', p.issue)
  if (p.confidence) qs.set('parse_confidence', p.confidence)
  if (p.status) qs.set('status', p.status)
  if (p.genre) qs.set('genre', p.genre)
  if (p.key) qs.set('key', p.key)
  if (p.bpmMin !== null) qs.set('bpm_min', String(p.bpmMin))
  if (p.bpmMax !== null) qs.set('bpm_max', String(p.bpmMax))
  if (p.missing === 'bpm') qs.set('has_bpm', 'false')
  if (p.missing === 'key') qs.set('has_key', 'false')
  qs.set('sort', p.sort)
  qs.set('order', p.order)
  qs.set('limit', String(PAGE_SIZE))
  qs.set('offset', String((p.page - 1) * PAGE_SIZE))
  return qs.toString()
}

export function activeFilterCount(p: LibraryParams): number {
  let n = 0
  if (p.issue) n += 1
  if (p.confidence) n += 1
  if (p.status) n += 1
  if (p.genre) n += 1
  if (p.key) n += 1
  if (p.bpmMin !== null || p.bpmMax !== null) n += 1
  if (p.missing) n += 1
  return n
}
