"""
Deterministic metadata sanitation proposals and DB-only apply.

This module targets suspicious artist/title contamination already visible in
the tracks table. It never writes audio tags, renames files, changes BPM/key/cue
data, performs network lookups, or uses AI.
"""
from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Literal

Confidence = Literal["HIGH", "MEDIUM", "LOW"]
ReviewStatus = Literal["pending", "approved", "rejected", "deferred", "applied", "no_op"]
DerivedStatus = Literal["PENDING", "APPROVED", "PARTIAL", "REJECTED", "APPLIED", "PARTIAL_APPLIED", "NO_OP"]

QUEUE_FILENAME = "metadata_sanitation_queue.jsonl"
STATE_FILENAME = "metadata_sanitation_state.json"
FIELD_NAMES = ("artist", "title")
CONFIDENCE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}

_JUNK_SUFFIX_RE = re.compile(r"(?i)(?:\s+|(?<=[a-z]))(?:MaciaDownloads|Downloads)\s*$")
_ALBUM_VERSION_RE = re.compile(r"(?i)(?:\s+|(?<=[a-z]))AlbumVersion\s*$")
_SOURCE_PAREN_RE = re.compile(
    r"(?i)\s*[\(\[][^)\]]*(?:fordjonly|djcity|zipdj|zippy|blogspot|soundcloud|\.com)[^)\]]*[\)\]]\s*"
)
_SOURCE_TOKEN_RE = re.compile(
    r"(?i)(?:\b(?:fordjonly|djcity|zipdj|zippy|blogspot|soundcloud)(?:\.com)?\b|(?:^|\s)[\w-]+\.com\b|\.com\b)"
)
_MULTI_SEPARATOR_RE = re.compile(r"\s*(?:[-_/|]{2,}|[|]{1})\s*")
_DUPLICATED_WORD_RE = re.compile(r"(?i)\b([a-z][\w']{2,})\b(?:\s+\1\b){1,}")
_SPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class Paths:
    root: Path
    db_path: Path
    queue_path: Path
    state_path: Path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def paths_for_root(root: str | Path) -> Paths:
    root_path = Path(root).expanduser().resolve(strict=False)
    intelligence_dir = root_path / "data" / "intelligence"
    return Paths(
        root=root_path,
        db_path=root_path / "logs" / "processed.db",
        queue_path=intelligence_dir / QUEUE_FILENAME,
        state_path=intelligence_dir / STATE_FILENAME,
    )


def _connect(db_path: Path, *, readonly: bool) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"pipeline database not found at {db_path}")
    if readonly:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_spaces(value: str) -> str:
    return _SPACE_RE.sub(" ", value).strip()


def _drop_empty_edges(value: str) -> str:
    return value.strip(" \t\r\n-_/|.,")


def _lower_confidence(current: Confidence, candidate: Confidence) -> Confidence:
    return current if CONFIDENCE_ORDER[current] <= CONFIDENCE_ORDER[candidate] else candidate


def _append_unique(items: list[str], value: str) -> None:
    if value not in items:
        items.append(value)


