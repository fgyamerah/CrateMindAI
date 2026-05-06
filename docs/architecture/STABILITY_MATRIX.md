# Stability Matrix

**Hardened:** 2026-05-05. See `docs/PHASE3_SAFETY_AUDIT.md` for the latest Phase 3 verification notes.

## Phase 3 Core Safety Status

| Area | Status | Notes |
|---|---|---|
| path safety | stable | Root-scoped Phase 3 commands resolve a selected root, use `<root>/logs/processed.db`, and reject/report paths outside that root. Older commands still need migration. |
| DB consistency | stable | `tracks` is canonical when populated; active non-stale `processed_state` is fallback; stale rows are excluded from current-state counts. |
| pipeline writes | guarded | The hardened write-capable command set defaults to dry-run and requires `--apply` plus confirmation. Other older commands still need confirmation standardization. |
| reconciliation | partial | Plan mode is safe; full `path-reconcile --apply` is not implemented. Only `--apply-auto-safe-only` and `--mark-stale-pstate` exist. |

| Component | Status | Risk | Production Use | Notes |
|---|---|---|---|---|
| metadata-sanitize | semi-stable | MODERATE | Yes, preview first | Has `--apply`; rollback via JSON log exists, but rollback is not universal. |
| ai-normalize | experimental | HIGH | Small batches only | Local AI per context; artist locked; no BPM/key/cues; threshold docs conflict. |
| artist-intelligence | semi-stable | HIGH | Cautious | Writes artist tags with confidence gate and review queue. |
| artist-repair | experimental | HIGH | Cautious | Present; high-confidence writes, lower-confidence review/quarantine flow. |
| metadata-enrich-online | experimental | HIGH | Small batches only | Writes album/label/ISRC/title; review queue and hard blocks; threshold/path conflicts need verification. |
| label-intelligence | experimental | MODERATE | Review/report use | Label parsing/enrichment is evolving. |
| label-clean | semi-stable | MODERATE | Yes with review | Read-only unless `--write-tags`; threshold default around 0.85. |
| filename-normalize | semi-stable | HIGH | Cautious | Preview default, collision suffixes; no universal rollback. |
| dedupe | semi-stable | HIGH | Cautious | Moves duplicates to quarantine, never deletes automatically; restore tooling missing. |
| library-organize | semi-stable | HIGH | Cautious | Preferred safe organization path; Phase 3 code calls `update_track_path_references`; queues/cues/sets can still stale. |
| modules/organizer.py | deprecated | CRITICAL | Do not use | Legacy organizer retained for old pipeline compatibility; uses pre-Phase-3 path mutation and can delete old `tracks` rows. Prefer `library_organize.py`. |
| rekordbox-export | stable | LOW | Yes | Writes export artifacts, not audio tags. |
| playlists | stable | LOW | Yes | Writes M3U/XML outputs, not audio tags. |
| backend jobs | semi-stable | MODERATE | Yes with caution | Backend reads pipeline DB; writes jobs/anomaly DB; job schema has limited constraints. |
| frontend UI | UNVERIFIED | MODERATE | UNVERIFIED | Static docs mention UI, but implementation was not inspected in this pass. |
| rollback/history | unsafe | HIGH | Do not rely on globally | Only sanitizer rollback is clear; `track_history` may be unused by active pipeline. |
| review queues | semi-stable | HIGH | Yes, with stale-path checks | Multiple queues; dedupe varies; queue paths can stale after file moves. |
| quarantine workflow | semi-stable | HIGH | Cautious | Files moved not deleted; destination/path docs conflict; no restore command. |
| BPM anomaly review | semi-stable | MODERATE | Yes | Backend review statuses preserve human decisions; cross-DB references can dangle. |
| set-builder | semi-stable | LOW | Yes | Writes set outputs and DB records; stored file paths can stale. |
| cue-suggest | guarded | MODERATE | Advisory only | Dry-run default; `--apply` requires confirmation; must not overwrite MIK cues. |
| convert-audio | guarded | HIGH | Manual/small batches only | Dry-run default; `--apply` requires confirmation; archives source; `--overwrite` increases risk. |
| audit-quality | semi-stable | MODERATE | Yes, read-only default | Optional tag writes and low-quality moves need rollback manifest. |
| metadata-clean | guarded | HIGH | Avoid bulk apply | Dry-run default; `--apply` requires confirmation; broad tag cleanup and ID3v1 stripping. |
| tag-normalize | guarded | HIGH | Backup first | Dry-run default; `--apply` requires confirmation; ID3 conversion/removal not easily reversible. |
| analyze-missing | guarded | HIGH | Only with verified missing fields | Dry-run default; `--apply` requires confirmation; writes BPM/key to DB/audio tags. |
| orphan-scan | semi-stable | MODERATE | Yes with preview | Marks stale rows with `--apply`; does not delete files. |
| backend SSD sync | semi-stable | HIGH | Preview first | `allow_delete` defaults false; delete-enabled sync is destructive on destination. |

## Hardened Operational Matrix

