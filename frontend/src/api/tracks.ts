import { apiFetch } from './client'
import type {
  TrackDetail,
  TrackIssueCounts,
  TrackListParams,
  TrackPage,
  TrackStats,
  TrackSummary,
} from '../types/track'

function buildQS(params: TrackListParams): string {
  const parts: string[] = []
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      const key = k === 'q' ? 'search' : k
      parts.push(`${encodeURIComponent(key)}=${encodeURIComponent(String(v))}`)
    }
  }
  return parts.length ? `?${parts.join('&')}` : ''
}

export function fetchTrackPage(params: TrackListParams = {}): Promise<TrackPage> {
  return apiFetch.get<TrackPage>(`/tracks${buildQS(params)}`)
}

export async function fetchTracks(params: TrackListParams = {}): Promise<TrackSummary[]> {
  const page = await fetchTrackPage(params)
  return page.items
}

export function fetchTrack(id: number): Promise<TrackDetail> {
  return apiFetch.get<TrackDetail>(`/tracks/${id}`)
}

export function fetchTrackStats(): Promise<TrackStats> {
  return apiFetch.get<TrackStats>('/tracks/stats')
}

export function fetchTrackIssues(): Promise<TrackIssueCounts> {
  return apiFetch.get<TrackIssueCounts>('/tracks/issues')
}