def _sanitize_value(value: Any) -> tuple[str, Confidence, list[str], list[str]]:
    original = _clean_text(value)
    cleaned = original
    confidence: Confidence = "HIGH"
    flags: list[str] = []
    reasons: list[str] = []

    normalized = _normalize_spaces(cleaned)
    if normalized != cleaned:
        cleaned = normalized
        reasons.append("whitespace corruption normalized")

    separator_normalized = _MULTI_SEPARATOR_RE.sub(" - ", cleaned)
    if separator_normalized != cleaned:
        cleaned = separator_normalized
        reasons.append("malformed separators normalized")

    without_source_parens = _SOURCE_PAREN_RE.sub(" ", cleaned)
    without_source_tokens = _SOURCE_TOKEN_RE.sub(" ", without_source_parens)
    if without_source_tokens != cleaned:
        cleaned = without_source_tokens
        confidence = _lower_confidence(confidence, "LOW")
        _append_unique(flags, "source_token_removed")
        reasons.append("source token removed")

    without_album_version = _ALBUM_VERSION_RE.sub("", cleaned)
    if without_album_version != cleaned:
        cleaned = without_album_version
        confidence = _lower_confidence(confidence, "LOW")
        _append_unique(flags, "ambiguous_version_cleanup")
        reasons.append("ambiguous version cleanup")

    without_junk_suffix = _JUNK_SUFFIX_RE.sub("", cleaned)
    if without_junk_suffix != cleaned:
        cleaned = without_junk_suffix
        _append_unique(flags, "junk_suffix_removed")
        reasons.append("junk suffix removed")

    def dedupe(match: re.Match[str]) -> str:
        word = match.group(1)
        repeated = match.group(0).split()
        if len(repeated) <= 2:
            return match.group(0)
        return word

    deduped = _DUPLICATED_WORD_RE.sub(dedupe, cleaned)
    if deduped != cleaned:
        cleaned = deduped
        confidence = _lower_confidence(confidence, "MEDIUM")
        _append_unique(flags, "duplicated_word_cleanup")
        reasons.append("duplicated word cleanup")

    cleaned = _drop_empty_edges(_normalize_spaces(cleaned))
    if not cleaned or cleaned == original:
        return original, confidence, [], []
    return cleaned, confidence, flags, reasons


def _row_confidence(results: list[tuple[str, Confidence, list[str], list[str]]]) -> Confidence:
    confidence: Confidence = "HIGH"
    for _, field_confidence, _, _ in results:
        confidence = _lower_confidence(confidence, field_confidence)
    return confidence


def _confidence_reason(flags: list[str], reasons: list[str]) -> str:
    if "ambiguous_version_cleanup" in flags:
        return "manual review required; ambiguous version cleanup"
    if "source_token_removed" in flags:
        return "source/piracy token removed"
    if "duplicated_word_cleanup" in flags:
        return "duplicated word cleanup"
    if "junk_suffix_removed" in flags:
        return "junk suffix removed safely"
    if reasons:
        return "; ".join(dict.fromkeys(reasons))
    return "metadata sanitation cleanup"


def _empty_field_state(current: Any, proposed: Any, *, status: ReviewStatus = "pending") -> dict[str, Any]:
    return {
        "status": status,
        "current": current,
        "proposed": proposed,
        "original_proposed": proposed,
        "edited": False,
    }


