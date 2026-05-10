# CrateMindAI Project Context

**Updated:** 2026-05-10  
**Purpose:** Canonical low-token engineering memory for future AI sessions.

## Current Direction

CrateMindAI is a local-first, human-guided DJ library metadata operations workstation.
The platform now centers on reviewable metadata workflows rather than a pipeline-only
automation model.

## Current Architecture

* `pipeline.py` is the CLI router.
* SQLite `logs/processed.db` stores the canonical `tracks` table and historical `processed_state`.
* `tracks` is the working metadata surface.
* `processed_state` is audit/history and is not edited by repair, sanitation, or manual metadata workflows.
* FastAPI serves the backend API.
* React/Vite serves the operational UI.
* Metadata Repair recovers artist/title from filename/context.
* Metadata Sanitation removes junk/source/download contamination.
* Manual Metadata Editor supports human correction when heuristics cannot decide.
* Issue routing sends tracks to the correct review surface.
* The reconciliation ledger records validation and state transitions.

## Safety Rules

* Never overwrite BPM, key, or cue data.
* Never write audio tags unless explicitly requested.
* Never rename or delete audio files unless explicitly requested.
* Prefer DB-only operations first.
* Preserve human review before apply.
* Treat `tracks` as the canonical current-state table.
* Treat `processed_state` as history/audit only.

## Documentation References

* `docs/architecture/HUMAN_REVIEW_MODEL.md`
* `docs/architecture/UI_WORKFLOW_MODEL.md`
* `docs/operations/SESSION_COMPLETION_CHECKLIST.md`
* `docs/architecture/METADATA_OWNERSHIP_MATRIX.md`
* `docs/architecture/STABILITY_MATRIX.md`

## Current Phase Notes

* Phase 11B is complete: manual metadata editor is in place.
* Next likely phases:
  * mark reviewed / suppress false positives
  * duplicate detection
  * online provider enrichment: Discogs / Spotify / Deezer
  * controlled tag write-back later

## Operational Notes

* Use `/mnt/music_ssd/KKDJ` as the default library root.
* Keep review surfaces explicit and auditable.
* Avoid broad automatic reconciliation or silent mutation.
