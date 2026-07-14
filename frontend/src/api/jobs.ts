import { apiFetch } from './client'
import type { Job } from '../types/job'

export function fetchJobs(limit = 100, offset = 0): Promise<Job[]> {
  return apiFetch.get<Job[]>(`/jobs?limit=${limit}&offset=${offset}`)
}

export function fetchJob(id: string): Promise<Job> {
  return apiFetch.get<Job>(`/jobs/${id}`)
}

export function fetchJobLogs(id: string, tail?: number): Promise<string> {
  const qs = tail !== undefined ? `?tail=${tail}` : ''
  return apiFetch.text(`/jobs/${id}/logs${qs}`)
}

export interface SubmitJobRequest {
  command: string
  args: string[]
}

export function submitJob(req: SubmitJobRequest): Promise<Job> {
  return apiFetch.post<Job>('/jobs', req)
}

export interface CancelResponse {
  job_id:  string
  success: boolean
  message: string
}

export function cancelJob(id: string): Promise<CancelResponse> {
  return apiFetch.post<CancelResponse>(`/jobs/${id}/cancel`, {})
}
