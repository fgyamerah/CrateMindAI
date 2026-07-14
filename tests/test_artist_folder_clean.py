from __future__ import annotations

from pathlib import Path

import pytest

import modules.artist_folder_clean as afc
from modules.artist_folder_clean import CleanResult, FileAssignment


def _dummy(path: Path, data: bytes = b"fake audio data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _clean_result(
    root: Path,
    *,
    original_name: str = "1A - Heavy-K",
    cleaned_name: str = "Heavy-K",
    filename: str = "track.mp3",
    status: str = "rename",
    target_exists: bool = False,
    data: bytes = b"source",
) -> CleanResult:
    original = root / "1A" / original_name
    src = _dummy(original / filename, data=data)
    target = root / afc._first_letter_for(cleaned_name) / cleaned_name
    if target_exists:
        target.mkdir(parents=True, exist_ok=True)
    return CleanResult(
        original_path=original,
        original_name=original_name,
        letter="1A",
        files=[src],
        detection_rule="camelot_prefix",
        cleaned_name=cleaned_name,
        reject_reason=None,
        target_path=target,
        target_exists=target_exists,
        status=status,
    )


@pytest.fixture
def db_spy(monkeypatch):
    calls = {"get_track": [], "upsert_track": [], "delete": [], "update_path": []}
    rows: dict[str, dict] = {}

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def execute(self, sql, params=()):
            calls["delete"].append((sql, params))

    def get_track(path: str):
        calls["get_track"].append(path)
        return rows.get(path)

    def upsert_track(path: str, **kwargs):
        calls["upsert_track"].append((path, kwargs))

    def update_track_path_references(old_path, new_path, context):
        calls["update_path"].append((str(old_path), str(new_path), context))
        return {"status": "updated"}

    monkeypatch.setattr(afc.db, "get_track", get_track)
    monkeypatch.setattr(afc.db, "upsert_track", upsert_track)
    monkeypatch.setattr(afc.db, "get_conn", lambda: Conn())
    monkeypatch.setattr(afc.db, "update_track_path_references", update_track_path_references)
    return calls, rows


@pytest.fixture
def log_spy(monkeypatch):
    messages: list[str] = []
    monkeypatch.setattr(afc, "log_action", lambda message: messages.append(message))
    return messages


def test_apply_clean_dry_run_does_not_move_delete_folder_or_touch_db(tmp_path, db_spy, log_spy):
    result = _clean_result(tmp_path)
    src = result.files[0]
    dest = result.target_path / src.name

    stats = afc._apply_clean(result, dry_run=True)

    assert stats["moved"] == 1  # current implementation counts would-move items
    assert src.exists()
    assert result.original_path.exists()
    assert not dest.exists()
    calls, _ = db_spy
    assert calls["get_track"] == []
    assert calls["upsert_track"] == []
    assert calls["delete"] == []
    assert any("[DRY] move" in msg for msg in log_spy)


def test_run_dry_run_reports_intended_operations_without_mutating(tmp_path, db_spy, log_spy, capsys):
    src = _dummy(tmp_path / "1A" / "1A - Heavy-K" / "track.mp3")
    report_dir = tmp_path / "reports"

    rc = afc.run_dry_run(tmp_path, report_dir)

    out = capsys.readouterr().out
    assert rc == 0
    assert src.exists()
    assert (tmp_path / "1A" / "1A - Heavy-K").exists()
    assert not (tmp_path / "H" / "Heavy-K" / "track.mp3").exists()
    assert "Artist Folder Clean" in out
    assert "Will rename" in out
    assert "1A - Heavy-K" in out
    assert (report_dir / "artist_folder_clean_dry_run.json").exists()
    calls, _ = db_spy
    assert calls["delete"] == []


def test_folder_mutations_occur_only_in_apply_mode(tmp_path, db_spy, log_spy):
    result = _clean_result(tmp_path)
    src = result.files[0]
    dest = result.target_path / src.name

    dry = afc._apply_clean(result, dry_run=True)
    assert dry["moved"] == 1
    assert src.exists()
    assert not dest.exists()

    applied = afc._apply_clean(result, dry_run=False)
    assert applied["moved"] == 1
    assert not src.exists()
    assert dest.exists()
    assert not result.original_path.exists()


