import { useEffect, useState } from 'react'
import { Check, Loader2, Wand2, X } from 'lucide-react'
import { ApiError } from '../api/client'
import {
  applyManualMetadata,
  previewManualMetadata,
  type ManualMetadataApplyResponse,
  type ManualMetadataPreviewResponse,
} from '../api/manualMetadata'

interface ManualMetadataTarget {
  track_id: number
  artist: string | null
  title: string | null
  filename?: string | null
  filepath?: string | null
}

interface Props {
  target: ManualMetadataTarget
  onClose: () => void
  onApplied: (result: ManualMetadataApplyResponse) => void | Promise<void>
}

function normalizeSpaces(value: string): string {
  return value.trim().replace(/\s+/g, ' ')
}

function dedupeWords(value: string): string {
  return value.replace(/\b([\w']+)(\s+\1\b)+/gi, '$1')
}

export default function ManualMetadataEditor({ target, onClose, onApplied }: Props) {
  const [artist, setArtist] = useState(target.artist || '')
  const [title, setTitle] = useState(target.title || '')
  const [preview, setPreview] = useState<ManualMetadataPreviewResponse | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setArtist(target.artist || '')
    setTitle(target.title || '')
    setPreview(null)
    setError(null)
  }, [target])

  const payload = {
    track_id: target.track_id,
    artist,
    title,
  }

  async function runPreview() {
    setBusy(true)
    setError(null)
    try {
      setPreview(await previewManualMetadata(payload))
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : 'Preview failed'
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  async function runApply() {
    if (!preview) {
      await runPreview()
      return
    }
    setBusy(true)
    setError(null)
    try {
      const result = await applyManualMetadata(payload)
      await onApplied(result)
      onClose()
    } catch (err) {
      const msg = err instanceof ApiError ? err.displayMessage : err instanceof Error ? err.message : 'Apply failed'
      setError(msg)
    } finally {
      setBusy(false)
    }
  }

  function quickNormalize() {
    setArtist((value) => normalizeSpaces(value))
    setTitle((value) => normalizeSpaces(value))
    setPreview(null)
  }

  function replaceSlashes() {
    setArtist((value) => normalizeSpaces(value.replace(/\s*\/\s*/g, ', ')))
    setTitle((value) => normalizeSpaces(value.replace(/\s*\/\s*/g, ', ')))
    setPreview(null)
  }

  function removeDuplicates() {
    setArtist((value) => normalizeSpaces(dedupeWords(value)))
    setTitle((value) => normalizeSpaces(dedupeWords(value)))
    setPreview(null)
  }

  return (
    <div className="modal-backdrop manual-metadata-backdrop" onClick={onClose}>
      <div
        className="modal manual-metadata-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Manual metadata editor"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="modal-header">
          <div className="modal-title manual-metadata-title">
            <span>Manual Edit</span>
            <code>{target.track_id}</code>
          </div>
          <div className="modal-actions">
            <button className="icon-btn" type="button" onClick={onClose} aria-label="Close">
              <X size={14} />
            </button>
          </div>
        </div>

        <div
          className="modal-body manual-metadata-body"
          onKeyDown={(event) => {
            if (event.key !== 'Enter') return
            if (event.ctrlKey || event.metaKey) {
              event.preventDefault()
              void runApply()
            } else if (!(event.target instanceof HTMLTextAreaElement)) {
              event.preventDefault()
              void runPreview()
            }
          }}
        >
          <section className="manual-metadata-source">
            <strong>{target.filename || 'Selected track'}</strong>
            {target.filepath && <code>{target.filepath}</code>}
          </section>

          <section className="manual-metadata-fields">
            <label>
              <span>Artist</span>
              <input value={artist} autoFocus onChange={(event) => {
                setArtist(event.target.value)
                setPreview(null)
              }} />
            </label>
            <label>
              <span>Title</span>
              <input value={title} onChange={(event) => {
                setTitle(event.target.value)
                setPreview(null)
              }} />
            </label>
          </section>

          <section className="manual-metadata-tools">
            <button className="btn btn--ghost btn--sm" type="button" onClick={quickNormalize}>
              <Wand2 size={13} />
              Normalize spacing
            </button>
            <button className="btn btn--ghost btn--sm" type="button" onClick={replaceSlashes}>
              Slashes to commas
            </button>
            <button className="btn btn--ghost btn--sm" type="button" onClick={removeDuplicates}>
              Remove duplicated words
            </button>
          </section>

          {error && <div className="metadata-repair-error manual-metadata-error">{error}</div>}

          <section className="manual-metadata-diff">
            {preview ? (
              <>
                <div className="manual-metadata-diff-head">
                  <strong>{preview.no_op ? 'No changes' : `${preview.changed_fields.length} changed field(s)`}</strong>
                  <span>{preview.validation_warnings.join(', ') || 'validated'}</span>
                </div>
                {preview.diff.map((item) => (
                  <div key={item.field} className={`manual-metadata-diff-row${item.changed ? ' manual-metadata-diff-row--changed' : ''}`}>
                    <span>{item.field}</span>
                    <code>{item.current || 'Empty'}</code>
                    <span aria-hidden="true">-&gt;</span>
                    <code>{item.proposed || 'Empty'}</code>
                  </div>
                ))}
              </>
            ) : (
              <div className="manual-metadata-placeholder">Run preview to validate the DB-only artist/title edit.</div>
            )}
          </section>
        </div>

        <div className="modal-footer manual-metadata-footer">
          <span>DB only. Tags, filenames, BPM, keys, cues, and processed_state are untouched.</span>
          <div className="modal-actions">
            <button className="btn btn--ghost btn--sm" type="button" onClick={onClose}>Cancel</button>
            <button className="btn btn--ghost btn--sm" type="button" disabled={busy} onClick={() => void runPreview()}>
              {busy ? <Loader2 size={13} className="spin" /> : null}
              Preview
            </button>
            <button className="btn btn--primary btn--sm" type="button" disabled={busy || !preview || preview.no_op} onClick={() => void runApply()}>
              <Check size={13} />
              Apply
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
