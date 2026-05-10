from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

import pipeline
from modules import metadata_sanitation


def _create_db(root: Path) -> Path:
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
            bpm REAL,
            key_musical TEXT,
            key_camelot TEXT,
            parse_confidence TEXT,
            status TEXT NOT NULL DEFAULT 'ok'
        )
        """
    )
    rows = [
        ("Saxophone MaciaDownloads.mp3", "Known Artist", "Saxophone MaciaDownloads", 120.0, "8A"),
        ("Woman Woman AlbumVersion.mp3", "Known Artist", "Woman Woman AlbumVersion", 121.0, "9A"),
        ("TrackName fordjonly.com.mp3", "Known Artist", "TrackName fordjonly.com", 122.0, "10A"),
    ]
    for filename, artist, title, bpm, key in rows:
        conn.execute(
            """
            INSERT INTO tracks (
                filepath, filename, artist, title, bpm, key_musical, key_camelot,
                parse_confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'HIGH', 'ok')
            """,
            (
                str(root / "library" / filename),
                filename,
                artist,
                title,
                bpm,
                key,
                key,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _items(root: Path) -> list[dict]:
    result = metadata_sanitation.scan(root)
    assert Path(result["queue_path"]).exists()
    return metadata_sanitation.load_queue(root)


def test_maciadownloads_suffix_proposes_clean_title(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Saxophone MaciaDownloads.mp3")

    assert proposal["proposed"]["title"] == "Saxophone"
    assert proposal["confidence"] == "HIGH"
    assert proposal["risk_flags"] == ["junk_suffix_removed"]
    assert proposal["fields"]["title"]["original_proposed"] == "Saxophone"


def test_album_version_cleanup_preserves_duplicate_title_words(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Woman Woman AlbumVersion.mp3")

    assert proposal["proposed"]["title"] == "Woman Woman"
    assert proposal["confidence"] == "LOW"
    assert proposal["risk_flags"] == ["ambiguous_version_cleanup"]


def test_source_token_removed_from_title(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "TrackName fordjonly.com.mp3")

    assert proposal["proposed"]["title"] == "TrackName"
    assert proposal["confidence"] == "LOW"
    assert proposal["risk_flags"] == ["source_token_removed"]


def test_editable_proposal_saved_and_apply_uses_edit_without_bpm_key_changes(tmp_path):
    root = tmp_path / "root"
    db_path = _create_db(root)
    _items(root)
    track_id = _track_id(db_path, "Saxophone MaciaDownloads.mp3")

    state = metadata_sanitation.set_field_proposal(root, track_id, "title", "Edited Saxophone")
    field_state = state["items"][str(track_id)]["fields"]["title"]
    assert field_state["proposed"] == "Edited Saxophone"
    assert field_state["original_proposed"] == "Saxophone"
    assert field_state["edited"] is True

    metadata_sanitation.set_field_review_status(root, track_id, "title", "approved")
    result = metadata_sanitation.apply_approved(root, apply=True)
    assert result["applied_field_count"] == 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT title, bpm, key_musical, key_camelot FROM tracks WHERE id = ?", (track_id,)).fetchone()
    conn.close()
    assert row["title"] == "Edited Saxophone"
    assert row["bpm"] == 120.0
    assert row["key_musical"] == "8A"
    assert row["key_camelot"] == "8A"


def test_no_op_suppression_on_rescan(tmp_path):
    root = tmp_path / "root"
    db_path = _create_db(root)
    _items(root)
    track_id = _track_id(db_path, "Saxophone MaciaDownloads.mp3")
    metadata_sanitation.set_field_review_status(root, track_id, "title", "approved")
    metadata_sanitation.apply_approved(root, apply=True)

    metadata_sanitation.scan(root)
    items = metadata_sanitation.load_queue(root)

    assert all(item["track_id"] != track_id for item in items)


def test_empty_edited_proposal_rejected(tmp_path):
    root = tmp_path / "root"
    db_path = _create_db(root)
    _items(root)
    track_id = _track_id(db_path, "Saxophone MaciaDownloads.mp3")

    try:
        metadata_sanitation.set_field_proposal(root, track_id, "title", "   ")
    except ValueError as exc:
        assert "cannot be empty" in str(exc)
    else:
        raise AssertionError("empty sanitation proposal edit was not rejected")


def _track_id(db_path: Path, filename: str) -> int:
    conn = sqlite3.connect(db_path)
    track_id = conn.execute("SELECT id FROM tracks WHERE filename = ?", (filename,)).fetchone()[0]
    conn.close()
    return int(track_id)


def test_pipeline_help_includes_metadata_sanitation_commands(capsys, monkeypatch):
    with pytest.raises(SystemExit) as excinfo:
        monkeypatch.setattr(sys, "argv", ["pipeline.py", "--help"])
        pipeline.main()
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "metadata-sanitation-scan" in out
    assert "metadata-sanitation-apply" in out


def test_pipeline_dispatches_metadata_sanitation_commands(monkeypatch):
    calls: list[tuple[str, bool, bool]] = []

    def fake_scan(args):
        calls.append(("scan", getattr(args, "apply", False), getattr(args, "yes", False)))
        return 0

    def fake_apply(args):
        calls.append(("apply", getattr(args, "apply", False), getattr(args, "yes", False)))
        return 0

    monkeypatch.setattr(pipeline, "run_metadata_sanitation_scan", fake_scan)
    monkeypatch.setattr(pipeline, "run_metadata_sanitation_apply", fake_apply)

    with pytest.raises(SystemExit) as excinfo:
        monkeypatch.setattr(sys, "argv", ["pipeline.py", "metadata-sanitation-scan", "--root", "/tmp/root"])
        pipeline.main()
    assert excinfo.value.code == 0

    with pytest.raises(SystemExit) as excinfo:
        monkeypatch.setattr(
            sys,
            "argv",
            ["pipeline.py", "metadata-sanitation-apply", "--root", "/tmp/root", "--apply", "--yes"],
        )
        pipeline.main()
    assert excinfo.value.code == 0

    assert calls == [
        ("scan", False, False),
        ("apply", True, True),
    ]
