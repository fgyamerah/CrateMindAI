import { apiFetch } from './client'
import type { RunListItem, RunSummary, RunDetailEntry } from '../types/job'

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

export function fetchRunList(command?: string, limit = 20): Promise<RunListItem[]> {
  const p = new URLSearchParams({ limit: String(limit) })
  if (command) p.set('command', command)
  return apiFetch.get<RunListItem[]>(`/library/runs?${p}`)
}

export function fetchRunSummary(command: string, prefix: string): Promise<RunSummary> {
  return apiFetch.get<RunSummary>(
    `/library/runs/${encodeURIComponent(command)}/${encodeURIComponent(prefix)}/summary`,
  )
}

export function fetchRunDetail(
  command: string,
  prefix:  string,
  group:   string,
  page:    string,
): Promise<RunDetailEntry[]> {
  return apiFetch.get<RunDetailEntry[]>(
    `/library/runs/${encodeURIComponent(command)}/${encodeURIComponent(prefix)}/detail/${group}/${page}`,
  )
}
