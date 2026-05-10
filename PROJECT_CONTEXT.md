# CrateMindAI Project Context

**Updated:** 2026-05-06  
**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Latest Milestone

- README updated for the Phase 1-8 CrateMindAI platform milestone.
- Current stable commit hash: `b4c6ffb4048c4c98d225f6c65e40a7cce7f1a8e3`.
- Next recommended phase: Phase 7 Full Reconciliation.
- Warning: back up `<root>/logs/processed.db` before any reconciliation apply work.

## Phase 7 Planning

- Phase 7 started as planning only.
- Full reconciliation apply spec created at `docs/architecture/FULL_RECONCILIATION_APPLY_SPEC.md`.
- No runtime behavior changed.
- No reconciliation apply behavior has been added.

## Overview

CrateMindAI is a local-first DJ library automation toolkit. It processes audio files into a cleaner, Rekordbox-ready library through deterministic cleanup, local AI-assisted normalization, artist intelligence, online enrichment, label tooling, organization, dedupe, exports, and backend/UI workflows.

Detailed safety docs:

- `docs/audits/SAFETY_GAP_AUDIT.md`
- `docs/audits/COMMAND_RISK_MATRIX.md`
- `docs/audits/FILESYSTEM_DB_CONSISTENCY_AUDIT.md`
- `docs/safety/SAFETY_MODEL.md`
- `docs/safety/ROLLBACK_AND_RECOVERY.md`
- `docs/operations/OPERATOR_SAFETY_PLAYBOOK.md`
- `docs/architecture/METADATA_OWNERSHIP_MATRIX.md`
- `docs/architecture/STABILITY_MATRIX.md`

## Architecture Summary

- `pipeline.py` is the main CLI entry point.
- `modules/` contains core pipeline operations: metadata cleanup, analysis, organizer, dedupe, cue suggestions, playlists, exports, conversion, and audits.
- `ai/` contains local Ollama AI normalization and dataset capture.
- `intelligence/artist/` handles deterministic artist normalization and aliases.
- `intelligence/enrichment/` handles online metadata enrichment and review queues.
- `intelligence/label/` handles label parsing/enrichment/reporting.
- `backend/` is the FastAPI web backend with its own `jobs.db`.
- `frontend/` is the web UI; detailed safety was not inspected in this pass.

## Safety Doctrine

Prefer no change over unsafe change.

- Preview first.
- `--apply` should gate destructive changes, but this is not universal.
- Mixed In Key owns BPM, key, and cue data.
- AI must not write BPM, key, cues, filenames, or folder structure.
- File moves and renames require path reconciliation.
- Quarantine means review later, not delete.

## Verified Safety Facts

- `ai-normalize` default confidence constant is `MIN_AI_CONFIDENCE = 0.80`.
- Enrichment matcher uses `THRESHOLD_APPLY = 0.90` and `THRESHOLD_REVIEW = 0.75`.
- Enrichment artist/version hard blocks cap blocked confidence at `min(top_conf, 0.74)`.
- Enrichment exact ISRC match bypasses gates and returns confidence 0.98.
- Enrichment review queue dedupes by exact `file_path`.
- `config.IGNORED_DIR` is `.BIN/IGNORED`; enrichment preserves paths relative to `.BIN` parent and adds `_dupN` collisions.
- `rename_processed_path()` updates `processed_state` only.

## Major Operational Risks

- Some older write-capable commands still do not require `--yes` or `--force` confirmation.
- Legacy `organizer.py` still moves files and deletes old `tracks` rows through a pre-Phase-3 path mutation pattern.
- `artist-merge`, `artist-folder-clean`, and `library-organize` move files, but their Phase 3 paths now call `update_track_path_references()`.
- Most tag writes and file moves lack universal rollback.
- Review queues and DB tables are path-based and can go stale after renames/moves.

## Subsystem State

Use `docs/architecture/STABILITY_MATRIX.md` as the current authoritative subsystem status table.

## Documentation Maintenance

Any new command, destructive behavior, metadata mutation, queue change, schema change, or rollback change must update `docs/MAINTENANCE_POLICY.md` requirements and the relevant audit docs.

## Current System State (Post Phase 3)

Phase 3 is stable enough to proceed to Phase 4 planning and implementation. Phase 4 has started as a documentation and ownership-hardening phase; runtime behavior should remain conservative until the remaining legacy path and metadata mutation risks are explicitly migrated.

### Architecture summary

CrateMindAI is now organized around a safer current-state model:

- `pipeline.py` remains the main CLI and command router.
- `processed_state` records stage/file processing history.
- `tracks` is being promoted to the canonical current-state track table.
- Path-audit and path-reconcile operate against an explicitly selected library root.
- Centralized DB path updates live in `db.update_track_path_references()`.
- `modules/organizer.py` is legacy/deprecated. Prefer `modules/library_organize.py` for Phase 3-safe organization paths.

### Canonical data flow

The intended current-state flow is:

1. Files exist under a selected library root.
2. Pipeline stages record processing outcomes in `processed_state`.
3. `build-tracks --root <root>` derives one canonical `tracks` row per valid, existing, non-stale processed-state path.
4. `path-audit --root <root>` uses `tracks.filepath` as canonical when `tracks` is populated, falling back to active non-stale `processed_state` when tracks is empty.
5. `path-reconcile --root <root>` plans repairs from audit findings. Full `--apply` remains intentionally unimplemented.

### Safety model

Current verified safety guarantees:

- `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, `db-prune-stale`, and `review-queue` default to dry-run.
- Those commands require `--apply` plus `--yes` or `--force` before write behavior.
- `path-audit` is read-only except report/log files.
- `path-reconcile` planning is read-only except plan/log files.
- `path-reconcile --apply-auto-safe-only` updates only `processed_state.filepath` for auto-safe candidates.
- `path-reconcile --mark-stale-pstate` marks only eligible processed-state rows stale and does not change file paths.
- `update_track_path_references()` updates `tracks` and non-stale `processed_state` in one transaction and never modifies stale processed-state rows.
- Root-scoped Phase 3 commands use `<root>/logs/processed.db` and `<root>/logs/`.

### Command behavior

Command mode policy:

- Default is preview/dry-run for hardened write-capable commands.
- Apply mode must be explicit.
- Destructive or write-capable Phase 3-hardened commands require confirmation.
- `review-queue` defaults to list-only dry-run behavior; interactive queue mutation requires `--apply --yes`.
- `path-reconcile --apply` is not implemented; only narrowly scoped apply helpers exist.

### Known limitations

- `modules/organizer.py` still contains a legacy `tracks` upsert plus `DELETE FROM tracks` path-mutation pattern.
- Not every older write-capable command requires `--yes` or `--force` yet.
- Root isolation is stable for `path-audit`, `path-reconcile`, and `build-tracks`, but older commands still use global config-derived paths in places.
- Queue, cue, set, and historical log references remain path-based and can become stale after external moves.
- Reconciliation does not move files, update queues, update cue references, or implement full apply mode.
- Frontend and backend write behavior were not fully re-audited in Phase 3.
