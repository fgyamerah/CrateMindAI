from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline


class _FakeAudio:
    def __init__(self, tags: dict[str, str], *, length: float, bitrate: int):
        self._tags = tags
        self.info = SimpleNamespace(length=length, bitrate=bitrate)

    def get(self, key: str):
        value = self._tags.get(key)
        if value is None:
            return None
        return [value]

    @property
    def tags(self):
        return self


def _make_file(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _create_db(root: Path, rows: list[dict]) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT,
            title TEXT,
            album TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            duration_sec REAL,
            bitrate_kbps INTEGER,
            parse_confidence TEXT,
            status TEXT NOT NULL DEFAULT 'pending'
        )
        """
    )
    for row in rows:
        filepath = str(row["filepath"])
        conn.execute(
            """
            INSERT INTO tracks (
                filepath, filename, artist, title, album, genre, bpm,
                key_musical, key_camelot, duration_sec, bitrate_kbps, parse_confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filepath,
                row.get("filename", Path(filepath).name),
                row.get("artist"),
                row.get("title"),
                row.get("album"),
                row.get("genre"),
                row.get("bpm"),
                row.get("key_musical"),
                row.get("key_camelot"),
                row.get("duration_sec"),
                row.get("bitrate_kbps"),
                row.get("parse_confidence"),
                row.get("status", "ok"),
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _read_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute("SELECT * FROM tracks ORDER BY id")]
    finally:
        conn.close()


def _run(root: Path, *, apply: bool = False, yes: bool = False, verbose: bool = False):
    return pipeline.run_extract_track_metadata(
        SimpleNamespace(root=str(root), apply=apply, yes=yes, verbose=verbose)
    )


def _patch_mutagen(monkeypatch, mapping: dict[str, _FakeAudio]):
    import mutagen

    def fake_file(path, easy=False):
        key = str(Path(path))
        if key not in mapping:
            raise OSError(f"unreadable: {key}")
        return mapping[key]

    monkeypatch.setattr(mutagen, "File", fake_file)


def test_extracts_metadata_correctly(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "track-a.mp3")
    db_path = _create_db(
        root,
        [
            {"filepath": track, "filename": "track-a.mp3"},
        ],
    )
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "artist": "Artist A",
                    "title": "Track A",
                    "album": "Album A",
                    "genre": "House",
                    "bpm": "124",
                    "key": "A minor",
                },
                length=301.2,
                bitrate=320000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["artist"] == "Artist A"
    assert row["title"] == "Track A"
    assert row["album"] == "Album A"
    assert row["genre"] == "House"
    assert row["bpm"] == 124.0
    assert row["key_musical"] == "A minor"
    assert row["duration_sec"] == pytest.approx(301.2)
    assert row["bitrate_kbps"] == 320


def test_dry_run_does_not_write_db(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "track-b.mp3")
    db_path = _create_db(root, [{"filepath": track, "filename": "track-b.mp3"}])
    before = db_path.read_bytes()
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "artist": "Artist B",
                    "title": "Track B",
                    "album": "Album B",
                    "genre": "Techno",
                    "bpm": "126",
                    "key": "8A",
                },
                length=302.4,
                bitrate=256000,
            )
        },
    )

    assert _run(root) == 0

    assert db_path.read_bytes() == before
    row = _read_rows(db_path)[0]
    assert row["artist"] is None
    assert row["bpm"] is None


def test_existing_bpm_and_key_are_preserved(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "track-c.mp3")
    db_path = _create_db(
        root,
        [
            {
                "filepath": track,
                "filename": "track-c.mp3",
                "artist": None,
                "title": None,
                "album": None,
                "genre": None,
                "bpm": 127.0,
                "key_musical": "G minor",
                "key_camelot": "6A",
                "duration_sec": None,
                "bitrate_kbps": None,
            }
        ],
    )
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "artist": "Artist C",
                    "title": "Track C",
                    "album": "Album C",
                    "genre": "Deep House",
                    "bpm": "130",
                    "key": "A minor",
                },
                length=304.8,
                bitrate=192000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["bpm"] == 127.0
    assert row["key_musical"] == "G minor"
    assert row["key_camelot"] == "6A"
    assert row["artist"] == "Artist C"
    assert row["title"] == "Track C"
    assert row["album"] == "Album C"
    assert row["genre"] == "Deep House"


def test_unreadable_file_handled_safely(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    missing = root / "music" / "missing.mp3"
    db_path = _create_db(root, [{"filepath": missing, "filename": "missing.mp3"}])

    assert _run(root) == 0

    assert _read_rows(db_path)[0]["artist"] is None
    log_path = sorted((root / "logs" / "metadata_extract").glob("*_extract.log"))[-1]
    log_text = log_path.read_text(encoding="utf-8")
    assert "unreadable_files=1" in log_text


def test_filename_fallback_extracts_clean_metadata(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "C Minor - Kunapendeza feat. Alai K.mp3")
    db_path = _create_db(root, [{"filepath": track, "filename": track.name}])
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "album": "Album D",
                    "genre": "Amapiano",
                    "bpm": "122",
                    "key": "6A",
                },
                length=299.1,
                bitrate=256000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["artist"] == "C Minor"
    assert row["title"] == "Kunapendeza feat. Alai K"
    assert row["parse_confidence"] in {"HIGH", "MEDIUM"}


def test_filename_version_is_preserved_in_extracted_title(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "Javier Mio - Ampreiah (Original Mix).aif")
    db_path = _create_db(root, [{"filepath": track, "filename": track.name}])
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "album": "Album D2",
                    "genre": "Deep House",
                },
                length=301.4,
                bitrate=192000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["artist"] == "Javier Mio"
    assert row["title"] == "Ampreiah (Original Mix)"
    assert row["parse_confidence"] in {"HIGH", "MEDIUM"}


def test_malformed_filename_is_rejected_safely(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "including Manoo Remix, Original Instrumental.mp3")
    db_path = _create_db(root, [{"filepath": track, "filename": track.name}])
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "album": "Album E",
                    "genre": "House",
                    "bpm": "124",
                    "key": "8A",
                },
                length=300.0,
                bitrate=320000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["artist"] is None
    assert row["title"] is None
    assert row["parse_confidence"] == "LOW"


def test_valid_embedded_tags_are_preserved(tmp_path, monkeypatch):
    root = tmp_path / "library"
    root.mkdir()
    track = _make_file(root / "music" / "wrong-name.mp3")
    db_path = _create_db(root, [{"filepath": track, "filename": track.name}])
    _patch_mutagen(
        monkeypatch,
        {
            str(track): _FakeAudio(
                {
                    "artist": "Javier Mio",
                    "title": "Ampreiah (Original Mix)",
                    "album": "Album F",
                    "genre": "Deep House",
                    "bpm": "128",
                    "key": "B minor",
                },
                length=301.0,
                bitrate=192000,
            )
        },
    )

    assert _run(root, apply=True, yes=True) == 0

    row = _read_rows(db_path)[0]
    assert row["artist"] == "Javier Mio"
    assert row["title"] == "Ampreiah (Original Mix)"
    assert row["parse_confidence"] == "HIGH"
