"""
Deterministic metadata repair proposals and DB-only apply.

This module never writes audio tags, renames files, performs network lookups,
or uses AI. Proposal scan writes only the review queue JSONL. Apply writes only
tracks.artist and tracks.title after explicit approval.
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

from modules.filename_parse import parse_filename_metadata

Confidence = Literal["HIGH", "MEDIUM", "LOW", "REVIEW_REQUIRED"]
ReviewStatus = Literal["pending", "approved", "rejected", "deferred", "applied", "no_op"]
DerivedStatus = Literal["PENDING", "APPROVED", "PARTIAL", "REJECTED", "APPLIED", "PARTIAL_APPLIED", "NO_OP"]

QUEUE_FILENAME = "metadata_repair_queue.jsonl"
STATE_FILENAME = "metadata_repair_state.json"
ALLOWED_CONFIDENCE = {"HIGH", "MEDIUM", "LOW"}
FIELD_NAMES = ("artist", "title")
KNOWN_ARTIST_JUNK = {"track lists", "track list", "tracks", "unknown artist", "unknown"}
_NUMBERED_ARTIST_RE = re.compile(r"^\s*(?:\d{1,3}[\.\)-]\s+)+(.+?)\s*$")
_PAREN_SOURCE_TOKEN_RE = re.compile(
    r"\s*\((?:[^)]*(?:fordjonly|djcity|zipdj|blogspot|soundcloud|zippy|\.com)[^)]*)\)\s*",
    re.IGNORECASE,
)
_SOURCE_TOKEN_RE = re.compile(
    r"(?i)(?:\b(?:fordjonly|djcity|zipdj|blogspot|soundcloud|zippy)\.?(?:com)?\b|\.com\b)"
)
_ARTIST_JUNK_PHRASES = (
    "including",
    "original instrumental",
    "reprise",
    "cut of",
    "remix",
    "rmx",
    "mixes",
    "versions",
    "track lists",
)
_URL_PIRACY_TOKENS = (
    "fordjonly",
    ".com",
    "zippy",
    "blogspot",
    "soundcloud",
    "rip",
)
_SEVERE_RISK_FLAGS = {"artist_junk_descriptor", "piracy_token_detected", "soundcloud_rip_token"}


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
    if readonly:
        if not db_path.exists():
            raise FileNotFoundError(f"pipeline database not found at {db_path}")
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    else:
        if not db_path.exists():
            raise FileNotFoundError(f"pipeline database not found at {db_path}")
        conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _is_missing(value: Any) -> bool:
    return not _clean_text(value)


def _strip_numbering_junk(value: str) -> str | None:
    match = _NUMBERED_ARTIST_RE.match(value)
    if not match:
        return None
    cleaned = match.group(1).strip()
    return cleaned if cleaned and cleaned != value.strip() else None


def _strip_source_tokens(value: str) -> tuple[str, bool]:
    cleaned = value.strip()
    without_parens = _PAREN_SOURCE_TOKEN_RE.sub(" ", cleaned)
    without_tokens = _SOURCE_TOKEN_RE.sub(" ", without_parens)
    normalized = re.sub(r"\s{2,}", " ", without_tokens).strip(" -_.,")
    return normalized, normalized != cleaned


def _cleanup_parsed_values(artist: str, title: str) -> tuple[str, str, list[str]]:
    flags: list[str] = []
    numbered_artist = _strip_numbering_junk(artist)
    if numbered_artist:
        artist = numbered_artist
        flags.append("numbering_junk_stripped")
    cleaned_title, source_stripped = _strip_source_tokens(title)
    if source_stripped:
        title = cleaned_title
        flags.append("piracy_token_detected")
    return artist.strip(), title.strip(), flags


def _is_known_artist_junk(value: str) -> bool:
    return value.strip().lower() in KNOWN_ARTIST_JUNK


def _token_count(value: str) -> int:
    return len([token for token in re.split(r"[\s,./\\_-]+", value.strip()) if token])


def _punctuation_density(value: str) -> float:
    if not value:
        return 0.0
    punct = sum(1 for ch in value if not ch.isalnum() and not ch.isspace())
    return punct / max(len(value), 1)


def _contains_any(value: str, needles: Iterable[str]) -> bool:
    value_lc = value.lower()
    return any(needle in value_lc for needle in needles)


def _confidence_floor(current: Confidence, floor: Confidence) -> Confidence:
    order = {"LOW": 0, "REVIEW_REQUIRED": 1, "MEDIUM": 2, "HIGH": 3}
    return current if order[current] <= order[floor] else floor


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
    current = value.get("current", fallback_current)
    original_proposed = value.get("original_proposed", fallback_proposed)
    proposed = value.get("proposed", fallback_proposed)
    normalized = {
        "status": status,
        "current": current,
        "proposed": proposed,
        "original_proposed": original_proposed,
        "edited": bool(value.get("edited", False)),
    }
    for key in ("applied_at", "applied_value", "previous_value", "effective_status"):
        if key in value:
            normalized[key] = value[key]
    return normalized


def _field_statuses(fields: dict[str, dict[str, Any]]) -> list[ReviewStatus]:
    statuses: list[ReviewStatus] = []
    for field in FIELD_NAMES:
        status = str(fields.get(field, {}).get("status") or "pending").lower()
        if status not in {"pending", "approved", "rejected", "deferred", "applied", "no_op"}:
            status = "pending"
        statuses.append(status)  # type: ignore[arg-type]
    return statuses


def _derived_status(fields: dict[str, dict[str, Any]]) -> DerivedStatus:
    statuses = _field_statuses(fields)
    unique = set(statuses)
    if unique == {"applied"}:
        return "APPLIED"
    if unique == {"no_op"}:
        return "NO_OP"
    if unique <= {"applied", "no_op"}:
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


def _proposal_fields(current_artist: Any, current_title: Any, artist: Any, title: Any) -> dict[str, Any]:
    return {
        "artist": _empty_field_state(current_artist, artist),
        "title": _empty_field_state(current_title, title),
    }


def _effective_proposed(fields: dict[str, dict[str, Any]], field: str) -> Any:
    field_state = fields.get(field) if isinstance(fields, dict) else {}
    if not isinstance(field_state, dict):
        return None
    return field_state.get("proposed")


def _confidence_profile(
    *,
    repair_type: str,
    current_artist: str,
    current_title: str,
    filename: str,
    proposed_artist: str,
    proposed_title: str,
    parse_confidence: str,
    initial_risk_flags: list[str] | None = None,
) -> tuple[Confidence, str, list[str]]:
    risk_flags: list[str] = list(dict.fromkeys(initial_risk_flags or []))
    artist_source = " ".join(filter(None, [current_artist, proposed_artist, filename])).lower()
    title_source = " ".join(filter(None, [current_title, proposed_title, filename])).lower()

    if _is_known_artist_junk(current_artist):
        risk_flags.append("source_artist_junk")

    if _contains_any(proposed_artist, _ARTIST_JUNK_PHRASES) or _contains_any(proposed_title, _ARTIST_JUNK_PHRASES):
        risk_flags.append("artist_junk_descriptor")
    if _contains_any(artist_source, _URL_PIRACY_TOKENS) or _contains_any(title_source, _URL_PIRACY_TOKENS):
        risk_flags.append("piracy_token_detected")
    if "soundcloud" in artist_source and "rip" in artist_source:
        risk_flags.append("soundcloud_rip_token")

    token_count = _token_count(proposed_artist)
    if token_count > 4:
        risk_flags.append("artist_too_long")
    if token_count > 6:
        risk_flags.append("artist_over_token_limit")

    punctuation_density = _punctuation_density(proposed_artist)
    if punctuation_density >= 0.22 or proposed_artist.count(",") >= 2:
        risk_flags.append("punctuation_density_high")

    if repair_type == "suspicious_numbered_artist_cleanup" and "numbering_junk_stripped" not in risk_flags:
        risk_flags.append("numbering_junk_stripped")

    if repair_type == "missing_artist_from_filename" and not risk_flags:
        return "HIGH", "clean artist-title parse", []
    if repair_type == "missing_title_from_filename" and not risk_flags:
        return "HIGH", "clean artist-title parse", []
    if repair_type == "suspicious_artist_from_filename" and not risk_flags:
        return "HIGH", "clean artist-title parse", []

    confidence: Confidence = "MEDIUM" if parse_confidence in {"HIGH", "MEDIUM"} else "LOW"
    if repair_type == "suspicious_numbered_artist_cleanup":
        confidence = "MEDIUM"
        if parse_confidence == "LOW":
            confidence = "LOW"
        if not risk_flags or risk_flags == ["numbering_junk_stripped"]:
            return confidence, "numbering junk stripped safely", risk_flags

    if "artist_junk_descriptor" in risk_flags:
        confidence = "REVIEW_REQUIRED" if confidence == "HIGH" else "LOW"
        return confidence, "suspicious remix descriptor artist", risk_flags

    if "piracy_token_detected" in risk_flags or "soundcloud_rip_token" in risk_flags:
        confidence = "LOW" if confidence != "LOW" else confidence
        return confidence, "piracy token detected", risk_flags

    if "artist_over_token_limit" in risk_flags or "artist_too_long" in risk_flags:
        confidence = _confidence_floor(confidence, "MEDIUM" if confidence == "HIGH" else "LOW")

    if "punctuation_density_high" in risk_flags:
        confidence = _confidence_floor(confidence, "MEDIUM" if confidence == "HIGH" else "LOW")

    if "source_artist_junk" in risk_flags:
        confidence = _confidence_floor(confidence, "REVIEW_REQUIRED")

    if confidence == "HIGH":
        return confidence, "clean artist-title parse", risk_flags
    if repair_type == "suspicious_numbered_artist_cleanup":
        return confidence, "numbering junk stripped safely", risk_flags
    if "source_artist_junk" in risk_flags:
        return confidence, "known junk source artist", risk_flags
    return confidence, "clean artist-title parse", risk_flags


def _proposal(
    row: sqlite3.Row,
    *,
    artist: str,
    title: str,
    repair_type: str,
    confidence: Confidence,
    reason: str,
    risk_flags: list[str],
    created_at: str,
) -> dict[str, Any]:
    return {
        "track_id": int(row["id"]),
        "filepath": _clean_text(row["filepath"]),
        "filename": _clean_text(row["filename"]) or Path(_clean_text(row["filepath"])).name,
        "current": {
            "artist": row["artist"],
            "title": row["title"],
            "parse_confidence": row["parse_confidence"],
        },
        "proposed": {
            "artist": artist,
            "title": title,
        },
        "fields": _proposal_fields(row["artist"], row["title"], artist, title),
        "repair_type": repair_type,
        "confidence": confidence,
        "confidence_reason": reason,
        "risk_flags": risk_flags,
        "reason": reason,
        "status": "pending",
        "created_at": created_at,
    }


def build_proposal_for_track(row: sqlite3.Row | dict[str, Any], *, created_at: str | None = None) -> dict[str, Any] | None:
    data = dict(row)
    shim = _DictRow(data)
    filename = _clean_text(data.get("filename")) or Path(_clean_text(data.get("filepath"))).name
    stem = Path(filename).stem
    parsed = parse_filename_metadata(stem)
    parsed_artist = _clean_text(parsed.artist)
    parsed_title = _clean_text(parsed.combined_title())
    parsed_artist, parsed_title, cleanup_flags = _cleanup_parsed_values(parsed_artist, parsed_title)
    if "track_prefix_removed" in getattr(parsed, "reasons", []) and "numbering_junk_stripped" not in cleanup_flags:
        cleanup_flags.append("numbering_junk_stripped")
    parse_confidence = _clean_text(parsed.parse_confidence).upper()
    current_artist = _clean_text(data.get("artist"))
    current_title = _clean_text(data.get("title"))
    created = created_at or _utc_now()

    if parse_confidence not in ALLOWED_CONFIDENCE:
        parse_confidence = "LOW"

    if parsed_artist and parsed_title:
        if _is_missing(current_artist):
            confidence, confidence_reason, risk_flags = _confidence_profile(
                repair_type="missing_artist_from_filename",
                current_artist=current_artist,
                current_title=current_title,
                filename=filename,
                proposed_artist=parsed_artist,
                proposed_title=parsed_title,
                parse_confidence=parse_confidence,
                initial_risk_flags=cleanup_flags,
            )
            return _proposal(
                shim,
                artist=parsed_artist,
                title=parsed_title,
                repair_type="missing_artist_from_filename",
                confidence=confidence,
                reason=confidence_reason,
                risk_flags=risk_flags,
                created_at=created,
            )
        if _is_missing(current_title):
            confidence, confidence_reason, risk_flags = _confidence_profile(
                repair_type="missing_title_from_filename",
                current_artist=current_artist or parsed_artist,
                current_title=current_title,
                filename=filename,
                proposed_artist=current_artist or parsed_artist,
                proposed_title=parsed_title,
                parse_confidence=parse_confidence,
                initial_risk_flags=cleanup_flags,
            )
            return _proposal(
                shim,
                artist=current_artist or parsed_artist,
                title=parsed_title,
                repair_type="missing_title_from_filename",
                confidence=confidence,
                reason=confidence_reason,
                risk_flags=risk_flags,
                created_at=created,
            )
        if (
            _is_known_artist_junk(current_artist)
            and parsed_artist
            and not _is_known_artist_junk(parsed_artist)
            and parsed_artist.strip().lower() != current_artist.strip().lower()
        ):
            confidence, confidence_reason, risk_flags = _confidence_profile(
                repair_type="suspicious_artist_from_filename",
                current_artist=current_artist,
                current_title=current_title,
                filename=filename,
                proposed_artist=parsed_artist,
                proposed_title=current_title or parsed_title,
                parse_confidence=parse_confidence,
                initial_risk_flags=cleanup_flags,
            )
            return _proposal(
                shim,
                artist=parsed_artist,
                title=parsed_title,
                repair_type="suspicious_artist_from_filename",
                confidence=confidence,
                reason=confidence_reason,
                risk_flags=risk_flags,
                created_at=created,
            )

    numbered_cleanup = _strip_numbering_junk(current_artist)
    if numbered_cleanup:
        confidence, confidence_reason, risk_flags = _confidence_profile(
            repair_type="suspicious_numbered_artist_cleanup",
            current_artist=current_artist,
            current_title=current_title,
            filename=filename,
            proposed_artist=numbered_cleanup,
            proposed_title=current_title or parsed_title,
            parse_confidence=parse_confidence,
            initial_risk_flags=cleanup_flags,
        )
        return _proposal(
            shim,
            artist=numbered_cleanup,
            title=current_title or parsed_title,
            repair_type="suspicious_numbered_artist_cleanup",
            confidence=confidence,
            reason=confidence_reason,
            risk_flags=risk_flags,
            created_at=created,
        )

    return None


class _DictRow:
    def __init__(self, data: dict[str, Any]):
        self._data = data

    def __getitem__(self, key: str) -> Any:
        return self._data.get(key)


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
        select_exprs = [
            column if column in columns else f"NULL AS {column}"
            for column in wanted
        ]
        return conn.execute(
            f"SELECT {', '.join(select_exprs)} FROM tracks ORDER BY id"
        ).fetchall()


def _current_track_values(db_path: Path) -> dict[int, dict[str, Any]]:
    if not db_path.exists():
        return {}
    with _connect(db_path, readonly=True) as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()}
        select_exprs = [
            column if column in columns else f"NULL AS {column}"
            for column in ("id", "artist", "title")
        ]
        rows = conn.execute(f"SELECT {', '.join(select_exprs)} FROM tracks").fetchall()
    return {
        int(row["id"]): {
            "artist": row["artist"],
            "title": row["title"],
        }
        for row in rows
    }


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
    state = load_state(root)
    state_items = state.get("items", {})
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
                field_status = str(fields[field].get("status") or "pending").lower()
                if proposed_value and _clean_text(current_db[field]) == proposed_value:
                    fields[field]["effective_status"] = "applied" if field_status == "applied" else "no_op"
                    if field_status not in {"applied", "rejected", "deferred"}:
                        fields[field]["status"] = "no_op"
        item["fields"] = fields
        item["proposed"] = {
            "artist": _effective_proposed(fields, "artist"),
            "title": _effective_proposed(fields, "title"),
        }
        item["status"] = _derived_status(fields)
        item["effective_status"] = item["status"]
        if review_item.get("updated_at"):
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
        return conn.execute(
            f"SELECT {', '.join(select_exprs)} FROM tracks WHERE id = ?",
            (track_id,),
        ).fetchone()


def generate_track_proposal(root: str | Path, track_id: int) -> dict[str, Any]:
    p = paths_for_root(root)
    row = _track_row(p.db_path, track_id)
    if row is None:
        raise LookupError(f"track {track_id} not found in pipeline database")

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
            }
        return {
            "root": str(p.root),
            "track_id": track_id,
            "generated": False,
            "replaced": False,
            "no_op_reason": "no deterministic repair proposal available",
            "queue_path": str(p.queue_path),
            "proposal": None,
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
    if not include_applied:
        items = [
            item for item in items
            if str(item.get("effective_status") or item.get("status") or "").upper() not in {"APPLIED", "NO_OP"}
        ]
    if repair_type:
        items = [item for item in items if item.get("repair_type") == repair_type]
    if confidence:
        want = confidence.upper()
        items = [item for item in items if str(item.get("confidence") or "").upper() == want]
    if status:
        want_status = status.lower()
        items = [item for item in items if str(item.get("status") or "pending").lower() == want_status]
    total = len(items)
    return {
        "items": items[offset: offset + limit],
        "counts": _counts(items),
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def summary(root: str | Path) -> dict[str, Any]:
    items = load_queue(root)
    counts = _counts(items)
    return {
        "queue_total": len(items),
        "pending_count": counts["by_status"].get("pending", 0),
        "approved_count": counts["by_status"].get("approved", 0),
        "partial_count": counts["by_status"].get("partial", 0),
        "rejected_count": counts["by_status"].get("rejected", 0),
        "deferred_count": counts["by_status"].get("deferred", 0),
        "applied_count": counts["by_status"].get("applied", 0),
        "partial_applied_count": counts["by_status"].get("partial_applied", 0),
        "no_op_count": counts["by_status"].get("no_op", 0),
        "high_count": counts["by_confidence"].get("HIGH", 0),
        "medium_count": counts["by_confidence"].get("MEDIUM", 0),
        "low_count": counts["by_confidence"].get("LOW", 0),
        "counts": counts,
        "queue_path": str(paths_for_root(root).queue_path),
        "state_path": str(paths_for_root(root).state_path),
        "updated_at": load_state(root).get("updated_at"),
    }


def load_state(root: str | Path) -> dict[str, Any]:
    p = paths_for_root(root)
    if not p.state_path.exists():
        return {"updated_at": None, "items": {}, "approved": [], "rejected": [], "deferred": [], "partial": [], "pending": [], "applied": [], "partial_applied": [], "no_op": []}
    try:
        raw = json.loads(p.state_path.read_text(encoding="utf-8"))
    except Exception:
        return {"updated_at": None, "items": {}, "approved": [], "rejected": [], "deferred": [], "partial": [], "pending": [], "applied": [], "partial_applied": [], "no_op": []}
    if not isinstance(raw, dict):
        return {"updated_at": None, "items": {}, "approved": [], "rejected": [], "deferred": [], "partial": [], "pending": [], "applied": [], "partial_applied": [], "no_op": []}
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    normalized: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[int]] = {"approved": [], "rejected": [], "deferred": [], "partial": [], "pending": [], "applied": [], "partial_applied": [], "no_op": []}
    for key, value in items.items():
        if not isinstance(value, dict):
            continue
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
        try:
            track_id = int(value.get("track_id", key))
        except Exception:
            continue
        current = value.get("current") if isinstance(value.get("current"), dict) else {}
        proposed = value.get("proposed") if isinstance(value.get("proposed"), dict) else {}
        normalized_fields: dict[str, dict[str, Any]] = {}
        for field in FIELD_NAMES:
            normalized_fields[field] = _normalize_field_state(
                fields.get(field) if isinstance(fields, dict) else None,
                fallback_current=(current or {}).get(field),
                fallback_proposed=(proposed or {}).get(field),
            )
        status = _derived_status(normalized_fields)
        normalized[str(track_id)] = {
            **value,
            "track_id": track_id,
            "fields": normalized_fields,
            "review_status": status.lower(),
            "status": status,
        }
        buckets.setdefault(status.lower(), []).append(track_id)
    return {
        "updated_at": raw.get("updated_at"),
        "items": dict(sorted(normalized.items(), key=lambda pair: int(pair[0]))),
        "approved": sorted(buckets["approved"]),
        "rejected": sorted(buckets["rejected"]),
        "deferred": sorted(buckets["deferred"]),
        "partial": sorted(buckets["partial"]),
        "pending": sorted(buckets["pending"]),
        "applied": sorted(buckets["applied"]),
        "partial_applied": sorted(buckets["partial_applied"]),
        "no_op": sorted(buckets["no_op"]),
    }


def save_state(root: str | Path, state: dict[str, Any]) -> dict[str, Any]:
    p = paths_for_root(root)
    p.state_path.parent.mkdir(parents=True, exist_ok=True)
    normalized = load_state_from_payload(state)
    payload = {
        "updated_at": normalized.get("updated_at") or _utc_now(),
        "items": normalized.get("items", {}),
    }
    tmp_path = p.state_path.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(p.state_path)
    return load_state(root)


def load_state_from_payload(raw: dict[str, Any]) -> dict[str, Any]:
    items = raw.get("items") if isinstance(raw.get("items"), dict) else {}
    payload = {"updated_at": raw.get("updated_at"), "items": items}
    # Normalize without touching disk.
    normalized: dict[str, dict[str, Any]] = {}
    buckets: dict[str, list[int]] = {"approved": [], "rejected": [], "deferred": [], "partial": [], "pending": [], "applied": [], "partial_applied": [], "no_op": []}
    for key, value in items.items():
        if not isinstance(value, dict):
            continue
        try:
            track_id = int(value.get("track_id", key))
        except Exception:
            continue
        current = value.get("current") if isinstance(value.get("current"), dict) else {}
        proposed = value.get("proposed") if isinstance(value.get("proposed"), dict) else {}
        fields = value.get("fields") if isinstance(value.get("fields"), dict) else {}
        normalized_fields: dict[str, dict[str, Any]] = {}
        for field in FIELD_NAMES:
            normalized_fields[field] = _normalize_field_state(
                fields.get(field) if isinstance(fields, dict) else None,
                fallback_current=(current or {}).get(field),
                fallback_proposed=(proposed or {}).get(field),
            )
        status = _derived_status(normalized_fields)
        normalized[str(track_id)] = {
            **value,
            "track_id": track_id,
            "fields": normalized_fields,
            "review_status": status.lower(),
            "status": status,
        }
        buckets.setdefault(status.lower(), []).append(track_id)
    return {
        "updated_at": payload["updated_at"],
        "items": normalized,
        "approved": sorted(buckets["approved"]),
        "rejected": sorted(buckets["rejected"]),
        "deferred": sorted(buckets["deferred"]),
        "partial": sorted(buckets["partial"]),
        "pending": sorted(buckets["pending"]),
        "applied": sorted(buckets["applied"]),
        "partial_applied": sorted(buckets["partial_applied"]),
        "no_op": sorted(buckets["no_op"]),
    }


def set_review_status(root: str | Path, track_id: int, review_status: Literal["approved", "rejected", "deferred"]) -> dict[str, Any]:
    return set_field_review_status(root, track_id, "artist", review_status, mirror_fields=True)


def set_field_review_status(
    root: str | Path,
    track_id: int,
    field: Literal["artist", "title"],
    review_status: Literal["approved", "rejected", "deferred"],
    *,
    mirror_fields: bool = False,
) -> dict[str, Any]:
    queue_item = next((item for item in load_queue(root) if int(item.get("track_id") or -1) == int(track_id)), None)
    if queue_item is None:
        raise LookupError(f"track {track_id} is not present in the metadata repair queue")
    if field not in FIELD_NAMES:
        raise ValueError(f"unsupported metadata repair field: {field}")
    state = load_state(root)
    items = state.setdefault("items", {})
    existing = items.get(str(track_id), {})
    current_fields = existing.get("fields") if isinstance(existing.get("fields"), dict) else {}
    queue_fields = queue_item.get("fields") if isinstance(queue_item.get("fields"), dict) else {}
    normalized_fields: dict[str, dict[str, Any]] = {}
    for name in FIELD_NAMES:
        selected_status = review_status if mirror_fields or name == field else str(
            (current_fields or {}).get(name, {}).get("status") or "pending"
        ).lower()
        if selected_status not in {"pending", "approved", "rejected", "deferred", "applied", "no_op"}:
            selected_status = "pending"
        existing_field = (current_fields or {}).get(name, {})
        queue_field = (queue_fields or {}).get(name, {})
        normalized_fields[name] = {
            "status": selected_status,
            "current": existing_field.get("current", queue_field.get("current")),
            "proposed": existing_field.get("proposed", queue_field.get("proposed")),
            "original_proposed": existing_field.get("original_proposed", queue_field.get("original_proposed", queue_field.get("proposed"))),
            "edited": bool(existing_field.get("edited", False)),
            **{key: existing_field[key] for key in ("applied_at", "applied_value", "previous_value") if key in existing_field},
        }
    status = _derived_status(normalized_fields)
    items[str(track_id)] = {
        "track_id": int(track_id),
        "fields": normalized_fields,
        "review_status": status.lower(),
        "status": status,
        "updated_at": _utc_now(),
    }
    state["updated_at"] = _utc_now()
    return save_state(root, state)


def set_field_proposal(
    root: str | Path,
    track_id: int,
    field: Literal["artist", "title"],
    proposed: str,
) -> dict[str, Any]:
    queue_item = next((item for item in load_queue(root) if int(item.get("track_id") or -1) == int(track_id)), None)
    if queue_item is None:
        raise LookupError(f"track {track_id} is not present in the metadata repair queue")
    if field not in FIELD_NAMES:
        raise ValueError(f"unsupported metadata repair field: {field}")
    cleaned_proposed = _clean_text(proposed)
    if not cleaned_proposed:
        raise ValueError("proposed metadata value cannot be empty")

    state = load_state(root)
    items = state.setdefault("items", {})
    existing = items.get(str(track_id), {})
    current_fields = existing.get("fields") if isinstance(existing.get("fields"), dict) else {}
    queue_fields = queue_item.get("fields") if isinstance(queue_item.get("fields"), dict) else {}
    normalized_fields: dict[str, dict[str, Any]] = {}
    for name in FIELD_NAMES:
        existing_field = (current_fields or {}).get(name, {})
        queue_field = (queue_fields or {}).get(name, {})
        original_proposed = existing_field.get("original_proposed", queue_field.get("original_proposed", queue_field.get("proposed")))
        field_proposed = existing_field.get("proposed", queue_field.get("proposed"))
        edited = bool(existing_field.get("edited", False))
        if name == field:
            field_proposed = cleaned_proposed
            edited = cleaned_proposed != _clean_text(original_proposed)
        status = str(existing_field.get("status") or queue_field.get("status") or "pending").lower()
        if status not in {"pending", "approved", "rejected", "deferred", "applied", "no_op"}:
            status = "pending"
        normalized_fields[name] = {
            "status": status,
            "current": existing_field.get("current", queue_field.get("current")),
            "proposed": field_proposed,
            "original_proposed": original_proposed,
            "edited": edited,
            **{key: existing_field[key] for key in ("applied_at", "applied_value", "previous_value") if key in existing_field},
        }

    status = _derived_status(normalized_fields)
    items[str(track_id)] = {
        "track_id": int(track_id),
        "fields": normalized_fields,
        "review_status": status.lower(),
        "status": status,
        "updated_at": _utc_now(),
    }
    state["updated_at"] = _utc_now()
    return save_state(root, state)


def _record_apply_outcomes(
    root: str | Path,
    *,
    changes: list[dict[str, Any]],
    no_ops: list[dict[str, Any]],
    applied_at: str,
) -> dict[str, Any]:
    if not changes and not no_ops:
        return load_state(root)
    state = load_state(root)
    items = state.setdefault("items", {})
    queue_by_id = {int(item.get("track_id")): item for item in load_queue(root) if item.get("track_id") is not None}

    for record in changes:
        track_id = int(record["track_id"])
        item = items.setdefault(str(track_id), {"track_id": track_id, "fields": {}})
        current_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        queue_fields = (queue_by_id.get(track_id, {}).get("fields") or {})
        fields: dict[str, dict[str, Any]] = {}
        for field in FIELD_NAMES:
            existing = current_fields.get(field, {}) if isinstance(current_fields, dict) else {}
            queue_field = queue_fields.get(field, {}) if isinstance(queue_fields, dict) else {}
            fields[field] = _normalize_field_state(
                existing,
                fallback_current=queue_field.get("current"),
                fallback_proposed=queue_field.get("proposed"),
            )
        for field in record.get("changed_fields", []):
            fields[field] = {
                **fields[field],
                "status": "applied",
                "applied_at": applied_at,
                "applied_value": record["after"][field],
                "previous_value": record["before"][field],
            }
        status = _derived_status(fields)
        items[str(track_id)] = {
            **item,
            "track_id": track_id,
            "fields": fields,
            "review_status": status.lower(),
            "status": status,
            "updated_at": applied_at,
        }

    for record in no_ops:
        track_id = int(record["track_id"])
        item = items.setdefault(str(track_id), {"track_id": track_id, "fields": {}})
        current_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        queue_fields = (queue_by_id.get(track_id, {}).get("fields") or {})
        fields = {}
        for field in FIELD_NAMES:
            existing = current_fields.get(field, {}) if isinstance(current_fields, dict) else {}
            queue_field = queue_fields.get(field, {}) if isinstance(queue_fields, dict) else {}
            fields[field] = _normalize_field_state(
                existing,
                fallback_current=queue_field.get("current"),
                fallback_proposed=queue_field.get("proposed"),
            )
        for field in record.get("no_op_fields", []):
            proposed_value = fields[field].get("proposed")
            fields[field] = {
                **fields[field],
                "status": "no_op",
                "applied_at": applied_at,
                "applied_value": proposed_value,
                "previous_value": proposed_value,
            }
        status = _derived_status(fields)
        items[str(track_id)] = {
            **item,
            "track_id": track_id,
            "fields": fields,
            "review_status": status.lower(),
            "status": status,
            "updated_at": applied_at,
        }

    state["updated_at"] = applied_at
    return save_state(root, state)


def _approved_plan(root: str | Path) -> tuple[Paths, list[dict[str, Any]], list[dict[str, Any]], int]:
    p = paths_for_root(root)
    queue_items = {int(item.get("track_id")): item for item in load_queue(root) if item.get("track_id") is not None}
    proposed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    approved_seen = 0
    for track_id in sorted(queue_items):
        item = queue_items[track_id]
        fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        approved_fields = [
            field
            for field in FIELD_NAMES
            if str((fields.get(field) or {}).get("status") or "pending").lower() == "approved"
        ]
        if not approved_fields:
            continue
        approved_seen += 1
        proposed.append({"track_id": track_id, "item": item, "approved_fields": approved_fields})
    return p, proposed, skipped, approved_seen


def apply_approved(root: str | Path, *, apply: bool = False) -> dict[str, Any]:
    p, proposed, skipped, approved_seen = _approved_plan(root)
    changes: list[dict[str, Any]] = []
    no_ops: list[dict[str, Any]] = []
    row_by_id: dict[int, sqlite3.Row] = {}
    with _connect(p.db_path, readonly=not apply) as conn:
        for item in proposed:
            track_id = int(item["track_id"])
            queue_item = item["item"]
            approved_fields = list(item["approved_fields"])
            columns = {col["name"] for col in conn.execute("PRAGMA table_info(tracks)").fetchall()}
            select_exprs = [
                column if column in columns else f"NULL AS {column}"
                for column in ("id", "filepath", "artist", "title", "bpm", "key_musical", "key_camelot")
            ]
            row = conn.execute(
                f"SELECT {', '.join(select_exprs)} FROM tracks WHERE id = ?",
                (track_id,),
            ).fetchone()
            if row is None:
                skipped.append({"track_id": track_id, "reason": "track_missing"})
                continue
            row_by_id[track_id] = row
            fields = queue_item.get("fields") if isinstance(queue_item.get("fields"), dict) else {}
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            changed_fields: list[str] = []
            no_op_fields: list[str] = []
            for field in approved_fields:
                field_state = fields.get(field) if isinstance(fields, dict) else {}
                proposed_value = _clean_text((field_state or {}).get("proposed"))
                current_value = row[field]
                before[field] = current_value
                after[field] = proposed_value or current_value
                if not proposed_value:
                    continue
                if _clean_text(current_value) == proposed_value:
                    no_op_fields.append(field)
                    continue
                changed_fields.append(field)
            if not changed_fields:
                skipped.append({"track_id": track_id, "reason": "no_effective_field_changes", "approved_fields": approved_fields})
                if no_op_fields:
                    no_ops.append({"track_id": track_id, "no_op_fields": no_op_fields})
                continue
            if no_op_fields:
                no_ops.append({"track_id": track_id, "no_op_fields": no_op_fields})
            change = {
                "track_id": track_id,
                "filepath": row["filepath"],
                "confidence": item.get("confidence"),
                "repair_type": item.get("repair_type"),
                "approved_fields": approved_fields,
                "changed_fields": changed_fields,
                "before": before,
                "after": after,
            }
            changes.append(change)
        if apply:
            for change in changes:
                sets: list[str] = []
                values: list[Any] = []
                for field in change["changed_fields"]:
                    sets.append(f"{field} = ?")
                    values.append(change["after"][field])
                if not sets:
                    continue
                values.append(change["track_id"])
                conn.execute(
                    f"UPDATE tracks SET {', '.join(sets)} WHERE id = ?",
                    tuple(values),
                )
            conn.commit()
            _record_apply_outcomes(root, changes=changes, no_ops=no_ops, applied_at=_utc_now())
    return {
        "root": str(p.root),
        "db_path": str(p.db_path),
        "queue_path": str(p.queue_path),
        "state_path": str(p.state_path),
        "dry_run": not apply,
        "approved_seen": approved_seen,
        "proposed_count": len(changes),
        "applied_count": len(changes) if apply else 0,
        "applied_field_count": sum(len(change.get("changed_fields", [])) for change in changes) if apply else 0,
        "skipped_count": len(skipped),
        "changes": changes,
        "skipped": skipped,
    }


def print_scan_summary(result: dict[str, Any]) -> None:
    counts = result.get("counts", {})
    confidence = counts.get("by_confidence", {})
    print("\n=== metadata-repair-scan ===")
    print(f"root: {result['root']}")
    print(f"queue: {result['queue_path']}")
    print(f"tracks: {result['total_tracks']}  proposals: {result['proposal_count']}  skipped: {result['skipped_count']}")
    print(
        "confidence: "
        f"HIGH={confidence.get('HIGH', 0)} "
        f"MEDIUM={confidence.get('MEDIUM', 0)} "
        f"LOW={confidence.get('LOW', 0)}"
    )


def print_apply_summary(result: dict[str, Any]) -> None:
    mode = "DRY RUN" if result.get("dry_run") else "APPLY"
    print(f"\n=== metadata-repair-apply {mode} ===")
    print(f"root: {result['root']}")
    print(f"approved seen: {result['approved_seen']}")
    print(f"proposed: {result['proposed_count']}  applied: {result['applied_count']}  skipped: {result['skipped_count']}")
