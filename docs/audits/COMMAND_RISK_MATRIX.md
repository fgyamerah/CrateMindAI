# Command Risk Matrix

**Date:** 2026-05-03  
**Sources:** `docs/generated/*`, `docs/audits/CLI_RISK_AUDIT.md`, targeted `pipeline.py` and module inspection.

## Risk Key

| Risk | Meaning |
|---|---|
| LOW | Read-only or writes generated docs/reports/exports only |
| MODERATE | Writes DB rows, reports, playlists, or low-impact tags |
| HIGH | Writes audio metadata, renames/moves files, or can stale DB paths |
| CRITICAL | Moves/merges files and deletes or rewrites primary DB state |

## Matrix

| Command | Writes Tags | Renames Files | Moves Files | Deletes Data | Writes DB | Dry Run | Apply Gate | Risk |
|---|---:|---:|---:|---:|---:|---|---|---|
| `label-intel` | No | No | No | Can overwrite label exports | Label SQLite/export | UNVERIFIED | No | MODERATE |
| `artist-folder-clean` | No | Folder rename/merge | Yes | `DELETE FROM tracks` | Yes | Preview mode | `--apply` | CRITICAL |
| `label-clean` | `organization`/TPUB with `--write-tags` | No | No | No | Reports only | `--dry-run` | `--write-tags` | MODERATE |
| `artist-merge` | No | Folder merge | Yes | `DELETE FROM tracks` | Yes | Preview mode | `--apply` | CRITICAL |
| `metadata-clean` | Yes | No | No | Deletes/strips tag frames, ID3v1 | No direct DB evidence | `--dry-run` | Missing | HIGH |
| `tag-normalize` | ID3 version/v1 block | No | No | Strips ID3v1 | No | `--dry-run` | Missing | HIGH |
| `filename-normalize` | No | Yes | Review quarantine optional | No | `processed_state` only VERIFIED | Preview mode | `--apply` | HIGH |
| `library-organize` | No | No | Yes | No | `processed_state` only VERIFIED | Preview mode | `--apply` | HIGH |
| `db-prune-stale` | No | No | No | Marks stale, no file delete | Yes | `--dry-run` | Missing; applies without dry-run | MODERATE |
| `convert-audio` | Preserves/writes converted metadata | No | Archives source | Unlinks failed outputs; `--overwrite` replaces output | No primary DB evidence | `--dry-run` | Missing | HIGH |
| `dedupe` | No | No | Quarantines duplicates | No automatic file delete | duplicate records | Preview via no `--apply` | `--apply` | HIGH |
| `orphan-scan` | No | No | No | Marks stale only | Yes | Preview mode | `--apply` | MODERATE |
| `playlists` | No | No | No | Overwrites playlist artifacts | No | `--dry-run` | Missing | LOW |
| `rekordbox-export` | No | No | No | Overwrites export artifacts | No | `--dry-run` | Missing | LOW |
| `analyze-missing` | BPM/key when missing | No | Corrupt isolation optional | No source delete | Yes | `--dry-run` | Missing | HIGH |
| `audit-quality` | QUALITY with `--write-tags` | No | Low-quality move optional | No | `quality_tier` unless dry-run | `--dry-run` | Missing; opt-in flags | MODERATE |
| `cue-suggest` | No audio tags | No | No | Replaces DB cue rows by filepath/cue_type | Yes | `--dry-run` | Missing | MODERATE |
| `set-builder` | No | No | No | No | Set playlist tables | `--dry-run` | Missing | LOW |
| `harmonic-suggest` | No | No | No | No | No | `--dry-run` for JSON output | Missing | LOW |
| `generate-docs` | No | No | No | Overwrites docs | No | Preview option | Missing | LOW |
| `validate-docs` | No | No | No | No | No | Read-only | N/A | LOW |
| `metadata-sanitize` | Yes; may clear bad ISRC | No | No | Deletes bad tag values | Processed state/logs | Preview default | `--apply` | MODERATE |
| `metadata-sanitize-rollback` | Title rollback | No | No | No | Processed/log state | Preview default | `--apply` | MODERATE |
| `title-number-recover` | Title tag | No | No | No | Processed/log state | Preview default | `--apply` | MODERATE |
| `artist-repair` | Artist tag | No | Review quarantine optional | No | Processed/log state | Preview default | `--apply` | HIGH |
| `artist-repair-review` | Approved artist tag | No | No | Queue state changes | Queue JSON | List/approve modes | `--apply-approved` | HIGH |
| `artist-intelligence` | Artist tag | No | No | No | Processed/log state | `--dry-run` | `--apply` | HIGH |
| `ai-normalize` | Title/version/label effective writes; artist hard-locked | No | No | No | Processed/log datasets | Preview default | `--apply` | HIGH |
| `build-fewshot` | No | No | No | Overwrites fewshot JSONL | No | N/A | Missing | LOW |
| `metadata-enrich-online` | Album/label/ISRC/title | No | IGNORED with flag | No file delete | Processed/log datasets | Preview default | `--apply` | HIGH |
| `review-queue` | Enrichment tag writes | No | No | Removes queue entries | Queue JSON | `--list-only` only | Missing | HIGH |

## Immediate Hardening Targets

- `metadata-clean`, `tag-normalize`, `analyze-missing`, `convert-audio`, `cue-suggest`, and `db-prune-stale` are default-destructive or default-writing when `--dry-run` is omitted.
- `review-queue` writes tags through an interactive keypress and has no `--apply` equivalent.
- File move commands update `processed_state` in some paths, but not all path-bearing tables or JSON queues.
- `artist-merge`, `artist-folder-clean`, and legacy `organizer.py` combine filesystem moves with `DELETE FROM tracks`.

