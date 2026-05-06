import { apiFetch } from './client'

export interface HealthResponse {
  ok: boolean
  library_root: string
  db_path: string
  db_exists: boolean
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
