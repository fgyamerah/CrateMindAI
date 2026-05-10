"""
DB-only manual artist/title edits for the tracks table.

This module never writes audio tags, renames files, touches BPM/key/cue data,
or modifies processed_state. It updates only changed artist/title fields and
optionally tracks.updated_at when that column exists.
"""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

_SPACE_RE = re.compile(r"\s+")
FIELD_NAMES = ("artist", "title")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _db_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve(strict=False) / "logs" / "processed.db"


def _audit_path(root: str | Path) -> Path:
    return Path(root).expanduser().resolve(strict=False) / "logs" / "manual_metadata" / "manual_metadata_audit.jsonl"


def normalize_value(value: Any) -> str:
    return _SPACE_RE.sub(" ", str(value or "").strip())


def validate_payload(payload: dict[str, Any]) -> tuple[int, dict[str, str], list[str]]:
    try:
        track_id = int(payload.get("track_id"))
    except Exception as exc:
        raise ValueError("track_id must be a positive integer") from exc
    if track_id <= 0:
        raise ValueError("track_id must be a positive integer")

    proposed = {
        "artist": normalize_value(payload.get("artist")),
        "title": normalize_value(payload.get("title")),
    }
    empty = [field for field, value in proposed.items() if not value]
    if empty:
        raise ValueError(f"{', '.join(empty)} cannot be empty")

    warnings: list[str] = []
    for field in FIELD_NAMES:
        raw = str(payload.get(field) or "")
        if raw != proposed[field]:
            warnings.append(f"{field} whitespace normalized")
    return track_id, proposed, warnings


@contextmanager
def _connect(db_path: Path, *, readonly: bool) -> Iterator[sqlite3.Connection]:
    if not db_path.exists():
        raise FileNotFoundError(f"pipeline database not found at {db_path}")
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}


def _select_track(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    columns = _columns(conn)
    select_exprs = [
        column if column in columns else f"NULL AS {column}"
        for column in ("id", "filepath", "filename", "artist", "title")
    ]
    return conn.execute(
        f"SELECT {', '.join(select_exprs)} FROM tracks WHERE id = ?",
        (track_id,),
    ).fetchone()


def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "artist": row["artist"],
        "title": row["title"],
    }


def _diff(current: dict[str, Any], proposed: dict[str, str]) -> list[dict[str, Any]]:
    return [
        {
            "field": field,
            "current": current.get(field),
            "proposed": proposed[field],
            "changed": normalize_value(current.get(field)) != proposed[field],
        }
        for field in FIELD_NAMES
    ]


def preview(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    track_id, proposed, warnings = validate_payload(payload)
    with _connect(_db_path(root), readonly=True) as conn:
        row = _select_track(conn, track_id)
        if row is None:
            raise LookupError(f"track {track_id} not found")
        current = _snapshot(row)

    changed_fields = [
        field for field in FIELD_NAMES
        if normalize_value(current.get(field)) != proposed[field]
    ]
    return {
        "track_id": track_id,
        "filepath": row["filepath"],
        "filename": row["filename"],
        "current": current,
        "proposed": proposed,
        "changed_fields": changed_fields,
        "no_op": not changed_fields,
        "validation_warnings": warnings,
        "diff": _diff(current, proposed),
    }


def _append_audit(root: str | Path, entry: dict[str, Any]) -> str:
    path = _audit_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
    return str(path)


def apply(root: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    preview_payload = preview(root, payload)
    if preview_payload["no_op"]:
        return {
            **preview_payload,
            "applied_fields": [],
            "before": preview_payload["current"],
            "after": preview_payload["current"],
            "audit_path": None,
        }

    db_path = _db_path(root)
    applied_at = _utc_now()
    with _connect(db_path, readonly=False) as conn:
        try:
            row = _select_track(conn, int(preview_payload["track_id"]))
            if row is None:
                raise LookupError(f"track {preview_payload['track_id']} not found")
            before = _snapshot(row)
            columns = _columns(conn)
            sets: list[str] = []
            values: list[Any] = []
            for field in preview_payload["changed_fields"]:
                sets.append(f"{field} = ?")
                values.append(preview_payload["proposed"][field])
            if "updated_at" in columns:
                sets.append("updated_at = ?")
                values.append(applied_at)
            values.append(preview_payload["track_id"])
            conn.execute(f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?", tuple(values))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    after = {
        **before,
        **{field: preview_payload["proposed"][field] for field in preview_payload["changed_fields"]},
    }
    audit_entry = {
        "timestamp": applied_at,
        "track_id": preview_payload["track_id"],
        "filepath": preview_payload["filepath"],
        "before": before,
        "after": after,
        "changed_fields": preview_payload["changed_fields"],
    }
    audit_path = _append_audit(root, audit_entry)
    return {
        **preview_payload,
        "applied_fields": preview_payload["changed_fields"],
        "before": before,
        "after": after,
        "audit_path": audit_path,
    }
