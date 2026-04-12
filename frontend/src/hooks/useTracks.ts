import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchTracks, fetchTrackStats } from '../api/tracks'
import type { TrackListParams, TrackStats, TrackSummary } from '../types/track'

/**
 * Fetches tracks with optional filter params and polls slowly.
 *
 * Track data changes only when a pipeline job completes — we don't need
 * the aggressive 2 s job poll.  30 s is plenty; the user can always hit
 * Refresh manually.
 */
const POLL_MS = 30_000

export interface UseTracksResult {
  tracks: TrackSummary[]
  stats: TrackStats | null
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useTracks(params: TrackListParams): UseTracksResult {
  const [tracks, setTracks]   = useState<TrackSummary[]>([])
  const [stats, setStats]     = useState<TrackStats | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError]     = useState<string | null>(null)

  // Keep a stable ref to the latest params so the interval callback sees
  // the current value without being recreated on every keystroke.
  const paramsRef = useRef(params)
  paramsRef.current = params

  const load = useCallback(async () => {
    try {
      const [data, statsData] = await Promise.all([
        fetchTracks(paramsRef.current),
        fetchTrackStats(),
      ])
      setTracks(data)
      setStats(statsData)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load tracks')
    } finally {
      setLoading((prev) => (prev ? false : prev))
    }
  }, []) // intentionally empty — params are read via ref inside load

  // Re-fetch whenever params change (search/filter)
  useEffect(() => {
    setLoading(true)
    load()
  }, [
    load,
    params.q,
    params.status,
    params.artist,
    params.genre,
    params.key,
    params.quality_tier,
    params.bpm_min,
    params.bpm_max,
    params.sort,
    params.order,
    params.limit,
    params.offset,
  ])

  // Background poll — slow cadence since track data is stable between runs
  useEffect(() => {
    const id = setInterval(load, POLL_MS)
    return () => clearInterval(id)
  }, [load])

  return { tracks, stats, loading, error, refresh: load }
}
