from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from modules import enrichment_apply


def _create_tracks_db(root: Path) -> Path:
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
            label TEXT,
            isrc TEXT,
            genre TEXT,
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            duration_sec REAL,
            bitrate_kbps INTEGER,
            status TEXT NOT NULL DEFAULT 'pending',
            parse_confidence TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, album, label, isrc, genre, bpm,
            key_musical, key_camelot, duration_sec, bitrate_kbps, status, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(root / "library" / "incoming" / "apply-me.flac"),
                "apply-me.flac",
                None,
                None,
                None,
                None,
                None,
                "House",
                128.0,
                "8A",
                "08A",
                301.5,
                320,
                "ok",
                "LOW",
            ),
            (
                str(root / "library" / "incoming" / "keep-me.flac"),
                "keep-me.flac",
                "Existing Artist",
                "Existing Title",
                "Existing Album",
                "Existing Label",
                "US-KEEP-0001",
                "Techno",
                126.0,
                "9A",
                "09A",
                299.0,
                320,
                "ok",
                "HIGH",
            ),
            (
                str(root / "library" / "incoming" / "medium.flac"),
                "medium.flac",
                None,
                None,
                None,
                None,
                None,
                "House",
                124.0,
                "10A",
                "10A",
                300.0,
                320,
                "ok",
                "MEDIUM",
            ),
            (
                str(root / "library" / "incoming" / "rejected.flac"),
                "rejected.flac",
                None,
                None,
                None,
                None,
                None,
                "House",
                122.0,
                "11A",
                "11A",
                298.0,
                320,
                "ok",
                "LOW",
            ),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _write_review_state(root: Path) -> Path:
    state_path = root / "data" / "intelligence" / "enrichment_review_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": "2026-05-06T12:00:00Z",
        "queue_total": 4,
        "items": {
            "1": {
                "track_id": 1,
                "review_status": "approved",
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_item": {
                    "filepath": str(root / "library" / "incoming" / "apply-me.flac"),
                    "provider": "discogs",
                    "confidence": "HIGH",
                    "score": 0.99,
                    "best_match": {
                        "artist": "New Artist",
                        "title": "New Title",
                        "album": "New Album",
                        "label": "New Label",
                        "isrc": "US-NEW-0001",
                    },
                },
            },
            "2": {
                "track_id": 2,
                "review_status": "approved",
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_item": {
                    "filepath": str(root / "library" / "incoming" / "keep-me.flac"),
                    "provider": "discogs",
                    "confidence": "HIGH",
                    "score": 0.98,
                    "best_match": {
                        "artist": "Different Artist",
                        "title": "Different Title",
                        "album": "Different Album",
                        "label": "Different Label",
                        "isrc": "US-DIFF-0002",
                    },
                },
            },
            "3": {
                "track_id": 3,
                "review_status": "approved",
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_item": {
                    "filepath": str(root / "library" / "incoming" / "medium.flac"),
                    "provider": "musicbrainz",
                    "confidence": "MEDIUM",
                    "score": 0.81,
                    "best_match": {
                        "artist": "Medium Artist",
                        "title": "Medium Title",
                        "album": "Medium Album",
                        "label": "Medium Label",
                        "isrc": "US-MED-0003",
                    },
                },
            },
            "4": {
                "track_id": 4,
                "review_status": "rejected",
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_item": {
                    "filepath": str(root / "library" / "incoming" / "rejected.flac"),
                    "provider": "musicbrainz",
                    "confidence": "HIGH",
                    "score": 0.55,
                    "best_match": {
                        "artist": "Rejected Artist",
                        "title": "Rejected Title",
                        "album": "Rejected Album",
                        "label": "Rejected Label",
                        "isrc": "US-REJ-0004",
                    },
                },
            },
        },
    }
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return state_path


@pytest.fixture()
def enrichment_root(tmp_path: Path) -> Path:
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    _create_tracks_db(root)
    _write_review_state(root)
    return root


def _row_by_id(root: Path, track_id: int) -> sqlite3.Row:
    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()


def test_dry_run_does_not_write_db(enrichment_root: Path) -> None:
    before = dict(_row_by_id(enrichment_root, 1))

    result = enrichment_apply.apply_approved_enrichment(enrichment_root, apply=False)

    after = dict(_row_by_id(enrichment_root, 1))
    assert result["dry_run"] is True
    assert result["proposed_count"] == 1
    assert result["applied_count"] == 0
    assert result["skipped_count"] == 2
    assert before == after
    assert Path(result["log_path"]).exists()


def test_apply_updates_only_approved_high_and_preserves_bpm_key(enrichment_root: Path) -> None:
    before_keep = dict(_row_by_id(enrichment_root, 2))
    result = enrichment_apply.apply_approved_enrichment(enrichment_root, apply=True)

    updated = dict(_row_by_id(enrichment_root, 1))
    keep_row = dict(_row_by_id(enrichment_root, 2))
    medium_row = dict(_row_by_id(enrichment_root, 3))
    rejected_row = dict(_row_by_id(enrichment_root, 4))

    assert result["dry_run"] is False
    assert result["proposed_count"] == 1
    assert result["applied_count"] == 1
    assert result["skipped_count"] == 2
    assert updated["artist"] == "New Artist"
    assert updated["title"] == "New Title"
    assert updated["album"] == "New Album"
    assert updated["label"] == "New Label"
    assert updated["isrc"] == "US-NEW-0001"
    assert updated["bpm"] == 128.0
    assert updated["key_musical"] == "8A"
    assert updated["key_camelot"] == "08A"
    assert updated["duration_sec"] == 301.5
    assert updated["bitrate_kbps"] == 320

    assert keep_row == before_keep
    assert medium_row["artist"] is None
    assert medium_row["title"] is None
    assert rejected_row["artist"] is None
    assert rejected_row["title"] is None
    assert Path(result["log_path"]).exists()

