from __future__ import annotations

import json
from pathlib import Path

import pytest

import modules.filename_normalize as fn


def _dummy(path: Path, data: bytes = b"fake audio data") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture
def logger_spy(monkeypatch):
    calls = {"record": [], "rename_path": [], "clear_stage": []}

    monkeypatch.setattr(fn._proc, "should_skip", lambda stage, path: False)
    monkeypatch.setattr(fn._proc, "clear_stage", lambda stage: calls["clear_stage"].append(stage))
    monkeypatch.setattr(
        fn._proc,
        "record",
        lambda stage, path, status, reason="": calls["record"].append(
            (stage, Path(path), status, reason)
        ),
    )
    monkeypatch.setattr(
        fn._proc,
        "rename_path",
        lambda old, new: calls["rename_path"].append((Path(old), Path(new))),
    )
    return calls


@pytest.fixture
def tag_reader(monkeypatch):
    tags_by_name: dict[str, dict[str, str]] = {}

    def fake_read_tags(path: Path) -> dict[str, str]:
        return {
            "artist": "",
            "title": "",
            "version": "",
            "album": "",
            **tags_by_name.get(path.name, {}),
        }

    monkeypatch.setattr(fn, "_read_tags", fake_read_tags)
    return tags_by_name


def test_preview_reports_intended_rename_without_renaming(tmp_path, tag_reader, logger_spy, capsys):
    src = _dummy(tmp_path / "bad-name.mp3")
    tag_reader[src.name] = {"artist": "Black Coffee", "title": "Wish You Were Here"}

    stats = fn.run(tmp_path, apply=False)

    out = capsys.readouterr().out
    assert stats["candidates"] == 1
    assert stats["renamed"] == 0
    assert src.exists()
    assert not (tmp_path / "Black Coffee - Wish You Were Here.mp3").exists()
    assert "=== filename-normalize PREVIEW ===" in out
    assert "FROM: bad-name.mp3" in out
    assert "TO  : Black Coffee - Wish You Were Here.mp3" in out
    assert logger_spy["rename_path"] == []
    assert logger_spy["record"] == []


def test_apply_requires_explicit_flag_and_updates_run_logger(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "bad-name.mp3")
    tag_reader[src.name] = {"artist": "Black Coffee", "title": "Wish You Were Here"}

    preview_stats = fn.run(tmp_path, apply=False)
    assert preview_stats["renamed"] == 0
    assert src.exists()

    apply_stats = fn.run(tmp_path, apply=True)
    dst = tmp_path / "Black Coffee - Wish You Were Here.mp3"

    assert apply_stats["renamed"] == 1
    assert not src.exists()
    assert dst.exists()
    assert logger_spy["rename_path"] == [(src, dst)]
    assert ("filename-normalize", dst, "success", "") in logger_spy["record"]


def test_collision_does_not_overwrite_existing_target(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "incoming.mp3", b"source")
    existing = _dummy(tmp_path / "Black Coffee - Drive.mp3", b"existing")
    tag_reader[src.name] = {"artist": "Black Coffee", "title": "Drive"}
    tag_reader[existing.name] = {"artist": "Black Coffee", "title": "Drive"}

    stats = fn.run(tmp_path, apply=True)
    collision = tmp_path / "Black Coffee - Drive (1).mp3"

    assert stats["collisions"] == 1
    assert stats["renamed"] == 1
    assert existing.read_bytes() == b"existing"
    assert collision.exists()
    assert collision.read_bytes() == b"source"
    assert not src.exists()


def test_missing_artist_or_title_is_skipped_without_dangerous_name(tmp_path, tag_reader, logger_spy):
    missing_artist = _dummy(tmp_path / "missing-artist.mp3")
    missing_title = _dummy(tmp_path / "missing-title.mp3")
    tag_reader[missing_artist.name] = {"artist": "", "title": "Track"}
    tag_reader[missing_title.name] = {"artist": "Artist", "title": ""}

    stats = fn.run(tmp_path, apply=True)

    assert stats["skipped_no_tags"] == 2
    assert stats["renamed"] == 0
    assert missing_artist.exists()
    assert missing_title.exists()
    assert not (tmp_path / " - Track.mp3").exists()
    assert not (tmp_path / "Artist - .mp3").exists()


