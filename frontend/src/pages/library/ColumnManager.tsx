import { useEffect, useRef, useState } from 'react'
import { Columns3 } from 'lucide-react'
import { COLUMNS } from './columns'

interface Props {
  visible: string[]
  onChange: (visible: string[]) => void
}

export default function ColumnManager({ visible, onChange }: Props) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="lib-colmgr" ref={ref}>
      <button
        type="button"
        className="cm-btn cm-btn--ghost"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => setOpen((o) => !o)}
        data-testid="column-manager-button"
      >
        <Columns3 size={14} aria-hidden="true" /> Columns
      </button>
      {open && (
        <div className="lib-colmgr-menu" role="group" aria-label="Column visibility" data-testid="column-manager-menu">
          {COLUMNS.map((col) => (
            <label key={col.id} className="lib-colmgr-item">
              <input
                type="checkbox"
                checked={visible.includes(col.id)}
                disabled={!col.hideable}
                onChange={(e) =>
                  onChange(
                    e.target.checked
                      ? [...visible, col.id]
                      : visible.filter((c) => c !== col.id),
                  )
                }
                data-testid={`column-toggle-${col.id}`}
              />
              {col.label}
              {!col.hideable && <span className="lib-colmgr-req">required</span>}
            </label>
          ))}
        </div>
      )}
    </div>
  )
}
