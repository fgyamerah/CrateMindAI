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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(root: Path, monkeypatch):
    missing_fallback = root / "logs" / "missing-fallback.db"
    monkeypatch.setattr(pipeline.config, "DB_PATH", missing_fallback)
    rc = pipeline.run_path_reconcile(
        SimpleNamespace(root=str(root), dry_run=True, apply=False)
    )
    assert rc == 0
    return _latest_plan(root)


def _latest_plan(root: Path) -> tuple[dict, Path, Path]:
    plan_dir = root / "logs" / "path_reconcile"
    json_path = sorted(plan_dir.glob("*_path_reconcile_plan.json"))[-1]
    text_path = sorted(plan_dir.glob("*_path_reconcile_plan.txt"))[-1]
    return json.loads(json_path.read_text(encoding="utf-8")), json_path, text_path


def _latest_plan_csv(root: Path) -> Path:
    plan_dir = root / "logs" / "path_reconcile"
    return sorted(plan_dir.glob("*_path_reconcile_plan.csv"))[-1]


def _actions(plan: dict, action_name: str) -> list[dict]:
    return [
        action for action in plan["planned_actions"]
        if action["action"] == action_name
    ]


def _processed_rows(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id, stage, filepath, status, reason FROM processed_state ORDER BY id"
            ).fetchall()
        ]
    finally:
        conn.close()


def _auto_safe_action(old_path: Path, new_path: Path) -> dict:
    return {
        "action": "update_path_reference",
        "old_path": str(old_path),
        "new_path": str(new_path),
        "review_tier": "AUTO_SAFE_CANDIDATE",
        "reason": "test",
    }


def _latest_apply_log(root: Path) -> Path:
    log_dir = root / "logs" / "path_reconcile"
    return sorted(log_dir.glob("*_apply_auto_safe.log"))[-1]


def _latest_mark_stale_log(root: Path) -> Path:
    log_dir = root / "logs" / "path_reconcile"
    return sorted(log_dir.glob("*_mark_stale_pstate.log"))[-1]


def _mark_stale_action(row_id: int | None, old_path: Path, replacement_path: Path) -> dict:
    source_rows = []
    if row_id is not None:
        source_rows = [{"table": "processed_state", "id": row_id, "stage": "metadata-sanitize"}]
    return {
        "action": "mark_stale_processed_state_path",
        "old_path": str(old_path),
        "replacement_path": str(replacement_path),
        "stage": "metadata-sanitize",
        "reason": "superseded_by_existing_path",
        "source_rows": source_rows,
    }


def test_dry_run_creates_plan_files(tmp_path, monkeypatch):
    _create_db(tmp_path, [])

    plan, json_path, text_path = _run(tmp_path, monkeypatch)

    assert plan["dry_run"] is True
    assert plan["apply_supported"] is False
    assert json_path.parent == tmp_path / "logs" / "path_reconcile"
    assert text_path.parent == tmp_path / "logs" / "path_reconcile"
    assert json_path.name.endswith("_path_reconcile_plan.json")
    assert text_path.name.endswith("_path_reconcile_plan.txt")
    assert "path-reconcile DRY-RUN PLAN" in text_path.read_text(encoding="utf-8")


def test_dry_run_does_not_modify_db_or_move_files(tmp_path, monkeypatch):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    candidate = _audio(tmp_path / "music" / "moved.mp3", b"candidate")
    old_path = tmp_path / "old" / "moved.mp3"
    db_path = _create_db(
        tmp_path,
        [
            {"filepath": tracked, "filesize_bytes": tracked.stat().st_size},
            {"filepath": old_path, "filesize_bytes": candidate.stat().st_size},
        ],
    )
    before_db_hash = _sha256(db_path)
    before_audio = {
        tracked: _sha256(tracked),
        candidate: _sha256(candidate),
    }

    _run(tmp_path, monkeypatch)

    assert _sha256(db_path) == before_db_hash
    assert tracked.exists()
    assert candidate.exists()
    assert {path: _sha256(path) for path in before_audio} == before_audio
    assert not old_path.exists()


