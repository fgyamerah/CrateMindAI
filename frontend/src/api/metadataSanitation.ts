import { apiFetch } from './client'
import type {
  MetadataSanitationApplyResponse,
  MetadataSanitationFieldName,
  MetadataSanitationQueueResponse,
  MetadataSanitationSummary,
} from '../types/metadataSanitation'

export interface MetadataSanitationQueueFilters {
  repair_type?: string
  confidence?: string
  status?: string
  include_applied?: boolean
}

export interface MetadataSanitationGenerateResponse {
  root: string
  track_id: number
  generated: boolean
  replaced: boolean
  no_op_reason: string | null
  queue_path: string
  proposal: Record<string, unknown> | null
}

function queryString(filters: MetadataSanitationQueueFilters): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== false && value !== '') params.set(key, String(value))
  })
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export function fetchMetadataSanitationQueue(
  filters: MetadataSanitationQueueFilters = {},
): Promise<MetadataSanitationQueueResponse> {
  return apiFetch.get<MetadataSanitationQueueResponse>(`/metadata-sanitation/queue${queryString(filters)}`)
}

export function fetchMetadataSanitationSummary(): Promise<MetadataSanitationSummary> {
  return apiFetch.get<MetadataSanitationSummary>('/metadata-sanitation/summary')
}

export function approveMetadataSanitation(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/approve`, {})
}

export function rejectMetadataSanitation(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/reject`, {})
}

export function deferMetadataSanitation(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/defer`, {})
}

export function approveMetadataSanitationField(trackId: number, field: MetadataSanitationFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/field/${field}/approve`, {})
}

export function rejectMetadataSanitationField(trackId: number, field: MetadataSanitationFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/field/${field}/reject`, {})
}

export function deferMetadataSanitationField(trackId: number, field: MetadataSanitationFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-sanitation/${trackId}/field/${field}/defer`, {})
}

export function updateMetadataSanitationFieldProposal(
  trackId: number,
  field: MetadataSanitationFieldName,
  proposed: string,
): Promise<unknown> {
  return apiFetch.patch(`/metadata-sanitation/${trackId}/field/${field}/proposal`, { proposed })
}

export function generateMetadataSanitationTrack(trackId: number): Promise<MetadataSanitationGenerateResponse> {
  return apiFetch.post<MetadataSanitationGenerateResponse>(`/metadata-sanitation/generate/${trackId}`, {})
}

export function dryRunMetadataSanitationApply(): Promise<MetadataSanitationApplyResponse> {
  return apiFetch.post<MetadataSanitationApplyResponse>('/metadata-sanitation/apply-approved/dry-run', {})
}

export function applyMetadataSanitationApproved(): Promise<MetadataSanitationApplyResponse> {
  return apiFetch.post<MetadataSanitationApplyResponse>('/metadata-sanitation/apply-approved/apply?confirm=true', {})
}
