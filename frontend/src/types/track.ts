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
  issues:       TrackIssue[]
}

// Shape returned by GET /api/tracks/{id} (full detail)
export interface TrackDetail extends TrackSummary {
  filesize_bytes: number | null
  error_msg:      string | null
  processed_at:   string | null
  pipeline_ver:   string | null
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

export interface TrackIssueItem {
  id:       number
  filepath: string
  filename: string
  artist:   string | null
  title:    string | null
  status:   TrackStatus
  issues:   TrackIssue[]
}

export interface TrackListParams {
  q?:            string
  status?:       string
  artist?:       string
  genre?:        string
  key?:          string
  quality_tier?: string
  bpm_min?:      number
  bpm_max?:      number
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
}

export const QUALITY_ORDER: QualityTier[] = ['LOSSLESS', 'HIGH', 'MEDIUM', 'LOW', 'UNKNOWN']