def _normalize_field_state(
    value: Any,
    *,
    fallback_current: Any,
    fallback_proposed: Any,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        return _empty_field_state(fallback_current, fallback_proposed)
    status = str(value.get("status") or value.get("review_status") or "pending").lower()
    if status not in {"pending", "approved", "rejected", "deferred", "applied", "no_op"}:
        status = "pending"
    normalized = {
        "status": status,
        "current": value.get("current", fallback_current),
        "proposed": value.get("proposed", fallback_proposed),
        "original_proposed": value.get("original_proposed", fallback_proposed),
        "edited": bool(value.get("edited", False)),
    }
    for key in ("applied_at", "applied_value", "previous_value", "effective_status", "current_db"):
        if key in value:
            normalized[key] = value[key]
    return normalized


def _proposal_fields(current_artist: Any, current_title: Any, proposed_artist: Any, proposed_title: Any) -> dict[str, Any]:
    return {
        "artist": _empty_field_state(current_artist, proposed_artist),
        "title": _empty_field_state(current_title, proposed_title),
    }


def _field_statuses(fields: dict[str, dict[str, Any]]) -> list[ReviewStatus]:
    statuses: list[ReviewStatus] = []
    for field in FIELD_NAMES:
        status = str(fields.get(field, {}).get("status") or "pending").lower()
        if status not in {"pending", "approved", "rejected", "deferred", "applied", "no_op"}:
            status = "pending"
        statuses.append(status)  # type: ignore[arg-type]
    return statuses


def _derived_status(fields: dict[str, dict[str, Any]]) -> DerivedStatus:
    unique = set(_field_statuses(fields))
    if unique == {"no_op"}:
        return "NO_OP"
    if unique == {"applied"} or unique <= {"applied", "no_op"}:
        return "APPLIED"
    if unique == {"approved"}:
        return "APPROVED"
    if unique == {"rejected"}:
        return "REJECTED"
    if unique <= {"pending", "deferred"}:
        return "PENDING"
    if "applied" in unique or "no_op" in unique:
        return "PARTIAL_APPLIED"
    if "approved" in unique or "rejected" in unique:
        return "PARTIAL"
    return "PENDING"


def build_proposal_for_track(row: sqlite3.Row | dict[str, Any], *, created_at: str | None = None) -> dict[str, Any] | None:
    data = dict(row)
    current_artist = _clean_text(data.get("artist"))
    current_title = _clean_text(data.get("title"))
    artist_result = _sanitize_value(current_artist)
    title_result = _sanitize_value(current_title)
    proposed_artist, proposed_title = artist_result[0], title_result[0]

    changed_artist = bool(current_artist) and proposed_artist != current_artist
    changed_title = bool(current_title) and proposed_title != current_title
    if not changed_artist and not changed_title:
        return None

    results = [artist_result, title_result]
    flags: list[str] = []
    reasons: list[str] = []
    for _, _, field_flags, field_reasons in results:
        for flag in field_flags:
            _append_unique(flags, flag)
        for reason in field_reasons:
            _append_unique(reasons, reason)

    if not flags and not reasons:
        return None

    filename = _clean_text(data.get("filename")) or Path(_clean_text(data.get("filepath"))).name
    return {
        "track_id": int(data.get("id")),
        "filepath": _clean_text(data.get("filepath")),
        "filename": filename,
        "current": {
            "artist": data.get("artist"),
            "title": data.get("title"),
            "parse_confidence": data.get("parse_confidence"),
        },
        "proposed": {
            "artist": proposed_artist,
            "title": proposed_title,
        },
        "fields": _proposal_fields(data.get("artist"), data.get("title"), proposed_artist, proposed_title),
        "repair_type": "metadata_sanitation",
        "confidence": _row_confidence(results),
        "confidence_reason": _confidence_reason(flags, reasons),
        "risk_flags": flags,
        "reason": _confidence_reason(flags, reasons),
        "status": "pending",
        "created_at": created_at or _utc_now(),
    }


def _track_rows(db_path: Path) -> list[sqlite3.Row]:
    with _connect(db_path, readonly=True) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        wanted = [
            "id",
            "filepath",
            "filename",
            "artist",
            "title",
            "bpm",
            "key_musical",
            "key_camelot",
            "parse_confidence",
            "status",
        ]
        select_exprs = [column if column in columns else f"NULL AS {column}" for column in wanted]
        return conn.execute(f"SELECT {', '.join(select_exprs)} FROM tracks ORDER BY id").fetchall()


def _current_track_values(db_path: Path) -> dict[int, dict[str, Any]]:
    if not db_path.exists():
        return {}
    with _connect(db_path, readonly=True) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        select_exprs = [column if column in columns else f"NULL AS {column}" for column in ("id", "artist", "title")]
        rows = conn.execute(f"SELECT {', '.join(select_exprs)} FROM tracks").fetchall()
    return {int(row["id"]): {"artist": row["artist"], "title": row["title"]} for row in rows}


def scan(root: str | Path) -> dict[str, Any]:
    p = paths_for_root(root)
    created_at = _utc_now()
    proposals: list[dict[str, Any]] = []
    skipped = 0
    for row in _track_rows(p.db_path):
        proposal = build_proposal_for_track(row, created_at=created_at)
        if proposal is None:
            skipped += 1
            continue
        proposals.append(proposal)

    p.queue_path.parent.mkdir(parents=True, exist_ok=True)
    with p.queue_path.open("w", encoding="utf-8") as handle:
        for proposal in proposals:
            handle.write(json.dumps(proposal, sort_keys=True) + "\n")

    return {
        "root": str(p.root),
        "db_path": str(p.db_path),
        "queue_path": str(p.queue_path),
        "total_tracks": len(proposals) + skipped,
        "proposal_count": len(proposals),
        "skipped_count": skipped,
        "counts": _counts(proposals),
        "sample_proposals": proposals[:5],
    }


def load_state(root: str | Path) -> dict[str, Any]:
    p = paths_for_root(root)
    if not p.state_path.exists():
        return {"items": {}, "updated_at": None}
    try:
        state = json.loads(p.state_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"items": {}, "updated_at": None}
    if not isinstance(state, dict):
        return {"items": {}, "updated_at": None}
    state.setdefault("items", {})
    return state


def save_state(root: str | Path, state: dict[str, Any]) -> None:
    p = paths_for_root(root)
    p.state_path.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = _utc_now()
    p.state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _effective_proposed(fields: dict[str, dict[str, Any]], field: str) -> Any:
    field_state = fields.get(field) if isinstance(fields, dict) else {}
    return field_state.get("proposed") if isinstance(field_state, dict) else None


def load_queue(root: str | Path) -> list[dict[str, Any]]:
    p = paths_for_root(root)
    if not p.queue_path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in p.queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)

    state_items = load_state(root).get("items", {})
    current_by_track = _current_track_values(p.db_path)
    for item in items:
        key = str(item.get("track_id"))
        review_item = state_items.get(key, {})
        state_fields = review_item.get("fields") if isinstance(review_item, dict) else {}
        queue_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        fields: dict[str, dict[str, Any]] = {}
        current_db = current_by_track.get(int(item.get("track_id") or -1), {})
        for field in FIELD_NAMES:
            queue_field = queue_fields.get(field, {})
            fallback_current = (queue_field or {}).get("current", (item.get("current") or {}).get(field))
            fallback_proposed = (queue_field or {}).get("proposed", (item.get("proposed") or {}).get(field))
            fields[field] = _normalize_field_state(
                (state_fields or {}).get(field) if isinstance(state_fields, dict) else None,
                fallback_current=fallback_current,
                fallback_proposed=fallback_proposed,
            )
            if field in current_db:
                fields[field]["current_db"] = current_db[field]
                proposed_value = _clean_text(fields[field].get("proposed"))
                status = str(fields[field].get("status") or "pending").lower()
                if proposed_value and _clean_text(current_db[field]) == proposed_value:
                    fields[field]["effective_status"] = "applied" if status == "applied" else "no_op"
                    if status not in {"applied", "rejected", "deferred"}:
                        fields[field]["status"] = "no_op"
        item["fields"] = fields
        item["proposed"] = {
            "artist": _effective_proposed(fields, "artist"),
            "title": _effective_proposed(fields, "title"),
        }
        item["status"] = _derived_status(fields)
        item["effective_status"] = item["status"]
        if isinstance(review_item, dict) and review_item.get("updated_at"):
            item["review_updated_at"] = review_item["updated_at"]
    return items


