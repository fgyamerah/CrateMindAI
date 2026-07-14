"""
modules/metadata_sanitize.py — Fully offline, deterministic metadata sanitation.

Scans audio files and applies conservative, rule-based transformations to:
  - album        : clear if obviously junk (URLs, domains, path fragments, promo)
  - isrc         : clear if format is invalid (strict: CC-XXX-YY-NNNNNNN)
  - title        : strip leading numeric prefixes; fix spacing/separators/parentheses
  - artist       : strip URLs; normalize ft./featuring → feat.; fix whitespace
  - organization : clear placeholder junk (unknown/n/a/none/…); normalize whitespace

Preview is the default — no writes without --apply.
Public entry point: run_metadata_sanitize(args) — called by pipeline.py dispatch.
"""
from __future__ import annotations

import json
import logging
import re
import sys
from dataclasses import dataclass, field as dc_field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import modules.run_logger as _proc
from modules.sanitizer import sanitize_text
from modules.textlog import log_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Regex constants
# ---------------------------------------------------------------------------

# ISRC: canonical 12-char form (dashes are stripped before validation)
_ISRC_RE = re.compile(r'^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$')

# Numeric title prefix — requires an explicit separator after the digits so
# "01 Luftballons" is NOT stripped (no separator). Handles:
#   "1 | Title"  "003. Title"  "***2 | Title"  "02- Title"  "3 – Title"
_RE_TITLE_NUM_PREFIX = re.compile(
    r'^\*{0,5}\s*\d{1,3}\s*(?:[|.–—]|-{1,2})\s+',
    re.UNICODE,
)

# Duplicated consecutive separators in title: "  -  -  " → " - "
_RE_TITLE_MULTI_SEP = re.compile(r'\s*[-–—]\s*(?:[-–—]\s*)+')

# Empty brackets/parens
_RE_EMPTY_BRACKET = re.compile(r'[\[(]\s*[\])]')

# Missing space before opening paren — "Title(Original Mix)" → "Title (Original Mix)"
_RE_MISSING_SPACE_BEFORE_PAREN = re.compile(r'(\S)\(')

# Unclosed parenthesis at end of string — "Title (Original Mix"
_RE_UNCLOSED_PAREN = re.compile(r'\s*\([^)]*$')

# Two or more consecutive spaces
_RE_MULTI_SPACE = re.compile(r' {2,}')

# Email addresses — clear the entire field value (album/label only)
_RE_EMAIL = re.compile(
    r'\b[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}\b',
)

# URL-looking strings in artist field
_RE_ARTIST_URL = re.compile(
    r'\bhttps?://\S+|\bwww\.\S+|(?<!\w)[a-z0-9\-]{2,}\.(?:com|net|org)\b',
    re.IGNORECASE,
)

# ft. / featuring → feat. normalisation
_RE_FT_DOT = re.compile(r'\bft\.(?!\s*$)', re.IGNORECASE)
_RE_FEATURING = re.compile(r'\bfeaturing\b', re.IGNORECASE)

# Duplicated separator in artist field: "A, , B" or "A / / B"
_RE_ARTIST_MULTI_SEP = re.compile(r'(?:\s*[,/]\s*){2,}')

# Label placeholder values to clear (exact match after strip + lower)
_LABEL_JUNK_LOWER: frozenset = frozenset({
    "unknown", "n/a", "na", "none", "not available", "unavailable",
    "-", "–", "—", "?", "??", "???",
    "various", "various artists", "va",
    "promo", "white label", "white lbl",
})

# ---------------------------------------------------------------------------
# Title-cleanup additions (new rules)
# ---------------------------------------------------------------------------

# Bare leading track-number with no separator — "2 Sada" → "Sada", "3 Afro" → "Afro"
# Existing _RE_TITLE_NUM_PREFIX handles cases WITH a separator (|, -, –, etc.).
# This rule covers the bare "N<space>Title" pattern.
_RE_TITLE_BARE_NUM_PREFIX = re.compile(
    r'^\d{1,3}\s+(?=[A-Za-z(])',
    re.UNICODE,
)

# Words that, when they follow a bare leading number, indicate the number is part
# of the real title ("4 You", "15 Minutes", "3 Years") and must NOT be stripped.
_BARE_NUM_PROTECTED_WORDS: frozenset = frozenset({
    "you", "me", "us", "them",
    "minutes", "hours", "days", "seconds", "years",
    "love", "life", "one", "two",
})

# Domain / piracy tokens in any parenthetical:
# "(fordjonly.com)", "(hulkshare.com)", "(htpthahouse-lovers.blogspot.com)" etc.
_RE_TITLE_DOMAIN_PAREN = re.compile(
    r'\s*\([^)]*(?:\.com|\.net|\.org|blogspot|hulkshare|zippyshare|fordjonly|djonly|mp3(?:[\s.)]|$))[^)]*\)',
    re.IGNORECASE,
)

