import hashlib
import csv
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pipeline


def _audio(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _create_db(
    root: Path,
    rows: list[dict],
    *,
    processed_rows: list[dict] | None = None,
) -> Path:
    db_path = root / "logs" / "processed.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT,
            filename TEXT,
            status TEXT,
            filesize_bytes INTEGER
        )
        """
    )
    for row in rows:
        filepath = str(row["filepath"])
        conn.execute(
            "INSERT INTO tracks(filepath, filename, status, filesize_bytes) "
            "VALUES (?, ?, ?, ?)",
            (
                filepath,
                row.get("filename", Path(filepath).name),
                row.get("status", "ok"),
                row.get("filesize_bytes"),
            ),
        )
    if processed_rows is not None:
        conn.execute(
            """
            CREATE TABLE processed_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                stage TEXT NOT NULL,
                filepath TEXT NOT NULL,
                file_size INTEGER NOT NULL DEFAULT 0,
                file_mtime REAL NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                reason TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for row in processed_rows:
            conn.execute(
                "INSERT INTO processed_state"
                "(stage, filepath, file_size, file_mtime, status, processed_at, reason) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    row.get("stage", "metadata-sanitize"),
                    str(row["filepath"]),
                    row.get("file_size", row.get("filesize_bytes", 0)),
                    row.get("file_mtime", 0),
                    row.get("status", "success"),
                    row.get("processed_at", "2026-05-04T00:00:00+00:00"),
                    row.get("reason", ""),
                ),
            )
    conn.commit()
    conn.close()
    return db_path


def _run(root: Path, monkeypatch, *, include_orphan_candidates: bool = False):
    missing_fallback = root / "logs" / "missing-fallback.db"
    monkeypatch.setattr(pipeline.config, "DB_PATH", missing_fallback)
    rc = pipeline.run_path_audit(
        SimpleNamespace(
            root=str(root),
            include_orphan_candidates=include_orphan_candidates,
        )
    )
    assert rc == 0
    return _latest_report(root)


def _latest_report(root: Path) -> tuple[dict, Path, Path]:
    report_dir = root / "logs" / "path_audit"
    json_path = sorted(report_dir.glob("path_audit_*.json"))[-1]
    text_path = sorted(report_dir.glob("path_audit_*.log"))[-1]
    return json.loads(json_path.read_text(encoding="utf-8")), json_path, text_path


def _latest_renames_csv(root: Path) -> Path:
    report_dir = root / "logs" / "path_audit"
    return sorted(report_dir.glob("*_possible_renames.csv"))[-1]


def _latest_orphan_candidates_csv(root: Path) -> Path:
    report_dir = root / "logs" / "path_audit"
    return sorted(report_dir.glob("*_orphan_candidates.csv"))[-1]


