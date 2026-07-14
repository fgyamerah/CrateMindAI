# DB Schema Audit

**Sources:** `docs/generated/schema_sql_index.md`, `docs/generated/sqlite_schema_dump.md`, `db.py`
**Date:** 2026-05-03

---

## Databases

| Database | Path | Owned by |
|----------|------|---------|
| `processed.db` | `MUSIC_ROOT/logs/processed.db` | pipeline CLI |
| `jobs.db` | `backend/data/jobs.db` | backend API only |
| label store | `intelligence/label/` (separate SQLite) | label-intel only |

`processed.db` is write-only from the pipeline; the backend reads it via a read-only URI connection. `jobs.db` is never touched by the pipeline CLI.

---

## `processed.db` — Tables

### `tracks`

**Purpose:** Central track registry. Primary source for playlist generation, rekordbox-export, enrichment, and all status checks.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | autoincrement |
| `filepath` | TEXT UNIQUE NOT NULL | absolute path — the primary key in practice |
| `filename` | TEXT NOT NULL | basename only |
| `artist` | TEXT | nullable |
| `title` | TEXT | nullable |
| `genre` | TEXT | nullable |
| `bpm` | REAL | nullable; no range check |
| `key_musical` | TEXT | nullable |
| `key_camelot` | TEXT | nullable |
| `duration_sec` | REAL | nullable |
| `bitrate_kbps` | INTEGER | nullable; no range check |
| `filesize_bytes` | INTEGER | nullable |
| `status` | TEXT NOT NULL DEFAULT 'pending' | free-text; no CHECK constraint |
| `error_msg` | TEXT | nullable |
| `processed_at` | TEXT | ISO datetime string |
| `pipeline_ver` | TEXT | nullable |
| `quality_tier` | TEXT | added via ALTER TABLE migration; nullable, no CHECK |

**Indexes:** `idx_tracks_status(status)`, `idx_tracks_filepath(filepath)`

**Rollback usefulness:** LOW. Stores current state only. Tag history is in `track_history`, not here.

**Stale-state risks:**
- `filepath` is absolute. Any rename without DB update makes the row stale silently.
- `artist`, `title`, `bpm`, `key_camelot` can drift from actual file tags if edited externally (e.g. in Kid3, Rekordbox) — the pipeline has no tag-sync check on read.
- `status='stale'` is the only soft-delete; rows are never hard-deleted. A large library accumulates stale rows with no automatic cleanup.
- `artist-merge`, `artist-folder-clean`, and `organizer.py` call `DELETE FROM tracks WHERE filepath=?` for the source path and rely on an immediate `upsert_track` for the destination. If the upsert fails, the row is gone with no recovery path.

**Missing constraints:**
- No `CHECK(status IN ('pending','ok','error','stale','duplicate'))` — any string accepted.
- No `CHECK(bpm > 0)` or `CHECK(bitrate_kbps > 0)`.
- No `CHECK(quality_tier IN ('LOSSLESS','HIGH','MEDIUM','LOW','UNKNOWN'))`.
- No index on `artist`, `genre`, or `title` — all export and playlist queries that filter/sort by these fields do full table scans.

---

### `track_history`

**Purpose:** Before/after tag snapshots at the organize+sanitize step. Designed for rollback via `scripts/rollback.py`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `filepath` | TEXT NOT NULL | final library path after organization |
| `original_path` | TEXT | inbox path before organization |
| `original_meta` | TEXT | JSON tag snapshot before sanitization |
| `cleaned_meta` | TEXT | JSON tag snapshot after sanitization |
| `actions` | TEXT | JSON list of action strings |
| `created_at` | TEXT NOT NULL | |
| `rolled_back` | INTEGER NOT NULL DEFAULT 0 | 0/1; no CHECK |
| `rolled_back_at` | TEXT | nullable |
| `rollback_note` | TEXT | nullable |

**Indexes:** `idx_history_filepath(filepath)`

**Rollback usefulness:** HIGH — the only table that stores `original_meta` for tag reversion. `mark_rolled_back()` exists in db.py. `scripts/rollback.py` references this table.

**Stale-state risks:**
- Written by the legacy `modules/sanitizer.py` flow only. The current `modules/metadata_sanitize.py` does **not** write to `track_history` — it uses `run_logger` (processed_state) and `--output-json` file instead. This table is **effectively unused by the active pipeline**.
- `update_track_history_cleaned()` targets `MAX(id) WHERE filepath=?`. If the filepath changed since the history row was written, the update silently affects zero rows.
- No FK to `tracks(filepath)`. When `DELETE FROM tracks` removes a row, the corresponding history rows become orphaned.

