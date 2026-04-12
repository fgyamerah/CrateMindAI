import { apiFetch } from './client'

export interface HealthResponse {
  status: string
  pipeline_py_found: boolean
}

export interface VersionResponse {
  backend_version: string
  toolkit_version: string
  pipeline_py: string
}

export const fetchHealth = (): Promise<HealthResponse> =>
  apiFetch.get<HealthResponse>('/health')

export const fetchVersion = (): Promise<VersionResponse> =>
  apiFetch.get<VersionResponse>('/version')
