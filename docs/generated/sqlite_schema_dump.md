# Generated SQLite Schema Dump

## `backend/data/jobs.db`
### index: `idx_bpm_anomalies_reason`
```sql
CREATE INDEX idx_bpm_anomalies_reason ON bpm_anomalies(reason)
```
### index: `idx_bpm_anomalies_status`
```sql
CREATE INDEX idx_bpm_anomalies_status ON bpm_anomalies(review_status)
```
### index: `idx_jobs_created`
```sql
CREATE INDEX idx_jobs_created ON jobs(created_at DESC)
```
### index: `idx_jobs_status`
```sql
CREATE INDEX idx_jobs_status  ON jobs(status)
```
### table: `bpm_anomalies`
```sql
CREATE TABLE bpm_anomalies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id            INTEGER NOT NULL,
    filepath            TEXT    NOT NULL,
    artist              TEXT,
    title               TEXT,
    genre               TEXT,
    current_bpm         REAL,
    suggested_bpm       REAL,
    reason              TEXT    NOT NULL,
    review_status       TEXT    NOT NULL DEFAULT 'pending',
    detected_at         TEXT    NOT NULL,
    reviewed_at         TEXT,
    review_note         TEXT,
    reanalysis_job_id   TEXT,
    UNIQUE(track_id)
)
```
### table: `jobs`
```sql
CREATE TABLE jobs (
    id          TEXT    PRIMARY KEY,
    command     TEXT    NOT NULL,
    args_json   TEXT    NOT NULL DEFAULT '[]',
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    exit_code   INTEGER,
    log_path    TEXT
, pid INTEGER, progress_current INTEGER, progress_total INTEGER, progress_percent REAL, progress_message TEXT)
```
### table: `sqlite_sequence`
```sql
CREATE TABLE sqlite_sequence(name,seq)
```

