export type SyncSource = 'library' | 'inbox'

export interface SyncFileChange {
  path:   string
  is_dir: boolean
}

export interface SyncPreviewRequest {
  source: SyncSource
}

export interface SyncPreviewResponse {
  source_path:  string
  dest_path:    string
  file_count:   number
  files:        SyncFileChange[]
  truncated:    boolean
  summary:      string | null
  warnings:     string[]
  ssd_mounted:  boolean
}

export interface SyncRunRequest {
  source:       SyncSource
  allow_delete: boolean
}

export interface SyncRunResponse {
  job_id:  string
  message: string
}

export interface SyncConfigResponse {
  sources:      Record<string, string>   // name → resolved path
  dest:         string
  rsync_bin:    string
  ssd_mounted:  boolean
}
