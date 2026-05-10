import { apiFetch } from './client'
import type { LibraryQualityResponse } from '../types/libraryQuality'

export function fetchLibraryQuality(): Promise<LibraryQualityResponse> {
  return apiFetch.get<LibraryQualityResponse>('/library/quality')
}
