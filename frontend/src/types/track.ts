export type TrackStatus =
  | 'pending'
  | 'ok'
  | 'rejected'
  | 'duplicate'
  | 'needs_review'
  | 'error'
  | 'stale'

export type QualityTier = 'LOSSLESS' | 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN'

export type TrackIssue =
  | 'missing_bpm'
  | 'missing_key'
  | 'missing_artist'
  | 'missing_title'
  | 'low_quality'
  | 'error'
  | 'needs_review'
  | 'weak_filename_parse'
  | 'suspicious_artist'
  | 'suspicious_title'

export type ParseConfidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'UNKNOWN'

// Shape returned by GET /api/tracks (list view)
export interface TrackSummary {
  id:           number
  filepath:     string
  filename:     string
  artist:       string | null
  title:        string | null
  genre:        string | null
  bpm:          number | null
  key_camelot:  string | null
  key_musical:  string | null
  duration_sec: number | null
  bitrate_kbps: number | null
  status:       TrackStatus
  quality_tier: QualityTier | null
  parse_confidence: ParseConfidence | null
  issues:       TrackIssue[]
  recommended_action?: string | null
  recommended_route?: string | null
}

// Shape returned by GET /api/tracks/{id} (full detail)
export interface TrackDetail extends TrackSummary {
  filesize_bytes: number | null
  filesystem_path: string
  error_msg:      string | null
  processed_at:   string | null
  pipeline_ver:   string | null
  enrichment_queue_item?: Record<string, unknown> | null
}

export interface TrackStats {
  total:          number
  by_status:      Record<string, number>
  by_quality:     Record<string, number>
  missing_bpm:    number
  missing_key:    number
  missing_artist: number
  missing_title:  number
}

export interface TrackPage {
  items:  TrackSummary[]
  limit:  number
  offset: number
  total:  number
}

export interface TrackIssueCounts {
  missing_artist:       number
  missing_title:        number
  weak_filename_parse:  number
  suspicious_artist:    number
  suspicious_title:     number
}

export interface TrackListParams {
  path?:         string
  q?:            string
  search?:       string
  status?:       string
  artist?:       string
  genre?:        string
  key?:          string
  quality_tier?: string
  bpm_min?:      number
  bpm_max?:      number
  has_key?:      boolean
  issue?:        TrackIssue | string
  parse_confidence?: ParseConfidence | string
  sort?:         'artist' | 'title' | 'bpm' | 'processed_at' | 'filename'
  order?:        'asc' | 'desc'
  limit?:        number
  offset?:       number
}

export const ISSUE_LABELS: Record<TrackIssue, string> = {
  missing_bpm:    'No BPM',
  missing_key:    'No Key',
  missing_artist: 'No Artist',
  missing_title:  'No Title',
  low_quality:    'Low Quality',
  error:          'Error',
  needs_review:   'Needs Review',
  weak_filename_parse: 'Weak Filename Parse',
  suspicious_artist:   'Suspicious Artist',
  suspicious_title:    'Suspicious Title',
}

export const QUALITY_ORDER: QualityTier[] = ['LOSSLESS', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']
