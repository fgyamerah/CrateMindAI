# Generated Schema / SQL Index

## `backend/app/api/routes/analysis.py`
- Line 7: `PATCH /api/analysis/bpm-anomalies/{id}     — update review status`
- Line 126: `Update the review status of a BPM anomaly record.`

## `backend/app/core/db.py`
- Line 22: `CREATE TABLE IF NOT EXISTS jobs (`
- Line 39: `CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);`
- Line 40: `CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);`
- Line 46: `CREATE TABLE IF NOT EXISTS bpm_anomalies (`
- Line 64: `CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_status ON bpm_anomalies(review_status);`
- Line 65: `CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_reason ON bpm_anomalies(reason);`
- Line 101: `SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we`
- Line 105: `conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {defn}")`
- Line 115: `Create tables if they don't exist and apply any pending column migrations.`

## `backend/app/services/bpm_analysis.py`
- Line 153: `"""UPDATE bpm_anomalies`
- Line 170: `"""INSERT INTO bpm_anomalies`
- Line 174: `ON CONFLICT(track_id) DO UPDATE SET`
- Line 271: `# Update review status`
- Line 281: `Update the review status of an anomaly record.`
- Line 294: `# Build update fields`
- Line 308: `f"UPDATE bpm_anomalies SET {', '.join(fields)} WHERE id = ?",`

## `backend/app/services/job_service.py`
- Line 55: `"""INSERT INTO jobs`
- Line 93: `"UPDATE jobs SET status='running', started_at=? WHERE id=?",`
- Line 102: `"""UPDATE jobs`
- Line 117: `"UPDATE jobs SET pid=? WHERE id=?",`
- Line 130: `Update job progress fields.  Called from the rsync background task`
- Line 135: `"""UPDATE jobs`
- Line 147: `"UPDATE jobs SET pid=NULL WHERE id=?",`

## `backend/app/services/rsync_runner.py`
- Line 346: `--info=progress2 lines to update the job's progress fields in the DB.`

## `db.py`
- Line 16: `CREATE TABLE IF NOT EXISTS track_history (`
- Line 29: `CREATE INDEX IF NOT EXISTS idx_history_filepath ON track_history(filepath);`
- Line 31: `CREATE TABLE IF NOT EXISTS tracks (`
- Line 50: `CREATE TABLE IF NOT EXISTS pipeline_runs (`
- Line 63: `CREATE TABLE IF NOT EXISTS duplicate_groups (`
- Line 73: `CREATE INDEX IF NOT EXISTS idx_tracks_status   ON tracks(status);`
- Line 74: `CREATE INDEX IF NOT EXISTS idx_tracks_filepath ON tracks(filepath);`
- Line 75: `CREATE INDEX IF NOT EXISTS idx_dupes_run       ON duplicate_groups(run_id);`
- Line 77: `CREATE TABLE IF NOT EXISTS cue_points (`
- Line 90: `CREATE INDEX IF NOT EXISTS idx_cues_filepath ON cue_points(filepath);`
- Line 92: `CREATE TABLE IF NOT EXISTS set_playlists (`
- Line 101: `CREATE TABLE IF NOT EXISTS set_playlist_tracks (`
- Line 111: `CREATE TABLE IF NOT EXISTS processed_state (`
- Line 123: `CREATE INDEX IF NOT EXISTS idx_pstate_stage_path ON processed_state(stage, filepath);`
- Line 156: `"ALTER TABLE tracks ADD COLUMN quality_tier TEXT",`
- Line 168: `"""Insert or update a track record. filepath is the unique key."""`
- Line 180: `f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})"`
- Line 181: `f" ON CONFLICT(filepath) DO UPDATE SET {updates}"`
- Line 206: `"UPDATE tracks SET status=?, error_msg=?, processed_at=? WHERE filepath=?",`
- Line 273: `"UPDATE tracks SET status='stale', error_msg=? WHERE filepath=?",`
- Line 368: `"INSERT INTO pipeline_runs (run_at, dry_run) VALUES (?, ?)",`
- Line 380: `f"UPDATE pipeline_runs SET {cols} WHERE id=?",`
- Line 428: `"INSERT INTO track_history "`
- Line 444: `Update the cleaned_meta field on the most recent history record for filepath.`
- Line 450: `"UPDATE track_history SET cleaned_meta=? "`
- Line 483: `"UPDATE track_history SET rolled_back=1, rolled_back_at=?, rollback_note=? WHERE id=?",`
- Line 503: `"""INSERT INTO cue_points`
- Line 506: `ON CONFLICT(filepath, cue_type) DO UPDATE SET`
- Line 559: `"INSERT INTO set_playlists (name, created_at, config_json, duration_sec, track_count)"`
- Line 566: `"INSERT INTO set_playlist_tracks (set_id, position, filepath, phase, transition_note)"`
- Line 604: `CREATE TABLE IF NOT EXISTS processed_state (`
- Line 615: `CREATE INDEX IF NOT EXISTS idx_pstate_stage_path ON processed_state(stage, filepath);`
- Line 616: `CREATE INDEX IF NOT EXISTS idx_pstate_filepath   ON processed_state(filepath);`
- Line 628: `"""CREATE TABLE IF NOT EXISTS processed_state (`
- Line 641: `"CREATE INDEX IF NOT EXISTS idx_pstate_stage_path "`
- Line 645: `"CREATE INDEX IF NOT EXISTS idx_pstate_filepath "`
- Line 673: `"""INSERT INTO processed_state`
- Line 676: `ON CONFLICT(stage, filepath) DO UPDATE SET`
- Line 690: `conn.execute("DELETE FROM processed_state WHERE stage=?", (stage,))`
- Line 695: `Update filepath in all processed_state rows after a file is renamed.`
- Line 702: `"UPDATE processed_state SET filepath=? WHERE filepath=?",`

