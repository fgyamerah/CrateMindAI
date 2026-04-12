"""
SQLite database layer — all pipeline state and logging lives here.
"""
import contextlib
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, Optional

import config

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
_SCHEMA = """
CREATE TABLE IF NOT EXISTS track_history (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath         TEXT    NOT NULL,       -- final path after organization
    original_path    TEXT,                   -- path before organization (inbox location)
    original_meta    TEXT,                   -- JSON: tags snapshot before sanitization
    cleaned_meta     TEXT,                   -- JSON: tags snapshot after sanitization
    actions          TEXT,                   -- JSON list of action strings performed
    created_at       TEXT    NOT NULL,
    rolled_back      INTEGER NOT NULL DEFAULT 0,
    rolled_back_at   TEXT,
    rollback_note    TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_filepath ON track_history(filepath);

CREATE TABLE IF NOT EXISTS tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath        TEXT    NOT NULL UNIQUE,
    filename        TEXT    NOT NULL,
    artist          TEXT,
    title           TEXT,
    genre           TEXT,
    bpm             REAL,
    key_musical     TEXT,
    key_camelot     TEXT,
    duration_sec    REAL,
    bitrate_kbps    INTEGER,
    filesize_bytes  INTEGER,
    status          TEXT    NOT NULL DEFAULT 'pending',
    error_msg       TEXT,
    processed_at    TEXT,
    pipeline_ver    TEXT
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at          TEXT    NOT NULL,
    dry_run         INTEGER NOT NULL DEFAULT 0,
    inbox_count     INTEGER DEFAULT 0,
    processed       INTEGER DEFAULT 0,
    rejected        INTEGER DEFAULT 0,
    duplicates      INTEGER DEFAULT 0,
    unsorted        INTEGER DEFAULT 0,
    errors          INTEGER DEFAULT 0,
    duration_sec    REAL
);

CREATE TABLE IF NOT EXISTS duplicate_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          INTEGER REFERENCES pipeline_runs(id),
    original        TEXT    NOT NULL,
    duplicate       TEXT    NOT NULL,
    reason          TEXT,
    resolved        INTEGER NOT NULL DEFAULT 0,
    resolved_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_tracks_status   ON tracks(status);
CREATE INDEX IF NOT EXISTS idx_tracks_filepath ON tracks(filepath);
CREATE INDEX IF NOT EXISTS idx_dupes_run       ON duplicate_groups(run_id);

CREATE TABLE IF NOT EXISTS cue_points (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    filepath     TEXT    NOT NULL,
    cue_type     TEXT    NOT NULL,
    time_sec     REAL    NOT NULL,
    bar          INTEGER,
    beat_in_bar  INTEGER DEFAULT 1,
    confidence   REAL    DEFAULT 0.5,
    source       TEXT    DEFAULT 'auto',
    analyzed_at  TEXT    NOT NULL,
    UNIQUE(filepath, cue_type)
);

CREATE INDEX IF NOT EXISTS idx_cues_filepath ON cue_points(filepath);

CREATE TABLE IF NOT EXISTS set_playlists (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL,
    created_at   TEXT    NOT NULL,
    config_json  TEXT,
    duration_sec REAL    DEFAULT 0,
    track_count  INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS set_playlist_tracks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    set_id          INTEGER NOT NULL REFERENCES set_playlists(id),
    position        INTEGER NOT NULL,
    filepath        TEXT    NOT NULL,
    phase           TEXT,
    transition_note TEXT,
    UNIQUE(set_id, position)
);
"""


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------
@contextlib.contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(config.DB_PATH))
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
def init_db() -> None:
    with get_conn() as conn:
        conn.executescript(_SCHEMA)
        # Schema migrations — ADD COLUMN is safe on existing DBs (SQLite ignores
        # OperationalError "duplicate column name" so we suppress it).
        for migration in [
            "ALTER TABLE tracks ADD COLUMN quality_tier TEXT",
        ]:
            try:
                conn.execute(migration)
            except Exception:
                pass  # column already exists — safe to ignore


# ---------------------------------------------------------------------------
# Track operations
# ---------------------------------------------------------------------------
def upsert_track(filepath: str, **kwargs: Any) -> None:
    """Insert or update a track record. filepath is the unique key."""
    kwargs["filepath"]     = filepath
    kwargs["filename"]     = Path(filepath).name
    kwargs.setdefault("processed_at", _now())
    kwargs.setdefault("pipeline_ver", config.PIPELINE_VERSION)

    cols         = list(kwargs.keys())
    placeholders = ", ".join("?" for _ in cols)
    updates      = ", ".join(
        f"{c}=excluded.{c}" for c in cols if c != "filepath"
    )
    sql = (
        f"INSERT INTO tracks ({', '.join(cols)}) VALUES ({placeholders})"
        f" ON CONFLICT(filepath) DO UPDATE SET {updates}"
    )
    with get_conn() as conn:
        conn.execute(sql, list(kwargs.values()))


