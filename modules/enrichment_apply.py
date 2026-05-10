"""
Controlled enrichment apply helper.

Reads approved review decisions from data/intelligence/enrichment_review_state.json
and applies only safe metadata columns to the tracks table.
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from modules.parser import is_valid_artist, is_valid_title


ALLOWED_COLUMNS = ("artist", "title", "album", "label", "isrc")


@dataclass(frozen=True)
class ApplySkip:
    track_id: int | None
    filepath: str
    reason: str
    note: str


@dataclass(frozen=True)
class ApplyChange:
    track_id: int
    filepath: str
    fields: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    confidence: str
    provider: str
    score: float | None
    review_status: str


def _resolve_root(root: str | Path) -> Path:
    resolved = Path(root).expanduser().resolve(strict=False)
    if not resolved.is_absolute():
        raise ValueError(f"root must be absolute: {resolved}")
    return resolved


def _db_path(root: Path) -> Path:
    return root / "logs" / "processed.db"


def _state_path(root: Path) -> Path:
    return root / "data" / "intelligence" / "enrichment_review_state.json"


def _log_path(root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return root / "logs" / "enrichment" / f"{stamp}_apply_approved.log"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _columns(db_path: Path) -> list[str]:
    if not db_path.exists():
        return []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("PRAGMA table_info(tracks)").fetchall()
        return [str(row["name"]) for row in rows]
    finally:
        conn.close()


def _track_row_by_id(conn: sqlite3.Connection, track_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()


def _track_row_by_path(conn: sqlite3.Connection, filepath: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM tracks WHERE filepath = ?", (filepath,)).fetchone()


def _best_match(item: dict[str, Any]) -> dict[str, Any] | None:
    queue_item = item.get("queue_item") if isinstance(item.get("queue_item"), dict) else None
    if queue_item and isinstance(queue_item.get("best_match"), dict):
        return queue_item["best_match"]
    if isinstance(item.get("best_match"), dict):
        return item["best_match"]
    return None


def _queue_item(item: dict[str, Any]) -> dict[str, Any]:
    queue_item = item.get("queue_item")
    if isinstance(queue_item, dict):
        return queue_item
    return item


def _confidence(item: dict[str, Any]) -> str:
    queue_item = _queue_item(item)
    confidence = str(queue_item.get("confidence") or item.get("confidence") or "").upper()
    return confidence


def _provider(item: dict[str, Any]) -> str:
    queue_item = _queue_item(item)
    return str(queue_item.get("provider") or item.get("provider") or "")


def _score(item: dict[str, Any]) -> float | None:
    queue_item = _queue_item(item)
    for key in ("score",):
        value = queue_item.get(key, item.get(key))
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _skip(
    track_id: int | None,
    filepath: str,
    reason: str,
    note: str,
    skips: list[ApplySkip],
) -> None:
    skips.append(ApplySkip(track_id=track_id, filepath=filepath, reason=reason, note=note))


def _select_updates(columns: list[str], row: sqlite3.Row, candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    updates: dict[str, Any] = {}

    for field in ALLOWED_COLUMNS:
        if field not in columns:
            continue
        current_value = row[field] if field in row.keys() else None
        before[field] = current_value
        candidate_value = candidate.get(field)
        if _is_blank(current_value) and not _is_blank(candidate_value):
            updates[field] = candidate_value
            after[field] = candidate_value
        else:
            after[field] = current_value

    return updates, {"before": before, "after": after}


def build_approved_enrichment_plan(root: str | Path) -> dict[str, Any]:
    root_path = _resolve_root(root)
    db_path = _db_path(root_path)
    state_path = _state_path(root_path)
    log_path = _log_path(root_path)
    state = _load_json(state_path)
    columns = _columns(db_path)

    approved_seen = 0
    proposed: list[ApplyChange] = []
    skips: list[ApplySkip] = []

    if not db_path.exists():
        return {
            "root": str(root_path),
            "db_path": str(db_path),
            "state_path": str(state_path),
            "log_path": str(log_path),
            "dry_run": True,
            "approved_seen": 0,
            "proposed_count": 0,
            "applied_count": 0,
            "skipped_count": 0,
            "changes": [],
            "skipped": [],
            "columns": columns,
        }

    items = state.get("items", {})
    if not isinstance(items, dict):
        items = {}

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        for raw_key, raw_item in items.items():
            if not isinstance(raw_item, dict):
                continue
            if str(raw_item.get("review_status", "")).lower() != "approved":
                continue
            approved_seen += 1

            track_id = raw_item.get("track_id")
            try:
                track_id_int = int(track_id) if track_id is not None else None
            except Exception:
                track_id_int = None

            queue_item = _queue_item(raw_item)
            filepath = str(queue_item.get("filepath") or raw_item.get("filepath") or "")
            if not filepath:
                _skip(track_id_int, filepath, "missing_filepath", "approved item has no filepath", skips)
                continue

            best_match = _best_match(raw_item)
            if not isinstance(best_match, dict):
                _skip(track_id_int, filepath, "missing_best_match", "approved item has no best_match", skips)
                continue

            confidence = _confidence(raw_item)
            if confidence != "HIGH":
                _skip(track_id_int, filepath, "confidence_not_high", f"confidence={confidence or 'UNKNOWN'}", skips)
                continue

            artist = str(best_match.get("artist") or "").strip()
            title = str(best_match.get("title") or "").strip()
            if not artist or not title:
                _skip(track_id_int, filepath, "empty_best_match", "best_match missing artist/title", skips)
                continue

            if not is_valid_artist(artist) or not is_valid_title(title):
                _skip(track_id_int, filepath, "suspicious_mismatch", "best_match failed safety validation", skips)
                continue

            row = None
            if track_id_int is not None:
                row = _track_row_by_id(conn, track_id_int)
            if row is None:
                row = _track_row_by_path(conn, filepath)
            if row is None:
                _skip(track_id_int, filepath, "track_missing_from_tracks", "no matching tracks row", skips)
                continue

            db_filepath = str(row["filepath"])
            if filepath and Path(filepath).resolve(strict=False) != Path(db_filepath).resolve(strict=False):
                _skip(track_id_int, filepath, "suspicious_mismatch", "review filepath does not match tracks row", skips)
                continue
            if track_id_int is not None and int(row["id"]) != track_id_int:
                _skip(track_id_int, filepath, "suspicious_mismatch", "review track_id does not match tracks row", skips)
                continue

            updates, field_snapshot = _select_updates(columns, row, best_match)
            if not updates:
                _skip(track_id_int, filepath, "no_missing_fields", "tracks row already has values for approved fields", skips)
                continue

            proposed.append(
                ApplyChange(
                    track_id=int(row["id"]),
                    filepath=db_filepath,
                    fields=sorted(updates.keys()),
                    before=field_snapshot["before"],
                    after=field_snapshot["after"],
                    confidence=confidence,
                    provider=_provider(raw_item),
                    score=_score(raw_item),
                    review_status="approved",
                )
            )
    finally:
        conn.close()

    return {
        "root": str(root_path),
        "db_path": str(db_path),
        "state_path": str(state_path),
        "log_path": str(log_path),
        "dry_run": True,
        "approved_seen": approved_seen,
        "proposed_count": len(proposed),
        "applied_count": 0,
        "skipped_count": len(skips),
        "changes": [change.__dict__ for change in proposed],
        "skipped": [skip.__dict__ for skip in skips],
        "columns": columns,
    }


def apply_approved_enrichment(root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    plan = build_approved_enrichment_plan(root)
    root_path = Path(plan["root"])
    log_path = Path(plan["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    if not apply or plan["proposed_count"] == 0 or not Path(plan["db_path"]).exists():
        log_path.write_text(
            json.dumps(plan, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return plan

    db_path = Path(plan["db_path"])
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    applied = 0
    applied_changes: list[dict[str, Any]] = []
    skipped = list(plan["skipped"])

    try:
        conn.execute("BEGIN")
        columns = plan["columns"]
        for change in plan["changes"]:
            row = _track_row_by_id(conn, int(change["track_id"]))
            if row is None:
                skipped.append(
                    ApplySkip(
                        track_id=int(change["track_id"]),
                        filepath=str(change["filepath"]),
                        reason="track_missing_from_tracks",
                        note="row disappeared before apply",
                    ).__dict__
                )
                continue
            updates = {field: row_data for field, row_data in change["after"].items() if field in columns and _is_blank(row[field]) and not _is_blank(row_data)}
            if not updates:
                skipped.append(
                    ApplySkip(
                        track_id=int(change["track_id"]),
                        filepath=str(change["filepath"]),
                        reason="no_missing_fields",
                        note="row no longer has missing fields",
                    ).__dict__
                )
                continue
            placeholders = ", ".join(f"{field}=?" for field in updates)
            params = list(updates.values()) + [int(change["track_id"])]
            conn.execute(f"UPDATE tracks SET {placeholders} WHERE id=?", params)
            applied += 1
            applied_changes.append(change)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    result = {
        **plan,
        "dry_run": False,
        "applied_count": applied,
        "skipped_count": len(skipped),
        "changes": applied_changes,
        "skipped": skipped,
    }
    log_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return result

