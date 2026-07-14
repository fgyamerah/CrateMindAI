export type Vibe      = 'warm' | 'peak' | 'deep' | 'driving'
export type Strategy  = 'safest' | 'energy_lift' | 'smooth_blend' | 'best_warmup' | 'best_late_set'
export type Structure = 'full' | 'simple' | 'peak_only'
export type Phase     = 'warmup' | 'build' | 'peak' | 'release' | 'outro'

export interface SetBuilderParams {
  duration:             number
  vibe:                 Vibe
  strategy:             Strategy
  structure:            Structure
  genre?:               string
  max_bpm_jump:         number
  strict_harmonic:      boolean
  artist_repeat_window: number
  name?:                string
  dry_run:              boolean
}

export interface SetBuilderJobResponse {
  job_id:  string
  message: string
}

export interface PlaylistSummary {
  id:           number
  name:         string
  created_at:   string
  duration_sec: number
  track_count:  number
  config_json:  string | null
}

export interface SetTrack {
  position:        number
  phase:           string
  artist:          string | null
  title:           string | null
  bpm:             number | null
  key_camelot:     string | null
  genre:           string | null
  duration_sec:    number | null
  transition_note: string | null
  filepath:        string
}

export interface PlaylistDetail {
  playlist: PlaylistSummary
  tracks:   SetTrack[]
}

export const VIBE_LABELS: Record<Vibe, string> = {
  warm:    'Warm — extended warmup, light peak',
  peak:    'Peak — strong peak focus, high BPM',
  deep:    'Deep — melodic/organic, relaxed pacing',
  driving: 'Driving — sustained mid-to-peak energy',
}

export const STRATEGY_LABELS: Record<Strategy, string> = {
  safest:        'Safest — widest compatible range',
  energy_lift:   'Energy Lift — progressive build',
  smooth_blend:  'Smooth Blend — gradual transitions',
  best_warmup:   'Best Warmup — optimised for early sets',
  best_late_set: 'Best Late Set — optimised for peak hour',
}

export const STRUCTURE_LABELS: Record<Structure, string> = {
  full:      'Full — warmup → build → peak → release → outro',
  simple:    'Simple — build → peak → outro',
  peak_only: 'Peak Only — peak section only',
}

export const PHASE_COLORS: Record<string, string> = {
  warmup:  '#64748b',
  build:   '#0ea5e9',
  peak:    '#f59e0b',
  release: '#8b5cf6',
  outro:   '#6b7280',
}
