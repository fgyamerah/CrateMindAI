import { apiFetch } from './client'
import type {
  MetadataRepairApplyResponse,
  MetadataRepairQueueResponse,
  MetadataRepairSummary,
  MetadataRepairFieldName,
} from '../types/metadataRepair'

export interface MetadataRepairQueueFilters {
  repair_type?: string
  confidence?: string
  status?: string
  include_applied?: boolean
}

export interface MetadataRepairGenerateResponse {
  root: string
  track_id: number
  generated: boolean
  replaced: boolean
  no_op_reason: string | null
  queue_path: string
  proposal: Record<string, unknown> | null
}

function queryString(filters: MetadataRepairQueueFilters): string {
  const params = new URLSearchParams()
  Object.entries(filters).forEach(([key, value]) => {
    if (value !== undefined && value !== false && value !== '') params.set(key, String(value))
  })
  const qs = params.toString()
  return qs ? `?${qs}` : ''
}

export function fetchMetadataRepairQueue(filters: MetadataRepairQueueFilters = {}): Promise<MetadataRepairQueueResponse> {
  return apiFetch.get<MetadataRepairQueueResponse>(`/metadata-repair/queue${queryString(filters)}`)
}

export function fetchMetadataRepairSummary(): Promise<MetadataRepairSummary> {
  return apiFetch.get<MetadataRepairSummary>('/metadata-repair/summary')
}

export function approveMetadataRepair(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/approve`, {})
}

export function rejectMetadataRepair(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/reject`, {})
}

export function deferMetadataRepair(trackId: number): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/defer`, {})
}

export function approveMetadataRepairField(trackId: number, field: MetadataRepairFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/field/${field}/approve`, {})
}

export function rejectMetadataRepairField(trackId: number, field: MetadataRepairFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/field/${field}/reject`, {})
}

export function deferMetadataRepairField(trackId: number, field: MetadataRepairFieldName): Promise<unknown> {
  return apiFetch.post(`/metadata-repair/${trackId}/field/${field}/defer`, {})
}

export function updateMetadataRepairFieldProposal(
  trackId: number,
  field: MetadataRepairFieldName,
  proposed: string,
): Promise<unknown> {
  return apiFetch.patch(`/metadata-repair/${trackId}/field/${field}/proposal`, { proposed })
}

export function generateMetadataRepairTrack(trackId: number): Promise<MetadataRepairGenerateResponse> {
  return apiFetch.post<MetadataRepairGenerateResponse>(`/metadata-repair/generate/${trackId}`, {})
}

export function dryRunMetadataRepairApply(): Promise<MetadataRepairApplyResponse> {
  return apiFetch.post<MetadataRepairApplyResponse>('/metadata-repair/apply-approved/dry-run', {})
}

export function applyMetadataRepairApproved(): Promise<MetadataRepairApplyResponse> {
  return apiFetch.post<MetadataRepairApplyResponse>('/metadata-repair/apply-approved/apply?confirm=true', {})
}
