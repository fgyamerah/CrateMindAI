export type MetadataRepairConfidence = 'HIGH' | 'MEDIUM' | 'LOW' | 'REVIEW_REQUIRED'
export type MetadataRepairStatus = 'pending' | 'approved' | 'rejected' | 'deferred'
export type MetadataRepairFieldName = 'artist' | 'title'
export type MetadataRepairFieldReviewStatus = 'pending' | 'approved' | 'rejected' | 'deferred' | 'applied' | 'no_op'
export type MetadataRepairDerivedStatus = 'PENDING' | 'APPROVED' | 'PARTIAL' | 'REJECTED' | 'APPLIED' | 'PARTIAL_APPLIED' | 'NO_OP'

export interface MetadataRepairFieldState {
  status: MetadataRepairFieldReviewStatus
  current: string | null
  proposed: string | null
  original_proposed?: string | null
  edited?: boolean
  applied_at?: string | null
  applied_value?: string | null
  previous_value?: string | null
  effective_status?: string
}

export interface MetadataRepairProposal {
  track_id: number
  filepath: string
  filename: string
  fields: Record<MetadataRepairFieldName, MetadataRepairFieldState>
  current: {
    artist: string | null
    title: string | null
    parse_confidence: string | null
  }
  proposed: {
    artist: string | null
    title: string | null
  }
  repair_type: string
  confidence: MetadataRepairConfidence | string
  confidence_reason: string
  risk_flags: string[]
  reason: string
  status: MetadataRepairDerivedStatus | string
  effective_status?: MetadataRepairDerivedStatus | string
  created_at: string
  review_updated_at?: string | null
}

export interface MetadataRepairQueueResponse {
  items: MetadataRepairProposal[]
  counts: Record<string, Record<string, number>>
  total: number
  limit: number
  offset: number
}

export interface MetadataRepairSummary {
  queue_total: number
  pending_count: number
  approved_count: number
  partial_count: number
  rejected_count: number
  deferred_count: number
  applied_count: number
  partial_applied_count: number
  no_op_count: number
  high_count: number
  medium_count: number
  low_count: number
  counts: Record<string, Record<string, number>>
  queue_path: string
  state_path: string
  updated_at: string | null
}

export interface MetadataRepairApplyResponse {
  root: string
  db_path: string
  queue_path: string
  state_path: string
  dry_run: boolean
  approved_seen: number
  proposed_count: number
  applied_count: number
  applied_field_count?: number
  skipped_count: number
  changes: Array<Record<string, unknown>>
  skipped: Array<Record<string, unknown>>
}
