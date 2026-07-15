import { X } from 'lucide-react'
import type { LibraryParams } from './libraryParams'
import { ISSUE_FILTERS, CONFIDENCE_FILTERS, STATUS_FILTERS, CAMELOT_KEYS, DEFAULT_PARAMS, activeFilterCount } from './libraryParams'
import { ISSUE_LABELS } from '../../types/track'

interface Props {
  params: LibraryParams
  genres: string[]
  onChange: (patch: Partial<LibraryParams>) => void
}

function Select({
  id,
  label,
  value,
  options,
  onChange,
}: {
  id: string
  label: string
  value: string
  options: { value: string; label: string }[]
  onChange: (v: string) => void
}) {
  return (
    <label className="lib-filter" htmlFor={id}>
      <span className="lib-filter-label">{label}</span>
      <select
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        data-testid={`filter-${id}`}
        className={value ? 'lib-filter-select lib-filter-select--active' : 'lib-filter-select'}
      >
        <option value="">Any</option>
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    </label>
  )
}

export default function FilterBar({ params, genres, onChange }: Props) {
  const count = activeFilterCount(params)
  return (
    <div className="lib-filterbar" role="group" aria-label="Track filters" data-testid="filter-bar">
      <Select
        id="issue"
        label="Issue"
        value={params.issue}
        options={ISSUE_FILTERS.map((i) => ({ value: i, label: ISSUE_LABELS[i] ?? i }))}
        onChange={(v) => onChange({ issue: v as LibraryParams['issue'], page: 1 })}
      />
      <Select
        id="status"
        label="Status"
        value={params.status}
        options={STATUS_FILTERS.map((s) => ({ value: s, label: s.replace('_', ' ') }))}
        onChange={(v) => onChange({ status: v as LibraryParams['status'], page: 1 })}
      />
      <Select
        id="confidence"
        label="Parse"
        value={params.confidence}
        options={CONFIDENCE_FILTERS.map((c) => ({ value: c, label: c }))}
        onChange={(v) => onChange({ confidence: v as LibraryParams['confidence'], page: 1 })}
      />
      <Select
        id="genre"
        label="Genre"
        value={params.genre}
        options={genres.map((g) => ({ value: g, label: g }))}
        onChange={(v) => onChange({ genre: v, page: 1 })}
      />
      <Select
        id="key"
        label="Key"
        value={params.key}
        options={CAMELOT_KEYS.map((k) => ({ value: k, label: k }))}
        onChange={(v) => onChange({ key: v, page: 1 })}
      />
      <Select
        id="missing"
        label="Missing"
        value={params.missing}
        options={[
          { value: 'bpm', label: 'BPM' },
          { value: 'key', label: 'Key' },
        ]}
        onChange={(v) => onChange({ missing: v as LibraryParams['missing'], page: 1 })}
      />
      <div className="lib-filter lib-filter--bpm">
        <span className="lib-filter-label">BPM</span>
        <input
          type="number"
          inputMode="decimal"
          min={0}
          max={999}
          placeholder="min"
          aria-label="Minimum BPM"
          value={params.bpmMin ?? ''}
          onChange={(e) =>
            onChange({ bpmMin: e.target.value === '' ? null : Number(e.target.value), page: 1 })
          }
          data-testid="filter-bpm-min"
        />
        <span aria-hidden="true">–</span>
        <input
          type="number"
          inputMode="decimal"
          min={0}
          max={999}
          placeholder="max"
          aria-label="Maximum BPM"
          value={params.bpmMax ?? ''}
          onChange={(e) =>
            onChange({ bpmMax: e.target.value === '' ? null : Number(e.target.value), page: 1 })
          }
          data-testid="filter-bpm-max"
        />
      </div>
      {count > 0 && (
        <button
          type="button"
          className="lib-clear-filters"
          onClick={() =>
            onChange({
              issue: DEFAULT_PARAMS.issue,
              status: DEFAULT_PARAMS.status,
              confidence: DEFAULT_PARAMS.confidence,
              genre: DEFAULT_PARAMS.genre,
              key: DEFAULT_PARAMS.key,
              bpmMin: null,
              bpmMax: null,
              missing: DEFAULT_PARAMS.missing,
              page: 1,
              view: '',
            })
          }
          data-testid="clear-filters-button"
        >
          <X size={13} aria-hidden="true" /> Clear {count} filter{count > 1 ? 's' : ''}
        </button>
      )}
    </div>
  )
}
