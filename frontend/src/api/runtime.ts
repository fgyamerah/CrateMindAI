import { apiFetch } from './client'

export interface PreflightCheck {
  id: string
  label: string
  status: 'pass' | 'warn' | 'fail'
  detail: string
  remediation: string
  optional: boolean
}

export interface PreflightResponse {
  status: 'ready' | 'degraded' | 'unsafe'
  library_root: string
  generated_at: string
  checks: PreflightCheck[]
}

export function getRuntimePreflight(): Promise<PreflightResponse> {
  return apiFetch.get<PreflightResponse>('/runtime/preflight')
}
