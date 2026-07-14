import { apiFetch } from './client'
import type { ExportRunRequest, ExportRunResponse, ValidateResponse } from '../types/export'
import type { Job } from '../types/job'

export function validateExport(): Promise<ValidateResponse> {
  return apiFetch.post<ValidateResponse>('/exports/validate', {})
}

export function runExport(req: ExportRunRequest): Promise<ExportRunResponse> {
  return apiFetch.post<ExportRunResponse>('/exports/run', req)
}

export function fetchExports(limit = 20, offset = 0): Promise<Job[]> {
  return apiFetch.get<Job[]>(`/exports?limit=${limit}&offset=${offset}`)
}

export function fetchExport(id: string): Promise<Job> {
  return apiFetch.get<Job>(`/exports/${id}`)
}