## `intelligence/artist/artist_alias_store.py`
- Line 255: `# Update in-place if this file+artist was already queued`

## `intelligence/enrichment/__init__.py`
- Line 22: `#   3. Update this file and DJToolkit_CONTEXT.txt`

## `intelligence/enrichment/runner.py`
- Line 198: `current_tags["album"] = ""          # update in-memory view immediately`
- Line 757: `Add or update a file in the review queue.`

## `intelligence/enrichment/traxsource_lookup.py`
- Line 31: `of 2026-04.  If Traxsource restructures its frontend, update the _SEL_*`
- Line 75: `# CSS selectors — update these if Traxsource changes its HTML structure.`
- Line 221: `"the page structure may have changed; update _SEL_TRACK_ROW",`

## `intelligence/label/exporters.py`
- Line 48: `CREATE TABLE labels (`
- Line 68: `CREATE INDEX idx_labels_label_name ON labels(label_name);`
- Line 69: `CREATE INDEX idx_labels_bp_id ON labels(beatport_id);`
- Line 70: `CREATE INDEX idx_labels_ts_id ON labels(traxsource_id);`
- Line 75: `INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`

## `modules/analyzer.py`
- Line 530: `if update and not dry_run:`

## `modules/artist_folder_clean.py`
- Line 646: `# DB update + folder cleanup helpers`
- Line 668: `conn.execute("DELETE FROM tracks WHERE filepath=?", (old_str,))`
- Line 670: `log.warning("FOLDER-CLEAN: DB update failed for %s → %s: %s",`

## `modules/artist_merge.py`
- Line 618: `# Update DB: re-register under new path`
- Line 638: `"DELETE FROM tracks WHERE filepath=?", (old_str,)`
- Line 846: `files into canonical folders, remove vacated folders, update the database.`

## `modules/audit_quality.py`
- Line 511: `store_in_db    — update tracks.quality_tier in the pipeline DB`
- Line 606: `log.debug("DB quality_tier update skipped for %s: %s", path.name, exc)`

## `modules/organizer.py`
- Line 799: `conn.execute("DELETE FROM tracks WHERE filepath=?", (old_path_str,))`

## `modules/run_logger.py`
- Line 124: `Update all processed-state records when a file is renamed.`

## `modules/sanitizer.py`
- Line 333: `# Update DB fields we track`
- Line 334: `db_update = {}`
- Line 344: `# Update track history with the post-sanitization snapshot`

## `modules/set_builder.py`
- Line 562: `# --- Update tracking state ---`

## `pipeline.py`
- Line 474: `"(copy to known_labels.txt to update parser blocklist)")`
- Line 889: `"UPDATE tracks SET status='stale', error_msg=? WHERE filepath=?",`

## `scripts/rollback.py`
- Line 186: `# Update DB to reflect new (old) location`

## `tools/static_analysis/generate_repo_inventory.py`
- Line 193: `"CREATE TABLE",`
- Line 194: `"CREATE INDEX",`
- Line 195: `"ALTER TABLE",`
- Line 196: `"INSERT INTO",`
- Line 197: `"UPDATE ",`
- Line 198: `"DELETE FROM",`

