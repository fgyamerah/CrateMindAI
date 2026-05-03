# Rollback and Recovery

**Date:** 2026-05-03  
**Scope:** Operational recovery guidance based on current audited behavior.

## SAFE RECOVERY

These paths have explicit or mostly safe recovery behavior.

| Area | Recovery method | Limits |
|---|---|---|
| `metadata-sanitize` title rollback | Use `metadata-sanitize-rollback --jsonl <log> --apply` | Only covers logged sanitizer rollback cases; log must exist. |
| Preview mistakes | Rerun with no write flag or inspect generated JSON/report | No mutation occurred. |
| Quarantined duplicate files | Manually move files back from quarantine | Requires knowing original path; no universal manifest verified. |
| BPM anomaly review status | Update review status in backend | Does not modify audio tags or fix stale track refs. |

## PARTIAL RECOVERY

These can often be recovered manually, but not reliably by tooling.

| Area | Possible recovery | Why partial |
|---|---|---|
| AI/enrichment/artist tag writes | Restore from backups, prior exports, Rekordbox, or manual tag edits | No universal before/after tag snapshot found. |
| Filename renames | Infer old path from logs/terminal/report and rename back manually | Only `processed_state` path update is verified. |
| Library moves | Move files back manually from sorted/quarantine folders | DB tables and queues may still need repair. |
| `track_history` rollback | `scripts/rollback.py` reads `track_history` | Active sanitizer may not populate this table. |
| Review queue mistakes | Re-add entry manually or rerun source command | Old recommendations may be overwritten by dedupe logic. |
| DB stale rows | `orphan-scan`/`db-prune-stale` can mark stale | Does not reconstruct moved paths. |

## IRREVERSIBLE OPERATIONS

Treat these as irreversible unless backups exist.

| Operation | Risk |
|---|---|
| `metadata-clean` raw frame cleanup and ID3v1 stripping | Original frames may be unrecoverable without file backup. |
| `tag-normalize` ID3v2.4 to ID3v2.3 conversion and ID3v1 removal | Exact original tag container state is not preserved. |
| `artist-merge` / `artist-folder-clean` `DELETE FROM tracks` | DB rows can be lost if destination state is incomplete. |
| `convert-audio --overwrite` | Existing destination can be replaced. |
| Backend SSD sync with delete enabled | Destination files can be removed by `rsync --delete`. |
| Manual deletion of quarantine folders | Pipeline generally moves files there expecting human review. |

## Operational Recovery Procedures

### Before Recovery

1. Stop running pipeline/backend jobs.
2. Back up `processed.db`, `backend/data/jobs.db`, `data/intelligence/`, and affected audio folders.
3. Preserve logs under the music root and project `logs/` directories.
4. Identify the exact command, timestamp, input path, and flags used.

### Tag Recovery

1. Check whether a command-specific JSON/JSONL log exists.
2. For `metadata-sanitize`, use rollback preview first, then `--apply` only after sampling.
3. For AI/enrichment/artist/label writes, compare with backups/Rekordbox/Kid3 and repair manually.
4. Rerun a read-only audit after manual tag fixes.

### Filesystem Recovery

1. Locate moved files in `.BIN/IGNORED`, `.BIN/CHKARTISTNAMES`, duplicates/quarantine, archive, or low-quality folders.
2. Reconstruct intended original paths from reports/logs.
3. Move a small sample back manually.
4. Run preview-only orphan/reconciliation checks.
5. Only after validation, repair DB paths or rescan.

### DB Recovery

1. Prefer restoring from a pre-run SQLite backup for critical merge/folder errors.
2. If no backup exists, use filesystem as truth and regenerate/update DB through safe preview-first commands.
3. Treat `track_history` as incomplete unless the affected run is known to have populated it.
4. Validate `tracks`, `cue_points`, `set_playlist_tracks`, `duplicate_groups`, and review queues after path repairs.

## Required Future Recovery Tooling

- Append-only operation ledger for all destructive commands.
- Unified restore command for file moves and renames.
- Before/after tag snapshots for every tag write.
- Queue validation and stale-entry repair.
- DB/filesystem reconciliation command.

