import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pipeline


def _audio(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _create_db(
    root: Path,
    *,
    processed_rows: list[dict] | None = None,
    track_rows: list[dict] | None = None,
) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE processed_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            filepath TEXT NOT NULL,
            file_size INTEGER NOT NULL DEFAULT 0,
            file_mtime REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL,
            processed_at TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            duration_sec REAL,
            bitrate_kbps INTEGER,
            filesize_bytes INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            error_msg TEXT,
            processed_at TEXT,
            pipeline_ver TEXT
        )
        """
    )
    for row in processed_rows or []:
        conn.execute(
            "INSERT INTO processed_state"
            "(stage, filepath, file_size, file_mtime, status, processed_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                row.get("stage", "metadata-sanitize"),
                str(row["filepath"]),
                row.get("file_size", row.get("filesize_bytes", 0)),
                row.get("file_mtime", 0),
                row.get("status", "success"),
                row.get("processed_at", "2026-05-04T00:00:00+00:00"),
                row.get("reason", ""),
            ),
        )
    for row in track_rows or []:
        filepath = str(row["filepath"])
        conn.execute(
            "INSERT INTO tracks(filepath, filename, filesize_bytes, status, processed_at, pipeline_ver) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                filepath,
                row.get("filename", Path(filepath).name),
                row.get("filesize_bytes"),
                row.get("status", "pending"),
                row.get("processed_at"),
                row.get("pipeline_ver"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _run(root: Path) -> int:
    return pipeline.run_build_tracks(SimpleNamespace(root=str(root)))


def _rows(db_path: Path, table: str) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall()]
    finally:
        conn.close()


def _latest_log(root: Path) -> Path:
    return sorted((root / "logs" / "tracks").glob("*_build_tracks.log"))[-1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_tracks_excludes_stale_and_missing_rows(tmp_path):
    valid = _audio(tmp_path / "sorted" / "valid.mp3", b"valid")
    stale = tmp_path / "sorted" / "stale.mp3"
    missing = tmp_path / "sorted" / "missing.mp3"
    db_path = _create_db(
        tmp_path,
        processed_rows=[
            {"stage": "library-organize", "filepath": valid, "file_size": 1},
            {"stage": "library-organize", "filepath": stale, "status": "stale", "file_size": 1},
            {"stage": "library-organize", "filepath": missing, "file_size": 1},
        ],
    )

    assert _run(tmp_path) == 0

    tracks = _rows(db_path, "tracks")
    assert [row["filepath"] for row in tracks] == [str(valid.resolve())]
    log_text = _latest_log(tmp_path).read_text(encoding="utf-8")
    assert "source_rows=3" in log_text
    assert "inserted=1" in log_text
    assert "skipped_stale=1" in log_text
    assert "skipped_missing_file=1" in log_text


def test_build_tracks_collapses_duplicate_filepaths_preferring_highest_stage(tmp_path):
    track = _audio(tmp_path / "sorted" / "same.mp3", b"same")
    db_path = _create_db(
        tmp_path,
        processed_rows=[
            {
                "stage": "metadata-sanitize",
                "filepath": track,
                "file_size": 10,
                "status": "sanitize_status",
                "processed_at": "2026-05-04T00:00:00+00:00",
            },
            {
                "stage": "library-organize",
                "filepath": track,
                "file_size": 10,
                "status": "organized",
                "processed_at": "2026-05-04T01:00:00+00:00",
            },
        ],
    )

    assert _run(tmp_path) == 0

    tracks = _rows(db_path, "tracks")
    assert len(tracks) == 1
    assert tracks[0]["filepath"] == str(track.resolve())
    assert tracks[0]["status"] == "organized"
    assert tracks[0]["processed_at"] == "2026-05-04T01:00:00+00:00"
    assert "duplicate_filepaths_collapsed=1" in _latest_log(tmp_path).read_text(encoding="utf-8")


def test_build_tracks_updates_existing_track(tmp_path):
    track = _audio(tmp_path / "sorted" / "existing.mp3", b"new-size")
    db_path = _create_db(
        tmp_path,
        processed_rows=[
            {
                "stage": "library-organize",
                "filepath": track,
                "file_size": 1,
                "status": "success",
                "processed_at": "2026-05-04T01:00:00+00:00",
            },
        ],
        track_rows=[
            {
                "filepath": track,
                "filename": "old-name.mp3",
                "filesize_bytes": 1,
                "status": "pending",
                "processed_at": "old",
            }
        ],
    )

    assert _run(tmp_path) == 0

    tracks = _rows(db_path, "tracks")
    assert len(tracks) == 1
    assert tracks[0]["filename"] == "existing.mp3"
    assert tracks[0]["filesize_bytes"] == track.stat().st_size
    assert tracks[0]["status"] == "success"
    assert tracks[0]["processed_at"] == "2026-05-04T01:00:00+00:00"
    assert "updated=1" in _latest_log(tmp_path).read_text(encoding="utf-8")


def test_build_tracks_is_idempotent_on_second_run(tmp_path):
    track = _audio(tmp_path / "sorted" / "idempotent.mp3", b"idempotent")
    db_path = _create_db(
        tmp_path,
        processed_rows=[
            {"stage": "library-organize", "filepath": track, "file_size": track.stat().st_size},
        ],
    )

    assert _run(tmp_path) == 0
    first_tracks = _rows(db_path, "tracks")
    assert _run(tmp_path) == 0
    second_tracks = _rows(db_path, "tracks")

    assert first_tracks == second_tracks
    assert len(second_tracks) == 1
    log_text = _latest_log(tmp_path).read_text(encoding="utf-8")
    assert "inserted=0" in log_text
    assert "updated=0" in log_text
    assert "unchanged=1" in log_text


def test_build_tracks_does_not_modify_processed_state_or_files(tmp_path):
    track = _audio(tmp_path / "sorted" / "preserve.mp3", b"preserve")
    db_path = _create_db(
        tmp_path,
        processed_rows=[
            {"stage": "library-organize", "filepath": track, "file_size": track.stat().st_size},
        ],
    )
    before_processed = _rows(db_path, "processed_state")
    before_hash = _sha256(track)

    assert _run(tmp_path) == 0

    assert _rows(db_path, "processed_state") == before_processed
    assert track.exists()
    assert _sha256(track) == before_hash
