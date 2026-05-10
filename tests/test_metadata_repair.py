from __future__ import annotations

import sqlite3
from pathlib import Path

from modules import metadata_repair


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
        ("Alpha - Missing Artist (Club Mix).mp3", None, "Old Title", 120.0, "8A", "HIGH"),
        ("Beta - Missing Title.mp3", "Beta", None, 121.0, "9A", "HIGH"),
        ("11. Bontan - Clean Track.mp3", "11. Bontan", "Clean Track", 121.5, "9B", "HIGH"),
        ("Gamma - Cleanup.mp3", "11. Manoo Remix", "Cleanup", 122.0, "10A", "MEDIUM"),
        ("Manoo - Better Track.mp3", "Track Lists", "Junk", 123.0, "11A", "HIGH"),
        ("weakfilename.mp3", None, None, 124.0, "12A", "LOW"),
        ("19. Anza, Chumee - Sing It Back (Extended Mix) (fordjonly.com).mp3", None, None, 125.0, "1A", "LOW"),
    ]
    for filename, artist, title, bpm, key, confidence in rows:
        conn.execute(
            """
            INSERT INTO tracks (
                filepath, filename, artist, title, bpm, key_musical, key_camelot,
                parse_confidence, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ok')
            """,
            (
                str(root / "library" / filename),
                filename,
                artist,
                title,
                bpm,
                key,
                key,
                confidence,
            ),
        )
    conn.commit()
    conn.close()
    return db_path


def _items(root: Path) -> list[dict]:
    result = metadata_repair.scan(root)
    assert Path(result["queue_path"]).exists()
    return metadata_repair.load_queue(root)