def test_apply_exits_with_not_implemented_error(tmp_path, monkeypatch, capsys):
    _create_db(tmp_path, [])
    monkeypatch.setattr(pipeline.config, "DB_PATH", tmp_path / "logs" / "missing.db")

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(root=str(tmp_path), dry_run=False, apply=True)
    )

    err = capsys.readouterr().err
    assert rc == 2
    assert "path-reconcile --apply is not implemented yet" in err
    assert not (tmp_path / "logs" / "path_reconcile").exists()


def test_apply_auto_safe_only_updates_only_auto_safe_processed_state_rows(
    tmp_path, monkeypatch
):
    safe_candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1000)
    unsafe_candidate = _audio(tmp_path / "new" / "Artist - Song (Remix).mp3", b"y" * 1000)
    safe_old = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    unsafe_old = tmp_path / "old" / "Artist - Song (Original Mix).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "library-organize", "filepath": safe_old, "file_size": 1000},
            {"stage": "library-organize", "filepath": unsafe_old, "file_size": 1000},
        ],
    )
    monkeypatch.setattr(pipeline.config, "DB_PATH", tmp_path / "logs" / "missing.db")

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            root=str(tmp_path),
            dry_run=False,
            apply=False,
            apply_auto_safe_only=True,
        )
    )

    assert rc == 0
    rows = _processed_rows(db_path)
    assert rows[0]["filepath"] == str(safe_candidate.resolve())
    assert rows[1]["filepath"] == str(unsafe_old)
    log_text = _latest_apply_log(tmp_path).read_text(encoding="utf-8")
    assert "total_candidates=1" in log_text
    assert "applied_count=1" in log_text
    assert "rows_updated=1" in log_text
    assert "skipped_count=0" in log_text
    assert unsafe_candidate.exists()


def test_apply_auto_safe_only_does_not_move_or_modify_files(tmp_path):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"audio-bytes")
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "library-organize", "filepath": old_path, "file_size": candidate.stat().st_size},
        ],
    )
    before_hash = _sha256(candidate)

    result = pipeline._path_reconcile_apply_auto_safe(
        tmp_path,
        db_path,
        {"planned_actions": [_auto_safe_action(old_path, candidate)]},
    )

    assert result["rows_updated"] == 1
    assert candidate.exists()
    assert _sha256(candidate) == before_hash
    assert not old_path.exists()


def test_apply_auto_safe_only_rolls_back_on_failure(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "library-organize", "filepath": old_path, "file_size": 1000},
        ],
    )
    real_connect = sqlite3.connect

    class FailingConnection:
        def __init__(self, inner):
            self.inner = inner

        @property
        def row_factory(self):
            return self.inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value

        def execute(self, sql, params=()):
            cursor = self.inner.execute(sql, params)
            if sql.startswith("UPDATE processed_state"):
                raise RuntimeError("simulated update failure")
            return cursor

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            return self.inner.rollback()

        def close(self):
            return self.inner.close()

    def failing_connect(*args, **kwargs):
        return FailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", failing_connect)

    try:
        pipeline._path_reconcile_apply_auto_safe(
            tmp_path,
            db_path,
            {"planned_actions": [_auto_safe_action(old_path, candidate)]},
        )
        assert False, "expected simulated failure"
    except RuntimeError:
        pass

    rows = _processed_rows(db_path)
    assert rows[0]["filepath"] == str(old_path)


