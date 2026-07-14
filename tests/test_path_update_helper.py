import sqlite3
from pathlib import Path

import pytest

import db
import modules.artist_folder_clean as afc
import modules.artist_merge as artist_merge
import modules.library_organize as library_organize


def _audio(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _init_db(tmp_path: Path, monkeypatch) -> Path:
    db_path = tmp_path / "logs" / "processed.db"
    monkeypatch.setattr(db.config, "MUSIC_ROOT", tmp_path)
    monkeypatch.setattr(db.config, "DB_PATH", db_path)
    db.init_db()
    return db_path


def _row(db_path: Path, sql: str, params=()):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        found = conn.execute(sql, params).fetchone()
        return dict(found) if found else None
    finally:
        conn.close()


def _insert_track_and_state(db_path: Path, old_path: Path, *, stale_path: Path | None = None):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tracks(filepath, filename, status) VALUES (?, ?, ?)",
            (str(old_path), old_path.name, "ok"),
        )
        conn.execute(
            "INSERT INTO processed_state(stage, filepath, file_size, file_mtime, status, processed_at, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("library-organize", str(old_path), 1, 0, "success", "2026-05-05T00:00:00+00:00", ""),
        )
        if stale_path is not None:
            conn.execute(
                "INSERT INTO processed_state(stage, filepath, file_size, file_mtime, status, processed_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("metadata-sanitize", str(stale_path), 1, 0, "stale", "2026-05-05T00:00:00+00:00", ""),
            )
        conn.commit()
    finally:
        conn.close()


def test_update_track_path_references_successful_update(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    old_path = _audio(tmp_path / "old.mp3")
    new_path = tmp_path / "new.mp3"
    _insert_track_and_state(db_path, old_path)

    result = db.update_track_path_references(old_path, new_path, "test")

    assert result["status"] == "updated"
    assert result["tracks_updated"] == 1
    assert result["processed_state_updated"] == 1
    assert _row(db_path, "SELECT filepath, filename FROM tracks WHERE filepath=?", (str(new_path),)) == {
        "filepath": str(new_path),
        "filename": "new.mp3",
    }
    assert _row(
        db_path,
        "SELECT filepath FROM processed_state WHERE stage='library-organize'",
    )["filepath"] == str(new_path)


def test_update_track_path_references_skips_missing_old_path(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    old_path = tmp_path / "missing.mp3"
    new_path = tmp_path / "new.mp3"

    result = db.update_track_path_references(old_path, new_path, "test")

    assert result["status"] == "skipped"
    assert result["reason"] == "old_path_not_found"
    assert _row(db_path, "SELECT COUNT(*) AS n FROM tracks")["n"] == 0


def test_update_track_path_references_skips_new_path_collision(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    old_path = _audio(tmp_path / "old.mp3")
    new_path = _audio(tmp_path / "new.mp3")
    _insert_track_and_state(db_path, old_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO tracks(filepath, filename, status) VALUES (?, ?, ?)",
            (str(new_path), new_path.name, "ok"),
        )
        conn.commit()
    finally:
        conn.close()

    result = db.update_track_path_references(old_path, new_path, "test")

    assert result["status"] == "skipped"
    assert result["reason"] == "new_path_tracks_collision"
    assert _row(db_path, "SELECT filepath FROM tracks WHERE filepath=?", (str(old_path),)) is not None


def test_update_track_path_references_does_not_modify_stale_rows(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    old_path = _audio(tmp_path / "old.mp3")
    new_path = tmp_path / "new.mp3"
    _insert_track_and_state(db_path, old_path, stale_path=old_path)

    result = db.update_track_path_references(old_path, new_path, "test")

    assert result["status"] == "updated"
    stale = _row(db_path, "SELECT filepath FROM processed_state WHERE status='stale'")
    assert stale["filepath"] == str(old_path)


def test_update_track_path_references_rolls_back_on_failure(tmp_path, monkeypatch):
    db_path = _init_db(tmp_path, monkeypatch)
    old_path = _audio(tmp_path / "old.mp3")
    new_path = tmp_path / "new.mp3"
    _insert_track_and_state(db_path, old_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TRIGGER fail_pstate_update
            BEFORE UPDATE ON processed_state
            BEGIN
                SELECT RAISE(ABORT, 'simulated processed_state failure');
            END
            """
        )
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.DatabaseError):
        db.update_track_path_references(old_path, new_path, "test")

    assert _row(db_path, "SELECT filepath FROM tracks WHERE filepath=?", (str(old_path),)) is not None
    assert _row(db_path, "SELECT filepath FROM tracks WHERE filepath=?", (str(new_path),)) is None


def test_modules_call_central_path_update_helper(monkeypatch, tmp_path):
    calls = []

    def helper(old_path, new_path, context):
        calls.append((Path(old_path), Path(new_path), context))
        return {"status": "updated"}

    monkeypatch.setattr(afc.db, "update_track_path_references", helper)
    afc._update_db(str(tmp_path / "old.mp3"), str(tmp_path / "new.mp3"), artist=None)
    assert calls == [(tmp_path / "old.mp3", tmp_path / "new.mp3", "artist_folder_clean")]

    assert "update_track_path_references" in Path(artist_merge.__file__).read_text(encoding="utf-8")
    assert "DELETE FROM tracks" not in Path(artist_merge.__file__).read_text(encoding="utf-8")
    assert "update_track_path_references" in Path(library_organize.__file__).read_text(encoding="utf-8")