**Missing constraints:**
- No FK to `tracks(filepath)` — history rows outlive their track rows.
- `rolled_back` is INTEGER; no `CHECK(rolled_back IN (0,1))`.
- No index on `created_at` — history queries ordered by `created_at DESC` do full table scans.

---

### `pipeline_runs`

**Purpose:** Audit log of main pipeline invocations with aggregate statistics.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `run_at` | TEXT NOT NULL | ISO datetime |
| `dry_run` | INTEGER NOT NULL DEFAULT 0 | 0/1 |
| `inbox_count` | INTEGER DEFAULT 0 | |
| `processed` | INTEGER DEFAULT 0 | |
| `rejected` | INTEGER DEFAULT 0 | |
| `duplicates` | INTEGER DEFAULT 0 | |
| `unsorted` | INTEGER DEFAULT 0 | |
| `errors` | INTEGER DEFAULT 0 | |
| `duration_sec` | REAL | |

**Indexes:** none.

**Rollback usefulness:** NONE. Summary statistics only; no per-file data.

**Stale-state risks:** Only written by the legacy main pipeline entry; subcommands (`metadata-sanitize`, `artist-repair`, etc.) do not create `pipeline_runs` rows. The table under-represents actual pipeline activity.

**Missing constraints:**
- No index on `run_at` — range queries are full scans.
- `dry_run` is INTEGER with no CHECK.

---

### `duplicate_groups`

**Purpose:** Record of duplicate file pairs detected during `dedupe`.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `run_id` | INTEGER | REFERENCES pipeline_runs(id); nullable FK |
| `original` | TEXT NOT NULL | filepath kept |
| `duplicate` | TEXT NOT NULL | filepath quarantined |
| `reason` | TEXT | |
| `resolved` | INTEGER NOT NULL DEFAULT 0 | always 0 — never updated |
| `resolved_at` | TEXT | always NULL |

**Indexes:** `idx_dupes_run(run_id)`

**Rollback usefulness:** LOW. Records that duplication was detected; does not track where the quarantined file was moved.

**Stale-state risks:**
- `original` and `duplicate` are plain text filepaths with no FK to `tracks`. Both can become stale after any rename.
- `resolved` is never set to 1 anywhere in db.py — `mark_resolved()` is absent. Every row is permanently unresolved.
- Rows accumulate indefinitely across runs with no cleanup.

**Missing constraints:**
- No FK from `original`/`duplicate` to `tracks(filepath)`.
- No index on `original` or `duplicate` — filepath lookups are full scans.
- No `CHECK(resolved IN (0,1))`.
- `mark_resolved()` helper is absent — the field is currently inert.

---

### `cue_points`

**Purpose:** Algorithmic cue point suggestions from `cue-suggest`. NOT the authoritative cue store — MIK owns cues.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `filepath` | TEXT NOT NULL | |
| `cue_type` | TEXT NOT NULL | e.g. intro_start, drop, outro_start |
| `time_sec` | REAL NOT NULL | |
| `bar` | INTEGER | nullable |
| `beat_in_bar` | INTEGER DEFAULT 1 | |
| `confidence` | REAL DEFAULT 0.5 | |
| `source` | TEXT DEFAULT 'auto' | |
| `analyzed_at` | TEXT NOT NULL | |
| | UNIQUE(filepath, cue_type) | upsert on conflict |

**Indexes:** `idx_cues_filepath(filepath)`

**Rollback usefulness:** NONE. Overwritten on every re-analysis.

**Stale-state risks:**
- No FK to `tracks`. Cue rows survive track deletion and track renames.
- `rename_processed_path()` updates `processed_state` on rename but does NOT update `cue_points`. After `filename-normalize`, all cue rows for that file become orphans under the old path.
- No cleanup command exists for orphaned cue rows.

**Missing constraints:**
- No FK to `tracks(filepath)`; no cascade delete.
- `confidence` has no `CHECK(confidence >= 0 AND confidence <= 1)`.

---

### `set_playlists` + `set_playlist_tracks`

**Purpose:** Persist generated DJ sets from `set-builder` for web UI display and history.

`set_playlists`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `name` | TEXT NOT NULL | |
| `created_at` | TEXT NOT NULL | |
| `config_json` | TEXT | |
| `duration_sec` | REAL DEFAULT 0 | |
| `track_count` | INTEGER DEFAULT 0 | |