def test_apply_auto_safe_only_skips_ambiguous_old_path_candidates(tmp_path):
    candidate_one = _audio(tmp_path / "new-a" / "Artist - Clean Title.mp3", b"x" * 1000)
    candidate_two = _audio(tmp_path / "new-b" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "library-organize", "filepath": old_path, "file_size": 1000},
        ],
    )

    result = pipeline._path_reconcile_apply_auto_safe(
        tmp_path,
        db_path,
        {
            "planned_actions": [
                _auto_safe_action(old_path, candidate_one),
                _auto_safe_action(old_path, candidate_two),
            ]
        },
    )

    assert result["applied_count"] == 0
    assert result["skipped_count"] == 2
    assert {item["reason"] for item in result["skipped"]} == {
        "multiple_candidate_matches_for_old_path"
    }
    assert _processed_rows(db_path)[0]["filepath"] == str(old_path)


def test_apply_auto_safe_only_skips_same_stage_new_path_conflict(tmp_path):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "library-organize", "filepath": old_path, "file_size": 1000},
            {"stage": "library-organize", "filepath": candidate, "file_size": 1000},
        ],
    )

    result = pipeline._path_reconcile_apply_auto_safe(
        tmp_path,
        db_path,
        {"planned_actions": [_auto_safe_action(old_path, candidate)]},
    )

    assert result["applied_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"].startswith(
        "new_path_already_exists_in_same_stage"
    )
    assert _processed_rows(db_path)[0]["filepath"] == str(old_path)


def test_mark_stale_pstate_marks_stale_rows(tmp_path, monkeypatch):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": replacement.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": replacement.stat().st_size},
        ],
    )
    before_hash = _sha256(replacement)

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(
            root=str(tmp_path),
            dry_run=False,
            apply=False,
            apply_auto_safe_only=False,
            mark_stale_pstate=True,
        )
    )

    assert rc == 0
    rows = _processed_rows(db_path)
    assert rows[0]["status"] == "stale"
    assert rows[0]["reason"] == f"superseded_by_existing_path:{replacement.resolve()}"
    assert rows[0]["filepath"] == str(old_path)
    assert rows[1]["status"] == "success"
    assert replacement.exists()
    assert _sha256(replacement) == before_hash
    log_text = _latest_mark_stale_log(tmp_path).read_text(encoding="utf-8")
    assert "marked_count=1" in log_text
    assert "rows_updated=1" in log_text
    assert "skipped_count=0" in log_text


def test_mark_stale_pstate_does_not_mark_non_stale_rows(tmp_path):
    old_path = _audio(tmp_path / "old" / "Artist - Clean Title (1).mp3", b"x" * 1000)
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": old_path.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": replacement.stat().st_size},
        ],
    )

    result = pipeline._path_reconcile_mark_stale_pstate(
        tmp_path,
        db_path,
        {"planned_actions": [_mark_stale_action(1, old_path, replacement)]},
    )

    assert result["marked_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "old_path_exists_on_disk"
    assert _processed_rows(db_path)[0]["status"] == "success"


def test_mark_stale_pstate_rolls_back_on_failure(tmp_path, monkeypatch):
    replacement = _audio(tmp_path / "sorted" / "Artist - Clean Title.mp3", b"x" * 1000)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": replacement.stat().st_size},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": replacement.stat().st_size},
        ],
    )
    real_connect = sqlite3.connect

    class FailingConnection:
        def __init__(self, inner):
            self.inner = inner

        @property
        def row_factory(self):
            return self.inner.row_factory

        @row_factory.setter
        def row_factory(self, value):
            self.inner.row_factory = value

        def execute(self, sql, params=()):
            cursor = self.inner.execute(sql, params)
            if sql.startswith("UPDATE processed_state"):
                raise RuntimeError("simulated stale update failure")
            return cursor

        def commit(self):
            return self.inner.commit()

        def rollback(self):
            return self.inner.rollback()

        def close(self):
            return self.inner.close()

    def failing_connect(*args, **kwargs):
        return FailingConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(sqlite3, "connect", failing_connect)

    try:
        pipeline._path_reconcile_mark_stale_pstate(
            tmp_path,
            db_path,
            {"planned_actions": [_mark_stale_action(1, old_path, replacement)]},
        )
        assert False, "expected simulated failure"
    except RuntimeError:
        pass

    assert _processed_rows(db_path)[0]["status"] == "success"


