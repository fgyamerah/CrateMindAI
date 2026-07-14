import { apiFetch } from './client'
import type {
  SyncConfigResponse,
  SyncPreviewRequest,
  SyncPreviewResponse,
  SyncRunRequest,
  SyncRunResponse,
} from '../types/sync'
import type { Job } from '../types/job'

export function fetchSyncConfig(): Promise<SyncConfigResponse> {
  return apiFetch.get<SyncConfigResponse>('/sync/config')
}

export function previewSync(req: SyncPreviewRequest): Promise<SyncPreviewResponse> {
  return apiFetch.post<SyncPreviewResponse>('/sync/preview', req)
}

export function startSync(req: SyncRunRequest): Promise<SyncRunResponse> {
  return apiFetch.post<SyncRunResponse>('/sync/run', req)
}

export function fetchSyncJobs(limit = 20, offset = 0): Promise<Job[]> {
  return apiFetch.get<Job[]>(`/sync?limit=${limit}&offset=${offset}`)
}

export function fetchSyncJob(id: string): Promise<Job> {
  return apiFetch.get<Job>(`/sync/${id}`)
}