# Trailing BPM at very end — preceded by space or ')' (lookbehind, fixed width 1 char)
# Range enforced in code: 80–160 only.
_RE_TITLE_TRAILING_BPM = re.compile(r'(?<=[\s)])\s*(\d{2,3})\s*$')

# "(Feat." → "(feat." casing normalisation in titles
_RE_TITLE_FEAT_UPPER = re.compile(r'\(Feat\.')

# Keywords that mark a trailing paren as a VERSION/MIX — these must NOT be stripped.
_PROTECTED_PAREN_WORDS: frozenset = frozenset({
    "mix", "edit", "version", "remix", "dub", "vocal", "club", "rework",
    "reprise", "instrumental", "radio", "extended", "original", "short",
    "vip", "acapella", "acoustic", "live", "bootleg", "reconstruction",
    "refix", "remaster", "remastered", "retro", "intro", "outro",
    "cut", "flip", "mashup", "blend", "snippet", "preview", "interlude",
})

# Keywords indicating a trailing paren is a record-label name.
_LABEL_PAREN_KEYWORDS: frozenset = frozenset({
    "records", "recordings", "music", "entertainment", "digital", "audio",
    "sound", "sounds", "label", "labels", "group", "media", "distrokid",
    "publishing", "productions", "distribution",
})

# Known label strings to strip even without a keyword match (lowercase, no parens).
_KNOWN_STRIP_LABELS: frozenset = frozenset({
    "shockit",
    "techno and chill",
})

# Version token stored in processed-state reason for "no_change" records.
# Bump this string whenever title-cleanup rules are added or changed so that
# stale "no_change" records are invalidated and all files are re-evaluated.
_SANITIZE_RULES_VERSION = "v6"
_RULES_REASON = f"rules:{_SANITIZE_RULES_VERSION}"