`set_playlist_tracks`:

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `set_id` | INTEGER NOT NULL | REFERENCES set_playlists(id) |
| `position` | INTEGER NOT NULL | UNIQUE with set_id |
| `filepath` | TEXT NOT NULL | no FK to tracks |
| `phase` | TEXT | |
| `transition_note` | TEXT | |

**Rollback usefulness:** NONE. Append-only; old sets accumulate.

**Stale-state risks:**
- `filepath` in `set_playlist_tracks` is plain text; not FK'd to `tracks`. File renames make stored set track references stale.
- No cascade mechanism. Renaming a track leaves the set records pointing at the old path.

**Missing constraints:**
- No index on `set_playlists.name`.
- No index on `set_playlist_tracks.filepath`.
- No retention policy or cleanup command.

---

### `processed_state`

**Purpose:** Per-stage incremental-run cache. Stores file_size + file_mtime at last processing time. Used by `run_logger` to skip unchanged files.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | |
| `stage` | TEXT NOT NULL | free-text stage name |
| `filepath` | TEXT NOT NULL | |
| `file_size` | INTEGER NOT NULL DEFAULT 0 | |
| `file_mtime` | REAL NOT NULL DEFAULT 0 | Unix timestamp |
| `status` | TEXT NOT NULL | e.g. no_change, success, error, review |
| `processed_at` | TEXT NOT NULL | |
| `reason` | TEXT NOT NULL DEFAULT '' | |
| | UNIQUE(stage, filepath) | upsert on conflict |

**Indexes:** `idx_pstate_stage_path(stage, filepath)`, `idx_pstate_filepath(filepath)`

**Rollback usefulness:** NONE. Operational skip cache only.

**Stale-state risks:**
- Change detection uses `file_size + file_mtime` only — not content hash. Two different files with the same size and mtime (e.g. after a copy+touch) will be incorrectly skipped.
- `--reset-stage` deletes ALL rows for a stage; no per-file reset is possible.
- `stage` is free-text; a typo in a stage name silently creates an isolated namespace with no validation.

**Missing constraints:**
- No FK to `tracks(filepath)`.
- No `CHECK` on `status` or `stage` values.
- Table DDL is duplicated: defined in both `_SCHEMA` and `_PSTATE_DDL` — two separate `CREATE TABLE IF NOT EXISTS` blocks for the same table exist in db.py. The `idx_pstate_filepath` index only appears in `_PSTATE_DDL`, not in `_SCHEMA`. If `_ensure_pstate()` is never called (clean DB initialized with `init_db()` only), `idx_pstate_filepath` may be absent.

---

## `jobs.db` — Tables

### `jobs`

**Purpose:** Async pipeline job records for backend web UI.

| Field | Type | Notes |
|-------|------|-------|
| `id` | TEXT PK | UUID string |
| `command` | TEXT NOT NULL | |
| `args_json` | TEXT NOT NULL DEFAULT '[]' | |
| `status` | TEXT NOT NULL DEFAULT 'pending' | free-text |
| `created_at` | TEXT NOT NULL | |
| `started_at` | TEXT | |
| `finished_at` | TEXT | |
| `exit_code` | INTEGER | |
| `log_path` | TEXT | |
| `pid` | INTEGER | added via ALTER TABLE — schema fragmentation |
| `progress_current` | INTEGER | added via ALTER TABLE |
| `progress_total` | INTEGER | added via ALTER TABLE |
| `progress_percent` | REAL | added via ALTER TABLE |
| `progress_message` | TEXT | added via ALTER TABLE |

**Indexes:** `idx_jobs_status(status)`, `idx_jobs_created(created_at DESC)`

**Rollback usefulness:** NONE. Log only.

**Stale-state risks:**
- `log_path` is a text path; log file can be deleted externally without DB update.
- The five `pid`/`progress_*` columns appear in the schema dump as a trailing inline addition after a comma on the `log_path` line — they were added via `ALTER TABLE`, not via the original `CREATE TABLE`. This is visible fragmentation in the schema dump.

**Missing constraints:**
- No `CHECK(status IN ('pending','running','succeeded','failed','cancelled'))`.
- No retention policy; rows accumulate indefinitely.

---

### `bpm_anomalies`

**Purpose:** BPM anomaly detection results from the backend analysis service.

