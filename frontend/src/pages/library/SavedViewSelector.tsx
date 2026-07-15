import { useState } from 'react'
import { Bookmark, Trash2, Pencil } from 'lucide-react'
import type { SavedViewDefinition } from './savedViews'
import { deleteView, renameView, saveView } from './savedViews'
import type { LibraryParams } from './libraryParams'

interface Props {
  views: SavedViewDefinition[]
  currentViewId: string
  params: LibraryParams
  visibleColumns: string[]
  onSelectView: (view: SavedViewDefinition) => void
  onViewsChanged: () => void
}

export default function SavedViewSelector({
  views,
  currentViewId,
  params,
  visibleColumns,
  onSelectView,
  onViewsChanged,
}: Props) {
  const [saving, setSaving] = useState(false)
  const [name, setName] = useState('')
  const current = views.find((v) => v.id === currentViewId)

  const handleSave = () => {
    if (!name.trim()) return
    const view = saveView(name, params, visibleColumns)
    setSaving(false)
    setName('')
    onViewsChanged()
    onSelectView(view)
  }

  return (
    <div className="lib-views">
      <label className="lib-filter" htmlFor="saved-view-select">
        <span className="lib-filter-label">View</span>
        <select
          id="saved-view-select"
          className="lib-filter-select"
          value={currentViewId}
          onChange={(e) => {
            const view = views.find((v) => v.id === e.target.value)
            if (view) onSelectView(view)
          }}
          data-testid="saved-view-select"
        >
          <option value="" disabled>
            Custom…
          </option>
          <optgroup label="Built-in">
            {views.filter((v) => v.builtIn).map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </optgroup>
          {views.some((v) => !v.builtIn) && (
            <optgroup label="My views">
              {views.filter((v) => !v.builtIn).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </label>

      {saving ? (
        <span className="lib-view-save">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') handleSave()
              if (e.key === 'Escape') setSaving(false)
            }}
            placeholder="View name"
            aria-label="New view name"
            autoFocus
            data-testid="save-view-name-input"
          />
          <button type="button" className="cm-btn" onClick={handleSave} data-testid="save-view-confirm">
            Save
          </button>
        </span>
      ) : (
        <button
          type="button"
          className="cm-icon-btn"
          onClick={() => setSaving(true)}
          aria-label="Save current filters as a view"
          title="Save current filters as a view"
          data-testid="save-view-button"
        >
          <Bookmark size={15} />
        </button>
      )}

      {current && !current.builtIn && (
        <>
          <button
            type="button"
            className="cm-icon-btn"
            aria-label={`Rename view ${current.name}`}
            title="Rename view"
            onClick={() => {
              const next = window.prompt('Rename view', current.name)
              if (next) {
                renameView(current.id, next)
                onViewsChanged()
              }
            }}
            data-testid="rename-view-button"
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            className="cm-icon-btn"
            aria-label={`Delete view ${current.name}`}
            title="Delete view"
            onClick={() => {
              if (window.confirm(`Delete saved view "${current.name}"? This only removes the saved filter set.`)) {
                deleteView(current.id)
                onViewsChanged()
                onSelectView(views.find((v) => v.id === 'all') as SavedViewDefinition)
              }
            }}
            data-testid="delete-view-button"
          >
            <Trash2 size={14} />
          </button>
        </>
      )}
    </div>
  )
}