def test_missing_artist_from_filename_proposal(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3")

    assert proposal["proposed"]["artist"] == "Alpha"
    assert proposal["proposed"]["title"] == "Missing Artist (Club Mix)"
    assert proposal["fields"]["artist"]["current"] is None
    assert proposal["fields"]["artist"]["proposed"] == "Alpha"
    assert proposal["fields"]["artist"]["status"] == "pending"
    assert proposal["confidence"] == "HIGH"
    assert proposal["confidence_reason"] == "clean artist-title parse"
    assert proposal["risk_flags"] == []


def test_missing_title_from_filename_proposal(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Beta - Missing Title.mp3")

    assert proposal["proposed"]["artist"] == "Beta"
    assert proposal["proposed"]["title"] == "Missing Title"
    assert proposal["fields"]["title"]["current"] is None
    assert proposal["fields"]["title"]["proposed"] == "Missing Title"
    assert proposal["confidence"] == "HIGH"
    assert proposal["confidence_reason"] == "clean artist-title parse"
    assert proposal["risk_flags"] == []


def test_clean_numbered_artist_cleanup_remains_medium(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "11. Bontan - Clean Track.mp3")

    assert proposal["proposed"]["artist"] == "Bontan"
    assert proposal["confidence"] == "MEDIUM"
    assert proposal["confidence_reason"] == "numbering junk stripped safely"
    assert proposal["risk_flags"] == ["numbering_junk_stripped"]


def test_manoo_remix_numbered_cleanup_is_downgraded(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Gamma - Cleanup.mp3")

    assert proposal["proposed"]["artist"] == "Manoo Remix"
    assert proposal["proposed"]["title"] == "Cleanup"
    assert proposal["confidence"] in {"REVIEW_REQUIRED", "LOW"}
    assert proposal["confidence_reason"] == "suspicious remix descriptor artist"
    assert "artist_junk_descriptor" in proposal["risk_flags"]


def test_low_confidence_skipped_or_not_auto_safe(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    metadata_repair.scan(root)
    items = metadata_repair.load_queue(root)

    assert all(item["filename"] != "weakfilename.mp3" for item in items)
    assert all(item["confidence"] in {"REVIEW_REQUIRED", "LOW"} for item in items if item["filename"] == "Gamma - Cleanup.mp3")


def test_low_confidence_filename_parse_still_proposes_values(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(
        item for item in items
        if item["filename"] == "19. Anza, Chumee - Sing It Back (Extended Mix) (fordjonly.com).mp3"
    )

    assert proposal["proposed"]["artist"] == "Anza, Chumee"
    assert proposal["proposed"]["title"] == "Sing It Back (Extended Mix)"
    assert proposal["fields"]["artist"]["proposed"] == "Anza, Chumee"
    assert proposal["fields"]["title"]["proposed"] == "Sing It Back (Extended Mix)"
    assert proposal["confidence"] == "LOW"
    assert proposal["confidence_reason"] == "piracy token detected"
    assert "numbering_junk_stripped" in proposal["risk_flags"]
    assert "piracy_token_detected" in proposal["risk_flags"]


def test_track_lists_source_is_downgraded(tmp_path):
    root = tmp_path / "root"
    _create_db(root)

    items = _items(root)
    proposal = next(item for item in items if item["filename"] == "Manoo - Better Track.mp3")

    assert proposal["proposed"]["artist"] == "Manoo"
    assert proposal["proposed"]["title"] == "Better Track"
    assert proposal["confidence"] in {"REVIEW_REQUIRED", "LOW"}
    assert proposal["confidence_reason"] == "known junk source artist"
    assert "source_artist_junk" in proposal["risk_flags"]


def test_field_level_state_tracks_partial_decisions(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    items = metadata_repair.load_queue(root)

    metadata_repair.set_field_review_status(root, items[0]["track_id"], "artist", "approved")
    metadata_repair.set_field_review_status(root, items[0]["track_id"], "title", "rejected")
    metadata_repair.set_field_review_status(root, items[1]["track_id"], "title", "approved")
    metadata_repair.set_field_review_status(root, items[2]["track_id"], "artist", "rejected")
    state = metadata_repair.load_state(root)

    first = state["items"][str(items[0]["track_id"])]
    second = state["items"][str(items[1]["track_id"])]
    third = state["items"][str(items[2]["track_id"])]

    assert first["fields"]["artist"]["status"] == "approved"
    assert first["fields"]["title"]["status"] == "rejected"
    assert first["status"] == "PARTIAL"
    assert second["fields"]["artist"]["status"] == "pending"
    assert second["fields"]["title"]["status"] == "approved"
    assert second["status"] == "PARTIAL"
    assert third["fields"]["artist"]["status"] == "rejected"
    assert third["fields"]["title"]["status"] == "pending"
    assert third["status"] == "PARTIAL"


def test_apply_updates_only_approved_fields_and_preserves_rejected_title(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    missing_artist = next(
        item for item in metadata_repair.load_queue(root)
        if item["repair_type"] == "missing_artist_from_filename"
    )
    metadata_repair.set_field_review_status(root, missing_artist["track_id"], "artist", "approved")
    metadata_repair.set_field_review_status(root, missing_artist["track_id"], "title", "rejected")

    dry_run = metadata_repair.apply_approved(root, apply=False)
    assert dry_run["proposed_count"] == 1
    assert dry_run["applied_count"] == 0

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    before = conn.execute("SELECT * FROM tracks WHERE id = ?", (missing_artist["track_id"],)).fetchone()
    assert before["artist"] is None
    assert before["bpm"] == 120.0
    assert before["key_musical"] == "8A"
    conn.close()

    applied = metadata_repair.apply_approved(root, apply=True)
    assert applied["applied_count"] == 1

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    after = conn.execute("SELECT * FROM tracks WHERE id = ?", (missing_artist["track_id"],)).fetchone()
    other = conn.execute("SELECT * FROM tracks WHERE filename = ?", ("Beta - Missing Title.mp3",)).fetchone()
    conn.close()

    assert after["artist"] == "Alpha"
    assert after["title"] == "Old Title"
    assert after["bpm"] == 120.0
    assert after["key_musical"] == "8A"
    assert after["key_camelot"] == "8A"
    assert other["title"] is None


def test_title_only_approval_updates_title_only(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    missing_title = next(
        item for item in metadata_repair.load_queue(root)
        if item["repair_type"] == "missing_title_from_filename"
    )
    metadata_repair.set_field_review_status(root, missing_title["track_id"], "artist", "rejected")
    metadata_repair.set_field_review_status(root, missing_title["track_id"], "title", "approved")

    applied = metadata_repair.apply_approved(root, apply=True)
    assert applied["applied_count"] == 1

    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM tracks WHERE id = ?", (missing_title["track_id"],)).fetchone()
    conn.close()

    assert row["artist"] == "Beta"
    assert row["title"] == "Missing Title"


def test_edited_proposed_artist_and_title_are_saved(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = next(
        item for item in metadata_repair.load_queue(root)
        if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3"
    )

    track_id = item["track_id"]
    metadata_repair.set_field_proposal(root, track_id, "artist", "Edited Alpha")
    metadata_repair.set_field_proposal(root, track_id, "title", "Edited Title")
    updated = next(item for item in metadata_repair.load_queue(root) if item["track_id"] == track_id)

    assert updated["fields"]["artist"]["proposed"] == "Edited Alpha"
    assert updated["fields"]["artist"]["original_proposed"] == "Alpha"
    assert updated["fields"]["artist"]["edited"] is True
    assert updated["fields"]["title"]["proposed"] == "Edited Title"
    assert updated["fields"]["title"]["original_proposed"] == "Missing Artist (Club Mix)"
    assert updated["fields"]["title"]["edited"] is True


def test_apply_uses_edited_proposed_value(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = next(
        item for item in metadata_repair.load_queue(root)
        if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3"
    )

    metadata_repair.set_field_proposal(root, item["track_id"], "artist", "Edited Alpha")
    metadata_repair.set_field_review_status(root, item["track_id"], "artist", "approved")
    applied = metadata_repair.apply_approved(root, apply=True)

    assert applied["applied_count"] == 1
    conn = sqlite3.connect(root / "logs" / "processed.db")
    row = conn.execute("SELECT artist, title, bpm, key_musical FROM tracks WHERE id = ?", (item["track_id"],)).fetchone()
    conn.close()
    assert row[0] == "Edited Alpha"
    assert row[1] == "Old Title"
    assert row[2] == 120.0
    assert row[3] == "8A"


def test_apply_marks_fields_applied_and_hides_applied_rows_by_default(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = next(
        item for item in metadata_repair.load_queue(root)
        if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3"
    )
    metadata_repair.set_field_review_status(root, item["track_id"], "artist", "approved")
    metadata_repair.set_field_review_status(root, item["track_id"], "title", "approved")

    applied = metadata_repair.apply_approved(root, apply=True)
    assert applied["applied_count"] == 1
    assert applied["applied_field_count"] == 2

    state = metadata_repair.load_state(root)
    field = state["items"][str(item["track_id"])]["fields"]["artist"]
    assert field["status"] == "applied"
    assert field["previous_value"] is None
    assert field["applied_value"] == "Alpha"
    assert field["applied_at"]

    active = metadata_repair.queue_response(root)
    assert all(row["track_id"] != item["track_id"] for row in active["items"])

    visible = metadata_repair.queue_response(root, include_applied=True)
    applied_row = next(row for row in visible["items"] if row["track_id"] == item["track_id"])
    assert applied_row["status"] == "APPLIED"
    assert applied_row["fields"]["artist"]["status"] == "applied"


def test_no_op_rows_hidden_by_default_and_visible_with_toggle(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = next(
        item for item in metadata_repair.load_queue(root)
        if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3"
    )
    conn = sqlite3.connect(root / "logs" / "processed.db")
    conn.execute(
        "UPDATE tracks SET artist = ?, title = ? WHERE id = ?",
        ("Alpha", "Missing Artist (Club Mix)", item["track_id"]),
    )
    conn.commit()
    conn.close()

    active = metadata_repair.queue_response(root)
    assert all(row["track_id"] != item["track_id"] for row in active["items"])

    visible = metadata_repair.queue_response(root, include_applied=True)
    row = next(row for row in visible["items"] if row["track_id"] == item["track_id"])
    assert row["status"] == "NO_OP"
    assert row["fields"]["artist"]["status"] == "no_op"


def test_rescan_skips_no_op_after_apply(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = next(
        item for item in metadata_repair.load_queue(root)
        if item["filename"] == "Alpha - Missing Artist (Club Mix).mp3"
    )
    metadata_repair.set_field_review_status(root, item["track_id"], "artist", "approved")
    metadata_repair.apply_approved(root, apply=True)

    metadata_repair.scan(root)
    rescanned = metadata_repair.load_queue(root)
    assert all(row["track_id"] != item["track_id"] for row in rescanned)


def test_empty_edited_proposal_rejected(tmp_path):
    root = tmp_path / "root"
    _create_db(root)
    metadata_repair.scan(root)
    item = metadata_repair.load_queue(root)[0]

    try:
        metadata_repair.set_field_proposal(root, item["track_id"], "artist", "   ")
    except ValueError as exc:
        assert "cannot be empty" in str(exc)
    else:
        raise AssertionError("empty proposal should be rejected")
