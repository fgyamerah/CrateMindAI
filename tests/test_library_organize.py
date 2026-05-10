from __future__ import annotations

from pathlib import Path

import pytest

import modules.library_organize as lo


def _dummy(path: Path, data: bytes = b"fake audio data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def logger_spy(monkeypatch):
    calls = {"record": [], "rename_path": [], "clear_stage": [], "update_path": []}

    monkeypatch.setattr(lo._proc, "should_skip", lambda stage, path: False)
    monkeypatch.setattr(lo._proc, "clear_stage", lambda stage: calls["clear_stage"].append(stage))
    monkeypatch.setattr(
        lo._proc,
        "record",
        lambda stage, path, status, reason="": calls["record"].append(
            (stage, Path(path), status, reason)
        ),
    )
    monkeypatch.setattr(
        lo._proc,
        "rename_path",
        lambda old, new: calls["rename_path"].append((Path(old), Path(new))),
    )
    monkeypatch.setattr(
        lo.db,
        "update_track_path_references",
        lambda old, new, context: calls["update_path"].append(
            (Path(old), Path(new), context)
        ) or {"status": "updated"},
    )
    return calls


@pytest.fixture
def artist_reader(monkeypatch):
    artists_by_name: dict[str, str] = {}

    def fake_read_artist(path: Path) -> str:
        return artists_by_name.get(path.name, "")

    monkeypatch.setattr(lo, "_read_artist", fake_read_artist)
    return artists_by_name


def test_dry_run_reports_move_without_moving_or_db_update(
    tmp_path, artist_reader, logger_spy, capsys
):
    src = _dummy(tmp_path / "track.mp3")
    artist_reader[src.name] = "Black Coffee"

    stats = lo.run(tmp_path, apply=False)

    out = capsys.readouterr().out
    expected = tmp_path / "B" / "Black Coffee" / "track.mp3"
    assert stats["candidates"] == 1
    assert stats["moved"] == 0
    assert src.exists()
    assert not expected.exists()
    assert "=== library-organize PREVIEW ===" in out
    assert "MOVE:" in out
    assert str(expected) in out
    assert logger_spy["rename_path"] == []
    assert logger_spy["record"] == []


def test_apply_explicitly_required_for_moves(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "track.mp3")
    artist_reader[src.name] = "Black Coffee"

    preview = lo.run(tmp_path, apply=False)
    assert preview["moved"] == 0
    assert src.exists()

    applied = lo.run(tmp_path, apply=True)
    expected = tmp_path / "B" / "Black Coffee" / "track.mp3"

    assert applied["moved"] == 1
    assert not src.exists()
    assert expected.exists()
    assert logger_spy["rename_path"] == []
    assert logger_spy["update_path"] == [(src, expected, "library_organize")]
    assert ("library-organize", expected, "success", "") in logger_spy["record"]


def test_destination_stays_inside_sorted_root(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "track.mp3")
    artist_reader[src.name] = "A..Outside"

    stats = lo.run(tmp_path, apply=True)
    moved = tmp_path / "A" / "A..Outside" / "track.mp3"

    assert stats["moved"] == 1
    assert moved.exists()
    assert moved.resolve().is_relative_to(tmp_path.resolve())
    assert not (tmp_path.parent / "A..Outside" / "track.mp3").exists()


def test_collision_does_not_overwrite_existing_file(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "incoming.mp3", b"source")
    existing = _dummy(tmp_path / "B" / "Black Coffee" / "incoming.mp3", b"existing")
    artist_reader[src.name] = "Black Coffee"
    artist_reader[existing.name] = "Black Coffee"

    stats = lo.run(tmp_path, apply=True)
    collision = tmp_path / "B" / "Black Coffee" / "incoming (1).mp3"

    assert stats["collisions"] == 1
    assert stats["moved"] == 1
    assert existing.read_bytes() == b"existing"
    assert collision.exists()
    assert collision.read_bytes() == b"source"
    assert not src.exists()


def test_unsupported_files_are_ignored(tmp_path, artist_reader, logger_spy):
    mp3 = _dummy(tmp_path / "track.mp3")
    txt = _dummy(tmp_path / "notes.txt")
    artist_reader[mp3.name] = "Black Coffee"
    artist_reader[txt.name] = "Should Not Matter"

    stats = lo.run(tmp_path, apply=True)

    assert stats["scanned"] == 1
    assert txt.exists()
    assert (tmp_path / "B" / "Black Coffee" / "track.mp3").exists()


def test_missing_artist_is_handled_safely(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "no-pattern.mp3")
    artist_reader[src.name] = ""

    stats = lo.run(tmp_path, apply=True)

    assert stats["skipped_no_artist"] == 1
    assert stats["moved"] == 0
    assert src.exists()
    assert ("library-organize", src, "skipped", "no_artist_tag") in logger_spy["record"]


def test_missing_artist_can_fallback_to_safe_filename_artist(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "Black Coffee - Drive.mp3")
    artist_reader[src.name] = ""

    stats = lo.run(tmp_path, apply=True)
    expected = tmp_path / "B" / "Black Coffee" / "Black Coffee - Drive.mp3"

    assert stats["moved"] == 1
    assert expected.exists()
    assert not src.exists()


def test_path_traversal_artist_is_not_moved_outside_root(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "unsafe.mp3")
    artist_reader[src.name] = "../../Outside"

    stats = lo.run(tmp_path, apply=True)

    assert stats["moved"] == 0
    assert stats["skipped_unsafe_artist"] == 1
    assert src.exists()
    assert not (tmp_path.parent / "Outside" / "unsafe.mp3").exists()


def test_repeated_run_is_idempotent(tmp_path, artist_reader, logger_spy):
    src = _dummy(tmp_path / "B" / "Black Coffee" / "track.mp3")
    artist_reader[src.name] = "Black Coffee"

    first = lo.run(tmp_path, apply=True)
    second = lo.run(tmp_path, apply=True)

    assert first["skipped_already_correct"] == 1
    assert second["skipped_already_correct"] == 1
    assert first["moved"] == 0
    assert second["moved"] == 0
    assert src.exists()
    assert logger_spy["rename_path"] == []


def test_dry_run_does_not_move_unsafe_artist_to_review_folder(
    tmp_path, artist_reader, logger_spy
):
    src = _dummy(tmp_path / "unsafe.mp3")
    artist_reader[src.name] = "African RootsLeboBebucho"

    stats = lo.run(tmp_path, apply=False, move_unsafe_artists=True)

    assert stats["unsafe_artist_count"] == 1
    assert stats["moved_to_chkartistnames"] == 0
    assert src.exists()
    assert not (tmp_path.parent / ".BIN" / "CHKARTISTNAMES").exists()
    assert logger_spy["rename_path"] == []


def test_sanitize_dirname_removes_path_unsafe_characters():
    assert lo._sanitize_dirname('AC/DC:Live?*"<>|') == "ACDCLive"