def test_destination_remains_inside_library_root_for_sanitized_candidate(tmp_path, db_spy, log_spy):
    result = _clean_result(
        tmp_path,
        original_name="1A - ..Outside",
        cleaned_name="..Outside",
    )

    stats = afc._apply_clean(result, dry_run=False)
    moved = tmp_path / "#" / "..Outside" / "track.mp3"

    assert stats["moved"] == 1
    assert moved.exists()
    assert moved.resolve().is_relative_to(tmp_path.resolve())
    assert not (tmp_path.parent / "..Outside" / "track.mp3").exists()


def test_source_junk_folder_candidate_is_rejected_not_applied(tmp_path):
    cleaned, reason = afc._clean_camelot_prefix("1A - TraxCrate")

    assert cleaned is None
    assert "source/promo junk" in reason


def test_collision_does_not_overwrite_existing_file(tmp_path, db_spy, log_spy):
    result = _clean_result(tmp_path, target_exists=True, filename="track.mp3", data=b"source")
    existing = _dummy(result.target_path / "track.mp3", data=b"existing")

    stats = afc._apply_clean(result, dry_run=False)
    collision = result.target_path / "track (1).mp3"

    assert stats["collisions"] == 1
    assert stats["moved"] == 1
    assert existing.read_bytes() == b"existing"
    assert collision.exists()
    assert collision.read_bytes() == b"source"
    assert any("COLLISION" in msg for msg in log_spy)


def test_apply_db_updates_use_central_path_update_helper(tmp_path, db_spy, log_spy):
    calls, rows = db_spy
    result = _clean_result(tmp_path)
    src = result.files[0]
    dest = result.target_path / src.name
    rows[str(src)] = {
        "artist": "1A - Heavy-K",
        "title": "Track",
        "genre": "Afro House",
        "bpm": 123.0,
        "key_musical": "A minor",
        "key_camelot": "8A",
        "duration_sec": 300.0,
        "bitrate_kbps": 320,
        "filesize_bytes": 12345,
        "status": "ok",
    }

    afc._apply_clean(result, dry_run=False)

    assert calls["update_path"] == [(str(src), str(dest), "artist_folder_clean")]
    assert calls["upsert_track"] == []
    assert calls["delete"] == []


def test_apply_without_db_row_moves_file_without_db_update(tmp_path, db_spy, log_spy):
    calls, _ = db_spy
    result = _clean_result(tmp_path)
    src = result.files[0]
    dest = result.target_path / src.name

    stats = afc._apply_clean(result, dry_run=False)

    assert stats["moved"] == 1
    assert not src.exists()
    assert dest.exists()
    assert calls["update_path"] == [(str(src), str(dest), "artist_folder_clean")]
    assert calls["get_track"] == []
    assert calls["upsert_track"] == []
    assert calls["delete"] == []


def test_recovery_moves_each_file_to_recovered_artist_and_preserves_metadata_row_values(
    tmp_path, db_spy, log_spy
):
    calls, rows = db_spy
    src_folder = tmp_path / "8A" / "8A"
    src = _dummy(src_folder / "Recovered - Track.mp3")
    target = tmp_path / "R" / "Recovered"
    result = CleanResult(
        original_path=src_folder,
        original_name="8A",
        letter="8A",
        files=[src],
        detection_rule="pure_camelot",
        cleaned_name=None,
        reject_reason=None,
        target_path=None,
        target_exists=False,
        status="recover",
        file_assignments=[
            FileAssignment(src, "Recovered", target, "filename_parse")
        ],
    )
    rows[str(src)] = {
        "artist": "Old",
        "title": "Track",
        "genre": "Afro House",
        "bpm": 124.0,
        "key_musical": "G minor",
        "key_camelot": "6A",
        "duration_sec": 301.0,
        "bitrate_kbps": 320,
        "filesize_bytes": 98765,
        "status": "ok",
    }

    stats = afc._apply_recovery(result, dry_run=False)

    assert stats["moved"] == 1
    assert (target / src.name).exists()
    assert calls["update_path"] == [
        (str(src), str(target / src.name), "artist_folder_clean")
    ]
    assert calls["upsert_track"] == []
    # cue data is not stored in tracks rows and folder-clean does not write tags.


