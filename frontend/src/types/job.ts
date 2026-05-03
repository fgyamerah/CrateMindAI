export type JobStatus = 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Job {
  id:               string
  command:          string
  args:             string[]
  status:           JobStatus
  created_at:       string
  started_at:       string | null
  finished_at:      string | null
  exit_code:        number | null
  log_path:         string | null

  // Process PID — present while the job is running, null otherwise
  pid:              number | null

  // Progress — only populated for ssd-sync jobs
  progress_current: number | null
  progress_total:   number | null
  progress_percent: number | null
  progress_message: string | null
}

export function isActive(job: Job): boolean {
  return job.status === 'pending' || job.status === 'running'
}

// Commands that the backend accepts.
// Keep in sync with backend/app/services/toolkit_runner.py ALLOWED_COMMANDS.
export const ALLOWED_COMMANDS = [
  'analyze-missing',
  'artist-folder-clean',
  'artist-merge',
  'audit-quality',
  'convert-audio',
  'cue-suggest',
  'db-prune-stale',
  'dedupe',
  'generate-docs',
  'harmonic-suggest',
  'label-clean',
  'label-intel',
  'metadata-clean',
  'metadata-sanitize',
  'playlists',
  'rekordbox-export',
  'set-builder',
  'tag-normalize',
  'validate-docs',
] as const

export type AllowedCommand = (typeof ALLOWED_COMMANDS)[number]

// ---------------------------------------------------------------------------
// Pipeline run results
// ---------------------------------------------------------------------------

export interface RunListItem {
  prefix:     string
  command:    string
  started_at: string | null
  label:      string
}

export interface RunSummary {
  prefix:           string
  command:          string
  started_at:       string | null
  finished_at:      string | null
  duration:         number | null
  files_scanned:    number | null
  files_processed:  number | null
  changed:          number | null
  skipped:          number | null
  errors:           number | null
  review_count:     number | null
  moved_to_ignored: number | null
  detail_groups:    Record<string, string[]>
}

export interface RunDetailEntry {
  filepath: string | null
  reason:   string | null
  details:  unknown
}

export type ResultGroup = 'modified' | 'skipped' | 'errors'
