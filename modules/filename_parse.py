"""
Deterministic filename parsing helpers.

Used by local metadata extraction and API issue heuristics.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Sequence

from .parser import (
    classify_name_candidate,
    is_valid_artist,
    is_valid_title,
    normalize_separators,
    _extract_version,
    parse_filename_stem,
    remove_prefix_markers,
    remove_track_number_prefix,
)

_RE_MULTIPLE_SEPARATORS = re.compile(r"(?:\s*-\s*){2,}")
_RE_SINGLE_SEPARATOR = re.compile(r"^(.+?)\s*-\s*(.+)$")
_RE_TRAILING_GOLD = re.compile(r"(?:\s*[-–—]\s*gold\s*)$", re.IGNORECASE)
_RE_WEAK_NOISE = re.compile(
    r"(?i)(https?://|www\.|\.com\b|fordjonly|djcity|zipdj|traxsource|promo|download)"
)
_RE_FOLDERISH_ARTIST = re.compile(
    r"(?i)\b(folder|folders|album|library|downloads?|inbox|sorted|music|track(?:s)?|collection|playlist|mixes?|set)\b"
)
_RE_FOLDERISH_TITLE = re.compile(
    r"(?i)\b(folder|folders|album|library|downloads?|inbox|sorted|collection|playlist|mixes?)\b"
)


@dataclass
class FilenameParseResult:
    artist: str = ""
    title: str = ""
    version: str = ""
    parse_confidence: str = "LOW"
    reasons: List[str] = field(default_factory=list)
    accepted: bool = False
    suspicious_artist: bool = False
    suspicious_title: bool = False

    def combined_title(self) -> str:
        if self.version and self.version.lower() not in self.title.lower():
            return f"{self.title} ({self.version})".strip()
        return self.title.strip()


def _strip_suffix_junk(title: str) -> tuple[str, bool]:
    cleaned = title.strip()
    changed = False
    while True:
        new = _RE_TRAILING_GOLD.sub("", cleaned).strip()
        if new == cleaned:
            break
        cleaned = new
        changed = True
    return cleaned, changed


def _has_clear_separator(stem: str) -> bool:
    return " - " in stem


def _split_single_separator(text: str) -> tuple[str, str, str] | None:
    """
    Split a malformed single-hyphen separator such as "Artist-Title" or
    "Artist -Title" only when both sides still look like real names.

    This is intentionally conservative. Short artist/title fragments such as
    "A-ha" are rejected.
    """
    if text.count("-") != 1 or " - " in text:
        return None

    match = _RE_SINGLE_SEPARATOR.match(text)
    if not match:
        return None

    left = match.group(1).strip()
    right = match.group(2).strip()
    if len(left) < 2 or len(right) < 2:
        return None
    if len(left) < 3 and len(right) < 3:
        return None
    if not is_valid_artist(left):
        return None
    title, _version = _extract_version(right)
    if not is_valid_title(title):
        return None
    return left, title, _version


def _score_confidence(
    *,
    normalized_stem: str,
    raw_stem: str,
    artist: str,
    title: str,
    version: str,
    reasons: list[str],
    suspicious_artist: bool,
    suspicious_title: bool,
) -> str:
    if not artist or not title:
        return "LOW"
    if not is_valid_artist(artist) or not is_valid_title(title):
        return "LOW"
    if suspicious_artist or suspicious_title:
        return "LOW"
    if _RE_WEAK_NOISE.search(raw_stem):
        return "LOW"

    score = 100
    if not _has_clear_separator(normalized_stem):
        score -= 35 if "single_separator_normalized" in reasons else 60
    if "single_separator_normalized" in reasons:
        score -= 10
    if "track_prefix_removed" in reasons:
        score -= 15
    if "prefix_marker_removed" in reasons:
        score -= 15
    if "malformed_separator_normalized" in reasons:
        score -= 20
    if "suffix_junk_stripped" in reasons:
        score -= 15
    if "folderish_contamination" in reasons:
        score -= 35
    if version:
        score -= 0

    if score >= 80:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    return "LOW"


def _flag_suspicious_artist(value: str) -> bool:
    if not value:
        return False
    if not is_valid_artist(value):
        return True
    if classify_name_candidate(value)["type"] == "label":
        return True
    return bool(_RE_FOLDERISH_ARTIST.search(value) or _RE_WEAK_NOISE.search(value))


def _flag_suspicious_title(value: str) -> bool:
    if not value:
        return False
    if not is_valid_title(value):
        return True
    if classify_name_candidate(value)["type"] == "label":
        return True
    return bool(_RE_FOLDERISH_TITLE.search(value) or _RE_WEAK_NOISE.search(value))


def parse_filename_metadata(stem: str) -> FilenameParseResult:
    """
    Parse a filename stem into artist/title/version with a confidence score.

    Confidence rules are conservative:
    - HIGH for a clear artist-title separator and clean values
    - MEDIUM for deterministic cleanup cases that still parse cleanly
    - LOW when the parse is weak or suspicious
    """
    result = FilenameParseResult()
    if not stem:
        return result

    raw = stem.strip()
    reasons: list[str] = []

    normalized = normalize_separators(raw)
    if normalized != raw:
        reasons.append("separator_normalized")

    collapsed = _RE_MULTIPLE_SEPARATORS.sub(" - ", normalized)
    if collapsed != normalized:
        reasons.append("malformed_separator_normalized")
    text = collapsed

    stripped_prefix, prefix_type = remove_prefix_markers(text)
    if prefix_type:
        reasons.append("prefix_marker_removed")
        text = stripped_prefix

    stripped_number, track_number = remove_track_number_prefix(text)
    if track_number is not None and " - " in stripped_number:
        reasons.append("track_prefix_removed")
        text = stripped_number

    parsed = parse_filename_stem(text)
    artist = str(parsed.get("artist") or "").strip()
    title = str(parsed.get("title") or "").strip()
    version = str(parsed.get("version") or "").strip()

    if not artist or not title:
        fallback = _split_single_separator(text)
        if fallback is not None:
            artist, title, fallback_version = fallback
            if fallback_version:
                version = fallback_version
            reasons.append("single_separator_normalized")

    title, stripped_gold = _strip_suffix_junk(title)
    if stripped_gold:
        reasons.append("suffix_junk_stripped")

    suspicious_artist = _flag_suspicious_artist(artist)
    suspicious_title = _flag_suspicious_title(title)

    if not artist or not title:
        confidence = "LOW"
    else:
        confidence = _score_confidence(
            normalized_stem=text,
            raw_stem=raw,
            artist=artist,
            title=title,
            version=version,
            reasons=reasons,
            suspicious_artist=suspicious_artist,
            suspicious_title=suspicious_title,
        )

    accepted = confidence in {"HIGH", "MEDIUM"} and not suspicious_artist and not suspicious_title
    if not accepted and confidence == "MEDIUM":
        confidence = "LOW"

    result.artist = artist
    result.title = title
    result.version = version
    result.parse_confidence = confidence
    result.reasons = reasons
    result.accepted = accepted
    result.suspicious_artist = suspicious_artist
    result.suspicious_title = suspicious_title
    return result


def issue_flags_for_metadata(
    *,
    artist: str | None,
    title: str | None,
    parse_confidence: str | None = None,
) -> list[str]:
    issues: list[str] = []
    confidence = (parse_confidence or "").upper()
    if confidence in {"MEDIUM", "LOW"}:
        issues.append("weak_filename_parse")
    if artist and _flag_suspicious_artist(artist):
        issues.append("suspicious_artist")
    if title and _flag_suspicious_title(title):
        issues.append("suspicious_title")
    return issues
