export interface LibraryQualityQueueSummary {
  queue_total: number
  pending: number
  approved: number
  partial: number
  applied: number
  no_op: number
  by_confidence: Record<'HIGH' | 'MEDIUM' | 'LOW', number>
}

export interface LibraryQualityCoverage {
  with_artist: number
  with_title: number
  with_bpm: number
  with_camelot: number
  with_genre: number
}

export interface LibraryQualityAction {
  label: string
  reason: string
  target: string
}

export interface LibraryQualityResponse {
  total_tracks: number
  issue_total: number
  issues_by_type: {
    missing_artist: number
    missing_title: number
    suspicious_artist: number
    suspicious_title: number
    weak_filename_parse: number
  }
  metadata_repair: LibraryQualityQueueSummary
  metadata_sanitation: LibraryQualityQueueSummary
  coverage: LibraryQualityCoverage
  recommended_next_actions: LibraryQualityAction[]
}
