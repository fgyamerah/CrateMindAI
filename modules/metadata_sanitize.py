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


def _sanitize_title(value: str) -> Tuple[str, str]:
    if not value:
        return value, ""

    result = value
    reasons: List[str] = []

    stripped = _RE_TITLE_NUM_PREFIX.sub("", result)
    if stripped != result:
        result = stripped
        reasons.append("title_prefix_removed")

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

def _print_track_preview(result: TrackSanitizeResult) -> None:
    print(f"  {Path(result.filepath).name}")
    for c in result.changes:
        new_display = repr(c.new_value) if c.new_value else "(cleared)"
        print(f"    [{c.reason}] {c.field}: {c.old_value!r} → {new_display}")
    for s in result.skipped:
        print(f"    [SKIP] {s}")


def _write_json_log(output_path: str, results: List[TrackSanitizeResult]) -> None:
    data = [
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

    mode_label = "APPLY" if apply_mode else "PREVIEW"
    print(f"metadata-sanitize [{mode_label}] — {len(files)} file(s)")
    print()

    all_results: List[TrackSanitizeResult] = []
    changed_count = 0
    applied_count = 0
    error_count = 0

    for path in files:
        result = _sanitize_track(path)
        all_results.append(result)

        if result.is_corrupt:
            error_count += 1
            print(f"  [SKIP] {path.name} — corrupt_file")
            log_action(f"SANITIZE-SKIP: {path.name} | corrupt_file")
            continue

        if not result.changes and not result.skipped:
            continue

        if result.changes:
            changed_count += 1
        _print_track_preview(result)

        if apply_mode:
            if result.changes:
                ok, failed_fields = _apply_sanitized(path, result.changes)
                if ok:
                    applied_count += 1
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
            for s in result.skipped:
                log_action(f"SANITIZE-SKIP: {path.name} | {s}")

    print()
    print(f"Files scanned               : {len(files)}")
    print(f"Files with proposed changes : {changed_count}")
    if error_count:
        print(f"Files unreadable            : {error_count}")
    if apply_mode:
        print(f"Files written               : {applied_count}")
    else:
        print()
        print("Dry-run mode — no files modified. Pass --apply to write changes.")

    if output_json:
        _write_json_log(output_json, all_results)
        print(f"JSON log written to: {output_json}")

    return 0