def get_track(filepath: str) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tracks WHERE filepath=?", (filepath,)
        ).fetchone()


def is_processed(filepath: str) -> bool:
    """Return True only if this track completed the pipeline successfully."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM tracks WHERE filepath=?", (filepath,)
        ).fetchone()
        return row is not None and row["status"] == "ok"


def mark_status(filepath: str, status: str, error_msg: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE tracks SET status=?, error_msg=?, processed_at=? WHERE filepath=?",
            (status, error_msg, _now(), filepath),
        )


def get_tracks_by_status(status: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tracks WHERE status=?", (status,)
        ).fetchall()


def get_all_ok_tracks():
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM tracks WHERE status='ok' ORDER BY artist, title"
        ).fetchall()


def prune_stale_tracks(
    lib_root: "Path",
    dry_run: bool = False,
) -> tuple:
    """
    Mark DB rows as 'stale' when their filepath no longer exists on disk AND
    the file cannot be found anywhere under lib_root by filename.

    Files are NEVER deleted from the database — they are marked status='stale'
    so they are excluded from future exports and can be reviewed.

    Args:
        lib_root: root directory to search for files (e.g. /mnt/music_ssd/KKDJ/)
        dry_run:  if True, report but do not write any changes

    Returns:
        (checked, pruned) — number of ok rows checked, number marked stale
    """
    from pathlib import Path as _Path
    import config as _config

    rows = get_all_ok_tracks()
    checked = len(rows)

    # Build filename index over lib_root so we can detect moved files
    lib_index: dict = {}
    lr = _Path(lib_root)
    if lr.exists():
        for ext in _config.AUDIO_EXTENSIONS:
            for p in lr.rglob(f"*{ext}"):
                key = p.name.lower()
                if key not in lib_index:
                    lib_index[key] = p

    pruned = 0
    with get_conn() as conn:
        for row in rows:
            fp = str(row["filepath"])
            if _Path(fp).exists():
                continue
            # Not at DB path — check if findable in lib_root by filename
            key = _Path(fp).name.lower()
            if key in lib_index:
                continue   # file exists elsewhere in current library — keep row
            # Genuinely stale: not on disk and not remappable
            pruned += 1
            if not dry_run:
                conn.execute(
                    "UPDATE tracks SET status='stale', error_msg=? WHERE filepath=?",
                    ("path not found on current filesystem", fp),
                )
    return checked, pruned


# ---------------------------------------------------------------------------
# Pipeline run operations
# ---------------------------------------------------------------------------
def start_run(dry_run: bool) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pipeline_runs (run_at, dry_run) VALUES (?, ?)",
            (_now(), int(dry_run)),
        )
        return cur.lastrowid


def finish_run(run_id: int, **stats: Any) -> None:
    if not stats:
        return
    cols = ", ".join(f"{k}=?" for k in stats)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE pipeline_runs SET {cols} WHERE id=?",
            list(stats.values()) + [run_id],
        )


# ---------------------------------------------------------------------------
# Duplicate operations
# ---------------------------------------------------------------------------
def log_duplicate(run_id: int, original: str, duplicate: str, reason: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO duplicate_groups (run_id, original, duplicate, reason)"
            " VALUES (?, ?, ?, ?)",
            (run_id, original, duplicate, reason),
        )


def get_unresolved_duplicates(run_id: int):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM duplicate_groups WHERE run_id=? AND resolved=0",
            (run_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Track history operations
# ---------------------------------------------------------------------------
def save_track_history(
    filepath: str,
    original_path: str,
    original_meta: dict,
    actions: list,
) -> int:
    """
    Insert a history record immediately after a file is organized.

    Args:
        filepath:      Final library path (after move).
        original_path: Original inbox path (before move).
        original_meta: Dict of tag values captured before sanitization.
        actions:       List of action strings, e.g. ['organized', 'sanitized'].

    Returns the new history row ID.
    """
    import json
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO track_history "
            "(filepath, original_path, original_meta, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                filepath,
                original_path,
                json.dumps(original_meta, ensure_ascii=False),
                json.dumps(actions),
                _now(),
            ),
        )
        return cur.lastrowid


def update_track_history_cleaned(filepath: str, cleaned_meta: dict) -> None:
    """
    Update the cleaned_meta field on the most recent history record for filepath.
    Called by the sanitizer after it has written sanitized tags.
    """
    import json
    with get_conn() as conn:
        conn.execute(
            "UPDATE track_history SET cleaned_meta=? "
            "WHERE filepath=? AND id=(SELECT MAX(id) FROM track_history WHERE filepath=?)",
            (json.dumps(cleaned_meta, ensure_ascii=False), filepath, filepath),
        )


def get_track_history(filepath: Optional[str] = None, include_rolled_back: bool = False):
    """
    Return history records, optionally filtered by filepath and rollback status.
    """
    with get_conn() as conn:
        if filepath:
            sql = "SELECT * FROM track_history WHERE filepath=?"
            args: list = [filepath]
        else:
            sql = "SELECT * FROM track_history WHERE 1=1"
            args = []
        if not include_rolled_back:
            sql += " AND rolled_back=0"
        sql += " ORDER BY created_at DESC"
        return conn.execute(sql, args).fetchall()


def get_history_by_id(history_id: int) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM track_history WHERE id=?", (history_id,)
        ).fetchone()


def mark_rolled_back(history_id: int, note: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE track_history SET rolled_back=1, rolled_back_at=?, rollback_note=? WHERE id=?",
            (_now(), note, history_id),
        )


# ---------------------------------------------------------------------------
# Cue point operations
# ---------------------------------------------------------------------------

def save_cue_points(filepath: str, cues: list) -> None:
    """
    Upsert cue points for a track.
    Each item in cues must be a dict with keys:
      cue_type, time_sec, bar, beat_in_bar, confidence, source
    Existing cues for the same filepath+cue_type are replaced.
    """
    now = _now()
    with get_conn() as conn:
        for cue in cues:
            conn.execute(
                """INSERT INTO cue_points
                   (filepath, cue_type, time_sec, bar, beat_in_bar, confidence, source, analyzed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(filepath, cue_type) DO UPDATE SET
                     time_sec=excluded.time_sec,
                     bar=excluded.bar,
                     beat_in_bar=excluded.beat_in_bar,
                     confidence=excluded.confidence,
                     source=excluded.source,
                     analyzed_at=excluded.analyzed_at""",
                (
                    filepath,
                    cue["cue_type"],
                    cue["time_sec"],
                    cue.get("bar"),
                    cue.get("beat_in_bar", 1),
                    cue.get("confidence", 0.5),
                    cue.get("source", "auto"),
                    now,
                ),
            )


def get_cue_points(filepath: str) -> list:
    """Return all cue points for a track, ordered by time."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM cue_points WHERE filepath=? ORDER BY time_sec",
            (filepath,),
        ).fetchall()


