"""
Read-only metadata enrichment scorer.

This module scores Spotify/Deezer-style candidate metadata against rows from
the canonical tracks table. It writes JSONL review logs only; it never writes
audio tags, updates the DB, or changes files.
"""
from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Callable, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class TrackInput:
    filepath: str
    filename: str
    artist: str
    title: str
    duration_sec: float | None = None
    label: str = ""
    isrc: str = ""


Candidate = Mapping[str, object]
Provider = Callable[[dict], Sequence[Candidate]]


def _clean_text(value: object) -> str:
    text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def similarity(left: object, right: object) -> float:
    left_norm = _clean_text(left)
    right_norm = _clean_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    return SequenceMatcher(None, left_norm, right_norm).ratio()


def duration_similarity(track_duration: float | None, candidate_duration: object) -> float:
    if track_duration in (None, 0, "") or candidate_duration in (None, 0, ""):
        return 1.0
    try:
        left = float(track_duration)
        right = float(candidate_duration)
    except (TypeError, ValueError):
        return 0.0
    if left <= 0 or right <= 0:
        return 0.0
    diff = abs(left - right)
    return max(0.0, 1.0 - (diff / max(left, right)))


def confidence_tier(score: float) -> str:
    if score >= 0.92:
        return "HIGH"
    if score >= 0.75:
        return "MEDIUM"
    return "LOW"


def score_candidate(track: TrackInput, candidate: Candidate) -> dict:
    track_isrc = _clean_text(track.isrc).upper()
    candidate_isrc = _clean_text(candidate.get("isrc")).upper()
    exact_isrc = bool(track_isrc and candidate_isrc and track_isrc == candidate_isrc)

    title_sim = similarity(track.title, candidate.get("title"))
    artist_sim = similarity(track.artist, candidate.get("artist"))
    candidate_duration = candidate.get("duration_sec", candidate.get("duration"))
    dur_sim = duration_similarity(track.duration_sec, candidate_duration)
    label_sim = (
        similarity(track.label, candidate.get("label"))
        if track.label and candidate.get("label")
        else 1.0
    )

    score = (
        0.5 * title_sim
        + 0.3 * artist_sim
        + 0.1 * dur_sim
        + 0.1 * label_sim
    )
    if exact_isrc:
        score = 1.0

    return {
        "provider": candidate.get("provider", ""),
        "title": candidate.get("title", ""),
        "artist": candidate.get("artist", ""),
        "album": candidate.get("album", ""),
        "label": candidate.get("label", ""),
        "isrc": candidate.get("isrc", ""),
        "duration_sec": candidate_duration,
        "score": round(score, 6),
        "confidence": confidence_tier(score),
        "signals": {
            "title_similarity": round(title_sim, 6),
            "artist_similarity": round(artist_sim, 6),
            "duration_similarity": round(dur_sim, 6),
            "label_similarity": round(label_sim, 6),
            "exact_isrc": exact_isrc,
        },
    }


def _parse_filename(filename: str) -> tuple[str, str]:
    stem = Path(filename).stem
    if " - " in stem:
        artist, title = stem.split(" - ", 1)
        return artist.strip(), title.strip()
    return "", stem.strip()


def _track_from_row(row: sqlite3.Row) -> TrackInput:
    keys = set(row.keys())
    filepath = str(row["filepath"])
    filename = str(row["filename"] if "filename" in keys and row["filename"] else Path(filepath).name)
    parsed_artist, parsed_title = _parse_filename(filename)
    artist = str(row["artist"] or "").strip() if "artist" in keys else ""
    title = str(row["title"] or "").strip() if "title" in keys else ""
    return TrackInput(
        filepath=filepath,
        filename=filename,
        artist=artist or parsed_artist,
        title=title or parsed_title,
        duration_sec=row["duration_sec"] if "duration_sec" in keys else None,
        label=str(row["label"] or "").strip() if "label" in keys else "",
        isrc=str(row["isrc"] or "").strip() if "isrc" in keys else "",
    )


def load_tracks(root: Path) -> list[TrackInput]:
    db_path = root / "logs" / "processed.db"
    if not db_path.exists():
        return []
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    try:
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tracks'"
        ).fetchone()
        if table is None:
            return []
        columns = [row["name"] for row in conn.execute("PRAGMA table_info(tracks)").fetchall()]
        select_cols = [
            "filepath",
            "filename" if "filename" in columns else "NULL AS filename",
            "artist" if "artist" in columns else "NULL AS artist",
            "title" if "title" in columns else "NULL AS title",
            "duration_sec" if "duration_sec" in columns else "NULL AS duration_sec",
            "label" if "label" in columns else "NULL AS label",
            "isrc" if "isrc" in columns else "NULL AS isrc",
        ]
        rows = conn.execute(f"SELECT {', '.join(select_cols)} FROM tracks").fetchall()
        return [_track_from_row(row) for row in rows if row["filepath"]]
    finally:
        conn.close()


def _mock_isrc(query: dict, provider: str) -> str:
    base = _clean_text(f"{query.get('artist', '')} {query.get('title', '')}")
    checksum = sum(ord(ch) for ch in f"{provider}:{base}") % 10000000000
    return f"MOCK{checksum:010d}"[:12]


def _duration_or_default(query: dict) -> int:
    try:
        duration = int(float(query.get("duration_sec") or 0))
    except (TypeError, ValueError):
        duration = 0
    return duration if duration > 0 else 240


