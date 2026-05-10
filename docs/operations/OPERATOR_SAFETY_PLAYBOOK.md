# Operator Safety Playbook

## Core Rule

Preview first. Apply only after sampling the proposed changes.

## Safe Batch Sizes

| Operation | First batch | Normal batch after validation |
|---|---:|---:|
| AI normalization | 10-25 files | 50-100 files |
| Online enrichment | 10-25 files | 50-100 files |
| Artist repair/intelligence | 10-25 files | 50-100 files |
| Filename rename | 10-25 files | 100 files |
| Folder/library moves | 5-10 folders | 25-50 folders |
| Metadata-clean/tag-normalize/analyze-missing | 10 files | Only after backup |
| Dedupe/quarantine | 10 groups | 25-50 groups |

## Backup Recommendations

- Back up affected audio folders before any HIGH or CRITICAL operation.
- Back up `processed.db` and `backend/data/jobs.db`.
- Back up `data/intelligence/` queues before review/apply workflows.
- Preserve generated JSON reports and terminal logs.

## Pre-Run Checklist

- Confirm input path is the intended folder.
- Run preview or `--dry-run`.
- Check sample output manually.
- Confirm no Mixed In Key BPM/key/cue fields will be overwritten.
- Confirm queue entries point to existing files.
- Confirm quarantine destination has enough space.
- For `rsync --delete`, run preview immediately before sync.

## Recommended Pipeline Order

1. `metadata-sanitize` preview.
2. `metadata-sanitize --apply` on small batches.
3. `ai-normalize` preview, then small apply batches if needed.
4. `artist-intelligence` or `artist-repair` preview.
5. `metadata-enrich-online` preview, then review queue.
6. `label-clean` report, then `--write-tags` only for sampled high-confidence results.
7. `filename-normalize` preview before any library moves.
8. `library-organize` preview.
9. `dedupe` preview; quarantine only after confirming keep/remove decisions.
10. `playlists` / `rekordbox-export`.

## Dangerous Command Warnings

| Command | Warning |
|---|---|
| `metadata-clean` | Applies by default unless `--dry-run`; can strip raw frames and ID3v1. |
| `tag-normalize` | Applies by default unless `--dry-run`; ID3 conversion is not fully reversible. |
| `analyze-missing` | Applies by default unless `--dry-run`; writes BPM/key when missing. |
| `convert-audio` | Applies by default unless `--dry-run`; archives sources and can overwrite outputs. |
| `cue-suggest` | Writes DB cue rows unless `--dry-run`; suggestions are not MIK cues. |
| `review-queue` | Interactive `apply` writes tags without a command-level `--apply` flag. |
| `artist-merge` | Moves files and deletes old `tracks` rows; backup first. |
| `artist-folder-clean` | Moves/merges folders and deletes old `tracks` rows; backup first. |
| Backend SSD sync | Delete-enabled sync can remove destination files. |

## Review Queue Workflow

1. List queue entries first.
2. Check file exists.
3. Check current tags still match queued `current_tags` or original artist.
4. Apply only a few items first.
5. Rerun list and verify queue removal/update.
6. Archive queue file before bulk approval/apply.

## Quarantine Inspection Workflow

1. Review `.BIN/IGNORED`, `.BIN/CHKARTISTNAMES`, duplicate quarantine, low-quality folders, and conversion archive.
2. Do not delete quarantine files until a later session.
3. Spot-check file paths against DB rows.
4. Restore false positives manually before any cleanup.

## Post-Run Validation Checklist

- Confirm changed files still exist at expected paths.
- Run a preview orphan/stale check.
- Verify a sample of tags in an external tag editor.
- Check review queues for stale entries.
- Check `cue_points` and set playlists if files were renamed.
- Regenerate playlists only after path consistency looks correct.

## Recovery Checklist

- Stop jobs.
- Back up current state before attempting recovery.
- Find command log/report.
- Restore from backups for CRITICAL move/DB issues where possible.
- Recover files from quarantine/archive before modifying DB.
- Rerun preview-only audits after each recovery step.

