"""
Label cleaning — detection, confidence scoring, and optional tag write-back.

Detection priority per track:
  1. Embedded organization/TPUB tag  (confidence 0.95 if valid)
  2. Fallback metadata fields         (grouping 0.75, comment 0.60, album 0.45)
  3. Filename pattern parsing         (0.55 – 0.70 depending on pattern)
  4. Unresolved                       (confidence 0.0)

Write-back only occurs when confidence >= WRITE_THRESHOLD (default 0.85),
which intentionally limits automatic writes to embedded-tag cases where the
tag was already present but needed cleaning.  Fallback / filename results
appear in reports for manual review.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .filename_parser import parse_label_from_filename
from .normalizer import AliasRegistry, build_label_names, normalize_label

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Junk-label detection — loaded from config/junk_patterns.json
# ---------------------------------------------------------------------------
try:
    from modules.junk_patterns import load_junk_patterns as _load_jp
    _jp = _load_jp()
    _JUNK_EXACT:             frozenset[str]   = _jp.exact_bad_labels
    _GENRE_WORDS:            frozenset[str]   = _jp.genre_words
    _SOURCE_JUNK_SUBSTRINGS: tuple[str, ...]  = _jp.source_junk_substrings
    _domain_tlds_str = "|".join(re.escape(t) for t in _jp.domain_tlds) if _jp.domain_tlds else "com|net|org|fm|dj|co|io"
except Exception:
    # Fallback: hardcoded minimums if modules/ is not importable
    _JUNK_EXACT: frozenset[str] = frozenset({
        "", "unknown", "n/a", "na", "none", "null",
        "test", "promo", "various", "various artists", "va",
        "-", "--", "?", "??", "tbc", "tba", "untitled",
    })
    _GENRE_WORDS: frozenset[str] = frozenset({
        "house", "techno", "deep house", "tech house", "afro house",
        "drum and bass", "dnb", "jungle", "garage", "uk garage",
        "trance", "progressive", "melodic house", "organic house",
        "amapiano", "afrobeats", "electronic", "dance", "edm",
    })
    _SOURCE_JUNK_SUBSTRINGS: tuple[str, ...] = (
        "traxcrate", "fordjonly", "djcity", "zipdj", "musicafresca",
        "downloaded from", "promo only", "official audio", "official video",
        "official music video", "free download",
    )
    _domain_tlds_str = "com|net|org|fm|dj|co|io"

_JUNK_PATTERNS: list[re.Pattern] = [
    re.compile(r"^\s*$"),                                  # whitespace only
    re.compile(r"^[^a-z0-9]{1,3}$", re.IGNORECASE),      # pure symbols ≤ 3 chars
    re.compile(r"^[a-z]{2,6}[-_]?\d{3,7}$", re.IGNORECASE),  # catalog code e.g. ABC001
    re.compile(r"^[a-z]\d+$", re.IGNORECASE),             # short catalog e.g. A001
    re.compile(r"^0+$"),                                   # all zeros
]

# Camelot / Open Key notation  (e.g. 8A, 11B, 3A -, 10B -)
_CAMELOT_KEY = re.compile(r"^(1[0-2]|[1-9])[AB]\s*[-|_]*\s*$", re.IGNORECASE)

# URL and domain junk
_URL_PREFIX    = re.compile(r"https?://|www\.", re.IGNORECASE)
_DOMAIN_ENDING = re.compile(
    r"\.(" + _domain_tlds_str + r")(\s|/|$)", re.IGNORECASE
)

# Known DJ-pool / source-watermark substrings — loaded from JSON above.

# ---------------------------------------------------------------------------
# Candidate normalization (applied before junk checks)
# ---------------------------------------------------------------------------
_RE_REPEATED_SEP = re.compile(r"[\s\-|_]{2,}")
_RE_SURROUND_BRACKET = re.compile(r"^[\[\]()\s]+|[\[\]()\s]+$")


def _normalize_candidate(value: str) -> str:
    """
    Normalize a label candidate string before junk validation.

    - strips leading/trailing whitespace
    - removes surrounding brackets and parentheses
    - collapses repeated separators (space, dash, pipe, underscore)
    - strips leading/trailing punctuation marks
    """
    v = value.strip()
    v = _RE_SURROUND_BRACKET.sub("", v).strip()
    v = _RE_REPEATED_SEP.sub(" ", v).strip()
    v = v.strip(".-_|,;:!?")
    return v.strip()


# ---------------------------------------------------------------------------
# Reason-returning core (used by both is_junk_label and logging helpers)
# ---------------------------------------------------------------------------

def _junk_reason(value: str) -> "Optional[str]":
    """
    Return a human-readable rejection reason if *value* is junk, else None.

    Checks are applied in priority order.  The raw stripped value is used for
    URL / Camelot / source-watermark checks; the normalized form is used for
    structural pattern checks.
    """
    if not value:
        return "empty"
    raw = value.strip()
    if not raw:
        return "empty after strip"

    raw_lower = raw.lower()

    # Camelot / musical-key notation — check before normalization so that
    # trailing separator variants ("8A -") are caught by the regex directly.
    if _CAMELOT_KEY.match(raw):
        return "Camelot/musical key"

    # URL or domain watermark
    if _URL_PREFIX.search(raw_lower):
        return "URL or domain prefix"
    if _DOMAIN_ENDING.search(raw_lower):
        return "domain name"

    # DJ-pool / source watermarks
    for junk in _SOURCE_JUNK_SUBSTRINGS:
        if junk in raw_lower:
            return f"source junk ({junk!r})"

    # Now apply normalization for the remaining structural checks
    v  = _normalize_candidate(raw)
    vl = v.lower()

    if not v:
        return "empty after normalization"
    if len(v) == 1:
        return "single character"
    if vl in _JUNK_EXACT:
        return "exact junk value"
    if vl in _GENRE_WORDS:
        return "genre word"
    for pat in _JUNK_PATTERNS:
        if pat.match(vl):
            return "junk pattern"
    return None


def is_junk_label(value: str) -> bool:
    """
    Return True if value is clearly not a real label name.

    Rejects: empty, whitespace, 'unknown', 'n/a', single characters,
    pure catalog codes, obvious genre words, pure symbol strings,
    Camelot/musical keys, URLs, domain names, and DJ-pool watermarks.

    Normalizes the candidate before structural checks (strips surrounding
    brackets, collapses repeated separators, removes edge punctuation).
    """
    return _junk_reason(value) is not None


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------
def read_tags(path: Path) -> dict:
    """
    Read all metadata fields relevant to label detection.

    Returns a dict with keys:
      artist, title, album, albumartist, genre,
      organization, grouping, comment
    All values are strings (empty string if tag absent or unreadable).
    """
    out = {
        "artist": "", "title": "", "album": "", "albumartist": "",
        "genre": "", "organization": "", "grouping": "", "comment": "",
    }
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return out
        for key in out:
            try:
                vals = audio.get(key)
                if vals:
                    out[key] = str(vals[0]).strip()
            except Exception:
                pass
    except Exception as exc:
        log.debug("Could not read tags from %s: %s", path, exc)
    return out


# ---------------------------------------------------------------------------
# Confidence constants
# ---------------------------------------------------------------------------
_SOURCE_EMBEDDED   = "embedded_tag"
_SOURCE_FALLBACK   = "fallback_tag"
_SOURCE_FILENAME   = "filename"
_SOURCE_UNRESOLVED = "unresolved"

_CONF_EMBEDDED              = 0.95
_CONF_FALLBACK_GROUPING     = 0.75
_CONF_FALLBACK_COMMENT      = 0.60
_CONF_FALLBACK_ALBUMARTIST  = 0.50
_CONF_FALLBACK_ALBUM        = 0.45

#: Minimum confidence for automatic write-back (--write-tags).
#: At 0.85 only embedded_tag (0.95) qualifies; all fallback / filename results
#: are reported but not written unless user lowers this via --confidence-threshold.
WRITE_THRESHOLD = 0.85


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------
@dataclass
class TrackLabelResult:
    filepath: str
    artist: str
    title: str
    raw_label: Optional[str]          # exactly as found in tag / filename
    cleaned_label: Optional[str]      # after junk removal / source selection
    normalized_label: Optional[str]   # key for deduplication
    canonical_label: Optional[str]    # best display name (may differ after alias merge)
    source: str                       # embedded_tag | fallback_tag | filename | unresolved
    confidence: float                 # 0.0 – 1.0
    action_taken: str                 # kept | cleaned | filled | unresolved | written | error
    notes: list = field(default_factory=list)
    writable: bool = False            # True ↔ confidence >= write_threshold AND label found
    junk_count: int = 0               # number of candidate values rejected as junk


# ---------------------------------------------------------------------------
# Single-track detection
# ---------------------------------------------------------------------------

def detect_label(
    path: Path,
    write_threshold: float = WRITE_THRESHOLD,
) -> TrackLabelResult:
    """
    Detect the best available label for a single track.

    Never guesses aggressively — returns source='unresolved' rather than
    making a low-confidence claim.
    """
    tags  = read_tags(path)
    notes: list[str] = []
    _jc   = [0]  # [junk_count] — mutable cell so the nested _result() closure can read it

    def _result(raw, cleaned, source, confidence, action, extra=None):
        ln = build_label_names(cleaned) if cleaned else None
        return TrackLabelResult(
            filepath=str(path),
            artist=tags["artist"],
            title=tags["title"],
            raw_label=raw,
            cleaned_label=cleaned,
            normalized_label=ln.normalized if ln else None,
            canonical_label=ln.canonical  if ln else None,
            source=source,
            confidence=confidence,
            action_taken=action,
            notes=notes + (extra or []),
            writable=bool(cleaned) and confidence >= write_threshold,
            junk_count=_jc[0],
        )

    def _reject_junk(field: str, value: str, reason: str) -> None:
        """Log a junk rejection at INFO level and increment the per-track counter."""
        _jc[0] += 1
        log.info(
            "LABEL-CLEAN junk rejected — field=%-16s  value=%-30r  reason=%s  file=%s",
            field, value, reason, path.name,
        )
        notes.append(f"{field} junk ({reason}): {value!r}")

    # ------------------------------------------------------------------
    # 1. Primary: organization / TPUB
    # ------------------------------------------------------------------
    org = tags["organization"]
    if org:
        _reason = _junk_reason(org)
        if not _reason:
            return _result(org, org, _SOURCE_EMBEDDED, _CONF_EMBEDDED, "kept")
        _reject_junk("organization", org, _reason)

    # ------------------------------------------------------------------
    # 2. Fallback fields
    #    albumartist is only accepted if it looks label-ish (contains a
    #    label-indicator word) — otherwise it's too often just the track
    #    artist or "Various Artists".
    # ------------------------------------------------------------------
    _LABEL_INDICATOR = re.compile(
        r"\b(?:records?|recordings?|music|audio|label|trax|sounds?|"
        r"group|collective|entertainment|publishing|digital)\b",
        re.IGNORECASE,
    )

    for fld, conf, tag_label in [
        ("grouping",    _CONF_FALLBACK_GROUPING,    "grouping"),
        ("comment",     _CONF_FALLBACK_COMMENT,     "comment"),
    ]:
        val = tags[fld]
        if val:
            _reason = _junk_reason(val)
            if not _reason:
                notes.append(f"filled from {tag_label} tag")
                return _result(val, val, _SOURCE_FALLBACK, conf, "filled")
            _reject_junk(fld, val, _reason)

    # albumartist — only if it contains a label-indicator word
    aa = tags["albumartist"]
    if aa:
        _reason = _junk_reason(aa)
        if not _reason and _LABEL_INDICATOR.search(aa):
            notes.append("filled from albumartist tag (label indicator word present)")
            return _result(aa, aa, _SOURCE_FALLBACK, _CONF_FALLBACK_ALBUMARTIST, "filled")
        if _reason:
            _reject_junk("albumartist", aa, _reason)

    # album — only if it contains a label-indicator word
    alb = tags["album"]
    if alb:
        _reason = _junk_reason(alb)
        if not _reason and _LABEL_INDICATOR.search(alb):
            notes.append("filled from album tag (label indicator word present)")
            return _result(alb, alb, _SOURCE_FALLBACK, _CONF_FALLBACK_ALBUM, "filled")
        if _reason:
            _reject_junk("album", alb, _reason)

    # ------------------------------------------------------------------
    # 3. Filename parsing
    # ------------------------------------------------------------------
    fn_result = parse_label_from_filename(path.stem)
    if fn_result:
        _reason = _junk_reason(fn_result.label_candidate)
        if not _reason:
            notes.append(f"filename pattern: {fn_result.pattern}")
            return _result(
                fn_result.raw_match,
                fn_result.label_candidate,
                _SOURCE_FILENAME,
                fn_result.confidence,
                "filled",
            )
        _reject_junk("filename", fn_result.label_candidate, _reason)

    # ------------------------------------------------------------------
    # 4. Unresolved
    # ------------------------------------------------------------------
    return _result(org or None, None, _SOURCE_UNRESOLVED, 0.0, "unresolved")


# ---------------------------------------------------------------------------
# Batch scan
# ---------------------------------------------------------------------------

def scan_tracks(
    paths: list[Path],
    write_threshold: float = WRITE_THRESHOLD,
    alias_registry: Optional[AliasRegistry] = None,
) -> list[TrackLabelResult]:
    """
    Scan a list of audio files and return one TrackLabelResult per file.

    If alias_registry is provided, each detected label is registered so that
    canonical display names can be resolved consistently across the batch.
    """
    if alias_registry is None:
        alias_registry = AliasRegistry()

    results: list[TrackLabelResult] = []
    for path in paths:
        try:
            r = detect_label(path, write_threshold)
            if r.cleaned_label:
                alias_registry.register(r.cleaned_label)
            results.append(r)
            log.debug(
                "%-14s  conf=%.2f  %-40s  %s",
                r.source,
                r.confidence,
                (r.canonical_label or "(unresolved)")[:40],
                path.name,
            )
        except Exception as exc:
            log.warning("Error scanning %s: %s", path, exc)
            results.append(TrackLabelResult(
                filepath=str(path),
                artist="", title="",
                raw_label=None, cleaned_label=None,
                normalized_label=None, canonical_label=None,
                source=_SOURCE_UNRESOLVED, confidence=0.0,
                action_taken="error", notes=[f"scan error: {exc}"],
            ))

    # Second pass: apply canonical names from alias registry
    for r in results:
        if r.cleaned_label:
            r.canonical_label = alias_registry.canonical_for(r.cleaned_label)

    return results


# ---------------------------------------------------------------------------
# Tag write-back
# ---------------------------------------------------------------------------

def write_label_tag(path: Path, label: str) -> bool:
    """
    Write label to the file's organization/TPUB tag.

    Returns True on success.  Never raises — logs on failure.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            log.warning("mutagen returned None for %s — skipping write", path)
            return False
        audio["organization"] = [label]
        audio.save()
        return True
    except Exception as exc:
        log.warning("Could not write label tag to %s: %s", path, exc)
        return False
