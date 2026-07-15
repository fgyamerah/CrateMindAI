import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import type { LibraryParams, SortKey } from './libraryParams'
import { PAGE_SIZE, parseLibraryParams, serializeLibraryParams } from './libraryParams'
import type { SavedViewDefinition } from './savedViews'
import { allViews, paramsForView } from './savedViews'
import { loadVisibleColumns, persistVisibleColumns } from './columns'
import { useGenreOptions, useLibraryTracks } from './useLibraryTracks'
import LibraryToolbar from './LibraryToolbar'
import FilterBar from './FilterBar'
import TrackTable from './TrackTable'
import TrackCardList from './TrackCardList'
import TrackInspector from './TrackInspector'
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StatePanel'
import { formatNumber } from '../../lib/format'

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches)
  useEffect(() => {
    const mq = window.matchMedia(query)
    const onChange = () => setMatches(mq.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [query])
  return matches
}

export default function LibraryWorkspace() {
  const [searchParams, setSearchParams] = useSearchParams()
  const params = useMemo(() => parseLibraryParams(searchParams), [searchParams])

  const [searchDraft, setSearchDraft] = useState(params.q)
  const [selection, setSelection] = useState<Set<number>>(new Set())
  const [activeIndex, setActiveIndex] = useState(-1)
  const [visibleColumns, setVisibleColumns] = useState(loadVisibleColumns)
  const [viewsVersion, setViewsVersion] = useState(0)
  const views = useMemo(() => allViews(), [viewsVersion])
  const searchRef = useRef<HTMLInputElement>(null)

  const isPhone = useMediaQuery('(max-width: 767px)')
  const inspectorAsDrawer = useMediaQuery('(max-width: 1279px)')

  const tracksQuery = useLibraryTracks(params)
  const genres = useGenreOptions()

  const update = useCallback(
    (patch: Partial<LibraryParams>) => {
      const next = { ...params, ...patch }
      if (!('view' in patch)) next.view = ''
      setSearchParams(serializeLibraryParams(next), { replace: true })
    },
    [params, setSearchParams],
  )

  // debounced search → URL
  useEffect(() => {
    if (searchDraft === params.q) return
    const t = window.setTimeout(() => update({ q: searchDraft, page: 1 }), 300)
    return () => window.clearTimeout(t)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchDraft])

  // keep draft in sync when the URL changes externally (view selection, back nav)
  useEffect(() => {
    setSearchDraft(params.q)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [params.q])

  // reset selection when the result set context changes
  const contextKey = `${params.q}|${params.issue}|${params.status}|${params.confidence}|${params.genre}|${params.key}|${params.bpmMin}|${params.bpmMax}|${params.missing}|${params.page}|${params.sort}|${params.order}`
  useEffect(() => {
    setSelection(new Set())
    setActiveIndex(-1)
  }, [contextKey])

  // '/' focuses search; Escape closes inspector
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement
      const typing = ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
      if (e.key === '/' && !typing) {
        e.preventDefault()
        searchRef.current?.focus()
      } else if (e.key === 'Escape' && params.track !== null && !typing) {
        update({ track: null })
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [params.track, update])

  const handleSort = (key: SortKey) => {
    if (params.sort === key) {
      update({ order: params.order === 'asc' ? 'desc' : 'asc', page: 1 })
    } else {
      update({ sort: key, order: 'asc', page: 1 })
    }
  }

  const handleSelectView = (view: SavedViewDefinition) => {
    if (view.columns && view.columns.length > 0) {
      setVisibleColumns(view.columns)
      persistVisibleColumns(view.columns)
    }
    setSearchParams(serializeLibraryParams(paramsForView(view)), { replace: true })
  }

  const handleColumnsChange = (cols: string[]) => {
    setVisibleColumns(cols)
    persistVisibleColumns(cols)
  }

  const toggleSelect = (id: number, checked: boolean) => {
    setSelection((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  const data = tracksQuery.data
  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1

  return (
    <div className={`lib-workspace${params.track !== null && !inspectorAsDrawer ? ' lib-workspace--inspector' : ''}`} data-testid="library-workspace">
      <div className="lib-main">
        <header className="lib-header-row">
          <h1>Library</h1>
          <span className="lib-count" data-testid="library-count" aria-live="polite">
            {data
              ? `${formatNumber(data.total)} tracks${tracksQuery.isFetching ? ' · refreshing…' : ''}`
              : tracksQuery.isError
                ? 'count unavailable'
                : 'loading…'}
          </span>
        </header>

        <LibraryToolbar
          ref={searchRef}
          searchDraft={searchDraft}
          onSearchDraft={setSearchDraft}
          views={views}
          currentViewId={params.view}
          params={params}
          visibleColumns={visibleColumns}
          onColumnsChange={handleColumnsChange}
          onSelectView={handleSelectView}
          onViewsChanged={() => setViewsVersion((v) => v + 1)}
          selectionCount={selection.size}
          onClearSelection={() => setSelection(new Set())}
        />

        <FilterBar params={params} genres={genres.data ?? []} onChange={update} />

        {tracksQuery.isPending && <LoadingState label="Loading tracks…" testId="library-loading" />}

        {tracksQuery.isError && (
          <ErrorState
            title="Track list unavailable"
            detail="The library could not be loaded from the backend. The database may be missing or the runtime may be degraded — check system readiness on Home."
            onRetry={() => tracksQuery.refetch()}
            testId="library-error"
          />
        )}

        {data && data.items.length === 0 && (
          <EmptyState
            title="No tracks match"
            detail={
              data.total === 0 && !params.q && !params.issue && !params.genre
                ? 'The library database contains no tracks yet. Run a scan job to populate it.'
                : 'No tracks match the current search and filters. Adjust or clear filters to see more.'
            }
            testId="library-empty"
          />
        )}

        {data && data.items.length > 0 && (
          <>
            {isPhone ? (
              <TrackCardList
                tracks={data.items}
                onOpen={(id) => update({ track: id })}
                selection={selection}
                onToggleSelect={toggleSelect}
              />
            ) : (
              <TrackTable
                tracks={data.items}
                visible={visibleColumns}
                params={params}
                onSort={handleSort}
                selection={selection}
                onToggleSelect={toggleSelect}
                onToggleSelectAll={(checked) =>
                  setSelection(checked ? new Set(data.items.map((t) => t.id)) : new Set())
                }
                activeIndex={activeIndex}
                onActiveIndexChange={setActiveIndex}
                onOpen={(id) => update({ track: id })}
              />
            )}

            <footer className="lib-pagination" data-testid="library-pagination">
              <button
                type="button"
                className="cm-btn cm-btn--ghost"
                disabled={params.page <= 1}
                onClick={() => update({ page: params.page - 1 })}
                data-testid="pagination-prev"
              >
                <ChevronLeft size={14} aria-hidden="true" /> Prev
              </button>
              <span className="lib-page-info">
                Page {params.page} of {totalPages}
                <span className="lib-page-range">
                  {' '}
                  · rows {(params.page - 1) * PAGE_SIZE + 1}–
                  {Math.min(params.page * PAGE_SIZE, data.total)}
                </span>
              </span>
              <button
                type="button"
                className="cm-btn cm-btn--ghost"
                disabled={params.page >= totalPages}
                onClick={() => update({ page: params.page + 1 })}
                data-testid="pagination-next"
              >
                Next <ChevronRight size={14} aria-hidden="true" />
              </button>
              <span className="lib-kbd-hint" aria-hidden="true">
                / search · ↑↓ navigate · Enter open · Space select · Esc close
              </span>
            </footer>
          </>
        )}
      </div>

      {params.track !== null && (
        <TrackInspector
          trackId={params.track}
          onClose={() => update({ track: null })}
          asDrawer={inspectorAsDrawer}
        />
      )}
    </div>
  )
}