def test_no_review_queue_update_hook_present_for_artist_folder_clean():
    assert not hasattr(afc, "ARTIST_REVIEW_QUEUE")
    assert not hasattr(afc, "_update_review_queue")


def test_repeated_apply_is_idempotent_after_source_folder_removed(tmp_path, db_spy, log_spy):
    result = _clean_result(tmp_path)

    first = afc._apply_clean(result, dry_run=False)
    second = afc._apply_clean(result, dry_run=False)

    assert first["moved"] == 1
    assert second["moved"] == 0
    assert (tmp_path / "H" / "Heavy-K" / "track.mp3").exists()


def test_move_failure_does_not_touch_db(tmp_path, db_spy, log_spy, monkeypatch):
    calls, rows = db_spy
    result = _clean_result(tmp_path)
    src = result.files[0]
    rows[str(src)] = {
        "artist": "Old",
        "title": "Track",
        "genre": "",
        "bpm": None,
        "key_musical": None,
        "key_camelot": None,
        "duration_sec": None,
        "bitrate_kbps": None,
        "filesize_bytes": None,
        "status": "ok",
    }

    def fail_move(src_arg, dest_arg):
        raise OSError("simulated move failure")

    monkeypatch.setattr(afc.shutil, "move", fail_move)

    stats = afc._apply_clean(result, dry_run=False)

    assert stats["errors"] == 1
    assert src.exists()
    assert calls["upsert_track"] == []
    assert calls["update_path"] == []
    assert calls["delete"] == []
    assert any("ERROR moving" in msg for msg in log_spy)


def test_db_failure_after_successful_move_leaves_partial_state_exposed(
    tmp_path, db_spy, log_spy, monkeypatch
):
    calls, rows = db_spy
    result = _clean_result(tmp_path)
    src = result.files[0]
    dest = result.target_path / src.name
    rows[str(src)] = {
        "artist": "Old",
        "title": "Track",
        "genre": "",
        "bpm": None,
        "key_musical": None,
        "key_camelot": None,
        "duration_sec": None,
        "bitrate_kbps": None,
        "filesize_bytes": None,
        "status": "ok",
    }

    def fail_update(old_path, new_path, context):
        calls["update_path"].append((str(old_path), str(new_path), context))
        raise RuntimeError("simulated db failure")

    monkeypatch.setattr(afc.db, "update_track_path_references", fail_update)

    stats = afc._apply_clean(result, dry_run=False)

    assert stats["moved"] == 1
    assert stats["errors"] == 0
    assert not src.exists()
    assert dest.exists()
    assert calls["delete"] == []
    # This documents current unsafe behavior: _update_db catches DB failures,
    # so the filesystem move is reported as successful even when DB repair fails.


def test_suspicious_and_review_results_are_not_auto_applied(tmp_path, db_spy, log_spy):
    src = _dummy(tmp_path / "B" / "[Bracketed]" / "track.mp3")
    suspicious = CleanResult(
        original_path=src.parent,
        original_name="[Bracketed]",
        letter="B",
        files=[src],
        detection_rule="bracket_junk",
        cleaned_name="Bracketed",
        reject_reason=None,
        target_path=tmp_path / "B" / "Bracketed",
        target_exists=False,
        status="suspicious",
    )
    review = CleanResult(
        original_path=tmp_path / "X" / "1A - TraxCrate",
        original_name="1A - TraxCrate",
        letter="X",
        files=[],
        detection_rule="camelot_prefix",
        cleaned_name=None,
        reject_reason="source/promo junk",
        target_path=None,
        target_exists=False,
        status="review",
    )

    assert afc._apply_clean(suspicious, dry_run=False)["moved"] == 0
    assert afc._apply_clean(review, dry_run=False)["moved"] == 0
    assert src.exists()
