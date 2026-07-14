# Filesystem / Database Consistency Audit

**Date:** 2026-05-03  
**Sources:** generated SQL/danger indexes, `DB_SCHEMA_AUDIT.md`, targeted source inspection.

## Summary

The pipeline uses absolute file paths as operational identity. Any rename, move, quarantine, or external edit can desynchronize `processed.db`, `jobs.db`, review queues, cue suggestions, duplicate records, and set history.

## Verified Path Update Behavior

| Module/function | Behavior | Coverage | Risk |
|---|---|---|---|
| `modules/run_logger.rename_path()` | Calls `db.rename_processed_path(old,new)` | `processed_state` only | Other DB tables and queues keep old paths. |
| `db.rename_processed_path()` | `UPDATE processed_state SET filepath=? WHERE filepath=?` | VERIFIED | Does not update `tracks`, `cue_points`, `duplicate_groups`, `set_playlist_tracks`, review queues. |
| `modules/filename_normalize.run()` | Calls `_proc.rename_path(src,dst)` after `src.rename(dst)` | VERIFIED | Only processed-state path cache updated. |
| `modules/library_organize` | Calls `_proc.rename_path(src,dst)` after move | VERIFIED | Same limited coverage. |
| `modules/artist_merge` | Moves files, then deletes old `tracks` row | VERIFIED | Critical if destination upsert fails or child tables retain old paths. |
| `modules/artist_folder_clean` | Moves files, then deletes old `tracks` row | VERIFIED | Same critical divergence risk. |
| `modules/organizer.py` | Moves files and calls `db.save_track_history`; deletes old `tracks` row | VERIFIED legacy behavior | Beets path changes can be difficult to reconstruct. |

## Stale DB Risks

| Table | Path field | Stale trigger | Recovery difficulty |
|---|---|---|---|
| `tracks` | `filepath` | Any external move/rename; failed path update | HIGH |
| `track_history` | `filepath`, `original_path` | Rename after history write | HIGH |
| `processed_state` | `filepath` | External rename; non-participating move module | MODERATE |
| `cue_points` | `filepath` | Any file rename; not updated by `rename_processed_path()` | MODERATE |
| `duplicate_groups` | `original`, `duplicate` | Dedupe quarantine, later rename/move | HIGH |
| `set_playlist_tracks` | `filepath` | Rename/move after set built | MODERATE |
| `bpm_anomalies` | `filepath`, `track_id` | Cross-DB track delete/rename | MODERATE |

## Review Queue Stale Paths

| Queue | Path key | Dedup behavior | Stale risk |
|---|---|---|---|
| `data/intelligence/enrichment_review_queue.json` | `file_path` | VERIFIED replace by exact `file_path` | Move/rename creates a new key and leaves old proposal unusable. |
| `data/intelligence/artist_repair_queue.json` | `file` + `original_artist` | VERIFIED dedup by tuple and preserves approval flags | If file moves, approved entry may skip missing or become stale. |
| `data/intelligence/artist_review_queue.json` | UNVERIFIED | Updated in artist alias store | Lifecycle not fully verified. |
| `data/review/artist_review_queue.jsonl` | `file_path` | UNVERIFIED append behavior | Separate queue from intelligence queue; likely stale after moves. |
| AI JSONL datasets | file path in record | Append-only | Historical data, not live queue; stale by design. |

## Move Without Full DB Update

| Module | Move type | DB update found | Gap |
|---|---|---|---|
| `filename-normalize` | Rename file | `processed_state` only | `tracks.filepath` update not verified in targeted snippet. |
| `library-organize` | Move file | `processed_state` only | Same. |
| `metadata-enrich-online --move-ignored` | Move to `.BIN/IGNORED` | No DB path update found | Queue/DB may keep original active path. |
| `artist-repair --move-artist-review` | Move to CHKARTISTNAMES | Processed state records skipped/moved outcome | No global path update found. |
| `dedupe` / `library_dedupe` | Move duplicate to quarantine | Duplicate logging exists | Quarantine destination not enough for full restore in DB audit. |
| `audit-quality --move-low-quality` | Move low-quality files | UNVERIFIED | Restore manifest not found. |
| `convert-audio` | Move source to archive | No primary DB path update found | Converted file may not correspond to existing DB row until rescan. |

## DB Update Without File Success Risks

| Pattern | Risk |
|---|---|
| Move then DB update | If DB update fails, filesystem is already moved. |
| DB delete plus destination upsert | If upsert fails after delete, primary row is gone. |
| Queue apply then queue removal | Failed/partial tag writes can desync queue state if not carefully guarded. |
| Processed-state update only | Later stages may skip files while primary DB/queues still point elsewhere. |

## Recommended Controls

- Add one path update helper that updates every path-bearing table and live queue.
- Record move manifests with old path, new path, command, timestamp, file size, mtime, and checksum if affordable.
- Replace `DELETE FROM tracks` move cleanup with transactional update/tombstone behavior.
- Add `reconcile-library` to compare filesystem, `tracks`, `processed_state`, `cue_points`, duplicate groups, set playlists, jobs anomalies, and JSON queues.
- Validate queue entries before apply: file exists, size/mtime unchanged, current tag fingerprint matches queued snapshot.

