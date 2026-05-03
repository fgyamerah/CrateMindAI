# CrateMindAI Project Context

**Updated:** 2026-05-03  
**Purpose:** Canonical low-token engineering memory for future AI sessions.

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

- Default-writing commands: `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, `db-prune-stale`.
- Interactive `review-queue` can write tags without a command-level `--apply`.
- `artist-merge`, `artist-folder-clean`, and legacy `organizer.py` move files and delete old `tracks` rows.
- Most tag writes and file moves lack universal rollback.
- Review queues and DB tables are path-based and can go stale after renames/moves.

## Subsystem State

Use `docs/architecture/STABILITY_MATRIX.md` as the current authoritative subsystem status table.

## Documentation Maintenance

Any new command, destructive behavior, metadata mutation, queue change, schema change, or rollback change must update `docs/MAINTENANCE_POLICY.md` requirements and the relevant audit docs.

