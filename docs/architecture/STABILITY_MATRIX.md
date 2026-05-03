# Stability Matrix

**Hardened:** 2026-05-03. See the expanded matrix at the end of this file for verified operational-risk classifications.

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
| library-organize | semi-stable | HIGH | Cautious | Moves files; path consistency gaps across DB/queues/cues/sets. |
| rekordbox-export | stable | LOW | Yes | Writes export artifacts, not audio tags. |
| playlists | stable | LOW | Yes | Writes M3U/XML outputs, not audio tags. |
| backend jobs | semi-stable | MODERATE | Yes with caution | Backend reads pipeline DB; writes jobs/anomaly DB; job schema has limited constraints. |
| frontend UI | UNVERIFIED | MODERATE | UNVERIFIED | Static docs mention UI, but implementation was not inspected in this pass. |
| rollback/history | unsafe | HIGH | Do not rely on globally | Only sanitizer rollback is clear; `track_history` may be unused by active pipeline. |
| review queues | semi-stable | HIGH | Yes, with stale-path checks | Multiple queues; dedupe varies; queue paths can stale after file moves. |
| quarantine workflow | semi-stable | HIGH | Cautious | Files moved not deleted; destination/path docs conflict; no restore command. |
| BPM anomaly review | semi-stable | MODERATE | Yes | Backend review statuses preserve human decisions; cross-DB references can dangle. |
| set-builder | semi-stable | LOW | Yes | Writes set outputs and DB records; stored file paths can stale. |
| cue-suggest | experimental | MODERATE | Advisory only | Writes DB cue suggestions unless `--dry-run`; must not overwrite MIK cues. |
| convert-audio | unsafe | HIGH | Manual/small batches only | Applies by default unless `--dry-run`; archives source; `--overwrite` increases risk. |
| audit-quality | semi-stable | MODERATE | Yes, read-only default | Optional tag writes and low-quality moves need rollback manifest. |
| metadata-clean | unsafe | HIGH | Avoid bulk apply | Applies by default unless `--dry-run`; broad tag cleanup and ID3v1 stripping. |
| tag-normalize | unsafe | HIGH | Backup first | Applies by default unless `--dry-run`; ID3 conversion/removal not easily reversible. |
| analyze-missing | unsafe | HIGH | Only with verified missing fields | Applies by default unless `--dry-run`; writes BPM/key to DB/audio tags. |
| orphan-scan | semi-stable | MODERATE | Yes with preview | Marks stale rows with `--apply`; does not delete files. |
| backend SSD sync | semi-stable | HIGH | Preview first | `allow_delete` defaults false; delete-enabled sync is destructive on destination. |

## Hardened Operational Matrix

| Component | Status | Operational Risk | Production Suitability | Notes |
|---|---|---|---|---|
| metadata-sanitize | semi-stable | MODERATE | Production with preview | Preview default, `--apply` gate, deterministic. Rollback exists for logged sanitizer cases. |
| metadata-clean | unsafe | HIGH | Avoid bulk runs | Applies by default unless `--dry-run`; broad raw/easy tag cleanup and ID3v1 stripping. |
| tag-normalize | unsafe | HIGH | Backup first | Applies by default unless `--dry-run`; ID3 conversion/removal not fully reversible. |
| ai-normalize | experimental | HIGH | Small batches only | VERIFIED default confidence 0.80; local Ollama per context; artist output ignored; no BPM/key/cues. |
| artist-intelligence | semi-stable | HIGH | Cautious small batches | Preview/default safety with `--apply`; writes artist tags above confidence gate. |
| artist-repair | experimental | HIGH | Cautious small batches | High-confidence repairs can apply; lower confidence queued/quarantined. Queue dedupe verified. |
| artist-repair-review | experimental | HIGH | Manual only | `--apply-approved` writes approved repairs; validates current artist against queued original. |
| metadata-enrich-online | experimental | HIGH | Small batches only | VERIFIED apply 0.90/review 0.75; ISRC override 0.98; hard skips cap 0.74. |
| review-queue | unsafe | HIGH | Manual with backups | Interactive apply writes enrichment tags without command-level `--apply`. |
| label-intelligence | experimental | MODERATE | Report/review use | Label parsing/enrichment evolving. |
| label-clean | semi-stable | MODERATE | Production with review | Read-only unless `--write-tags`; default threshold around 0.85. |
| filename-normalize | semi-stable | HIGH | Cautious | Preview default and collision handling; rename tracking updates `processed_state` only. |
| library-organize | semi-stable | HIGH | Cautious | Moves files; only `processed_state` path update verified. |
| organizer | deprecated | CRITICAL | Avoid unless intentionally using legacy pipeline | Moves files, can use beets, writes history, deletes old `tracks` rows. |
| dedupe | semi-stable | HIGH | Cautious | Moves duplicates to quarantine; no universal restore manifest verified. |
| library_dedupe | semi-stable | HIGH | Cautious | Similar quarantine workflow; restore tooling missing. |
| artist-merge | unsafe | CRITICAL | Backup required | Moves/merges folders and deletes old `tracks` rows. |
| artist-folder-clean | unsafe | CRITICAL | Backup required | Moves/merges folders and deletes old `tracks` rows. |
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
| cue-suggest | experimental | MODERATE | Advisory only | Writes DB cue suggestions unless `--dry-run`; must not overwrite MIK cues. |
| convert-audio | unsafe | HIGH | Manual/small batches only | Applies by default unless `--dry-run`; archives source, can overwrite outputs. |
| audit-quality | semi-stable | MODERATE | Read-only default | Optional QUALITY tag writes and low-quality moves need rollback manifest. |
| analyze-missing | unsafe | HIGH | Only with verified missing fields | Applies by default unless `--dry-run`; writes BPM/key to DB/audio tags. |
| db-prune-stale | unsafe | MODERATE | Preview first | Applies by default unless `--dry-run`; marks rows stale. |
| orphan-scan | semi-stable | MODERATE | Production with preview | Preview default; `--apply` marks stale rows. |
| build-fewshot | stable | LOW | Production | Writes fewshot JSONL snapshot only. |
| generate-docs | stable | LOW | Production | Overwrites generated docs/readme sections. |
| validate-docs | stable | LOW | Production | Read-only validation. |
