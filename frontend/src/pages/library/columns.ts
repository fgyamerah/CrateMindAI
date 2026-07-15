import type { SortKey } from './libraryParams'

export interface ColumnDef {
  id: string
  label: string
  sortKey?: SortKey
  width: string
  align?: 'right'
  hideable: boolean
}

export const COLUMNS: ColumnDef[] = [
  { id: 'artist', label: 'Artist', sortKey: 'artist', width: 'minmax(140px, 1.2fr)', hideable: false },
  { id: 'title', label: 'Title', sortKey: 'title', width: 'minmax(180px, 1.6fr)', hideable: false },
  { id: 'genre', label: 'Genre', sortKey: 'genre', width: 'minmax(100px, 0.8fr)', hideable: true },
  { id: 'bpm', label: 'BPM', sortKey: 'bpm', width: '72px', align: 'right', hideable: true },
  { id: 'key', label: 'Key', sortKey: 'key', width: '64px', hideable: true },
  { id: 'duration', label: 'Length', sortKey: 'duration', width: '76px', align: 'right', hideable: true },
  { id: 'bitrate', label: 'Bitrate', sortKey: 'bitrate', width: '80px', align: 'right', hideable: true },
  { id: 'issues', label: 'Issues', width: 'minmax(120px, 1fr)', hideable: true },
  { id: 'confidence', label: 'Parse', width: '80px', hideable: true },
  { id: 'status', label: 'Status', sortKey: 'status', width: '110px', hideable: true },
  { id: 'folder', label: 'Folder', width: 'minmax(140px, 1.2fr)', hideable: true },
]

export const DEFAULT_VISIBLE = [
  'artist', 'title', 'genre', 'bpm', 'key', 'duration', 'issues', 'status',
]

const STORAGE_KEY = 'cratemindai.library.columns.v1'

export function loadVisibleColumns(): string[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_VISIBLE
    const parsed = JSON.parse(raw)
    if (!Array.isArray(parsed)) return DEFAULT_VISIBLE
    const valid = parsed.filter((c) => COLUMNS.some((col) => col.id === c))
    const required = COLUMNS.filter((c) => !c.hideable).map((c) => c.id)
    return Array.from(new Set([...required, ...valid]))
  } catch {
    return DEFAULT_VISIBLE
  }
}

export function persistVisibleColumns(cols: string[]): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cols))
}

export function gridTemplate(visible: string[]): string {
  const parts = ['36px'] // selection checkbox
  for (const col of COLUMNS) {
    if (visible.includes(col.id)) parts.push(col.width)
  }
  return parts.join(' ')
}