# Maps _sanitize_title() reason codes to named debug counters for the run summary.
_COUNTER_FOR_REASON: dict = {
    "title_bare_number_stripped":  "title_leading_number_fixes",
    "title_trailing_bpm_stripped": "bpm_suffix_removed",
    "title_domain_token_stripped": "junk_domain_removed",
    "title_label_suffix_stripped": "label_suffix_removed",
    "title_feat_normalized":       "feat_normalized",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SanitizeChange:
    field: str
    old_value: str
    new_value: str
    reason: str


@dataclass
class TrackSanitizeResult:
    filepath: str
    changes: List[SanitizeChange] = dc_field(default_factory=list)
    skipped: List[str] = dc_field(default_factory=list)  # reason strings for skipped fields
    is_corrupt: bool = False


# ---------------------------------------------------------------------------
# File collection (mirrors ai/normalizer._collect_files)
# ---------------------------------------------------------------------------

def _collect_files(input_path: Path, limit: Optional[int]) -> List[Path]:
    if input_path.is_file():
        return [input_path]

    files: List[Path] = []
    seen: set = set()
    for ext in config.AUDIO_EXTENSIONS:
        for path in sorted(input_path.rglob(f"*{ext}")):
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
        for path in sorted(input_path.rglob(f"*{ext.upper()}")):
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
    files.sort()
    if limit is not None and limit > 0:
        files = files[:limit]
    return files


# ---------------------------------------------------------------------------
# Tag I/O
# ---------------------------------------------------------------------------

def _read_tags(path: Path) -> Optional[Tuple[Dict[str, str], set]]:
    """
    Read title, artist, album, organization, isrc.
    Returns (flat_tags, multi_value_fields) or None on failure.
    multi_value_fields is the set of field names whose raw tag list had more than
    one entry — these must not be overwritten with a single collapsed value.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return None
        multi_value_fields: set = set()
        _EASY_FIELDS = ("title", "artist", "album", "organization")
        tags: Dict[str, str] = {}
        for k in _EASY_FIELDS:
            raw = audio.get(k) or []
            if len(raw) > 1:
                multi_value_fields.add(k)
            tags[k] = raw[0] if raw else ""
    except Exception as exc:
        log.debug("Could not read tags from %s: %s", path.name, exc)
        return None
    tags["isrc"] = _read_isrc(path)
    return tags, multi_value_fields


def _read_isrc(path: Path) -> str:
    """Read ISRC using format-specific raw access (easy mode omits TSRC for MP3)."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3
            id3 = ID3(str(path))
            frame = id3.get("TSRC")
            return str(frame).strip() if frame else ""
        elif suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            vals = audio.tags.get("isrc", []) if audio.tags else []
            return vals[0].strip() if vals else ""
        elif suffix == ".m4a":
            from mutagen import File as MFile
            audio = MFile(str(path))
            if audio is None:
                return ""
            key = "----:com.apple.iTunes:ISRC"
            val = audio.get(key)
            if val:
                v = val[0]
                raw = v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
                return raw.strip()
        elif suffix in (".aif", ".aiff"):
            # AIFF stores ID3 inside the container — use the AIFF class, not bare ID3()
            from mutagen.aiff import AIFF
            audio = AIFF(str(path))
            if audio.tags is None:
                return ""
            frame = audio.tags.get("TSRC")
            if frame:
                return str(frame).strip()
    except Exception:
        pass
    return ""


def _apply_sanitized(path: Path, changes: List[SanitizeChange]) -> Tuple[bool, set]:
    """
    Write sanitized changes back to the file.
    Returns (easy_ok, failed_fields) where failed_fields is the set of field
    names whose writes did not succeed (empty = all applied).
    """
    easy_changes = {c.field: c.new_value for c in changes if c.field != "isrc"}
    isrc_change = next((c for c in changes if c.field == "isrc"), None)
    failed_fields: set = set()

    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            log.warning("Could not open %s for writing", path.name)
            return False, {c.field for c in changes}
        for key, val in easy_changes.items():
            if val:
                audio[key] = [val]
            else:
                try:
                    del audio[key]
                except KeyError:
                    pass
        audio.save()
    except Exception as exc:
        log.error("Easy-tag write failed for %s: %s", path.name, exc)
        return False, {c.field for c in changes}

    if isrc_change is not None and not isrc_change.new_value:
        if not _clear_isrc(path):
            failed_fields.add("isrc")

    return True, failed_fields


def _clear_isrc(path: Path) -> bool:
    """Delete ISRC tag from file (format-specific). Returns True on success."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3
            id3 = ID3(str(path))
            if "TSRC" in id3:
                del id3["TSRC"]
                id3.save(str(path))
            return True
        elif suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            if audio.tags and "isrc" in audio.tags:
                del audio.tags["isrc"]
                audio.save()
            return True
        elif suffix == ".m4a":
            from mutagen import File as MFile
            audio = MFile(str(path))
            key = "----:com.apple.iTunes:ISRC"
            if audio and key in audio:
                del audio[key]
                audio.save()
            return True
        elif suffix in (".aif", ".aiff"):
            # AIFF stores ID3 inside the container — use the AIFF class, not bare ID3()
            from mutagen.aiff import AIFF
            audio = AIFF(str(path))
            if audio.tags and "TSRC" in audio.tags:
                del audio.tags["TSRC"]
                audio.save()
            return True
    except Exception as exc:
        log.debug("Could not clear ISRC from %s: %s", path.name, exc)
    return False


# ---------------------------------------------------------------------------
# Per-field sanitizers  →  (new_value, reason) or (original, "") if no change
# ---------------------------------------------------------------------------

def _sanitize_album(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""

    if _RE_EMAIL.search(value):
        return "", "album_junk_email"

    # Check for URL content first (URLs contain "/" so must precede path check)
    if re.search(r'https?://', value, re.IGNORECASE) or re.search(r'www\.', value, re.IGNORECASE):
        cleaned = sanitize_text(value)
        return ("", "album_junk_url") if not cleaned else (cleaned, "album_url_stripped")

    # Path fragments: starts with / or \ (Unix/Windows absolute path),
    # or contains a Windows drive prefix (C:\ or C:/).
    # A single / inside a name (e.g. "AC/DC", "His/Hers") does NOT trigger.
    if (
        value.startswith("/") or
        value.startswith("\\") or
        re.search(r'[A-Za-z]:[/\\]', value)
    ):
        return "", "album_junk_path_fragment"

    # Five or more consecutive dots signal machine-generated junk.
    # Three dots are a standard ellipsis (e.g. "Future...Past") and must not trigger.
    if re.search(r'\.{5,}', value):
        return "", "album_junk_excessive_dots"

    # Delegate to the shared junk-removal logic (handles domains, promo phrases)
    cleaned = sanitize_text(value)
    if cleaned == value:
        return value, ""

    if not cleaned:
        if re.search(r'\b[a-z0-9\-]+\.[a-z]{2,4}\b', value, re.IGNORECASE):
            return "", "album_junk_domain"
        return "", "album_junk_promo"

    return cleaned, "album_promo_stripped"


def _sanitize_isrc(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""
    normalized = value.strip().upper().replace("-", "").replace(" ", "")
    if _ISRC_RE.match(normalized):
        return value, ""  # valid — no change
    return "", "invalid_isrc"


def _strip_label_suffix(value: str) -> Tuple[str, str]:
    """
    Strip a label-like trailing parenthetical from a title.
    Returns (new_value, reason) or (original, "") if nothing to strip.

    Protected: any paren that contains a mix/version keyword is never stripped.
    Triggered by: label keyword match OR known-label-name match.
    """
    if not value:
        return value, ""
    m = re.search(r'\s*\(([^)]*)\)\s*$', value)
    if not m:
        return value, ""
    content = m.group(1)
    content_lower = re.sub(r'[^a-z\s]', ' ', content.lower())
    words = content_lower.split()

    # Never strip mix/version parens
    for w in words:
        if w in _PROTECTED_PAREN_WORDS:
            return value, ""

    # Strip on label-keyword match
    for w in words:
        if w in _LABEL_PAREN_KEYWORDS:
            stripped = value[:m.start()].rstrip()
            return (stripped, "title_label_suffix_stripped") if stripped else (value, "")

    # Strip on known-label-name match
    if content.strip().lower() in _KNOWN_STRIP_LABELS:
        stripped = value[:m.start()].rstrip()
        return (stripped, "title_label_suffix_stripped") if stripped else (value, "")

    return value, ""


def _sanitize_title(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""

    result = value
    reasons: List[str] = []

    # 1. Separator-based numeric prefix (existing rule: "01 | Title", "002. Title")
    stripped = _RE_TITLE_NUM_PREFIX.sub("", result)
    if stripped != result:
        result = stripped
        reasons.append("title_prefix_removed")

    # 2. Bare numeric prefix — no separator ("2 Sada" → "Sada", "3 Afro" → "Afro")
    #    Preserve (do NOT strip) when any guard fires:
    #      (a) leading number >= 10  ("15 Minutes", "24 Hours")
    #      (b) first remaining word is in _BARE_NUM_PROTECTED_WORDS  ("4 You")
    #      (c) first remaining word itself starts with a digit — numeric/ordinal token
    #          ("3 13th Friday" → "13th", "4 100 Sure" → "100")
    #    Reason code logged on preservation: title_leading_number_preserved_ambiguous
    _m_bare = re.match(r'^(\d{1,3})\s+(?=[A-Za-z(])', result)
    if _m_bare:
        _leading_num = int(_m_bare.group(1))
        bare = result[_m_bare.end():].strip()
        _bare_words = bare.split()
        _bare_first = _bare_words[0].lower() if _bare_words else ""
        _first_is_numeric_or_ordinal = bool(_bare_words and re.match(r'^\d', _bare_words[0]))
        _preserve = (
            _leading_num >= 10
            or _bare_first in _BARE_NUM_PROTECTED_WORDS
            or _first_is_numeric_or_ordinal
        )
        if len(bare) >= 2 and not _preserve:
            result = bare
            reasons.append("title_bare_number_stripped")
        elif len(bare) >= 2 and _preserve:
            log.debug("title_leading_number_preserved_ambiguous: %r", result)

    # 3. Domain / piracy paren tokens — strip globally
    cleaned = _RE_TITLE_DOMAIN_PAREN.sub("", result).strip()
    if cleaned != result:
        result = cleaned
        reasons.append("title_domain_token_stripped")

    # 4. Trailing BPM (80–160) at very end — strip before label so "(Label)122" works
    m = _RE_TITLE_TRAILING_BPM.search(result)
    if m:
        try:
            bpm_int = int(m.group(1))
            if 80 <= bpm_int <= 160:
                prefix = result[:m.start()].rstrip()
                if prefix:
                    result = prefix
                    reasons.append("title_trailing_bpm_stripped")
        except ValueError:
            pass

    # 5. Label-like trailing parenthetical suffix ("(Xumba Recordings)" etc.)
    result, label_reason = _strip_label_suffix(result)
    if label_reason:
        reasons.append(label_reason)

    # 6. Normalize (Feat. → (feat. casing in titles
    normed = _RE_TITLE_FEAT_UPPER.sub("(feat.", result)
    if normed != result:
        result = normed
        reasons.append("title_feat_normalized")

    # 7–11. Existing structural cleanup
    fixed = _RE_TITLE_MULTI_SEP.sub(" - ", result)
    if fixed != result:
        result = fixed
        reasons.append("title_separator_fixed")

    fixed = _RE_EMPTY_BRACKET.sub("", result)
    if fixed != result:
        result = fixed
        reasons.append("title_spacing_fixed")

    fixed = _RE_UNCLOSED_PAREN.sub("", result)
    if fixed != result:
        result = fixed
        reasons.append("title_parentheses_fixed")

    fixed = _RE_MISSING_SPACE_BEFORE_PAREN.sub(r'\1 (', result)
    if fixed != result:
        result = fixed
        if "title_spacing_fixed" not in reasons:
            reasons.append("title_spacing_fixed")

    fixed = _RE_MULTI_SPACE.sub(" ", result).strip()
    if fixed != result:
        result = fixed
        if "title_spacing_fixed" not in reasons:
            reasons.append("title_spacing_fixed")

    if not reasons:
        return value, ""
    return result, reasons[0]


def _sanitize_artist(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""

    result = value
    reasons: List[str] = []

    cleaned = _RE_ARTIST_URL.sub("", result).strip()
    if cleaned != result:
        result = cleaned
        reasons.append("artist_url_removed")

    normed = _RE_FT_DOT.sub("feat.", result)
    normed = _RE_FEATURING.sub("feat.", normed)
    if normed != result:
        result = normed
        reasons.append("artist_feat_normalized")

    fixed = _RE_ARTIST_MULTI_SEP.sub(", ", result)
    if fixed != result:
        result = fixed
        reasons.append("artist_spacing_fixed")

    fixed = _RE_MULTI_SPACE.sub(" ", result).strip()
    if fixed != result:
        result = fixed
        if "artist_spacing_fixed" not in reasons:
            reasons.append("artist_spacing_fixed")

    if not reasons:
        return value, ""
    return result, reasons[0]


def _sanitize_label(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""

    if _RE_EMAIL.search(value):
        return "", "label_junk_email"

    if value.strip().lower() in _LABEL_JUNK_LOWER:
        return "", "label_junk_placeholder"

    fixed = _RE_MULTI_SPACE.sub(" ", value).strip()
    if fixed != value:
        return fixed, "label_spacing_fixed"

    return value, ""


# ---------------------------------------------------------------------------
# Per-track analysis
# ---------------------------------------------------------------------------

_FIELD_SANITIZERS = [
    ("album",        _sanitize_album),
    ("isrc",         _sanitize_isrc),
    ("title",        _sanitize_title),
    ("artist",       _sanitize_artist),
    ("organization", _sanitize_label),
]


def _sanitize_track(path: Path) -> TrackSanitizeResult:
    result = TrackSanitizeResult(filepath=str(path))
    read = _read_tags(path)
    if read is None:
        result.is_corrupt = True
        return result

    tags, multi_value_fields = read

    for field, sanitizer in _FIELD_SANITIZERS:
        if field in multi_value_fields:
            result.skipped.append(f"{field}: skipped_multi_value_artist"
                                  if field == "artist"
                                  else f"{field}: skipped_multi_value")
            continue

        old_val = tags.get(field, "")
        if not old_val:
            continue
        new_val, reason = sanitizer(old_val)
        if new_val == old_val or not reason:
            continue
        result.changes.append(SanitizeChange(
            field=field,
            old_value=old_val,
            new_value=new_val,
            reason=reason,
        ))

    return result


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_track_preview(result: TrackSanitizeResult, verbose: bool = False) -> None:
    if verbose and result.changes:
        print(f"  CHANGE: {Path(result.filepath).name}")
        for c in result.changes:
            new_display = repr(c.new_value) if c.new_value else "(cleared)"
            print(f"    {c.field}:")
            print(f"      BEFORE:  {c.old_value!r}")
            print(f"      AFTER:   {new_display}")
            print(f"      REASONS: {c.reason}")
        for s in result.skipped:
            print(f"    [SKIP] {s}")
    else:
        print(f"  {Path(result.filepath).name}")
        for c in result.changes:
            new_display = repr(c.new_value) if c.new_value else "(cleared)"
            print(f"    [{c.reason}] {c.field}: {c.old_value!r} → {new_display}")
        for s in result.skipped:
            print(f"    [SKIP] {s}")


def _write_json_log(
    output_path: str,
    results: List[TrackSanitizeResult],
    summary: dict,
) -> None:
    tracks = [
        {
            "file": r.filepath,
            "corrupt": r.is_corrupt,
            "changes": [
                {"field": c.field, "old": c.old_value, "new": c.new_value, "reason": c.reason}
                for c in r.changes
            ],
            "skipped": r.skipped,
        }
        for r in results
        if r.changes or r.skipped or r.is_corrupt
    ]
    data = {"results": summary, "tracks": tracks}
    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.error("Could not write JSON log to %s: %s", output_path, exc)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_metadata_sanitize(args) -> int:
    """Called by pipeline.py dispatch."""
    input_path = Path(args.input).expanduser().resolve()
    apply_mode = getattr(args, "apply", False)
    limit = getattr(args, "limit", None)
    output_json = getattr(args, "output_json", None)
    verbose = getattr(args, "verbose", False)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    if not input_path.exists():
        print(f"ERROR: Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    files = _collect_files(input_path, limit)
    if not files:
        print(f"No audio files found under {input_path}")
        return 0

    from utils.prompt_logger import get_run_logger as _grl
    _rl = _grl()
    _is_presanitize = _rl is not None and _rl._command != "metadata-sanitize"
    if _rl:
        if not _is_presanitize:
            _rl.inc("files_scanned", len(files))
            _rl.set_counter("input_path", str(input_path))
            _rl.set_counter("limit", limit)
            _rl.set_counter("applied", apply_mode)

    mode_label = "APPLY" if apply_mode else "PREVIEW"
    print(f"metadata-sanitize [{mode_label}] — {len(files)} file(s)")
    print()

    _stage = "metadata-sanitize"
    _force = getattr(args, "force", False)
    if getattr(args, "reset_stage", False):
        _proc.clear_stage(_stage)

    all_results: List[TrackSanitizeResult] = []
    changed_count    = 0
    applied_count    = 0
    error_count      = 0
    n_no_change      = 0
    n_skip_unchanged = 0
    _debug_counters: Dict[str, int] = {
        "title_leading_number_fixes": 0,
        "bpm_suffix_removed":         0,
        "junk_domain_removed":        0,
        "label_suffix_removed":       0,
        "feat_normalized":            0,
    }

    for path in files:
        # --- incremental-run skip check ---
        # reason_prefix ensures stale "no_change" records from old rule versions
        # are NOT skipped — new rules must get a chance to fire on those files.
        if not _force and _proc.should_skip(_stage, path, reason_prefix=_RULES_REASON):
            n_skip_unchanged += 1
            print(f"  SKIP_UNCHANGED:")
            print(f"    {path}")
            if _rl and not _is_presanitize:
                _rl.inc("skipped_unchanged")
            continue

        if _rl and not _is_presanitize:
            _rl.inc("files_processed")
        result = _sanitize_track(path)
        all_results.append(result)

        _pstate  = None   # set below; None = preview-pending (don't record)
        _preason = ""

        if result.is_corrupt:
            error_count += 1
            print(f"  [SKIP] {path.name} — corrupt_file")
            log_action(f"SANITIZE-SKIP: {path.name} | corrupt_file")
            if _rl:
                if _is_presanitize:
                    _rl.inc("sanitize_clean")
                else:
                    _rl.inc("errors")
                    _rl.record_outcome("errors", str(path), "corrupt_file", "")
            _proc.record(_stage, path, "error", "corrupt_file")
            continue

        if not result.changes and not result.skipped:
            n_no_change += 1
            if _rl:
                if _is_presanitize:
                    _rl.inc("sanitize_clean")
                else:
                    _rl.inc("unchanged")
            _proc.record(_stage, path, "no_change", _RULES_REASON)
            continue

        if result.changes:
            changed_count += 1
            for _c in result.changes:
                _ctr = _COUNTER_FOR_REASON.get(_c.reason)
                if _ctr:
                    _debug_counters[_ctr] += 1
            if _rl:
                if _is_presanitize:
                    _rl.inc("sanitize_changed")
                else:
                    _rl.inc("changed")
                    _rl.record_outcome(
                        "modified", str(path),
                        result.changes[0].reason,
                        "; ".join(f"{c.field}:{c.reason}" for c in result.changes),
                    )
        elif result.skipped and _rl and not _is_presanitize:
            _rl.inc("skipped")
            _rl.record_outcome("skipped", str(path), result.skipped[0], "")

        # Skipped-only path (no changes, has skips): record deterministically
        if not result.changes and result.skipped:
            _pstate  = "skipped"
            _preason = result.skipped[0] if result.skipped else ""

        _print_track_preview(result, verbose=verbose)

        if apply_mode:
            if result.changes:
                ok, failed_fields = _apply_sanitized(path, result.changes)
                if ok:
                    applied_count += 1
                    _pstate = "success"
                    for c in result.changes:
                        if c.field in failed_fields:
                            log.warning(
                                "ISRC clear failed for %s — not logged as applied", path.name
                            )
                            continue
                        log_action(
                            f"SANITIZE: {path.name} | {c.field} | "
                            f"{c.old_value!r} → {c.new_value!r} | {c.reason}"
                        )
                else:
                    print(f"    [ERROR] failed to write {path.name}", file=sys.stderr)
                    if _rl and not _is_presanitize:
                        _rl.record_outcome("errors", str(path), "write_failed", "")
                    _pstate  = "error"
                    _preason = "write_failed"
            for s in result.skipped:
                log_action(f"SANITIZE-SKIP: {path.name} | {s}")

        # _pstate is None when changes exist but apply_mode is False (preview pending)
        if _pstate is not None:
            _proc.record(_stage, path, _pstate, _preason)

    n_processed = len(files) - n_skip_unchanged
    print()
    print(f"Files scanned           : {len(files)}")
    print(f"Files skipped unchanged : {n_skip_unchanged}")
    print(f"Files processed         : {n_processed}")
    print(f"Files changed           : {changed_count}")
    print(f"Files unchanged         : {n_no_change}")
    print(f"Files written           : {applied_count}")
    print(f"Errors                  : {error_count}")
    if any(_debug_counters.values()):
        print()
        print("Changes by rule:")
        for _ctr_name, _ctr_val in _debug_counters.items():
            if _ctr_val:
                print(f"  {_ctr_name:<30}: {_ctr_val}")
    if not apply_mode and changed_count:
        print()
        print("Dry-run mode — no files modified. Pass --apply to write changes.")

    if output_json:
        _summary = {
            "processed":         n_processed,
            "skipped_unchanged": n_skip_unchanged,
            "changed":           changed_count,
            "unchanged":         n_no_change,
            "errors":            error_count,
        }
        _write_json_log(output_json, all_results, _summary)
        print(f"JSON log written to: {output_json}")

    return 0


# ---------------------------------------------------------------------------
# Rollback helpers
# ---------------------------------------------------------------------------

# Same words used by the forward guard — first-word match triggers a suspicious revert.
_SUSPICIOUS_REVERT_WORDS: frozenset = _BARE_NUM_PROTECTED_WORDS


def _load_rollback_records(path: Path, rule_filter: str) -> List[dict]:
    """
    Parse a metadata-sanitize log (JSON from --output-json, or JSONL) and return
    a list of {file, before, after} dicts whose title change matches rule_filter.
    """
    records: List[dict] = []
    content = path.read_text(encoding="utf-8")

    # --- JSON format from _write_json_log ---
    try:
        data = json.loads(content)
        if isinstance(data, dict) and "tracks" in data:
            for track in data["tracks"]:
                for change in track.get("changes", []):
                    if change.get("field") == "title" and change.get("reason") == rule_filter:
                        records.append({
                            "file":   track["file"],
                            "before": change["old"],
                            "after":  change["new"],
                        })
            return records
    except json.JSONDecodeError:
        pass

    # --- JSONL: one JSON object per line ---
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if obj.get("field") == "title" and obj.get("reason") == rule_filter:
                records.append({
                    "file":   obj["file"],
                    "before": obj["old"],
                    "after":  obj["new"],
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return records


def _is_suspicious_revert(before: str, after: str) -> Tuple[bool, str]:
    """Return (is_suspicious, reason) for a candidate rollback record."""
    after_words = after.split()
    first_word = after_words[0].lower() if after_words else ""
    if first_word in _SUSPICIOUS_REVERT_WORDS:
        return True, f"after_starts_with_known_title_word ({after_words[0]!r})"
    return False, "looks_like_track_index_junk"


def _read_current_title(path: Path) -> str:
    read = _read_tags(path)
    if read is None:
        return "(unreadable)"
    tags, _ = read
    return tags.get("title", "(no title tag)")


def _write_title_tag(path: Path, title: str) -> bool:
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return False
        audio["title"] = [title]
        audio.save()
        return True
    except Exception as exc:
        log.error("Failed to write title to %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Rollback CLI entry point
# ---------------------------------------------------------------------------

def run_metadata_sanitize_rollback(args) -> int:
    """Called by pipeline.py dispatch for metadata-sanitize-rollback."""
    jsonl_path = Path(args.jsonl).expanduser().resolve()
    rule_filter = getattr(args, "rule", "title_bare_number_stripped")
    preview = not getattr(args, "apply", False)
    only_suspicious = getattr(args, "only_suspicious", False)

    if not jsonl_path.exists():
        print(f"ERROR: Log file does not exist: {jsonl_path}", file=sys.stderr)
        return 1

    records = _load_rollback_records(jsonl_path, rule_filter)
    if not records:
        print(f"No title changes found in log matching rule: {rule_filter}")
        return 0

    mode_label = "PREVIEW" if preview else "APPLY"
    filter_label = " [--only-suspicious]" if only_suspicious else ""
    print(f"metadata-sanitize-rollback [{mode_label}]{filter_label}")
    print(f"Rule   : {rule_filter}")
    print(f"Log    : {jsonl_path}")
    print(f"Found  : {len(records)} candidate(s)")
    print()

    n_would_revert = 0
    n_skip = 0
    n_applied = 0
    n_error = 0

    for rec in records:
        filepath = rec["file"]
        before = rec["before"]
        after = rec["after"]

        suspicious, susp_reason = _is_suspicious_revert(before, after)

        if only_suspicious and not suspicious:
            action = "SKIP"
            display_reason = "not_suspicious"
        else:
            action = "WOULD_REVERT" if preview else "REVERT"
            display_reason = susp_reason if suspicious else "all_reverts_requested"

        current_title = _read_current_title(Path(filepath))
        stale_note = ""
        if current_title not in ("(unreadable)", "(no title tag)") and current_title != after:
            stale_note = "  [NOTE: current title differs from log — may have changed since run]"

        print(f"  {action}")
        print(f"    file    : {filepath}")
        print(f"    current : {current_title!r}{stale_note}")
        print(f"    before  : {before!r}")
        print(f"    after   : {after!r}")
        print(f"    reason  : {display_reason}")
        print()

        if action == "SKIP":
            n_skip += 1
            continue

        n_would_revert += 1

        if not preview:
            ok = _write_title_tag(Path(filepath), before)
            if ok:
                n_applied += 1
                log_action(
                    f"ROLLBACK: {Path(filepath).name} | title | "
                    f"{after!r} → {before!r} | {rule_filter}"
                )
            else:
                n_error += 1
                print(f"    [ERROR] failed to write {filepath}", file=sys.stderr)

    print(f"Candidates   : {len(records)}")
    print(f"Would revert : {n_would_revert}")
    print(f"Skipped      : {n_skip}")
    if not preview:
        print(f"Applied      : {n_applied}")
        print(f"Errors       : {n_error}")
    if preview and n_would_revert:
        print()
        print("Dry-run mode — pass --apply to write reverted titles.")

    return 0


# ---------------------------------------------------------------------------
# Title-number recovery helpers
# ---------------------------------------------------------------------------

_RE_RECOVER_NUM_PREFIX = re.compile(r'^(\d{1,3})\s+(.+)$', re.UNICODE)


def _parse_filename_title(path: Path) -> Optional[str]:
    """
    Extract the title portion from an 'Artist - Title.ext' filename.
    Returns None when no ' - ' separator is present.
    """
    idx = path.stem.find(" - ")
    if idx == -1:
        return None
    candidate = path.stem[idx + 3:].strip()
    return candidate or None


def _is_suspicious_recovery(leading_num: int, rest: str) -> Tuple[bool, str]:
    """
    Return (is_suspicious, reason) where True means the number was likely part
    of the real title and the stripped tag should be restored.

    Suspicious when: rest starts with a known protected word, or number >= 10.
    NOT suspicious (track-index junk): single-digit number + non-protected first word.
    """
    first_word = rest.split()[0].lower() if rest.split() else ""
    if first_word in _BARE_NUM_PROTECTED_WORDS:
        return True, f"rest_is_known_title_word ({rest.split()[0]!r})"
    if leading_num >= 10:
        return True, f"two_or_more_digit_number ({leading_num})"
    return False, "single_digit_track_index"


# ---------------------------------------------------------------------------
# Title-number-recover CLI entry point
# ---------------------------------------------------------------------------

def run_title_number_recover(args) -> int:
    """Called by pipeline.py dispatch for title-number-recover."""
    input_path = Path(args.input).expanduser().resolve()
    apply_mode = getattr(args, "apply", False)
    verbose = getattr(args, "verbose", False)
    limit = getattr(args, "limit", None)

    if not input_path.exists():
        print(f"ERROR: Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    files = _collect_files(input_path, limit)
    if not files:
        print(f"No audio files found under {input_path}")
        return 0

    mode_label = "APPLY" if apply_mode else "PREVIEW"
    print(f"title-number-recover [{mode_label}] — {len(files)} file(s)")
    print()

    n_candidates = 0
    n_skipped_index = 0
    n_recovered = 0
    n_error = 0

    for path in files:
        filename_title = _parse_filename_title(path)
        if not filename_title:
            if verbose:
                print(f"  SKIP_NO_SEPARATOR: {path.name}")
            continue

        m = _RE_RECOVER_NUM_PREFIX.match(filename_title)
        if not m:
            continue

        leading_num = int(m.group(1))
        rest = m.group(2).strip()

        current_title = _read_current_title(path)
        if current_title in ("(unreadable)", "(no title tag)"):
            if verbose:
                print(f"  SKIP_UNREADABLE: {path.name}")
            n_error += 1
            continue

        if current_title != rest:
            if verbose:
                print(f"  NO_MATCH: {path.name}")
                print(f"    filename_title : {filename_title!r}")
                print(f"    current_title  : {current_title!r}")
                print(f"    expected_rest  : {rest!r}")
            continue

        suspicious, reason = _is_suspicious_recovery(leading_num, rest)

        if not suspicious:
            n_skipped_index += 1
            if verbose:
                print(f"  SKIP_OBVIOUS_INDEX:")
                print(f"    FILE   : {path}")
                print(f"    REASON : {reason}")
                print()
            continue

        n_candidates += 1
        action = "RECOVER" if apply_mode else "WOULD_RECOVER"
        print(f"  {action}:")
        print(f"    FILE          : {path}")
        print(f"    CURRENT TITLE : {current_title!r}")
        print(f"    RESTORED TITLE: {filename_title!r}")
        print(f"    REASON        : {reason}")
        print()

        if apply_mode:
            ok = _write_title_tag(path, filename_title)
            if ok:
                n_recovered += 1
                log_action(
                    f"RECOVER: {path.name} | title | "
                    f"{current_title!r} → {filename_title!r} | title_number_recover"
                )
            else:
                n_error += 1
                print(f"    [ERROR] failed to write {path}", file=sys.stderr)

    print(f"Files scanned          : {len(files)}")
    print(f"Recovery candidates    : {n_candidates}")
    if apply_mode:
        print(f"Recovered              : {n_recovered}")
    print(f"Skipped (obvious index): {n_skipped_index}")
    print(f"Errors                 : {n_error}")

    if not apply_mode and n_candidates:
        print()
        print("Dry-run mode — pass --apply to write recovered titles.")

    return 0
