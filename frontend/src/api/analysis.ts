import { apiFetch } from './client'
import type {
  BpmAnomaly,
  BpmCheckResult,
  BpmSummary,
  ReanalyzeRequest,
  UpdateAnomalyRequest,
} from '../types/analysis'
import type { Job } from '../types/job'

export function runBpmCheck(): Promise<BpmCheckResult> {
  return apiFetch.post<BpmCheckResult>('/analysis/bpm-check', {})
}

export function fetchBpmAnomalies(params: {
  status?: string
  reason?: string
  limit?: number
  offset?: number
} = {}): Promise<BpmAnomaly[]> {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== '') {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    }
  }
  const qs = parts.length ? `?${parts.join('&')}` : ''
  return apiFetch.get<BpmAnomaly[]>(`/analysis/bpm-anomalies${qs}`)
}

export function fetchBpmSummary(): Promise<BpmSummary> {
  return apiFetch.get<BpmSummary>('/analysis/bpm-anomalies/summary')
}

export function updateAnomaly(
  id: number,
  body: UpdateAnomalyRequest,
): Promise<BpmAnomaly> {
  return apiFetch.patch<BpmAnomaly>(`/analysis/bpm-anomalies/${id}`, body)
}

export function submitReanalyze(body: ReanalyzeRequest): Promise<Job> {
  return apiFetch.post<Job>('/analysis/reanalyze', body)
}
