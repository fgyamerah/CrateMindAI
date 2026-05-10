import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

import pipeline


def _audio(path: Path, data: bytes = b"audio") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _create_db(root: Path, rows: list[dict]) -> Path:
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
    conn.commit()
    conn.close()
    return db_path


def _latest_audit(root: Path) -> dict:
    path = sorted((root / "logs" / "path_audit").glob("path_audit_*.json"))[-1]
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_plan(root: Path) -> dict:
    path = sorted((root / "logs" / "path_reconcile").glob("*_path_reconcile_plan.json"))[-1]
    return json.loads(path.read_text(encoding="utf-8"))


def test_selected_mnt_root_rejects_home_paths(tmp_path):
    mnt_root = (tmp_path / "mnt" / "music_ssd" / "KKDJ").resolve()
    home_path = (tmp_path / "home" / "koolkatdj" / "Music" / "music" / "library" / "track.mp3").resolve()
    mnt_root.mkdir(parents=True)
    home_path.parent.mkdir(parents=True)

    with pytest.raises(ValueError, match="path outside selected root"):
        pipeline.assert_path_under_root(home_path, mnt_root)


def test_selected_home_root_rejects_mnt_paths(tmp_path):
    home_root = (tmp_path / "home" / "koolkatdj" / "Music" / "music" / "library").resolve()
    mnt_path = (tmp_path / "mnt" / "music_ssd" / "KKDJ" / "library" / "track.mp3").resolve()
    home_root.mkdir(parents=True)
    mnt_path.parent.mkdir(parents=True)

    with pytest.raises(ValueError, match="path outside selected root"):
        pipeline.assert_path_under_root(mnt_path, home_root)


def test_path_traversal_rejected(tmp_path):
    root = (tmp_path / "library").resolve()
    root.mkdir()

    with pytest.raises(ValueError, match="path outside selected root"):
        pipeline.assert_path_under_root("../outside.mp3", root)


def test_logs_and_db_resolve_under_selected_root(tmp_path):
    root = tmp_path / "selected"
    root.mkdir()

    resolved_root = pipeline.resolve_library_root(SimpleNamespace(root=str(root)))
    db_path = pipeline._path_audit_db_path(resolved_root)

    assert resolved_root == root.resolve()
    assert db_path == root.resolve() / "logs" / "processed.db"
    assert pipeline.assert_path_under_root(root / "logs" / "path_audit", root) == root.resolve() / "logs" / "path_audit"


def test_path_audit_reports_mixed_root_db_paths_separately(tmp_path, monkeypatch):
    selected = tmp_path / "mnt" / "music_ssd" / "KKDJ"
    other = tmp_path / "home" / "koolkatdj" / "Music" / "music" / "library"
    selected.mkdir(parents=True)
    other.mkdir(parents=True)
    selected_candidate = _audio(selected / "library" / "track.mp3", b"same")
    mixed_missing = other / "track.mp3"
    _create_db(
        selected,
        [
            {"filepath": selected_candidate, "filesize_bytes": selected_candidate.stat().st_size},
            {"filepath": mixed_missing, "filesize_bytes": selected_candidate.stat().st_size},
        ],
    )
    monkeypatch.setattr(pipeline.config, "DB_PATH", other / "logs" / "processed.db")

    rc = pipeline.run_path_audit(SimpleNamespace(root=str(selected)))

    report = _latest_audit(selected)
    assert rc == 0
    assert report["summary"]["mixed_root_db_paths"] == 1
    assert report["mixed_root_db_paths"][0]["filepath"] == str(mixed_missing)
    assert report["summary"]["missing_files"] == 0
    assert report["summary"]["possible_renames"] == 0


def test_path_reconcile_does_not_plan_updates_across_roots(tmp_path, monkeypatch):
    selected = tmp_path / "mnt" / "music_ssd" / "KKDJ"
    other = tmp_path / "home" / "koolkatdj" / "Music" / "music" / "library"
    selected.mkdir(parents=True)
    other.mkdir(parents=True)
    selected_candidate = _audio(selected / "library" / "Track Name.mp3", b"x" * 1000)
    mixed_missing = other / "Track Name (1).mp3"
    _create_db(selected, [{"filepath": mixed_missing, "filesize_bytes": selected_candidate.stat().st_size}])
    monkeypatch.setattr(pipeline.config, "DB_PATH", other / "logs" / "processed.db")

    rc = pipeline.run_path_reconcile(
        SimpleNamespace(root=str(selected), dry_run=True, apply=False)
    )

    plan = _latest_plan(selected)
    assert rc == 0
    assert plan["audit_summary"]["mixed_root_db_paths"] == 1
    assert plan["planned_actions"] == []
