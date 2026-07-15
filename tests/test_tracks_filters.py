"""Route tests for /api/tracks filtering, sorting, and pagination (Phase C)."""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main


@pytest.fixture()
def client() -> TestClient:
    return TestClient(backend_main.app)


@pytest.fixture()
def seeded_root(tmp_path, monkeypatch):
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(tmp_path))
    db_path = tmp_path / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT NOT NULL UNIQUE,
            filename TEXT NOT NULL,
            artist TEXT, title TEXT, album TEXT, genre TEXT,
            bpm REAL, key_musical TEXT, key_camelot TEXT,
            duration_sec REAL, bitrate_kbps INTEGER, filesize_bytes INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            error_msg TEXT, processed_at TEXT, pipeline_ver TEXT,
            quality_tier TEXT, parse_confidence TEXT
        )
        """
    )
    rows = [
        (str(tmp_path / "library/A/Alpha/a.mp3"), "a.mp3", "Alpha", "One",
         None, "House", 120.0, "Am", "8A", 300.0, 320, 111, "ok", None,
         "2026-05-01T10:00:00Z", "1.4.0", "HIGH", "HIGH"),
        (str(tmp_path / "library/B/Beta/b.mp3"), "b.mp3", "Beta", "Two",
         None, "Techno", 128.0, "Em", "9A", 200.0, 192, 222, "ok", None,
         "2026-05-02T10:00:00Z", "1.4.0", "HIGH", "MEDIUM"),
        (str(tmp_path / "library/B/Beta/c.mp3"), "c.mp3", "Beta", "Three",
         None, "Techno", None, None, None, 250.0, 256, 333, "needs_review",
         None, "2026-05-03T10:00:00Z", "1.4.0", "MEDIUM", "LOW"),
        (str(tmp_path / "inbox/d.mp3"), "d.mp3", None, "Four",
         None, "House", 95.0, None, None, 180.0, 320, 444, "needs_review",
         None, "2026-05-04T10:00:00Z", "1.4.0", "LOW", "LOW"),
    ]
    conn.executemany(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, album, genre, bpm, key_musical,
            key_camelot, duration_sec, bitrate_kbps, filesize_bytes, status,
            error_msg, processed_at, pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return tmp_path


def _items(client, qs: str):
    resp = client.get(f"/api/tracks?{qs}")
    assert resp.status_code == 200
    return resp.json()


def test_has_bpm_filter(client, seeded_root):
    body = _items(client, "has_bpm=false")
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "c.mp3"
    body = _items(client, "has_bpm=true")
    assert body["total"] == 3


def test_key_filter_matches_camelot_and_musical(client, seeded_root):
    body = _items(client, "key=9a")
    assert body["total"] == 1
    assert body["items"][0]["artist"] == "Beta"
    body = _items(client, "key=am")
    assert body["total"] == 1
    assert body["items"][0]["artist"] == "Alpha"


def test_folder_filter_stays_inside_root(client, seeded_root):
    folder = str(seeded_root / "library" / "B")
    body = _items(client, f"folder={folder}")
    assert body["total"] == 2
    # traversal outside the root is ignored rather than honored
    body = _items(client, "folder=/etc")
    assert body["total"] == 4


def test_new_sort_columns(client, seeded_root):
    body = _items(client, "sort=bitrate&order=asc")
    assert body["items"][0]["bitrate_kbps"] == 192
    body = _items(client, "sort=duration&order=desc")
    assert body["items"][0]["duration_sec"] == 300.0
    body = _items(client, "sort=genre&order=asc")
    assert body["items"][0]["genre"] == "House"


def test_invalid_sort_falls_back_to_artist(client, seeded_root):
    body = _items(client, "sort=;DROP TABLE tracks;&order=asc")
    assert body["total"] == 4
    artists = [i["artist"] for i in body["items"]]
    assert artists == sorted(artists, key=lambda a: (a or "").lower())


def test_pagination_totals_stable_with_filters(client, seeded_root):
    body = _items(client, "genre=Techno&limit=1&offset=0")
    assert body["total"] == 2
    assert len(body["items"]) == 1
    body2 = _items(client, "genre=Techno&limit=1&offset=1")
    assert body2["total"] == 2
    assert body2["items"][0]["id"] != body["items"][0]["id"]


def test_combined_filters(client, seeded_root):
    body = _items(client, "genre=Techno&has_bpm=true&bpm_min=120")
    assert body["total"] == 1
    assert body["items"][0]["filename"] == "b.mp3"
