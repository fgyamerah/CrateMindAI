import { useRef } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ArrowUp, ArrowDown } from 'lucide-react'
import type { TrackSummary } from '../../types/track'
import type { LibraryParams, SortKey } from './libraryParams'
import { COLUMNS, gridTemplate } from './columns'
import TrackRow from './TrackRow'

const ROW_HEIGHT = 42

interface Props {
  tracks: TrackSummary[]
  visible: string[]
  params: LibraryParams
  onSort: (key: SortKey) => void
  selection: Set<number>
  onToggleSelect: (id: number, checked: boolean) => void
  onToggleSelectAll: (checked: boolean) => void
  activeIndex: number
  onActiveIndexChange: (index: number) => void
  onOpen: (id: number) => void
}

export default function TrackTable({
  tracks,
  visible,
  params,
  onSort,
  selection,
  onToggleSelect,
  onToggleSelectAll,
  activeIndex,
  onActiveIndexChange,
  onOpen,
}: Props) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: tracks.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_HEIGHT,
    overscan: 12,
  })

  const template = gridTemplate(visible)
  const allSelected = tracks.length > 0 && tracks.every((t) => selection.has(t.id))

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next =
        e.key === 'ArrowDown'
          ? Math.min(tracks.length - 1, activeIndex + 1)
          : Math.max(0, activeIndex - 1)
      onActiveIndexChange(next)
      virtualizer.scrollToIndex(next)
    } else if (e.key === 'Enter' && activeIndex >= 0 && tracks[activeIndex]) {
      e.preventDefault()
      onOpen(tracks[activeIndex].id)
    } else if (e.key === ' ' && activeIndex >= 0 && tracks[activeIndex]) {
      e.preventDefault()
      const id = tracks[activeIndex].id
      onToggleSelect(id, !selection.has(id))
    }
  }

  return (
    <div
      className="lib-table"
      role="grid"
      aria-label="Track list"
      aria-rowcount={tracks.length}
      data-testid="track-table"
    >
      <div role="row" className="lib-header" style={{ gridTemplateColumns: template }}>
        <div role="columnheader" className="lib-cell lib-cell--check">
          <input
            type="checkbox"
            checked={allSelected}
            onChange={(e) => onToggleSelectAll(e.target.checked)}
            aria-label="Select all visible tracks"
            data-testid="track-select-all"
          />
        </div>
        {COLUMNS.filter((c) => visible.includes(c.id)).map((col) => {
          const isSorted = col.sortKey && params.sort === col.sortKey
          const ariaSort = isSorted ? (params.order === 'asc' ? 'ascending' : 'descending') : undefined
          return (
            <div
              role="columnheader"
              aria-sort={ariaSort}
              key={col.id}
              className={`lib-cell lib-cell--head${col.align === 'right' ? ' lib-cell--right' : ''}`}
            >
              {col.sortKey ? (
                <button
                  type="button"
                  className={`lib-sort-btn${isSorted ? ' lib-sort-btn--active' : ''}`}
                  onClick={() => onSort(col.sortKey as SortKey)}
                  data-testid={`sort-${col.id}`}
                >
                  {col.label}
                  {isSorted &&
                    (params.order === 'asc' ? (
                      <ArrowUp size={12} aria-hidden="true" />
                    ) : (
                      <ArrowDown size={12} aria-hidden="true" />
                    ))}
                </button>
              ) : (
                col.label
              )}
            </div>
          )
        })}
      </div>

      <div
        ref={scrollRef}
        className="lib-scroll"
        tabIndex={0}
        onKeyDown={handleKeyDown}
        aria-label="Track rows — use arrow keys to navigate, Enter to open, Space to select"
        data-testid="track-table-body"
      >
        <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
          {virtualizer.getVirtualItems().map((vi) => {
            const track = tracks[vi.index]
            return (
              <div
                key={track.id}
                style={{
                  position: 'absolute',
                  top: 0,
                  left: 0,
                  width: '100%',
                  height: vi.size,
                  transform: `translateY(${vi.start}px)`,
                }}
              >
                <TrackRow
                  track={track}
                  visible={visible}
                  selected={selection.has(track.id)}
                  active={vi.index === activeIndex}
                  onToggleSelect={onToggleSelect}
                  onOpen={onOpen}
                  gridTemplate={template}
                />
              </div>
            )
          })}
        </div>
      </div>
    </div>
  )
}
