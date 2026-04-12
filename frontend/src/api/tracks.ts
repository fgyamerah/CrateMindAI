import { apiFetch } from './client'
import type {
  TrackDetail,
  TrackIssueItem,
  TrackListParams,
  TrackStats,
  TrackSummary,
} from '../types/track'

function buildQS(params: TrackListParams): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    }
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

export function fetchTracks(params: TrackListParams = {}): Promise<TrackSummary[]> {
  return apiFetch.get<TrackSummary[]>(`/tracks${buildQS(params)}`)
}

export function fetchTrack(id: number): Promise<TrackDetail> {
  return apiFetch.get<TrackDetail>(`/tracks/${id}`)
}

export function fetchTrackStats(): Promise<TrackStats> {
  return apiFetch.get<TrackStats>('/tracks/stats')
}

export function fetchTrackIssues(limit = 200): Promise<TrackIssueItem[]> {
  return apiFetch.get<TrackIssueItem[]>(`/tracks/issues?limit=${limit}`)
}
