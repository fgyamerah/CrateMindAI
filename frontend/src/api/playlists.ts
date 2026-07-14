import { apiFetch } from './client'
import type {
  PlaylistDetail,
  PlaylistSummary,
  SetBuilderJobResponse,
  SetBuilderParams,
} from '../types/playlist'

export function runSetBuilder(params: SetBuilderParams): Promise<SetBuilderJobResponse> {
  return apiFetch.post<SetBuilderJobResponse>('/playlists/set-builder', params)
}

export function fetchPlaylists(limit = 50, offset = 0): Promise<PlaylistSummary[]> {
  return apiFetch.get<PlaylistSummary[]>(`/playlists?limit=${limit}&offset=${offset}`)
}

export function fetchPlaylist(id: number): Promise<PlaylistDetail> {
  return apiFetch.get<PlaylistDetail>(`/playlists/${id}`)
}
