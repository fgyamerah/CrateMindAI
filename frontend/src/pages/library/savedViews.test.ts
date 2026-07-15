import { beforeEach, describe, expect, it } from 'vitest'
import {
  BUILT_IN_VIEWS,
  allViews,
  deleteView,
  loadUserViews,
  paramsForView,
  renameView,
  saveView,
} from './savedViews'
import { DEFAULT_PARAMS } from './libraryParams'

function memoryStorage() {
  const data = new Map<string, string>()
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
  }
}

let storage: ReturnType<typeof memoryStorage>
beforeEach(() => {
  storage = memoryStorage()
})

describe('saved views', () => {
  it('exposes the required built-in views', () => {
    const names = BUILT_IN_VIEWS.map((v) => v.name)
    expect(names).toContain('All tracks')
    expect(names).toContain('Needs attention')
    expect(names).toContain('Missing BPM')
    expect(names).toContain('Missing key')
    expect(names).toContain('Weak filename parse')
    expect(BUILT_IN_VIEWS.every((v) => v.builtIn)).toBe(true)
  })

  it('saves, renames, and deletes user views', () => {
    const view = saveView('My peak-time picks', { ...DEFAULT_PARAMS, genre: 'Techno' }, ['artist', 'bpm'], storage)
    expect(loadUserViews(storage)).toHaveLength(1)
    expect(view.builtIn).toBe(false)
    expect(view.params.genre).toBe('Techno')

    expect(renameView(view.id, 'Peak time', storage)).toBe(true)
    expect(loadUserViews(storage)[0].name).toBe('Peak time')

    expect(deleteView(view.id, storage)).toBe(true)
    expect(loadUserViews(storage)).toHaveLength(0)
    expect(deleteView('missing-id', storage)).toBe(false)
  })

  it('never stores track data, only definitions', () => {
    saveView('X', { ...DEFAULT_PARAMS, q: 'abc' }, [], storage)
    const raw = storage.getItem('cratemindai.library.savedViews.v1')!
    expect(raw).not.toContain('filepath')
    expect(raw).not.toContain('items')
  })

  it('survives corrupted storage', () => {
    storage.setItem('cratemindai.library.savedViews.v1', '{not json')
    expect(loadUserViews(storage)).toEqual([])
    expect(allViews(storage)).toHaveLength(BUILT_IN_VIEWS.length)
  })

  it('paramsForView resets page and selection', () => {
    const p = paramsForView({ id: 'all', name: 'All tracks', builtIn: true, params: { genre: 'House' } })
    expect(p.page).toBe(1)
    expect(p.track).toBeNull()
    expect(p.genre).toBe('House')
  })
})
