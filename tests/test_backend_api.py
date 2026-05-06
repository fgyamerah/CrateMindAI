from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import backend.app.main as backend_main
from backend.app.core.library_root import assert_path_under_root


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
            pipeline_ver TEXT,
            quality_tier TEXT
            ,parse_confidence TEXT
        )
        """
    )
    conn.executemany(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                str(root / "library" / "house" / "alpha.mp3"),
                "alpha.mp3",
                "Alpha",
                "First",
                "House",
                120.0,
                "8A",
                None,
                300.0,
                320,
                1234,
                "ok",
                None,
                "2026-05-05T10:00:00Z",
                "1.4.0",
                "HIGH",
                "HIGH",
            ),
            (
                str(root / "library" / "house" / "beta.mp3"),
                "beta.mp3",
                "Beta",
                "Second",
                "Techno",
                124.0,
                "9A",
                None,
                301.0,
                320,
                2222,
                "needs_review",
                None,
                "2026-05-05T11:00:00Z",
                "1.4.0",
                "MEDIUM",
                "MEDIUM",
            ),
            (
                str(root / "library" / "techno" / "gamma.mp3"),
                "gamma.mp3",
                "Gamma",
                "Third",
                "House",
                126.0,
                "10A",
                None,
                302.0,
                320,
                3333,
                "error",
                "bad file",
                "2026-05-05T12:00:00Z",
                "1.4.0",
                "LOW",
                "LOW",
            ),
            (
                str(root / "library" / "misc" / "delta.mp3"),
                "delta.mp3",
                "Music Corp",
                "Downloads",
                "House",
                None,
                None,
                None,
                303.0,
                320,
                4444,
                "ok",
                None,
                "2026-05-05T13:00:00Z",
                "1.4.0",
                "HIGH",
                "LOW",
            ),
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def _write_audit(root: Path) -> Path:
    audit_dir = root / "logs" / "path_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    audit_path = audit_dir / "path_audit_20260505_130000.json"
    audit_payload = {
        "summary": {
            "disk_audio_files": 12,
            "missing_files": 2,
            "untracked_files": 3,
            "stale_processed_state_rows_total": 4,
            "canonical_source": "tracks",
        },
        "root": str(root),
    }
    audit_path.write_text(json.dumps(audit_payload), encoding="utf-8")
    return audit_path


def _write_queue(root: Path) -> Path:
    queue_path = root / "data" / "intelligence" / "enrichment_review_queue.jsonl"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "filepath": str(root / "library" / "house" / "alpha.mp3"),
                        "confidence": "HIGH",
                        "action_suggestion": "auto_candidate",
                        "score": 0.98,
                    }
                ),
                json.dumps(
                    {
                        "filepath": str(root / "library" / "house" / "beta.mp3"),
                        "confidence": "MEDIUM",
                        "action_suggestion": "review",
                        "score": 0.81,
                    }
                ),
                json.dumps(
                    {
                        "filepath": str(root / "library" / "techno" / "gamma.mp3"),
                        "confidence": "LOW",
                        "action_suggestion": "ignore",
                        "score": 0.32,
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return queue_path


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)
    _create_tracks_db(root)
    _write_audit(root)
    _write_queue(root)
    with TestClient(backend_main.app) as test_client:
        yield test_client, root


def test_health_endpoint_reports_selected_root_and_db(client):
    test_client, root = client

    response = test_client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "ok": True,
        "library_root": str(root.resolve()),
        "db_path": str((root / "logs" / "processed.db").resolve()),
        "db_exists": True,
    }


def test_tracks_pagination_and_search(client):
    test_client, root = client

    response = test_client.get("/api/tracks", params={"limit": 1, "offset": 1})
    payload = response.json()

    assert response.status_code == 200
    assert payload["limit"] == 1
    assert payload["offset"] == 1
    assert payload["total"] == 4
    assert len(payload["items"]) == 1
    assert payload["items"][0]["artist"] == "Beta"

    search_response = test_client.get("/api/tracks", params={"search": "Gamma"})
    search_payload = search_response.json()
    assert search_response.status_code == 200
    assert search_payload["total"] == 1
    assert search_payload["items"][0]["filepath"] == str(root / "library" / "techno" / "gamma.mp3")


def test_track_filters_cover_issue_bpm_key_genre_and_parse_confidence(client):
    test_client, root = client

    issue_response = test_client.get("/api/tracks", params={"issue": "weak_filename_parse"})
    issue_payload = issue_response.json()
    assert issue_response.status_code == 200
    assert issue_payload["total"] == 3

    suspicious_response = test_client.get("/api/tracks", params={"issue": "suspicious_artist"})
    suspicious_payload = suspicious_response.json()
    assert suspicious_response.status_code == 200
    assert suspicious_payload["total"] == 1
    assert suspicious_payload["items"][0]["filepath"] == str(root / "library" / "misc" / "delta.mp3")

    bpm_response = test_client.get("/api/tracks", params={"bpm_min": 125})
    bpm_payload = bpm_response.json()
    assert bpm_response.status_code == 200
    assert bpm_payload["total"] == 1
    assert bpm_payload["items"][0]["artist"] == "Gamma"

    key_response = test_client.get("/api/tracks", params={"has_key": False})
    key_payload = key_response.json()
    assert key_response.status_code == 200
    assert key_payload["total"] == 1
    assert key_payload["items"][0]["artist"] == "Music Corp"

    genre_response = test_client.get("/api/tracks", params={"genre": "house", "parse_confidence": "HIGH"})
    genre_payload = genre_response.json()
    assert genre_response.status_code == 200
    assert genre_payload["total"] == 1
    assert genre_payload["items"][0]["artist"] == "Alpha"


def test_track_issues_return_grouped_counts(client):
    test_client, root = client

    response = test_client.get("/api/tracks/issues", params={"limit": 10})
    payload = response.json()

    assert response.status_code == 200
    assert payload == {
        "missing_artist": 0,
        "missing_title": 0,
        "weak_filename_parse": 3,
        "suspicious_artist": 1,
        "suspicious_title": 1,
    }


def test_enrichment_queue_filtering(client):
    test_client, _root = client

    response = test_client.get(
        "/api/enrichment/queue",
        params={"action": "review", "confidence": "MEDIUM"},
    )
    payload = response.json()

    assert response.status_code == 200
    assert payload["total"] == 1
    assert payload["counts"] == {
        "by_action": {"review": 1},
        "by_confidence": {"MEDIUM": 1},
    }
    assert payload["items"][0]["action_suggestion"] == "review"
    assert payload["items"][0]["confidence"] == "MEDIUM"


def test_enrichment_review_state_endpoints_persist_and_echo(client):
    test_client, root = client
    state_path = root / "data" / "intelligence" / "enrichment_review_state.json"

    empty_state = test_client.get("/api/enrichment/review/state")
    assert empty_state.status_code == 200
    assert empty_state.json()["approved"] == []
    assert empty_state.json()["rejected"] == []
    assert empty_state.json()["deferred"] == []

    approve = test_client.post("/api/enrichment/review/1/approve")
    reject = test_client.post("/api/enrichment/review/2/reject")
    defer = test_client.post("/api/enrichment/review/3/defer")

    assert approve.status_code == 200
    assert reject.status_code == 200
    assert defer.status_code == 200
    assert approve.json()["review_status"] == "approved"
    assert reject.json()["review_status"] == "rejected"
    assert defer.json()["review_status"] == "deferred"
    assert state_path.exists()

    state_payload = test_client.get("/api/enrichment/review/state").json()
    assert state_payload["approved"] == [1]
    assert state_payload["rejected"] == [2]
    assert state_payload["deferred"] == [3]
    assert state_payload["counts"] == {"approved": 1, "rejected": 1, "deferred": 1}
    assert state_payload["items"]["1"]["review_status"] == "approved"
    assert state_payload["items"]["2"]["review_status"] == "rejected"
    assert state_payload["items"]["3"]["review_status"] == "deferred"

    queue_payload = test_client.get("/api/enrichment/queue").json()
    review_map = {item["track_id"]: item["review_status"] for item in queue_payload["items"]}
    assert review_map[1] == "approved"
    assert review_map[2] == "rejected"
    assert review_map[3] == "deferred"

    track_payload = test_client.get("/api/tracks/1").json()
    assert track_payload["enrichment_queue_item"]["review_status"] == "approved"


def test_enrichment_review_export_and_summary(client):
    test_client, _root = client

    test_client.post("/api/enrichment/review/1/approve")
    test_client.post("/api/enrichment/review/2/reject")
    test_client.post("/api/enrichment/review/3/defer")

    export_response = test_client.get("/api/enrichment/review/export")
    assert export_response.status_code == 200
    assert "attachment" in export_response.headers.get("content-disposition", "")
    export_payload = export_response.json()
    assert export_payload["approved"] == [1]
    assert export_payload["rejected"] == [2]
    assert export_payload["deferred"] == [3]
    assert export_payload["counts"] == {"approved": 1, "rejected": 1, "deferred": 1}
    assert export_payload["updated_at"] is not None

    summary_response = test_client.get("/api/enrichment/review/summary")
    assert summary_response.status_code == 200
    summary = summary_response.json()
    assert summary["pending_count"] == 0
    assert summary["approved_count"] == 1
    assert summary["rejected_count"] == 1
    assert summary["deferred_count"] == 1
    assert summary["approved_high_count"] == 1
    assert summary["approved_medium_count"] == 0
    assert summary["rejected_by_reason"] == {}
    assert summary["last_updated"] is not None


def test_enrichment_apply_approved_endpoints_require_confirm_and_apply(client):
    test_client, root = client

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.execute("ALTER TABLE tracks ADD COLUMN album TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN label TEXT")
    conn.execute("ALTER TABLE tracks ADD COLUMN isrc TEXT")
    conn.execute(
        """
        INSERT INTO tracks (
            filepath, filename, artist, title, genre, bpm, key_musical, key_camelot,
            duration_sec, bitrate_kbps, filesize_bytes, status, error_msg, processed_at,
            pipeline_ver, quality_tier, parse_confidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(root / "library" / "incoming" / "apply-endpoint.flac"),
            "apply-endpoint.flac",
            None,
            None,
            "House",
            127.0,
            "7A",
            "07A",
            301.0,
            320,
            5555,
            "ok",
            None,
            "2026-05-05T14:00:00Z",
            "1.4.0",
            "HIGH",
            "LOW",
        ),
    )
    track_id = conn.execute("SELECT id FROM tracks WHERE filepath = ?", (str(root / "library" / "incoming" / "apply-endpoint.flac"),)).fetchone()[0]
    conn.commit()
    conn.close()

    state_path = root / "data" / "intelligence" / "enrichment_review_state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "updated_at": "2026-05-06T12:00:00Z",
                "queue_total": 1,
                "items": {
                    str(track_id): {
                        "track_id": track_id,
                        "review_status": "approved",
                        "updated_at": "2026-05-06T12:00:00Z",
                        "queue_item": {
                            "filepath": str(root / "library" / "incoming" / "apply-endpoint.flac"),
                            "confidence": "HIGH",
                            "provider": "discogs",
                            "score": 0.99,
                            "best_match": {
                                "artist": "Applied Artist",
                                "title": "Applied Title",
                                "album": "Applied Album",
                            },
                        },
                    }
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    dry_run = test_client.post("/api/enrichment/apply-approved/dry-run")
    assert dry_run.status_code == 200
    assert dry_run.json()["proposed_count"] == 1

    missing_confirm = test_client.post("/api/enrichment/apply-approved/apply")
    assert missing_confirm.status_code == 400

    apply_response = test_client.post("/api/enrichment/apply-approved/apply", params={"confirm": True})
    assert apply_response.status_code == 200
    payload = apply_response.json()
    assert payload["applied_count"] == 1
    assert payload["proposed_count"] == 1

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    finally:
        conn.close()
    assert row["artist"] == "Applied Artist"
    assert row["title"] == "Applied Title"
    assert row["album"] == "Applied Album"
    assert row["label"] is None
    assert row["isrc"] is None
    assert row["bpm"] == 127.0
    assert row["key_musical"] == "7A"
    assert row["key_camelot"] == "07A"


def test_enrichment_review_is_safe_without_db(tmp_path, monkeypatch):
    root = tmp_path / "library_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)

    with TestClient(backend_main.app) as test_client:
        state_response = test_client.get("/api/enrichment/review/state")
        assert state_response.status_code == 200
        assert state_response.json()["approved"] == []

        action_response = test_client.post("/api/enrichment/review/1/approve")
        assert action_response.status_code == 404


def test_latest_audit_endpoint_returns_latest_report(client):
    test_client, root = client

    response = test_client.get("/api/audit/latest")
    payload = response.json()

    assert response.status_code == 200
    assert payload["summary"]["canonical_source"] == "tracks"
    assert payload["summary"]["disk_audio_files"] == 12
    assert payload["root"] == str(root)


def test_track_detail_includes_enrichment_info(client):
    test_client, root = client

    response = test_client.get("/api/tracks/1")
    payload = response.json()

    assert response.status_code == 200
    assert payload["filesystem_path"] == str(root / "library" / "house" / "alpha.mp3")
    assert payload["parse_confidence"] == "HIGH"
    assert payload["enrichment_queue_item"]["action_suggestion"] == "auto_candidate"


def test_stats_endpoint_uses_latest_audit_without_scanning(client):
    test_client, _root = client

    response = test_client.get("/api/stats")
    payload = response.json()

    assert response.status_code == 200
    assert payload["tracks_count"] == 4
    assert payload["disk_audio_files"] == 12
    assert payload["missing_files"] == 2
    assert payload["untracked_files"] == 3
    assert payload["stale_processed_state_total"] == 4
    assert payload["canonical_source"] == "tracks"
    assert payload["last_audit_report"]["summary"]["canonical_source"] == "tracks"


def test_library_folder_and_overview_endpoints(client):
    test_client, root = client

    folders_response = test_client.get("/api/library/folders")
    folders = folders_response.json()
    assert folders_response.status_code == 200
    assert folders == [
        {"folder": str(root / "library" / "house"), "track_count": 2, "issue_count": 1},
        {"folder": str(root / "library" / "misc"), "track_count": 1, "issue_count": 1},
        {"folder": str(root / "library" / "techno"), "track_count": 1, "issue_count": 1},
    ]

    overview_response = test_client.get("/api/library/overview")
    overview = overview_response.json()
    assert overview_response.status_code == 200
    assert overview["total_tracks"] == 4
    assert overview["tracks_with_bpm"] == 3
    assert overview["tracks_with_camelot_key"] == 3
    assert overview["tracks_missing_artist"] == 0
    assert overview["tracks_missing_title"] == 0
    assert overview["parse_confidence_breakdown"] == {"HIGH": 1, "MEDIUM": 1, "LOW": 2}
    assert overview["genre_top_counts"][0]["count"] == 3


def test_missing_db_is_handled_safely(tmp_path, monkeypatch):
    root = tmp_path / "empty_root"
    root.mkdir(parents=True)
    monkeypatch.setenv("CRATEMINDAI_LIBRARY_ROOT", str(root))
    monkeypatch.setattr(backend_main, "init_db", lambda: None)

    with TestClient(backend_main.app) as test_client:
        health = test_client.get("/api/health").json()
        stats = test_client.get("/api/stats").json()
        tracks = test_client.get("/api/tracks").json()
        audit = test_client.get("/api/audit/latest").json()
        folders = test_client.get("/api/library/folders").json()
        overview = test_client.get("/api/library/overview").json()
        issue_counts = test_client.get("/api/tracks/issues").json()

    assert health["db_exists"] is False
    assert stats["tracks_count"] == 0
    assert stats["last_audit_report"] is None
    assert tracks == {"items": [], "limit": 100, "offset": 0, "total": 0}
    assert audit == {"available": False}
    assert folders == []
    assert overview["total_tracks"] == 0
    assert issue_counts == {
        "missing_artist": 0,
        "missing_title": 0,
        "weak_filename_parse": 0,
        "suspicious_artist": 0,
        "suspicious_title": 0,
    }


def test_read_only_requests_do_not_mutate_db(client):
    test_client, root = client
    db_path = root / "logs" / "processed.db"
    before = db_path.read_bytes()

    test_client.get("/api/tracks", params={"issue": "weak_filename_parse"})
    test_client.get("/api/tracks/1")
    test_client.get("/api/library/folders")
    test_client.get("/api/library/overview")
    test_client.get("/api/tracks/issues")

    assert db_path.read_bytes() == before


def test_root_containment_rejects_traversal(tmp_path):
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(ValueError, match="path outside selected root"):
        assert_path_under_root("../escape.mp3", root)
