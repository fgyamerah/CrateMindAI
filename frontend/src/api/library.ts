import { apiFetch } from './client'

export interface LibraryNode {
  label:      string
  path:       string
  executable: boolean
  children:   LibraryNode[]
}

export interface LibraryTreeResponse {
  root: LibraryNode
}

export interface LibraryStats {
  global_count: number
  folder_count: number
}

export function fetchLibraryTree(depth = 3): Promise<LibraryTreeResponse> {
  return apiFetch.get<LibraryTreeResponse>(`/library/tree?depth=${depth}`)
}

export function fetchLibraryStats(path?: string | null): Promise<LibraryStats> {
  const qs = path ? `?path=${encodeURIComponent(path)}` : ''
  return apiFetch.get<LibraryStats>(`/library/stats${qs}`)
}