def get_tracks_with_cues() -> list:
    """Return all filepaths that have at least one cue point stored."""
    with get_conn() as conn:
        return [
            row[0] for row in conn.execute(
                "SELECT DISTINCT filepath FROM cue_points ORDER BY filepath"
            ).fetchall()
        ]


# ---------------------------------------------------------------------------
# Set playlist operations
# ---------------------------------------------------------------------------

def save_set_playlist(name: str, tracks: list, config_json: str = "",
                      duration_sec: float = 0.0) -> int:
    """
    Persist a generated set playlist.
    tracks: list of dicts with keys filepath, phase, transition_note.
    Returns the new set_id.
    """
    now = _now()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO set_playlists (name, created_at, config_json, duration_sec, track_count)"
            " VALUES (?, ?, ?, ?, ?)",
            (name, now, config_json, duration_sec, len(tracks)),
        )
        set_id = cur.lastrowid
        for pos, t in enumerate(tracks, start=1):
            conn.execute(
                "INSERT INTO set_playlist_tracks (set_id, position, filepath, phase, transition_note)"
                " VALUES (?, ?, ?, ?, ?)",
                (set_id, pos, t["filepath"], t.get("phase", ""), t.get("transition_note", "")),
            )
    return set_id


def get_set_playlist(set_id: int) -> Optional[sqlite3.Row]:
    """Return a set playlist header row."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM set_playlists WHERE id=?", (set_id,)
        ).fetchone()


def get_set_playlist_tracks(set_id: int) -> list:
    """Return tracks for a set playlist joined with track metadata."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT spt.position, spt.phase, spt.transition_note,
                      t.filepath, t.artist, t.title, t.bpm, t.key_camelot,
                      t.key_musical, t.genre, t.duration_sec
               FROM set_playlist_tracks spt
               LEFT JOIN tracks t ON t.filepath = spt.filepath
               WHERE spt.set_id = ?
               ORDER BY spt.position""",
            (set_id,),
        ).fetchall()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