def test_mark_stale_pstate_skips_missing_replacement(tmp_path):
    replacement = tmp_path / "sorted" / "Artist - Clean Title.mp3"
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    db_path = _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": 1000},
            {"stage": "metadata-sanitize", "filepath": replacement, "file_size": 1000},
        ],
    )

    result = pipeline._path_reconcile_mark_stale_pstate(
        tmp_path,
        db_path,
        {"planned_actions": [_mark_stale_action(1, old_path, replacement)]},
    )

    assert result["marked_count"] == 0
    assert result["skipped_count"] == 1
    assert result["skipped"][0]["reason"] == "replacement_path_missing_on_disk"
    rows = _processed_rows(db_path)
    assert rows[0]["status"] == "success"
    assert rows[0]["filepath"] == str(old_path)


def test_missing_file_without_match_produces_mark_orphan_candidate(
    tmp_path, monkeypatch
):
    orphan = tmp_path / "old" / "orphan.aiff"
    _audio(tmp_path / "music" / "other.mp3", b"other")
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 999999}])

    plan, _, _ = _run(tmp_path, monkeypatch)

    orphan_actions = _actions(plan, "mark_orphan_candidate")
    assert orphan_actions == [
        {
            "action": "mark_orphan_candidate",
            "old_path": str(orphan),
            "reason": "missing_file_no_rename_candidate",
            "risk": "REVIEW_REQUIRED",
        }
    ]


def test_possible_rename_produces_update_path_reference(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "renamed.mp3", b"candidate")
    old_path = tmp_path / "old" / "renamed.mp3"
    _create_db(
        tmp_path,
        [{"filepath": old_path, "filesize_bytes": candidate.stat().st_size}],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "update_path_reference")
    assert len(actions) == 1
    assert actions[0]["old_path"] == str(old_path)
    assert actions[0]["new_path"] == str(candidate.resolve())
    assert actions[0]["confidence"] == 0.90
    assert actions[0]["reason"] == "same_basename"


def test_duplicate_db_path_produces_investigate_duplicate_path(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "dupe.mp3", b"dupe")
    _create_db(
        tmp_path,
        [
            {"filepath": tracked, "filesize_bytes": tracked.stat().st_size},
            {"filepath": tracked, "filesize_bytes": tracked.stat().st_size},
        ],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "investigate_duplicate_path")
    assert len(actions) == 1
    assert actions[0]["filepath"] == str(tracked)
    assert actions[0]["count"] == 2
    assert actions[0]["row_ids"] == [1, 2]