def _latest_stale_rows_csv(root: Path) -> Path:
    report_dir = root / "logs" / "path_audit"
    return sorted(report_dir.glob("*_stale_rows.csv"))[-1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_read_only_does_not_modify_db_or_audio_files_except_reports(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    untracked = _audio(tmp_path / "music" / "untracked.flac", b"untracked")
    db_path = _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
    )
    before_db_hash = _sha256(db_path)
    before_files = {
        path.relative_to(tmp_path): _sha256(path)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    report, json_path, text_path = _run(tmp_path, monkeypatch)

    after_files = {
        path.relative_to(tmp_path): _sha256(path)
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    added_files = set(after_files) - set(before_files)
    assert added_files == {
        json_path.relative_to(tmp_path),
        text_path.relative_to(tmp_path),
        _latest_renames_csv(tmp_path).relative_to(tmp_path),
        _latest_stale_rows_csv(tmp_path).relative_to(tmp_path),
    }
    assert _sha256(db_path) == before_db_hash
    assert tracked.exists()
    assert untracked.exists()
    assert tracked.read_bytes() == b"tracked"
    assert untracked.read_bytes() == b"untracked"
    assert report["read_only"] is True
    assert report["summary"]["orphan_candidate_scoring_enabled"] is False
    assert not list((tmp_path / "logs" / "path_audit").glob("*_orphan_candidates.csv"))


def test_missing_db_files_are_reported(tmp_path, monkeypatch):
    missing = tmp_path / "music" / "missing.mp3"
    _create_db(tmp_path, [{"filepath": missing, "filesize_bytes": 100}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["missing_files"] == 1
    assert report["missing_files"][0]["filepath"] == str(missing)


def test_untracked_disk_files_are_reported(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    untracked = _audio(tmp_path / "music" / "loose.mp3", b"loose")
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert str(untracked.resolve()) in report["untracked_files"]
    assert report["summary"]["untracked_files"] == 1


def test_possible_rename_detects_same_basename(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "same-name.mp3", b"candidate")
    old_path = tmp_path / "old" / "same-name.mp3"
    _create_db(
        tmp_path,
        [{"filepath": old_path, "filesize_bytes": candidate.stat().st_size}],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["possible_renames"] == 1
    rename = report["possible_renames"][0]
    assert rename["db_row"]["filepath"] == str(old_path)
    assert any(
        match["path"] == str(candidate.resolve())
        and match["reason"] == "same_basename"
        and match["size"] == candidate.stat().st_size
        for match in rename["matches"]
    )


def test_possible_rename_detects_same_size_and_similar_extension(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Missing Name.mp3", b"same-size")
    old_path = tmp_path / "old" / "missing-name.mp3"
    _create_db(
        tmp_path,
        [{"filepath": old_path, "filesize_bytes": candidate.stat().st_size}],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    matches = report["possible_renames"][0]["matches"]
    assert any(
        match["path"] == str(candidate.resolve())
        and match["reason"] == "fuzzy_filename"
        and match["size"] == candidate.stat().st_size
        for match in matches
    )


def test_fuzzy_rename_detects_numeric_suffix_removal(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Black Coffee - Drive.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Black Coffee - Drive (1).mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1010}])

    report, _, _ = _run(tmp_path, monkeypatch)

    rename = report["possible_renames"][0]
    assert rename["old_path"] == str(old_path)
    assert rename["new_path"] == str(candidate.resolve())
    assert rename["reason"] == "fuzzy_filename"
    assert rename["similarity"] > 0.85
    assert rename["size_diff_pct"] < 0.05


def test_fuzzy_rename_detects_case_difference(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "&ME - The Rapture.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "&Me - The Rapture.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    rename = report["possible_renames"][0]
    assert rename["new_path"] == str(candidate.resolve())
    assert rename["similarity"] > 0.85


def test_fuzzy_rename_detects_punctuation_and_bpm_key_cleanup(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Artist Title Original Mix.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Title (Original Mix) - 8A - 125.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    rename = report["possible_renames"][0]
    assert rename["new_path"] == str(candidate.resolve())
    assert rename["reason"] == "fuzzy_filename"
    assert rename["similarity"] > 0.85


def test_fuzzy_rename_allows_small_filesize_difference(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1030)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    rename = report["possible_renames"][0]
    assert rename["new_path"] == str(candidate.resolve())
    assert 0 < rename["size_diff_pct"] < 0.05


def test_fuzzy_rename_rejects_different_tracks(tmp_path, monkeypatch):
    _audio(tmp_path / "new" / "Completely Different Song.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["possible_renames"] == 0
    assert report["summary"]["orphan_db_rows"] == 1


def test_duplicate_db_entries_are_reported(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "dupe.mp3", b"dupe")
    _create_db(
        tmp_path,
        [
            {"filepath": tracked, "filesize_bytes": tracked.stat().st_size},
            {"filepath": tracked, "filesize_bytes": tracked.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["duplicate_db_entries"] == 1
    assert report["duplicate_db_entries"][0]["filepath"] == str(tracked)
    assert report["duplicate_db_entries"][0]["count"] == 2


def test_stale_queue_entries_are_reported(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    missing = tmp_path / "music" / "missing-from-queue.mp3"
    queue = tmp_path / "data" / "intelligence" / "artist_review_queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        json.dumps([{"filepath": str(missing)}, {"filepath": str(tracked)}]),
        encoding="utf-8",
    )
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["stale_queue_entries"] == 1
    assert report["stale_queue_entries"][0]["path"] == str(missing)
    assert report["stale_queue_entries"][0]["reason"] == "path_not_found"


def test_orphan_db_rows_are_missing_rows_without_rename_candidate(tmp_path, monkeypatch):
    orphan = tmp_path / "old" / "orphan.aiff"
    _audio(tmp_path / "music" / "different.mp3", b"different")
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 999999}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["missing_files"] == 1
    assert report["summary"]["possible_renames"] == 0
    assert report["summary"]["orphan_db_rows"] == 1
    assert report["orphan_db_rows"][0]["filepath"] == str(orphan)


def test_missing_processed_db_is_safe_and_does_not_create_db(tmp_path, monkeypatch):
    _audio(tmp_path / "music" / "loose.mp3", b"loose")
    fallback = tmp_path / "logs" / "absent.db"
    monkeypatch.setattr(pipeline.config, "DB_PATH", fallback)

    rc = pipeline.run_path_audit(SimpleNamespace(root=str(tmp_path)))

    assert rc == 0
    report, _, _ = _latest_report(tmp_path)
    assert report["db_error"] == f"database not found: {tmp_path / 'logs' / 'processed.db'}"
    assert report["summary"]["db_rows"] == 0
    assert report["summary"]["untracked_files"] == 1
    assert not (tmp_path / "logs" / "processed.db").exists()
    assert not fallback.exists()


def test_output_files_are_written_under_logs_path_audit(tmp_path, monkeypatch):
    _create_db(tmp_path, [])

    report, json_path, text_path = _run(tmp_path, monkeypatch)

    assert json_path.parent == tmp_path / "logs" / "path_audit"
    assert text_path.parent == tmp_path / "logs" / "path_audit"
    assert json_path.exists()
    assert text_path.exists()
    assert report["summary"]["db_rows"] == 0
    text = text_path.read_text(encoding="utf-8")
    assert "path-audit READ-ONLY" in text
    assert f"root={tmp_path.resolve()}" in text


def test_tracks_only_db_reports_source_counts(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["tracks_rows"] == 1
    assert report["summary"]["processed_state_rows"] == 0
    assert report["summary"]["canonical_source"] == "tracks"
    assert report["summary"]["combined_db_paths"] == 1
    assert report["path_sources"]["processed_state_path_column"] is None


def test_processed_state_only_db_tracks_file_as_known(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "processed.mp3", b"processed")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"filepath": tracked, "file_size": tracked.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["tracks_rows"] == 0
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["canonical_source"] == "processed_state"
    assert report["summary"]["historical_paths_count"] == 1
    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["untracked_files"] == 0
    assert report["path_sources"]["processed_state_path_column"] == "filepath"


def test_same_path_in_tracks_and_processed_state_does_not_double_count(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "both.mp3", b"both")
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
        processed_rows=[
            {"filepath": tracked, "file_size": tracked.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["tracks_rows"] == 1
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["canonical_source"] == "tracks"
    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["untracked_files"] == 0
    assert report["summary"]["duplicate_db_entries"] == 0
    assert report["summary"]["cross_source_overlap_count"] == 1


def test_tracks_populated_audit_uses_tracks_as_canonical_source(tmp_path, monkeypatch):
    processed_only = _audio(tmp_path / "music" / "processed-only.mp3", b"processed")
    missing_track = tmp_path / "music" / "missing-track.mp3"
    _create_db(
        tmp_path,
        [{"filepath": missing_track, "filesize_bytes": 123}],
        processed_rows=[
            {"filepath": processed_only, "file_size": processed_only.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["canonical_source"] == "tracks"
    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["missing_files"] == 1
    assert report["missing_files"][0]["filepath"] == str(missing_track)
    assert report["summary"]["untracked_files"] == 1
    assert report["untracked_files"] == [str(processed_only.resolve())]


def test_processed_state_stale_rows_do_not_affect_active_audit_when_tracks_exist(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    stale_missing = tmp_path / "music" / "stale-missing.mp3"
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
        processed_rows=[
            {"filepath": stale_missing, "file_size": 123, "status": "stale"},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["canonical_source"] == "tracks"
    assert report["summary"]["stale_processed_state_rows_total"] == 1
    assert report["summary"]["missing_files"] == 0
    assert report["summary"]["orphan_db_rows"] == 0
    assert report["missing_files"] == []
    assert report["orphan_db_rows"] == []


def test_untracked_files_compare_against_processed_state_paths(
    tmp_path, monkeypatch
):
    processed = _audio(tmp_path / "music" / "processed.mp3", b"processed")
    loose = _audio(tmp_path / "music" / "loose.mp3", b"loose")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"filepath": processed, "file_size": processed.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["untracked_files"] == 1
    assert report["untracked_files"] == [str(loose.resolve())]


def test_missing_processed_state_paths_are_reported_with_source(
    tmp_path, monkeypatch
):
    missing = tmp_path / "music" / "missing-processed.mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"filepath": missing, "file_size": 123},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["missing_files"] == 1
    assert report["missing_files"][0]["filepath"] == str(missing)
    assert report["missing_files"][0]["sources"] == ["processed_state"]


def test_repeated_processed_state_path_across_stages_is_informational(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "multi-stage.mp3", b"processed")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": tracked, "file_size": tracked.stat().st_size},
            {"stage": "artist-intelligence", "filepath": tracked, "file_size": tracked.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["historical_paths_count"] == 2
    assert report["summary"]["repeated_processed_state_paths"] == 1
    assert report["summary"]["duplicate_db_entries"] == 0
    assert report["duplicate_db_entries"] == []


def test_duplicate_processed_state_same_stage_entries_are_reported(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "same-stage.mp3", b"processed")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": tracked, "file_size": tracked.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": tracked, "file_size": tracked.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["repeated_processed_state_paths"] == 1
    assert report["summary"]["duplicate_db_entries"] == 1
    assert report["duplicate_db_entries"][0]["table"] == "processed_state"
    assert report["duplicate_db_entries"][0]["duplicate_type"] == "within_stage"
    assert report["duplicate_db_entries"][0]["stage"] == "metadata-sanitize"
    assert report["duplicate_db_entries"][0]["count"] == 2


def test_processed_state_final_stage_path_ignores_earlier_missing_path(
    tmp_path, monkeypatch
):
    old_path = tmp_path / "old" / "track.mp3"
    final_path = _audio(tmp_path / "library" / "track.mp3", b"final")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": 123},
            {"stage": "library-organize", "filepath": final_path, "file_size": final_path.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["historical_paths_count"] == 2
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["combined_db_paths"] == 1
    assert report["path_sources"]["current_processed_state_stage"] == "library-organize"
    assert report["summary"]["missing_files"] == 0
    assert report["summary"]["orphan_db_rows"] == 0
    assert report["untracked_files"] == []


def test_no_false_missing_for_renamed_file_when_final_stage_exists(
    tmp_path, monkeypatch
):
    old_path = tmp_path / "inbox" / "bad-name.mp3"
    normalized_path = tmp_path / "processing" / "Artist - Title.mp3"
    final_path = _audio(tmp_path / "sorted" / "A" / "Artist" / "Artist - Title.mp3", b"final")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": 111},
            {"stage": "filename-normalize", "filepath": normalized_path, "file_size": 111},
            {"stage": "library-organize", "filepath": final_path, "file_size": final_path.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["historical_paths_count"] == 3
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["missing_files"] == 0
    assert report["summary"]["possible_renames"] == 0
    assert report["summary"]["orphan_db_rows"] == 0


def test_possible_renames_csv_exists_when_renames_exist(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    _run(tmp_path, monkeypatch)

    csv_path = _latest_renames_csv(tmp_path)
    rows = list(csv.DictReader(csv_path.open(encoding="utf-8")))
    assert csv_path.exists()
    assert rows[0]["old_path"] == str(old_path)
    assert rows[0]["new_path"] == str(candidate.resolve())
    assert rows[0]["old_filename"] == old_path.name
    assert rows[0]["new_filename"] == candidate.name


def test_possible_renames_csv_is_sorted_by_similarity_then_size_diff(
    tmp_path, monkeypatch
):
    exact_candidate = _audio(tmp_path / "new" / "Artist - Perfect.mp3", b"x" * 1000)
    fuzzy_candidate = _audio(tmp_path / "new" / "Artist - Nearly Clean.mp3", b"x" * 1030)
    exact_old = tmp_path / "old" / "Artist - Perfect.mp3"
    fuzzy_old = tmp_path / "old" / "Artist - Nearly Clean (1).mp3"
    _create_db(
        tmp_path,
        [
            {"filepath": fuzzy_old, "filesize_bytes": 1000},
            {"filepath": exact_old, "filesize_bytes": 1000},
        ],
    )

    _run(tmp_path, monkeypatch)

    rows = list(csv.DictReader(_latest_renames_csv(tmp_path).open(encoding="utf-8")))
    assert [row["new_path"] for row in rows] == [
        str(exact_candidate.resolve()),
        str(fuzzy_candidate.resolve()),
    ]
    similarities = [float(row["similarity"]) for row in rows]
    size_diffs = [float(row["size_diff_pct"]) for row in rows]
    assert similarities == sorted(similarities, reverse=True)
    assert size_diffs[0] <= size_diffs[1]


def test_relocation_detects_sorted_to_instrumental(tmp_path, monkeypatch):
    candidate = _audio(
        tmp_path / "instrumental" / "Black Coffee Drive Instrumental.mp3",
        b"x" * 1040,
    )
    old_path = tmp_path / "library" / "sorted" / "B" / "Black Coffee" / "Black Coffee - Drive.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["relocation_candidates"] == 1
    assert report["summary"]["orphan_db_rows"] == 0
    relocation = report["relocation_candidates"][0]
    assert relocation["old_path"] == str(old_path)
    assert relocation["new_path"] == str(candidate.resolve())
    assert relocation["match_type"] == "relocation"
    assert relocation["token_overlap"] >= 0.70
    assert relocation["size_diff_pct"] < 0.10


def test_relocation_detects_sorted_to_edits(tmp_path, monkeypatch):
    candidate = _audio(
        tmp_path / "edits" / "Caiiro Akan Edit.flac",
        b"x" * 950,
    )
    old_path = tmp_path / "library" / "sorted" / "C" / "Caiiro" / "Caiiro - Akan.flac"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    relocation = report["relocation_candidates"][0]
    assert relocation["new_path"] == str(candidate.resolve())
    assert relocation["size_diff_pct"] < 0.10


def test_relocation_token_match_with_different_formatting(tmp_path, monkeypatch):
    candidate = _audio(
        tmp_path / "bootlegs" / "artist title bootleg.mp3",
        b"x" * 990,
    )
    old_path = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist_Title__Original Mix.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["relocation_candidates"] == 1
    assert report["relocation_candidates"][0]["new_path"] == str(candidate.resolve())


def test_relocation_rejects_unrelated_track(tmp_path, monkeypatch):
    _audio(tmp_path / "instrumental" / "Completely Different Song.mp3", b"x" * 1000)
    old_path = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist - Clean Title.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["relocation_candidates"] == 0
    assert report["summary"]["orphan_db_rows"] == 1


def test_orphan_analysis_groups_by_top_folder(tmp_path, monkeypatch):
    sorted_orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Missing One.mp3"
    edits_orphan = tmp_path / "edits" / "Missing Two.mp3"
    _create_db(
        tmp_path,
        [
            {"filepath": sorted_orphan, "filesize_bytes": 1000},
            {"filepath": edits_orphan, "filesize_bytes": 2000},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["orphan_by_top_folder"] == {"edits": 1, "sorted": 1}


def test_orphan_analysis_groups_by_stage_status(tmp_path, monkeypatch):
    missing_success = tmp_path / "library" / "sorted" / "A" / "Artist" / "Missing Success.mp3"
    missing_no_change = tmp_path / "library" / "sorted" / "B" / "Artist" / "Missing No Change.mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {
                "stage": "library-organize",
                "filepath": missing_success,
                "file_size": 1000,
                "status": "success",
            },
            {
                "stage": "library-organize",
                "filepath": missing_no_change,
                "file_size": 2000,
                "status": "no_change",
            },
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["orphan_by_stage_status"] == {
        "library-organize/no_change": 1,
        "library-organize/success": 1,
    }


def test_orphan_analysis_size_match_stats(tmp_path, monkeypatch):
    _audio(tmp_path / "disk" / "Unrelated Exact.mp3", b"x" * 1000)
    _audio(tmp_path / "disk" / "Unrelated Near.mp3", b"y" * 1080)
    exact_orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Missing Exact.mp3"
    near_orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Missing Near.mp3"
    none_orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Missing None.mp3"
    _create_db(
        tmp_path,
        [
            {"filepath": exact_orphan, "filesize_bytes": 1000},
            {"filepath": near_orphan, "filesize_bytes": 1100},
            {"filepath": none_orphan, "filesize_bytes": 5000},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["orphan_size_match_stats"] == {
        "exact_file_size_exists_elsewhere": 1,
        "near_file_size_within_10pct_exists_elsewhere": 2,
    }


def test_orphan_analysis_token_overlap_stats(tmp_path, monkeypatch):
    _audio(tmp_path / "disk" / "Artist Alpha Beta.mp3", b"x" * 5000)
    _audio(tmp_path / "disk" / "Artist Gamma.mp3", b"y" * 5000)
    token_70 = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Beta Delta.mp3"
    token_50 = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Gamma Delta Epsilon.mp3"
    token_none = tmp_path / "library" / "sorted" / "A" / "Artist" / "Completely Different Missing.mp3"
    _create_db(
        tmp_path,
        [
            {"filepath": token_70, "filesize_bytes": 1000},
            {"filepath": token_50, "filesize_bytes": 1000},
            {"filepath": token_none, "filesize_bytes": 1000},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["orphan_filename_token_match_stats"] == {
        "token_overlap_gte_50pct": 2,
        "token_overlap_gte_60pct": 1,
        "token_overlap_gte_70pct": 1,
    }
    assert report["summary"]["relocation_candidates"] == 0


def test_orphan_candidates_csv_generated_when_candidates_exist(tmp_path, monkeypatch):
    _audio(tmp_path / "disk" / "Artist Alpha Beta.mp3", b"x" * 5000)
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Missing.mp3"
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch, include_orphan_candidates=True)

    rows = list(csv.DictReader(_latest_orphan_candidates_csv(tmp_path).open(encoding="utf-8")))
    assert report["summary"]["orphan_candidate_scoring_enabled"] is True
    assert rows
    assert rows[0]["old_path"] == str(orphan)
    assert rows[0]["candidate_path"].endswith("Artist Alpha Beta.mp3")
    assert rows[0]["reason"]


def test_default_audit_does_not_create_orphan_candidates_csv(tmp_path, monkeypatch):
    _audio(tmp_path / "disk" / "Artist Alpha Beta.mp3", b"x" * 5000)
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Missing.mp3"
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["orphan_candidate_scoring_enabled"] is False
    assert report["orphan_candidates"] == []
    assert report["orphan_candidate_tiers"] == {
        "AUTO_SAFE_CANDIDATE": 0,
        "REVIEW_CAREFULLY": 0,
        "WEAK_MATCH": 0,
    }
    assert not list((tmp_path / "logs" / "path_audit").glob("*_orphan_candidates.csv"))


def test_orphan_candidates_csv_limits_to_top_five_per_orphan(tmp_path, monkeypatch):
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Missing.mp3"
    for idx in range(8):
        _audio(tmp_path / "disk" / f"Artist Alpha Candidate {idx}.mp3", b"x" * (5000 + idx))
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    _run(tmp_path, monkeypatch, include_orphan_candidates=True)

    rows = list(csv.DictReader(_latest_orphan_candidates_csv(tmp_path).open(encoding="utf-8")))
    assert len([row for row in rows if row["old_path"] == str(orphan)]) == 5


def test_orphan_candidates_csv_ranks_best_match_first(tmp_path, monkeypatch):
    best = _audio(tmp_path / "disk" / "Artist Alpha Beta.mp3", b"x" * 1000)
    _audio(tmp_path / "disk" / "Artist Alpha Gamma.mp3", b"x" * 1800)
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Beta Missing Delta.mp3"
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    _run(tmp_path, monkeypatch, include_orphan_candidates=True)

    rows = list(csv.DictReader(_latest_orphan_candidates_csv(tmp_path).open(encoding="utf-8")))
    orphan_rows = [row for row in rows if row["old_path"] == str(orphan)]
    assert orphan_rows[0]["candidate_path"] == str(best.resolve())
    assert float(orphan_rows[0]["score"]) >= float(orphan_rows[1]["score"])


def test_orphan_candidate_auto_safe_tier(tmp_path, monkeypatch):
    assert pipeline._path_audit_orphan_candidate_tier(
        score=0.99,
        token_overlap=1.0,
        size_diff_pct=0.0,
        same_extension=True,
        old_path=Path("Artist - Track (1).mp3"),
        new_path=Path("Artist - Track.mp3"),
    ) == "AUTO_SAFE_CANDIDATE"


def test_orphan_candidate_original_to_remix_is_not_auto_safe(tmp_path, monkeypatch):
    assert pipeline._path_audit_orphan_candidate_tier(
        score=0.99,
        token_overlap=1.0,
        size_diff_pct=0.0,
        same_extension=True,
        old_path=Path("Artist - Song (Original Mix).mp3"),
        new_path=Path("Artist - Song (Amapiano Remix).mp3"),
    ) == "REVIEW_CAREFULLY"


def test_orphan_candidate_numeric_title_change_is_not_auto_safe(tmp_path, monkeypatch):
    assert pipeline._path_audit_orphan_candidate_tier(
        score=0.99,
        token_overlap=1.0,
        size_diff_pct=0.0,
        same_extension=True,
        old_path=Path("4th Measure Men - You.mp3"),
        new_path=Path("4th Measure Men - 4 You.mp3"),
    ) == "REVIEW_CAREFULLY"


def test_orphan_candidate_major_artist_expansion_is_not_auto_safe(tmp_path, monkeypatch):
    assert pipeline._path_audit_orphan_candidate_tier(
        score=0.99,
        token_overlap=1.0,
        size_diff_pct=0.0,
        same_extension=True,
        old_path=Path("Africanism - Tourment D'amour (Original).mp3"),
        new_path=Path("DJ Gregory Africanism MoBlack - Tourment d'Amour (Original).mp3"),
    ) == "REVIEW_CAREFULLY"


def test_orphan_candidate_duplicate_auto_safe_candidates_are_downgraded(tmp_path, monkeypatch):
    candidate_one = _audio(tmp_path / "disk_a" / "Artist - Track.mp3", b"x" * 1000)
    candidate_two = _audio(tmp_path / "disk_b" / "Artist - Track (1).mp3", b"x" * 1000)
    old_path = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist - Track (2).mp3"

    rows = pipeline._path_audit_orphan_candidates(
        [{"filepath": str(old_path), "filesize_bytes": 1000}],
        [candidate_one, candidate_two],
    )

    assert len(rows) == 2
    assert {row["candidate_path"] for row in rows} == {str(candidate_one), str(candidate_two)}
    assert {row["review_tier"] for row in rows} == {"REVIEW_CAREFULLY"}


def test_orphan_candidate_weak_tier(tmp_path, monkeypatch):
    _audio(tmp_path / "disk" / "Artist Alpha Candidate.mp3", b"x" * 5000)
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Beta Missing Delta.mp3"
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    report, _, _ = _run(tmp_path, monkeypatch, include_orphan_candidates=True)

    rows = list(csv.DictReader(_latest_orphan_candidates_csv(tmp_path).open(encoding="utf-8")))
    assert rows[0]["review_tier"] == "WEAK_MATCH"
    assert report["orphan_candidate_tiers"]["WEAK_MATCH"] == 1


def test_orphan_candidate_tier_summary_counts(tmp_path, monkeypatch):
    review_candidate = _audio(tmp_path / "disk" / "Review One Two Three Extra.mp3", b"y" * 1000)
    weak_candidate = _audio(tmp_path / "disk" / "Weak One Candidate.mp3", b"z" * 5000)
    review_orphan = tmp_path / "library" / "sorted" / "R" / "Review" / "Review One Two Three Missing Delta.mp3"
    weak_orphan = tmp_path / "library" / "sorted" / "W" / "Weak" / "Weak One Missing Delta.mp3"
    _create_db(
        tmp_path,
        [
            {"filepath": review_orphan, "filesize_bytes": review_candidate.stat().st_size},
            {"filepath": weak_orphan, "filesize_bytes": 1000},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch, include_orphan_candidates=True)

    tiers = report["orphan_candidate_tiers"]
    assert tiers["REVIEW_CAREFULLY"] >= 1
    assert tiers["WEAK_MATCH"] >= 1


def test_stale_processed_state_row_detected_when_replacement_exists(
    tmp_path, monkeypatch
):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {
                "stage": "metadata-sanitize",
                "filepath": old_path,
                "file_size": replacement.stat().st_size,
                "processed_at": "2026-05-04T00:00:00+00:00",
            },
            {
                "stage": "metadata-sanitize",
                "filepath": replacement,
                "file_size": replacement.stat().st_size,
                "processed_at": "2026-05-04T01:00:00+00:00",
            },
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["stale_processed_state_count"] == 1
    assert report["stale_processed_state_rows"] == [
        {
            "old_path": str(old_path),
            "replacement_path": str(replacement.resolve()),
            "stage": "metadata-sanitize",
            "reason": "superseded_by_existing_path",
            "source_rows": [
                {"table": "processed_state", "id": 1, "stage": "metadata-sanitize"}
            ],
        }
    ]


def test_stale_processed_state_row_not_detected_if_replacement_not_in_db(
    tmp_path, monkeypatch
):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {
                "stage": "metadata-sanitize",
                "filepath": old_path,
                "file_size": replacement.stat().st_size,
            },
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["stale_processed_state_count"] == 0
    assert report["stale_processed_state_rows"] == []


def test_stale_processed_state_row_not_detected_if_old_file_still_exists(
    tmp_path, monkeypatch
):
    old_path = _audio(tmp_path / "old" / "Artist - Clean Title (1).mp3", b"x" * 1000)
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": old_path.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": replacement.stat().st_size},
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["stale_processed_state_count"] == 0
    assert report["stale_processed_state_rows"] == []


def test_stale_processed_state_rows_csv_generated(tmp_path, monkeypatch):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": replacement.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": replacement.stat().st_size},
        ],
    )

    _run(tmp_path, monkeypatch)

    rows = list(csv.DictReader(_latest_stale_rows_csv(tmp_path).open(encoding="utf-8")))
    assert rows == [
        {
            "old_path": str(old_path),
            "replacement_path": str(replacement.resolve()),
            "stage": "metadata-sanitize",
            "reason": "superseded_by_existing_path",
        }
    ]


def test_stale_processed_state_row_excluded_from_active_current_state(
    tmp_path, monkeypatch
):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {
                "stage": "metadata-sanitize",
                "filepath": old_path,
                "file_size": replacement.stat().st_size,
                "status": "stale",
                "reason": f"superseded_by_existing_path:{replacement}",
            },
            {
                "stage": "metadata-sanitize",
                "filepath": replacement,
                "file_size": replacement.stat().st_size,
                "status": "success",
            },
        ],
    )

    report, _, _ = _run(tmp_path, monkeypatch)

    assert report["summary"]["historical_paths_count"] == 2
    assert report["summary"]["stale_processed_state_rows_total"] == 1
    assert report["summary"]["active_processed_state_rows"] == 1
    assert report["summary"]["processed_state_rows"] == 1
    assert report["summary"]["combined_db_paths"] == 1
    assert report["summary"]["missing_files"] == 0
    assert report["summary"]["orphan_db_rows"] == 0
    assert report["summary"]["stale_processed_state_count"] == 0
    assert report["missing_files"] == []
    assert report["orphan_db_rows"] == []
    assert report["stale_processed_state_rows"] == []