def test_unsafe_characters_are_sanitized_and_extension_is_preserved():
    stem = fn._build_stem('AC/DC:Live?*', 'A/B<C>|"Tune"', "Club:Mix")
    assert stem == "ACDCLive - ABCTune (ClubMix)"


def test_path_traversal_metadata_stays_inside_input_directory(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "unsafe.mp3")
    tag_reader[src.name] = {"artist": "../Outside", "title": "../../Escape"}

    stats = fn.run(tmp_path, apply=True)

    files = [p for p in tmp_path.iterdir() if p.is_file()]
    assert stats["renamed"] == 1
    assert len(files) == 1
    assert files[0].parent == tmp_path
    assert files[0].suffix == ".mp3"
    assert ".." in files[0].name  # sanitized for traversal, not fully prettified
    assert not (tmp_path.parent / "Outside - Escape.mp3").exists()


def test_empty_generated_filename_is_blocked(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "bad.mp3")
    tag_reader[src.name] = {"artist": '///:::***???"""<<<>>>|||', "title": "\\\\\\///"}

    stats = fn.run(tmp_path, apply=True)

    assert stats["renamed"] == 0
    assert src.exists()
    assert not (tmp_path / " - .mp3").exists()


def test_dj_metadata_is_not_modified_by_filename_normalize(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "old.mp3", b"original bytes")
    metadata = {
        "artist": "Caiiro",
        "title": "The Akan",
        "version": "Original Mix",
        "album": "Album",
        "bpm": "123",
        "key": "8A",
        "cue_points": "intro=0;drop=64",
    }
    tag_reader[src.name] = metadata

    stats = fn.run(tmp_path, apply=True)
    dst = tmp_path / "Caiiro - The Akan (Original Mix).mp3"

    assert stats["renamed"] == 1
    assert dst.exists()
    assert dst.read_bytes() == b"original bytes"
    assert metadata["bpm"] == "123"
    assert metadata["key"] == "8A"
    assert metadata["cue_points"] == "intro=0;drop=64"


def test_idempotent_already_normalized_file_is_unchanged(tmp_path, tag_reader, logger_spy):
    src = _dummy(tmp_path / "Caiiro - The Akan (Original Mix).mp3")
    tag_reader[src.name] = {
        "artist": "Caiiro",
        "title": "The Akan",
        "version": "Original Mix",
    }

    first = fn.run(tmp_path, apply=True)
    second = fn.run(tmp_path, apply=True)

    assert first["skipped_no_change"] == 1
    assert second["skipped_no_change"] == 1
    assert first["renamed"] == 0
    assert second["renamed"] == 0
    assert src.exists()
    assert logger_spy["rename_path"] == []


def test_artist_review_queue_reports_unsafe_artist_without_move_by_default(
    tmp_path, tag_reader, logger_spy, monkeypatch
):
    queue = tmp_path / "review" / "artist_review_queue.jsonl"
    monkeypatch.setattr(fn, "_ARTIST_REVIEW_QUEUE", queue)

    src = _dummy(tmp_path / "unsafe-artist.mp3")
    tag_reader[src.name] = {
        "artist": "African RootsLeboBebucho",
        "title": "Track",
        "album": "Album",
    }

    stats = fn.run(tmp_path, apply=True, move_artist_review=False)

    assert stats["skipped_unsafe_artist"] == 1
    assert stats["artist_review_count"] == 1
    assert stats["renamed"] == 0
    assert src.exists()
    entries = [json.loads(line) for line in queue.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["file_path"] == str(src)
    assert entries[0]["moved"] is False
    assert entries[0]["reason"] == "unsafe_artist_concat"


def test_collect_audio_files_preserves_original_extension_filtering(tmp_path):
    mp3 = _dummy(tmp_path / "a.mp3")
    flac = _dummy(tmp_path / "b.flac")
    _dummy(tmp_path / "notes.txt")

    files = fn._collect_audio_files(tmp_path, limit=None)

    assert mp3 in files
    assert flac in files
    assert all(p.suffix != ".txt" for p in files)


# TODO: Real embedded BPM/key/cue tag preservation is not tested here because
# filename-normalize does not write tags and these tests intentionally mock
# tag reads to avoid heavy audio-container fixtures.
