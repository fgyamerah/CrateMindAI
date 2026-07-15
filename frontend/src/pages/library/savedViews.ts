/**
 * Saved library views.
 *
 * Only view *definitions* (filters/sort/columns) are stored in localStorage —
 * never track data or other server entities.
 */
import type { LibraryParams } from './libraryParams'
import { DEFAULT_PARAMS } from './libraryParams'

export interface SavedViewDefinition {
  id: string
  name: string
  builtIn: boolean
  params: Partial<Omit<LibraryParams, 'track' | 'page' | 'view'>>
  columns?: string[]
}

const STORAGE_KEY = 'cratemindai.library.savedViews.v1'

export const BUILT_IN_VIEWS: SavedViewDefinition[] = [
  { id: 'all', name: 'All tracks', builtIn: true, params: {} },
  { id: 'needs-attention', name: 'Needs attention', builtIn: true, params: { status: 'needs_review' } },
  { id: 'missing-artist', name: 'Missing artist', builtIn: true, params: { issue: 'missing_artist' } },
  { id: 'missing-title', name: 'Missing title', builtIn: true, params: { issue: 'missing_title' } },
  { id: 'missing-bpm', name: 'Missing BPM', builtIn: true, params: { missing: 'bpm' } },
  { id: 'missing-key', name: 'Missing key', builtIn: true, params: { missing: 'key' } },
  { id: 'weak-parse', name: 'Weak filename parse', builtIn: true, params: { issue: 'weak_filename_parse' } },
]

type Storage = Pick<globalThis.Storage, 'getItem' | 'setItem'>

export function loadUserViews(storage: Storage = localStorage): SavedViewDefinition[] {
  try {
    const raw = storage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(
        (v): v is SavedViewDefinition =>
          typeof v === 'object' && v !== null &&
          typeof (v as SavedViewDefinition).id === 'string' &&
          typeof (v as SavedViewDefinition).name === 'string' &&
          typeof (v as SavedViewDefinition).params === 'object',
      )
      .map((v) => ({ ...v, builtIn: false }))
  } catch {
    return []
  }
}

export function persistUserViews(views: SavedViewDefinition[], storage: Storage = localStorage): void {
  storage.setItem(STORAGE_KEY, JSON.stringify(views.filter((v) => !v.builtIn)))
}

export function allViews(storage: Storage = localStorage): SavedViewDefinition[] {
  return [...BUILT_IN_VIEWS, ...loadUserViews(storage)]
}

export function saveView(
  name: string,
  params: LibraryParams,
  columns: string[],
  storage: Storage = localStorage,
): SavedViewDefinition {
  const cleanName = name.trim().slice(0, 60)
  const view: SavedViewDefinition = {
    id: `user-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    name: cleanName || 'Untitled view',
    builtIn: false,
    params: {
      q: params.q,
      sort: params.sort,
      order: params.order,
      issue: params.issue,
      confidence: params.confidence,
      status: params.status,
      genre: params.genre,
      key: params.key,
      bpmMin: params.bpmMin,
      bpmMax: params.bpmMax,
      missing: params.missing,
    },
    columns,
  }
  const views = loadUserViews(storage)
  views.push(view)
  persistUserViews(views, storage)
  return view
}

export function renameView(id: string, name: string, storage: Storage = localStorage): boolean {
  const views = loadUserViews(storage)
  const view = views.find((v) => v.id === id)
  if (!view) return false
  view.name = name.trim().slice(0, 60) || view.name
  persistUserViews(views, storage)
  return true
}

export function deleteView(id: string, storage: Storage = localStorage): boolean {
  const views = loadUserViews(storage)
  const next = views.filter((v) => v.id !== id)
  if (next.length === views.length) return false
  persistUserViews(next, storage)
  return true
}

/** Apply a view definition on top of default params (page/track reset). */
export function paramsForView(view: SavedViewDefinition): LibraryParams {
  return { ...DEFAULT_PARAMS, ...view.params, page: 1, track: null, view: view.id }
}
