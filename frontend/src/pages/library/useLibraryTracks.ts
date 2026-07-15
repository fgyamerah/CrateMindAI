import { keepPreviousData, useQuery } from '@tanstack/react-query'
import { apiFetch } from '../../api/client'
import { fetchTrack } from '../../api/tracks'
import type { TrackPage } from '../../types/track'
import type { LibraryParams } from './libraryParams'
import { toApiQuery } from './libraryParams'

/**
 * Server-state hook for the track list. The query key is derived from the
 * sanitized URL params; TanStack Query aborts obsolete requests via the
 * provided AbortSignal during rapid search/filter changes.
 */
export function useLibraryTracks(params: LibraryParams) {
  const apiQuery = toApiQuery(params)
  return useQuery({
    queryKey: ['library-tracks', apiQuery],
    queryFn: ({ signal }) => apiFetch.get<TrackPage>(`/tracks?${apiQuery}`, { signal }),
    placeholderData: keepPreviousData,
    staleTime: 10_000,
  })
}

export function useTrackDetail(id: number | null) {
  return useQuery({
    queryKey: ['track-detail', id],
    queryFn: () => fetchTrack(id as number),
    enabled: id !== null,
  })
}

interface GenreOverview {
  genre_top_counts: Record<string, number>
}

export function useGenreOptions() {
  return useQuery({
    queryKey: ['library-genres'],
    queryFn: () => apiFetch.get<GenreOverview>('/library/overview'),
    staleTime: 5 * 60_000,
    select: (d) => Object.keys(d.genre_top_counts ?? {}).sort(),
  })
}
