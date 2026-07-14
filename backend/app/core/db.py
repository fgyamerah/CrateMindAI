"""
SQLite layer for the backend's own job-tracking database.

This is completely separate from the toolkit's pipeline database
(processed.db).  The jobs table records every pipeline.py invocation
made through the API, its current state, and where its log file lives.
"""
import contextlib
import logging
import sqlite3
from typing import Iterator

from .config import JOBS_DB_PATH

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id               TEXT    PRIMARY KEY,
    command          TEXT    NOT NULL,
    args_json        TEXT    NOT NULL DEFAULT '[]',
    status           TEXT    NOT NULL DEFAULT 'pending',
    created_at       TEXT    NOT NULL,
    started_at       TEXT,
    finished_at      TEXT,
    exit_code        INTEGER,
    log_path         TEXT,
    pid              INTEGER,
    progress_current INTEGER,
    progress_total   INTEGER,
    progress_percent REAL,
    progress_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_jobs_status  ON jobs(status);
CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);

-- BPM anomaly review state.
-- track_id and filepath reference the pipeline DB (processed.db) — read-only.
-- review_status: pending | reviewed | ignored | requeued | resolved
-- resolved = was anomalous at last check but looks fine now (re-scan promoted it)
CREATE TABLE IF NOT EXISTS bpm_anomalies (
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
);

CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_status ON bpm_anomalies(review_status);
CREATE INDEX IF NOT EXISTS idx_bpm_anomalies_reason ON bpm_anomalies(reason);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a WAL-mode connection; commit on clean exit, rollback on error."""
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(JOBS_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def _add_column_safe(
    conn: sqlite3.Connection, table: str, column: str, defn: str
) -> None:
    """
    Add a column to an existing table if it does not already exist.

    SQLite does not support ALTER TABLE ... ADD COLUMN IF NOT EXISTS, so we
    catch the OperationalError that fires when the column is already present.
    """
    try:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {defn}")
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            pass  # already migrated — safe to ignore
        else:
            raise


def init_db() -> None:
    """
    Create tables if they don't exist and apply any pending column migrations.
    Safe to call on every startup.
    """
    JOBS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as conn:
        conn.executescript(_SCHEMA)

        # Migrate older DBs that were created before progress tracking was added.
        for col, defn in [
            ("pid",              "INTEGER"),
            ("progress_current", "INTEGER"),
            ("progress_total",   "INTEGER"),
            ("progress_percent", "REAL"),
            ("progress_message", "TEXT"),
        ]:
            _add_column_safe(conn, "jobs", col, defn)

    log.info("Backend DB ready: %s", JOBS_DB_PATH)