def test_repeated_processed_state_different_stages_does_not_plan_duplicate_action(
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

    plan, _, _ = _run(tmp_path, monkeypatch)

    assert plan["audit_summary"]["repeated_processed_state_paths"] == 1
    assert plan["audit_summary"]["historical_paths_count"] == 2
    assert plan["audit_summary"]["processed_state_rows"] == 1
    assert plan["audit_summary"]["duplicate_db_entries"] == 0
    assert _actions(plan, "investigate_duplicate_path") == []


def test_processed_state_same_stage_duplicate_plans_investigation(
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

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "investigate_duplicate_path")
    assert len(actions) == 1
    assert actions[0]["filepath"] == str(tracked)
    assert actions[0]["count"] == 2


def test_stale_queue_with_matching_rename_produces_update_queue_reference(
    tmp_path, monkeypatch
):
    candidate = _audio(tmp_path / "new" / "queued.mp3", b"candidate")
    old_path = tmp_path / "old" / "queued.mp3"
    queue = tmp_path / "data" / "intelligence" / "artist_review_queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(json.dumps([{"filepath": str(old_path)}]), encoding="utf-8")
    _create_db(
        tmp_path,
        [{"filepath": old_path, "filesize_bytes": candidate.stat().st_size}],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "update_queue_reference")
    assert len(actions) == 1
    assert actions[0]["queue_file"] == str(queue.resolve())
    assert actions[0]["old_path"] == str(old_path)
    assert actions[0]["new_path"] == str(candidate.resolve())
    assert actions[0]["unresolved"] is False
    assert actions[0]["reason"] == "candidate_found_from_path_audit"


def test_stale_queue_without_candidate_produces_unresolved_queue_action(
    tmp_path, monkeypatch
):
    tracked = _audio(tmp_path / "music" / "tracked.mp3", b"tracked")
    missing = tmp_path / "old" / "missing-queue.mp3"
    queue = tmp_path / "data" / "intelligence" / "artist_review_queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(json.dumps([{"filepath": str(missing)}]), encoding="utf-8")
    _create_db(
        tmp_path,
        [{"filepath": tracked, "filesize_bytes": tracked.stat().st_size}],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "update_queue_reference")
    assert len(actions) == 1
    assert actions[0]["old_path"] == str(missing)
    assert actions[0]["new_path"] is None
    assert actions[0]["unresolved"] is True
    assert actions[0]["reason"] == "unresolved_no_candidate"


def test_reconcile_uses_processed_state_missing_paths_for_orphan_plan(
    tmp_path, monkeypatch
):
    missing = tmp_path / "old" / "processed-orphan.mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"filepath": missing, "file_size": 123},
        ],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    assert plan["audit_summary"]["tracks_rows"] == 0
    assert plan["audit_summary"]["processed_state_rows"] == 1
    assert plan["audit_summary"]["combined_db_paths"] == 1
    actions = _actions(plan, "mark_orphan_candidate")
    assert actions == [
        {
            "action": "mark_orphan_candidate",
            "old_path": str(missing),
            "reason": "missing_file_no_rename_candidate",
            "risk": "REVIEW_REQUIRED",
        }
    ]


def test_reconcile_uses_tracks_when_tracks_are_populated(tmp_path, monkeypatch):
    processed_only = _audio(tmp_path / "music" / "processed-only.mp3", b"processed")
    missing_track = tmp_path / "old" / "missing-track.mp3"
    _create_db(
        tmp_path,
        [{"filepath": missing_track, "filesize_bytes": 123}],
        processed_rows=[
            {"filepath": processed_only, "file_size": processed_only.stat().st_size},
        ],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    assert plan["audit_summary"]["canonical_source"] == "tracks"
    assert plan["audit_summary"]["combined_db_paths"] == 1
    actions = _actions(plan, "mark_orphan_candidate")
    assert len(actions) == 1
    assert actions[0]["old_path"] == str(missing_track)


def test_reconcile_uses_processed_state_possible_rename(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "processed-rename.mp3", b"candidate")
    old_path = tmp_path / "old" / "processed-rename.mp3"
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"filepath": old_path, "file_size": candidate.stat().st_size},
        ],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "update_path_reference")
    assert len(actions) == 1
    assert actions[0]["old_path"] == str(old_path)
    assert actions[0]["new_path"] == str(candidate.resolve())
    assert plan["audit_findings"]["missing_files"][0]["sources"] == ["processed_state"]


def test_reconcile_uses_fuzzy_rename_instead_of_orphan(tmp_path, monkeypatch):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1030)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    plan, _, _ = _run(tmp_path, monkeypatch)

    rename_actions = _actions(plan, "update_path_reference")
    assert len(rename_actions) == 1
    assert rename_actions[0]["old_path"] == str(old_path)
    assert rename_actions[0]["new_path"] == str(candidate.resolve())
    assert rename_actions[0]["reason"] == "fuzzy_filename"
    assert rename_actions[0]["risk"] == "REVIEW_REQUIRED"
    assert _actions(plan, "mark_orphan_candidate") == []