| Component | Status | Operational Risk | Production Suitability | Notes |
|---|---|---|---|---|
| metadata-sanitize | semi-stable | MODERATE | Production with preview | Preview default, `--apply` gate, deterministic. Rollback exists for logged sanitizer cases. |
| metadata-clean | guarded | HIGH | Avoid bulk runs | Dry-run default; `--apply` requires confirmation; broad raw/easy tag cleanup and ID3v1 stripping. |
| tag-normalize | guarded | HIGH | Backup first | Dry-run default; `--apply` requires confirmation; ID3 conversion/removal not fully reversible. |
| ai-normalize | experimental | HIGH | Small batches only | VERIFIED default confidence 0.80; local Ollama per context; artist output ignored; no BPM/key/cues. |
| artist-intelligence | semi-stable | HIGH | Cautious small batches | Preview/default safety with `--apply`; writes artist tags above confidence gate. |
| artist-repair | experimental | HIGH | Cautious small batches | High-confidence repairs can apply; lower confidence queued/quarantined. Queue dedupe verified. |
| artist-repair-review | experimental | HIGH | Manual only | `--apply-approved` writes approved repairs; validates current artist against queued original. |
| metadata-enrich-online | experimental | HIGH | Small batches only | VERIFIED apply 0.90/review 0.75; ISRC override 0.98; hard skips cap 0.74. |
| review-queue | guarded | HIGH | Manual with backups | Defaults to list-only dry-run; interactive mutation requires `--apply` plus confirmation. |
| label-intelligence | experimental | MODERATE | Report/review use | Label parsing/enrichment evolving. |
| label-clean | semi-stable | MODERATE | Production with review | Read-only unless `--write-tags`; default threshold around 0.85. |
| filename-normalize | semi-stable | HIGH | Cautious | Preview default and collision handling; rename tracking updates `processed_state` only. |
| library-organize | semi-stable | HIGH | Cautious | Preferred safe organization path; moves files; Phase 3 paths call `update_track_path_references`; queues/cues/sets can still stale. |
| modules/organizer.py | deprecated | CRITICAL | Do not use | Legacy organizer retained for old pipeline compatibility; can use beets, writes history, and deletes old `tracks` rows. Prefer `library_organize.py`. |
| dedupe | semi-stable | HIGH | Cautious | Moves duplicates to quarantine; no universal restore manifest verified. |
| library_dedupe | semi-stable | HIGH | Cautious | Similar quarantine workflow; restore tooling missing. |
| artist-merge | semi-stable | HIGH | Backup required | Moves/merges folders; Phase 3 paths call `update_track_path_references`; no universal rollback. |
| artist-folder-clean | semi-stable | HIGH | Backup required | Moves/merges folders; Phase 3 paths call `update_track_path_references`; no universal rollback. |
| rekordbox-export | stable | LOW | Production | Writes export artifacts, not source audio tags. |
| playlists | stable | LOW | Production | Writes M3U/XML artifacts; does not mutate audio. |
| backend jobs | semi-stable | MODERATE | Production with caution | Backend writes `jobs.db`; pipeline DB is read-only from backend paths. |
| backend SSD sync | semi-stable | HIGH | Preview first | Delete disabled by default, but delete-enabled sync is destructive on destination. |
| frontend UI | UNVERIFIED | MODERATE | UNVERIFIED | Not source-inspected in this pass. |
| rollback systems | unsafe | HIGH | Limited use | Sanitizer rollback exists; global rollback ledger missing. |
| review queues | semi-stable | HIGH | Use with stale-path checks | Multiple path-based queues; enrichment and artist-repair dedupe verified. |
| quarantine systems | semi-stable | HIGH | Inspect before deletion | Active enrichment IGNORED path is `.BIN/IGNORED`; restore tooling missing. |
| BPM anomaly review | semi-stable | MODERATE | Production with caution | Backend review state can dangle from pipeline DB tracks. |
| set-builder | semi-stable | LOW | Production | Writes set outputs and DB set records; paths can stale after renames. |
| cue-suggest | guarded | MODERATE | Advisory only | Dry-run default; `--apply` requires confirmation; must not overwrite MIK cues. |
| convert-audio | guarded | HIGH | Manual/small batches only | Dry-run default; `--apply` requires confirmation; archives source, can overwrite outputs. |
| audit-quality | semi-stable | MODERATE | Read-only default | Optional QUALITY tag writes and low-quality moves need rollback manifest. |
| analyze-missing | guarded | HIGH | Only with verified missing fields | Dry-run default; `--apply` requires confirmation; writes BPM/key to DB/audio tags. |
| db-prune-stale | guarded | MODERATE | Preview first | Dry-run default; `--apply` requires confirmation; marks rows stale. |
| orphan-scan | semi-stable | MODERATE | Production with preview | Preview default; `--apply` marks stale rows. |
| build-fewshot | stable | LOW | Production | Writes fewshot JSONL snapshot only. |
| generate-docs | stable | LOW | Production | Overwrites generated docs/readme sections. |
| validate-docs | stable | LOW | Production | Read-only validation. |