def search_spotify(query: dict) -> Sequence[Candidate]:
    artist = str(query.get("artist") or "").strip() or "Unknown Artist"
    title = str(query.get("title") or "").strip() or Path(str(query.get("filename") or "Unknown Title")).stem
    duration = _duration_or_default(query)
    label = str(query.get("label") or "").strip() or "Mock Spotify Records"
    return [
        {
            "provider": "spotify",
            "artist": artist,
            "title": title,
            "duration": duration + 1,
            "duration_sec": duration + 1,
            "label": label,
            "isrc": str(query.get("isrc") or "").strip() or _mock_isrc(query, "spotify"),
        },
        {
            "provider": "spotify",
            "artist": f"{artist} DJ",
            "title": f"{title} Edit",
            "duration": duration + 7,
            "duration_sec": duration + 7,
            "label": label,
            "isrc": _mock_isrc({**query, "artist": f"{artist} DJ", "title": f"{title} Edit"}, "spotify"),
        },
        {
            "provider": "spotify",
            "artist": "Unrelated Artist",
            "title": "Unrelated Track",
            "duration": max(60, duration - 73),
            "duration_sec": max(60, duration - 73),
            "label": "Unrelated Label",
            "isrc": _mock_isrc({"artist": "Unrelated Artist", "title": "Unrelated Track"}, "spotify"),
        },
    ]


def search_deezer(query: dict) -> Sequence[Candidate]:
    artist = str(query.get("artist") or "").strip() or "Unknown Artist"
    title = str(query.get("title") or "").strip() or Path(str(query.get("filename") or "Unknown Title")).stem
    duration = _duration_or_default(query)
    label = str(query.get("label") or "").strip() or "Mock Deezer Label"
    return [
        {
            "provider": "deezer",
            "artist": artist,
            "title": title,
            "duration": max(1, duration - 2),
            "duration_sec": max(1, duration - 2),
            "label": label,
            "isrc": str(query.get("isrc") or "").strip() or _mock_isrc(query, "deezer"),
        },
        {
            "provider": "deezer",
            "artist": f"{artist} DJ",
            "title": f"{title} Club",
            "duration": duration + 9,
            "duration_sec": duration + 9,
            "label": label,
            "isrc": _mock_isrc({**query, "artist": f"{artist} DJ", "title": f"{title} Club"}, "deezer"),
        },
        {
            "provider": "deezer",
            "artist": "Different Artist",
            "title": "Different Song",
            "duration": max(60, duration + 91),
            "duration_sec": max(60, duration + 91),
            "label": "Different Label",
            "isrc": _mock_isrc({"artist": "Different Artist", "title": "Different Song"}, "deezer"),
        },
    ]


def query_spotify(query: dict) -> Sequence[Candidate]:
    return []


def query_deezer(query: dict) -> Sequence[Candidate]:
    return []


def _score_track(
    track: TrackInput,
    providers: Mapping[str, Provider],
) -> dict:
    query = {
        "artist": track.artist,
        "title": track.title,
        "filename": track.filename,
        "duration_sec": track.duration_sec,
        "label": track.label,
        "isrc": track.isrc,
    }
    scored: list[dict] = []
    for provider_name, provider in providers.items():
        for raw_candidate in provider(query):
            candidate = dict(raw_candidate)
            candidate.setdefault("provider", provider_name)
            scored.append(score_candidate(track, candidate))

    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0] if scored else None
    score = float(best["score"]) if best else 0.0
    return {
        "filepath": track.filepath,
        "query": query,
        "candidates": scored,
        "best_match": best,
        "score": round(score, 6),
        "confidence": confidence_tier(score),
    }


def write_jsonl(entries: Iterable[dict], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def action_suggestion(confidence: str) -> str:
    if confidence == "HIGH":
        return "auto_candidate"
    if confidence == "MEDIUM":
        return "review"
    return "ignore"


def build_review_queue_entries(entries: Iterable[dict], timestamp: str) -> list[dict]:
    queue_entries: list[dict] = []
    for entry in entries:
        best_match = entry.get("best_match") or {}
        confidence = str(entry.get("confidence", "LOW"))
        queue_entries.append({
            "filepath": entry.get("filepath", ""),
            "query": entry.get("query", {}),
            "best_match": best_match,
            "score": entry.get("score", 0.0),
            "confidence": confidence,
            "provider": best_match.get("provider", ""),
            "action_suggestion": action_suggestion(confidence),
            "timestamp": timestamp,
        })
    return queue_entries


def run(
    root: str | Path,
    *,
    providers: Mapping[str, Provider] | None = None,
    mock_providers: bool = False,
    now: Callable[[], datetime] | None = None,
) -> dict:
    root_path = Path(root).expanduser().resolve()
    if providers is not None:
        provider_map = providers
    elif mock_providers:
        provider_map = {
            "spotify": search_spotify,
            "deezer": search_deezer,
        }
    else:
        provider_map = {
            "spotify": query_spotify,
            "deezer": query_deezer,
        }
    run_at = (now or datetime.now)()
    stamp = run_at.strftime("%Y%m%d_%H%M%S")
    timestamp = run_at.isoformat()
    log_path = root_path / "logs" / "enrichment" / f"{stamp}_enrich_online.jsonl"
    queue_path = root_path / "data" / "intelligence" / "enrichment_review_queue.jsonl"

    tracks = load_tracks(root_path)
    entries = [_score_track(track, provider_map) for track in tracks]
    write_jsonl(entries, log_path)
    queue_entries = build_review_queue_entries(entries, timestamp)
    write_jsonl(queue_entries, queue_path)
    return {
        "tracks_scored": len(entries),
        "log_path": str(log_path),
        "queue_path": str(queue_path),
        "entries": entries,
        "queue_entries": queue_entries,
    }