def test_reconcile_plan_csv_includes_review_required_for_fuzzy_rename(
    tmp_path, monkeypatch
):
    candidate = _audio(tmp_path / "new" / "Artist - Clean Title.mp3", b"x" * 1030)
    old_path = tmp_path / "old" / "Artist - Clean Title (1).mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    _run(tmp_path, monkeypatch)

    rows = list(csv.DictReader(_latest_plan_csv(tmp_path).open(encoding="utf-8")))
    assert rows == [
        {
            "action": "update_path_reference",
            "confidence": "0.8",
            "reason": "fuzzy_filename",
            "old_path": str(old_path),
            "new_path": str(candidate.resolve()),
            "risk": "REVIEW_REQUIRED",
        }
    ]


def test_reconcile_ignores_historical_processed_state_missing_path_when_final_exists(
    tmp_path, monkeypatch
):
    old_path = tmp_path / "old" / "historical.mp3"
    final_path = _audio(tmp_path / "sorted" / "H" / "Historical" / "historical.mp3", b"final")
    _create_db(
        tmp_path,
        [],
        processed_rows=[
            {"stage": "metadata-sanitize", "filepath": old_path, "file_size": 100},
            {"stage": "library-organize", "filepath": final_path, "file_size": final_path.stat().st_size},
        ],
    )

    plan, _, _ = _run(tmp_path, monkeypatch)

    assert plan["audit_summary"]["historical_paths_count"] == 2
    assert plan["audit_summary"]["processed_state_rows"] == 1
    assert plan["audit_summary"]["missing_files"] == 0
    assert plan["audit_summary"]["orphan_db_rows"] == 0
    assert _actions(plan, "mark_orphan_candidate") == []
    assert _actions(plan, "update_path_reference") == []


def test_reconcile_uses_relocation_candidate_for_update_path_reference(
    tmp_path, monkeypatch
):
    candidate = _audio(
        tmp_path / "instrumental" / "Black Coffee Drive Instrumental.mp3",
        b"x" * 1040,
    )
    old_path = tmp_path / "library" / "sorted" / "B" / "Black Coffee" / "Black Coffee - Drive.mp3"
    _create_db(tmp_path, [{"filepath": old_path, "filesize_bytes": 1000}])

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "update_path_reference")
    assert len(actions) == 1
    assert actions[0]["old_path"] == str(old_path)
    assert actions[0]["new_path"] == str(candidate.resolve())
    assert actions[0]["reason"] == "relocation"
    assert actions[0]["confidence"] == 0.65
    assert actions[0]["risk"] == "REVIEW_REQUIRED"
    assert _actions(plan, "mark_orphan_candidate") == []


def test_reconcile_does_not_create_action_from_review_only_orphan_candidates(
    tmp_path, monkeypatch
):
    _audio(tmp_path / "disk" / "Artist Alpha Beta.mp3", b"x" * 5000)
    orphan = tmp_path / "library" / "sorted" / "A" / "Artist" / "Artist Alpha Missing.mp3"
    _create_db(tmp_path, [{"filepath": orphan, "filesize_bytes": 1000}])

    plan, _, _ = _run(tmp_path, monkeypatch)

    assert plan["audit_summary"]["orphan_db_rows"] == 1
    assert _actions(plan, "update_path_reference") == []
    orphan_actions = _actions(plan, "mark_orphan_candidate")
    assert len(orphan_actions) == 1
    assert orphan_actions[0]["old_path"] == str(orphan)


def test_reconcile_includes_stale_processed_state_report_only_action(
    tmp_path, monkeypatch
):
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

    plan, _, _ = _run(tmp_path, monkeypatch)

    actions = _actions(plan, "mark_stale_processed_state_path")
    assert actions == [
        {
            "action": "mark_stale_processed_state_path",
            "old_path": str(old_path),
            "replacement_path": str(replacement.resolve()),
            "stage": "metadata-sanitize",
            "reason": "superseded_by_existing_path",
            "source_rows": [
                {"table": "processed_state", "id": 1, "stage": "metadata-sanitize"}
            ],
            "risk": "LOW",
            "report_only": True,
        }
    ]
