import { apiFetch } from './client'

export interface ManualMetadataRequest {
  track_id: number
  artist: string
  title: string
}

export interface ManualMetadataDiffItem {
  field: 'artist' | 'title' | string
  current: string | null
  proposed: string
  changed: boolean
}

export interface ManualMetadataPreviewResponse {
  track_id: number
  filepath: string
  filename: string
  current: Record<string, string | null>
  proposed: Record<string, string>
  changed_fields: string[]
  no_op: boolean
  validation_warnings: string[]
  diff: ManualMetadataDiffItem[]
}

export interface ManualMetadataApplyResponse extends ManualMetadataPreviewResponse {
  applied_fields: string[]
  before: Record<string, string | null>
  after: Record<string, string | null>
  audit_path: string | null
}

export function previewManualMetadata(payload: ManualMetadataRequest): Promise<ManualMetadataPreviewResponse> {
  return apiFetch.post<ManualMetadataPreviewResponse>('/manual-metadata/preview', payload)
}

export function applyManualMetadata(payload: ManualMetadataRequest): Promise<ManualMetadataApplyResponse> {
  return apiFetch.post<ManualMetadataApplyResponse>('/manual-metadata/apply', payload)
}
