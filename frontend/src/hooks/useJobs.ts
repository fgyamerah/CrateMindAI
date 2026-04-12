import { useState, useEffect, useCallback, useRef } from 'react'
import { fetchJobs } from '../api/jobs'
import type { Job } from '../types/job'

/**
 * Fetches and polls the jobs list.
 *
 * Polling strategy:
 *   - FAST_POLL (2 s) while any job is pending or running
 *   - SLOW_POLL (10 s) once all jobs are settled
 *
 * The `refresh` function triggers an immediate re-fetch (e.g. after submitting
 * a new job).
 */

const FAST_POLL_MS = 2_000
const SLOW_POLL_MS = 10_000

function hasActiveJobs(jobs: Job[]): boolean {
  return jobs.some((j) => j.status === 'pending' || j.status === 'running')
}

export interface UseJobsResult {
  jobs: Job[]
  loading: boolean
  error: string | null
  refresh: () => void
}

export function useJobs(): UseJobsResult {
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Use a ref so the interval callback always reads the latest jobs without
  // being re-created on every render.
  const jobsRef = useRef<Job[]>(jobs)
  jobsRef.current = jobs

  const load = useCallback(async () => {
    try {
      const data = await fetchJobs()
      setJobs(data)
      setError(null)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load jobs')
    } finally {
      setLoading((prev) => (prev ? false : prev))
    }
  }, [])

  useEffect(() => {
    // Fetch immediately on mount
    load()

    // Set up adaptive polling
    let timerId: ReturnType<typeof setTimeout>

    function schedule() {
      const delay = hasActiveJobs(jobsRef.current) ? FAST_POLL_MS : SLOW_POLL_MS
      timerId = setTimeout(async () => {
        await load()
        schedule() // reschedule after each fetch completes
      }, delay)
    }

    schedule()
    return () => clearTimeout(timerId)
  }, [load])

  return { jobs, loading, error, refresh: load }
}