def _raw_queue_items(queue_path: Path) -> list[dict[str, Any]]:
    if not queue_path.exists():
        return []
    items: list[dict[str, Any]] = []
    for line in queue_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            items.append(item)
    return items


def _write_queue_items(queue_path: Path, items: list[dict[str, Any]]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = queue_path.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for proposal in items:
            handle.write(json.dumps(proposal, sort_keys=True) + "\n")
    tmp_path.replace(queue_path)


def _track_row(db_path: Path, track_id: int) -> sqlite3.Row | None:
    with _connect(db_path, readonly=True) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        select_exprs = [
            column if column in columns else f"NULL AS {column}"
            for column in (
                "id",
                "filepath",
                "filename",
                "artist",
                "title",
                "bpm",
                "key_musical",
                "key_camelot",
                "parse_confidence",
                "status",
            )
        ]
        return conn.execute(
            f"SELECT {', '.join(select_exprs)} FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()


def _track_snapshot(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    issues = []
    current_artist = _clean_text(row["artist"])
    current_title = _clean_text(row["title"])
    parse_confidence = _clean_text(row["parse_confidence"])
    if not current_artist:
        issues.append("missing_artist")
    if not current_title:
        issues.append("missing_title")
    if parse_confidence.upper() in {"MEDIUM", "LOW"}:
        issues.append("weak_filename_parse")
    return {
        "artist": row["artist"],
        "title": row["title"],
        "filepath": row["filepath"],
        "filename": row["filename"],
        "issues": issues,
    }


def generate_track_proposal(root: str | Path, track_id: int) -> dict[str, Any]:
    p = paths_for_root(root)
    row = _track_row(p.db_path, track_id)
    if row is None:
        raise LookupError(f"track {track_id} not found in pipeline database")
    snapshot = _track_snapshot(row)

    proposal = build_proposal_for_track(row)
    queue_items = _raw_queue_items(p.queue_path)
    existing_index = next((idx for idx, item in enumerate(queue_items) if int(item.get("track_id") or -1) == int(track_id)), None)
    existing = queue_items[existing_index] if existing_index is not None else None

    if proposal is None:
        if existing is not None:
            return {
                "root": str(p.root),
                "track_id": track_id,
                "generated": False,
                "replaced": False,
                "no_op_reason": "proposal already exists",
                "queue_path": str(p.queue_path),
                "proposal": existing,
                "recommended_route": "metadata-sanitation",
                "track": snapshot,
            }
        return {
            "root": str(p.root),
            "track_id": track_id,
            "generated": False,
            "replaced": False,
            "no_op_reason": "no deterministic sanitation proposal available",
            "queue_path": str(p.queue_path),
            "proposal": None,
            "recommended_route": "metadata-sanitation",
            "track": snapshot,
        }

    if existing is not None and existing == proposal:
        return {
            "root": str(p.root),
            "track_id": track_id,
            "generated": False,
            "replaced": False,
            "no_op_reason": "proposal already exists",
            "queue_path": str(p.queue_path),
            "proposal": existing,
            "recommended_route": "metadata-sanitation",
            "track": snapshot,
        }

    if existing_index is None:
        queue_items.append(proposal)
    else:
        queue_items[existing_index] = proposal
    _write_queue_items(p.queue_path, queue_items)
    return {
        "root": str(p.root),
        "track_id": track_id,
        "generated": True,
        "replaced": existing is not None,
        "no_op_reason": None,
        "queue_path": str(p.queue_path),
        "proposal": proposal,
        "recommended_route": "metadata-sanitation",
        "track": snapshot,
    }


def _counts(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, int]]:
    by_type: Counter[str] = Counter()
    by_confidence: Counter[str] = Counter()
    by_status: Counter[str] = Counter()
    for item in items:
        by_type[str(item.get("repair_type") or "unknown")] += 1
        by_confidence[str(item.get("confidence") or "UNKNOWN").upper()] += 1
        by_status[str(item.get("status") or "pending").lower()] += 1
    return {
        "by_repair_type": dict(sorted(by_type.items())),
        "by_confidence": dict(sorted(by_confidence.items())),
        "by_status": dict(sorted(by_status.items())),
    }


def queue_response(
    root: str | Path,
    *,
    repair_type: str | None = None,
    confidence: str | None = None,
    status: str | None = None,
    include_applied: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> dict[str, Any]:
    items = load_queue(root)
    if repair_type:
        items = [item for item in items if str(item.get("repair_type")) == repair_type]
    if confidence:
        items = [item for item in items if str(item.get("confidence") or "").upper() == confidence.upper()]
    if status:
        items = [item for item in items if str(item.get("status") or "").upper() == status.upper()]
    if not include_applied:
        items = [item for item in items if str(item.get("status") or "").upper() not in {"APPLIED", "NO_OP"}]
    total = len(items)
    return {
        "items": items[offset : offset + limit],
        "counts": _counts(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def summary(root: str | Path) -> dict[str, Any]:
    p = paths_for_root(root)
    items = load_queue(root)
    counts = _counts(items)
    statuses = Counter(str(item.get("status") or "PENDING").upper() for item in items)
    confidences = Counter(str(item.get("confidence") or "UNKNOWN").upper() for item in items)
    state = load_state(root)
    return {
        "queue_total": len(items),
        "pending_count": statuses.get("PENDING", 0),
        "approved_count": statuses.get("APPROVED", 0),
        "partial_count": statuses.get("PARTIAL", 0),
        "rejected_count": statuses.get("REJECTED", 0),
        "deferred_count": counts["by_status"].get("deferred", 0),
        "applied_count": statuses.get("APPLIED", 0),
        "partial_applied_count": statuses.get("PARTIAL_APPLIED", 0),
        "no_op_count": statuses.get("NO_OP", 0),
        "high_count": confidences.get("HIGH", 0),
        "medium_count": confidences.get("MEDIUM", 0),
        "low_count": confidences.get("LOW", 0),
        "counts": counts,
        "queue_path": str(p.queue_path),
        "state_path": str(p.state_path),
        "updated_at": state.get("updated_at"),
    }


def _queue_item(root: str | Path, track_id: int) -> dict[str, Any]:
    for item in load_queue(root):
        if int(item.get("track_id") or -1) == int(track_id):
            return item
    raise LookupError(f"metadata sanitation proposal not found for track_id={track_id}")


def _state_item_for(root: str | Path, track_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    item = _queue_item(root, track_id)
    state = load_state(root)
    state_items = state.setdefault("items", {})
    key = str(track_id)
    if key not in state_items:
        state_items[key] = {
            "track_id": track_id,
            "fields": item["fields"],
            "updated_at": _utc_now(),
        }
    return state, state_items[key]


def set_field_review_status(root: str | Path, track_id: int, field: str, review_status: ReviewStatus) -> dict[str, Any]:
    if field not in FIELD_NAMES:
        raise ValueError(f"unsupported metadata sanitation field: {field}")
    if review_status not in {"approved", "rejected", "deferred"}:
        raise ValueError(f"unsupported review status: {review_status}")
    state, state_item = _state_item_for(root, track_id)
    fields = state_item.setdefault("fields", {})
    fields.setdefault(field, _queue_item(root, track_id)["fields"][field])
    fields[field]["status"] = review_status
    state_item["updated_at"] = _utc_now()
    save_state(root, state)
    return state


def set_review_status(root: str | Path, track_id: int, review_status: ReviewStatus) -> dict[str, Any]:
    if review_status not in {"approved", "rejected", "deferred"}:
        raise ValueError(f"unsupported review status: {review_status}")
    state, state_item = _state_item_for(root, track_id)
    fields = state_item.setdefault("fields", {})
    item = _queue_item(root, track_id)
    for field in FIELD_NAMES:
        fields.setdefault(field, item["fields"][field])
        fields[field]["status"] = review_status
    state_item["updated_at"] = _utc_now()
    save_state(root, state)
    return state


def set_field_proposal(root: str | Path, track_id: int, field: str, proposed: str) -> dict[str, Any]:
    if field not in FIELD_NAMES:
        raise ValueError(f"unsupported metadata sanitation field: {field}")
    cleaned = _clean_text(proposed)
    if not cleaned:
        raise ValueError("proposed value cannot be empty")
    state, state_item = _state_item_for(root, track_id)
    fields = state_item.setdefault("fields", {})
    item = _queue_item(root, track_id)
    fields.setdefault(field, item["fields"][field])
    fields[field]["proposed"] = cleaned
    fields[field]["edited"] = cleaned != _clean_text(fields[field].get("original_proposed"))
    state_item["updated_at"] = _utc_now()
    save_state(root, state)
    return state


def _approved_plan(root: str | Path) -> list[tuple[dict[str, Any], str, dict[str, Any]]]:
    plan: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    for item in load_queue(root):
        for field in FIELD_NAMES:
            field_state = item.get("fields", {}).get(field, {})
            if field_state.get("status") != "approved":
                continue
            proposed = _clean_text(field_state.get("proposed"))
            if not proposed:
                continue
            plan.append((item, field, field_state))
    return plan


def _record_apply_outcomes(root: str | Path, outcomes: list[dict[str, Any]]) -> None:
    if not outcomes:
        return
    state = load_state(root)
    state_items = state.setdefault("items", {})
    applied_at = _utc_now()
    for outcome in outcomes:
        key = str(outcome["track_id"])
        state_item = state_items.setdefault(key, {"track_id": outcome["track_id"], "fields": {}})
        fields = state_item.setdefault("fields", {})
        field = outcome["field"]
        queue_item = _queue_item(root, int(outcome["track_id"]))
        fields.setdefault(field, queue_item["fields"][field])
        fields[field]["status"] = "applied" if outcome["changed"] else "no_op"
        fields[field]["applied_at"] = applied_at
        fields[field]["applied_value"] = outcome["proposed_value"]
        fields[field]["previous_value"] = outcome["previous_value"]
        state_item["updated_at"] = applied_at
    save_state(root, state)


def apply_approved(root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    p = paths_for_root(root)
    plan = _approved_plan(root)
    grouped: dict[int, dict[str, Any]] = {}
    for item, field, field_state in plan:
        track_id = int(item["track_id"])
        grouped.setdefault(track_id, {"item": item, "fields": {}})
        grouped[track_id]["fields"][field] = field_state

    changes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    applied_tracks = 0
    applied_fields = 0
    conn = _connect(p.db_path, readonly=not apply)
    try:
        for track_id, payload in grouped.items():
            row = conn.execute("SELECT id, artist, title FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if row is None:
                skipped.append({"track_id": track_id, "reason": "missing_track"})
                continue
            changed_fields: dict[str, Any] = {}
            previous_fields: dict[str, Any] = {}
            for field, field_state in payload["fields"].items():
                proposed = _clean_text(field_state.get("proposed"))
                if not proposed:
                    skipped.append({"track_id": track_id, "field": field, "reason": "empty_proposal"})
                    continue
                previous = row[field]
                previous_fields[field] = previous
                changed = _clean_text(previous) != proposed
                if changed:
                    changed_fields[field] = proposed
                outcomes.append(
                    {
                        "track_id": track_id,
                        "field": field,
                        "previous_value": previous,
                        "proposed_value": proposed,
                        "changed": changed,
                    }
                )
            if changed_fields:
                changes.append(
                    {
                        "track_id": track_id,
                        "filename": payload["item"].get("filename"),
                        "previous": previous_fields,
                        "proposed": changed_fields,
                        "changed_fields": list(changed_fields.keys()),
                    }
                )
                if apply:
                    assignments = ", ".join(f"{field} = ?" for field in changed_fields)
                    values = list(changed_fields.values()) + [track_id]
                    conn.execute(f"UPDATE tracks SET {assignments} WHERE id = ?", values)
                    applied_tracks += 1
                    applied_fields += len(changed_fields)
            elif payload["fields"]:
                skipped.append({"track_id": track_id, "reason": "no_op"})
        if apply:
            conn.commit()
    finally:
        conn.close()

    if apply:
        _record_apply_outcomes(root, outcomes)

    return {
        "root": str(p.root),
        "db_path": str(p.db_path),
        "queue_path": str(p.queue_path),
        "state_path": str(p.state_path),
        "dry_run": not apply,
        "approved_seen": len(grouped),
        "proposed_count": len(changes),
        "applied_count": applied_tracks,
        "applied_field_count": applied_fields,
        "skipped_count": len(skipped),
        "changes": changes,
        "skipped": skipped,
    }