| Field | Type | Notes |
|-------|------|-------|
| `id` | INTEGER PK | autoincrement |
| `track_id` | INTEGER NOT NULL | logical FK to `processed.db tracks.id` — unenforceable |
| `filepath` | TEXT NOT NULL | display only; can drift |
| `artist` | TEXT | snapshot at detection time |
| `title` | TEXT | snapshot at detection time |
| `genre` | TEXT | snapshot at detection time |
| `current_bpm` | REAL | |
| `suggested_bpm` | REAL | |
| `reason` | TEXT NOT NULL | |
| `review_status` | TEXT NOT NULL DEFAULT 'pending' | |
| `detected_at` | TEXT NOT NULL | |
| `reviewed_at` | TEXT | |
| `review_note` | TEXT | |
| `reanalysis_job_id` | TEXT | FK to jobs.id — unenforced |
| | UNIQUE(track_id) | one anomaly per track |

**Indexes:** `idx_bpm_anomalies_status(review_status)`, `idx_bpm_anomalies_reason(reason)`

**Rollback usefulness:** NONE. Diagnostic records only.

**Stale-state risks:**
- `track_id` references `processed.db tracks.id` across two separate database files. SQLite cannot enforce cross-database FKs — this reference is unenforceable and will silently dangle if the track is deleted.
- `filepath`, `artist`, `title`, `genre` are snapshots captured at detection time; they do not update when file tags change.

**Missing constraints:**
- Cross-DB FK to `processed.db` is structurally unenforceable.
- No index on `filepath`.
- No `CHECK(review_status IN ('pending','reviewed','ignored','requeued','resolved'))`.

---

## Summary of Missing Indexes

| Table | Missing index | Impact |
|-------|--------------|--------|
| `tracks` | `artist` | playlist/export ORDER BY artist — full scan |
| `tracks` | `genre` | playlist generation WHERE genre — full scan |
| `tracks` | `title` | search queries — full scan |
| `track_history` | `created_at` | history ordered DESC — full scan |
| `pipeline_runs` | `run_at` | date-range queries — full scan |
| `duplicate_groups` | `original`, `duplicate` | filepath lookups — full scan |
| `set_playlists` | `name` | set lookup by name — full scan |
| `set_playlist_tracks` | `filepath` | track→set reverse lookup — full scan |
| `bpm_anomalies` | `filepath` | filepath lookups — full scan |

---

## DB/Filesystem Divergence Risks (consolidated)

| Risk | Tables affected | Trigger |
|------|----------------|---------|
| Rename without DB update | `tracks`, `track_history`, `cue_points`, `set_playlist_tracks`, `duplicate_groups` | Any external rename (Rekordbox, Finder, shell) |
| Hard DELETE leaves orphaned children | `track_history`, `cue_points`, `duplicate_groups` | `artist-merge`, `artist-folder-clean`, `organizer.py` DELETE calls |
| `cue_points` not updated on rename | `cue_points` | `filename-normalize` updates `processed_state` via `rename_processed_path()` but skips `cue_points` |
| `set_playlist_tracks.filepath` drift | `set_playlist_tracks` | Any rename after a set is built |
| Cross-DB `track_id` dangling | `bpm_anomalies` in jobs.db | Track deleted from processed.db |
| mtime-based skip false negative | `processed_state` | File replaced with same-size copy + same mtime |
| `track_history` unused by active pipeline | `track_history` | New `metadata-sanitize` module uses `--output-json` file, not this table |

---

## Critical Findings

1. **`track_history` is orphaned by the new pipeline.** The active `modules/metadata_sanitize.py` does not write to it. Rollback data lives in external JSON/JSONL files (`--output-json`), not in the DB. The `scripts/rollback.py` tool that reads `track_history` therefore has no data to work with for new-pipeline runs.

2. **`duplicate_groups.resolved` is permanently 0.** `mark_resolved()` does not exist in db.py. The field is inert schema.

3. **`cue_points` orphan on rename.** `rename_processed_path()` keeps processed_state in sync but skips cue_points. Every renamed file leaves cue data behind under the old path.

4. **`idx_pstate_filepath` may be absent on clean installs.** It is defined in `_PSTATE_DDL` but not in `_SCHEMA`. `init_db()` runs `_SCHEMA` via `executescript`; `_ensure_pstate()` (which applies `_PSTATE_DDL`) is only called lazily on first `processed_state` read/write. A DB initialized with `init_db()` but never used for processed_state may lack this index.

5. **`jobs.db` schema fragmentation.** The five progress/pid columns appear as ALTER TABLE additions visible in the raw schema dump. The CREATE TABLE statement in `backend/app/core/db.py` should be updated to include them natively.

6. **No FK cascade anywhere.** `PRAGMA foreign_keys=ON` is set on every connection, but no table uses `ON DELETE CASCADE`. Deleting a `pipeline_runs` row does not cascade to `duplicate_groups`; deleting a `set_playlists` row does not cascade to `set_playlist_tracks`.
