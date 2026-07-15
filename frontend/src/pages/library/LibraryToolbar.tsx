import { forwardRef } from 'react'
import { Search, X } from 'lucide-react'
import type { SavedViewDefinition } from './savedViews'
import type { LibraryParams } from './libraryParams'
import SavedViewSelector from './SavedViewSelector'
import ColumnManager from './ColumnManager'

interface Props {
  searchDraft: string
  onSearchDraft: (v: string) => void
  views: SavedViewDefinition[]
  currentViewId: string
  params: LibraryParams
  visibleColumns: string[]
  onColumnsChange: (cols: string[]) => void
  onSelectView: (view: SavedViewDefinition) => void
  onViewsChanged: () => void
  selectionCount: number
  onClearSelection: () => void
}

const LibraryToolbar = forwardRef<HTMLInputElement, Props>(function LibraryToolbar(
  {
    searchDraft,
    onSearchDraft,
    views,
    currentViewId,
    params,
    visibleColumns,
    onColumnsChange,
    onSelectView,
    onViewsChanged,
    selectionCount,
    onClearSelection,
  },
  searchRef,
) {
  return (
    <div className="lib-toolbar" data-testid="library-toolbar">
      <div className="lib-search">
        <Search size={15} aria-hidden="true" className="lib-search-icon" />
        <input
          ref={searchRef}
          type="search"
          value={searchDraft}
          onChange={(e) => onSearchDraft(e.target.value)}
          placeholder="Search artist, title, filename…  ( / )"
          aria-label="Search tracks"
          data-testid="library-search-input"
        />
        {searchDraft && (
          <button
            type="button"
            className="cm-icon-btn lib-search-clear"
            onClick={() => onSearchDraft('')}
            aria-label="Clear search"
            data-testid="library-search-clear"
          >
            <X size={13} />
          </button>
        )}
      </div>

      <SavedViewSelector
        views={views}
        currentViewId={currentViewId}
        params={params}
        visibleColumns={visibleColumns}
        onSelectView={onSelectView}
        onViewsChanged={onViewsChanged}
      />

      <div className="cm-topbar-spacer" />

      {selectionCount > 0 && (
        <span className="lib-selection-info" data-testid="selection-count" aria-live="polite">
          {selectionCount} selected
          <button
            type="button"
            className="lib-clear-filters"
            onClick={onClearSelection}
            data-testid="clear-selection-button"
          >
            Clear
          </button>
        </span>
      )}

      <ColumnManager visible={visibleColumns} onChange={onColumnsChange} />
    </div>
  )
})

export default LibraryToolbar
