"""
Retroactive artist-folder name cleanup.

Scans existing artist folders under the sorted library and detects folders
whose names are junk based on strict Camelot-key patterns or bracket wrapping.
Numbers alone are NOT a reason to reject a folder — "2point1" and
"3 Beatz Muzik(Dj Loy)" are valid artist names.

Detection rules (in priority order):
  1. pure_camelot    e.g. "10B", "1A", "12A"
       → pure key with no folder-level artist name
       → second pass recovers artist from file tags / filename
       → unrecoverable files go to "Unknown Artist"
  2. camelot_prefix  e.g. "1A - Afrikan Roots", "9A - DJ Shimza & XtetiQsoul"
       → strip Camelot prefix + leading symbol garbage (#, ., _ …)
       → validate candidate; if source/promo junk → review
       → if valid → rename / merge
  3. bracket_junk    e.g. "[HouseGrooveSA]", "[Zista So 9Dades]"
       → strip outer brackets, validate inner text
       → if plausible artist candidate  → suspicious (manual review, shown with candidate)
       → if inner text clearly invalid  → review (no candidate)

Outcomes per bad folder:
  "rename"     — camelot_prefix, cleaned name valid, target folder does not exist
  "merge"      — camelot_prefix, cleaned name valid, target folder already exists
  "recover"    — pure_camelot, per-file artist recovery attempted (tag/filename/unknown)
  "suspicious" — bracket_junk with a plausible inner name; never auto-applied
  "review"     — no valid artist name extractable; written to report only

Source-junk detection (is_source_junk):
  Applied after Camelot prefix stripping to prevent promo/source strings from
  becoming artist folder names.  Catches:
    - Known DJ-pool / promo watermarks  (traxcrate, djcity, zipdj, …)
    - Domain-like slugs  (Tukillas.Squeeze, HouseGrooveSA.com, …)
    - Strings that contain a dot between two long alpha-only words
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.junk_patterns import load_junk_patterns
from modules.textlog import log_action

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Unknown-Artist fallback constants
# ---------------------------------------------------------------------------
_UNKNOWN_ARTIST_NAME   = "Unknown Artist"
_UNKNOWN_LETTER        = "Unknown"


# ---------------------------------------------------------------------------
# Detection patterns
# ---------------------------------------------------------------------------

# Pure Camelot/Open-Key standalone — no artist name at all.
# Matches: "1A", "8B", "10A", "12B" — nothing before or after.
_RE_PURE_CAMELOT = re.compile(r'^(1[0-2]|[1-9])[AB]$', re.IGNORECASE)

# Camelot/Open-Key prefix followed by a dash separator and an artist name.
# Requires at least one space after the dash so that digit-starting artist
# names like "2point1" are never matched.
_RE_CAMELOT_PREFIX = re.compile(
    r'^(1[0-2]|[1-9])[AB]\s*-\s+(.+)$',
    re.IGNORECASE,
)

# Bracket-wrapped — the entire folder name is enclosed in [ ] or ( ).
_RE_FULL_BRACKET = re.compile(r'^[\[\(]\s*(.*?)\s*[\]\)]$')

# URL / domain check used inside _is_valid_artist_name to catch things like
# "djcity.com" or "www.fordjonly.com" stripped from bracket content.
_RE_URL_IN_NAME = re.compile(
    r'https?://|www\.'
    r'|\b[a-z0-9][\w\-]*\.(com|net|org|fm|dj|io|me|biz|us|tv|cc)\b',
    re.IGNORECASE,
)

# Light bracket inner-text sanitization — strips leading/trailing symbols.
_RE_BRACKET_EDGE_JUNK = re.compile(r'^[\s\-_|.,;:!?]+|[\s\-_|.,;:!?]+$')

# Leading symbolic garbage to remove AFTER a Camelot prefix is stripped.
# e.g. "5A - # Tukillas.Squeeze" → strip "5A - " → "# Tukillas.Squeeze"
#                                  → strip leading "#" → "Tukillas.Squeeze"
_RE_LEADING_SYMBOL_JUNK = re.compile(r'^[\s#.\-_|,;:!?()\[\]{}]+')


# ---------------------------------------------------------------------------
# Source / promo-junk detection
# ---------------------------------------------------------------------------

# Source/promo-junk sets — loaded from config/junk_patterns.json.
# Using module-level names so is_source_junk() references them without a closure.
_junk_patterns        = load_junk_patterns()
_SOURCE_JUNK_EXACT:      frozenset     = _junk_patterns.exact_source_junk
_SOURCE_JUNK_SUBSTRINGS: Tuple[str, ...] = _junk_patterns.source_junk_substrings

# Domain-like slug: 5+ alpha chars, a dot, 4+ alpha chars, no spaces.
# Catches "Tukillas.Squeeze", "HouseGrooveSA.net" etc.
# Requires at least 5 chars on the left to avoid catching "Dr.Dre" (2 chars).
_RE_DOMAIN_SLUG = re.compile(r'[a-zA-Z]{5,}\.[a-zA-Z]{4,}')


def is_source_junk(value: str) -> bool:
    """
    Return True if *value* looks like a promo/source watermark rather than
    an artist name.

    Checks (in order):
      1. Exact match against known source/promo names
      2. Substring match against known source/promo terms
      3. Domain-like slug pattern (5+ alpha letters . 4+ alpha letters)
      4. URL / domain via _RE_URL_IN_NAME

    >>> is_source_junk("TraxCrate")
    True
    >>> is_source_junk("musicafresca")
    True
    >>> is_source_junk("Tukillas.Squeeze")
    True
    >>> is_source_junk("HouseGrooveSA")
    True
    >>> is_source_junk("Afrikan Roots")
    False
    >>> is_source_junk("DJ Shimza")
    False
    >>> is_source_junk("Dr.Dre")
    False
    """
    if not value:
        return False
    s  = value.strip()
    sl = s.lower()

    if sl in _SOURCE_JUNK_EXACT:
        return True
    for substr in _SOURCE_JUNK_SUBSTRINGS:
        if substr in sl:
            return True
    if _RE_DOMAIN_SLUG.search(s):
        return True
    if _RE_URL_IN_NAME.search(s):
        return True
    return False


# ---------------------------------------------------------------------------
# Name validation
# ---------------------------------------------------------------------------

def _is_valid_artist_name(name: str) -> bool:
    """
    Return True if *name* passes the structural checks for a plausible artist
    folder name.

    NOTE: this only checks structure.  Always also call is_source_junk() when
    deciding whether to use the candidate — they are intentionally separate so
    is_source_junk() can be tested and extended independently.

    Rejected when:
      - Empty or fewer than 2 characters
      - Contains no letter at all
      - Is itself a pure Camelot key ("8B", "10A")
      - Contains a URL or domain
    Numbers alone are NOT grounds for rejection ("2point1", "808 State").
    """
    if not name:
        return False
    s = name.strip()
    if len(s) < 2:
        return False
    if not re.search(r'[a-zA-Z\u00C0-\u024F]', s):
        return False
    if _RE_PURE_CAMELOT.match(s):
        return False
    if _RE_URL_IN_NAME.search(s):
        return False
    return True


def _is_good_artist(name: str) -> bool:
    """
    Return True if name passes both structural validation AND source-junk
    rejection.  This is the single entry point used everywhere a cleaned
    candidate needs to be accepted as a real artist name.
    """
    return _is_valid_artist_name(name) and not is_source_junk(name)


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def _detect_bad_folder(name: str) -> Optional[str]:
    """
    Return a detection-rule label if *name* is a bad artist folder name,
    or None if the name appears valid.
    """
    if not name:
        return "empty"
    s = name.strip()
    if _RE_PURE_CAMELOT.match(s):
        return "pure_camelot"
    if _RE_CAMELOT_PREFIX.match(s):
        return "camelot_prefix"
    if _RE_FULL_BRACKET.match(s):
        return "bracket_junk"
    return None


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _strip_leading_symbols(s: str) -> str:
    """
    Strip leading symbolic garbage (#, ., _, -, whitespace …) from a string.
    Used after Camelot prefix removal to clean residual junk like "# Artist".
    """
    return _RE_LEADING_SYMBOL_JUNK.sub("", s).strip()


def _clean_camelot_prefix(name: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Strip a Camelot key prefix from *name*.

    Returns (candidate, rejection_reason):
      - (cleaned_name, None)   — valid artist candidate
      - (None, reason_string)  — could not produce a valid candidate; reason explains why

    Steps:
      1. Strip the Camelot prefix (e.g. "5A - ")
      2. Strip any leading symbolic garbage (e.g. "# ")
      3. Reject if the result is source/promo junk
      4. Reject if the result fails structural validation
    """
    m = _RE_CAMELOT_PREFIX.match(name.strip())
    if not m:
        return None, "no camelot prefix matched"

    raw       = m.group(2).strip()
    stripped  = _strip_leading_symbols(raw)

    if not stripped:
        return None, "empty after symbol stripping"

    if is_source_junk(stripped):
        return None, f"source/promo junk: {stripped!r}"

    if not _is_valid_artist_name(stripped):
        return None, f"invalid artist name: {stripped!r}"

    return stripped, None


def _clean_bracket_inner(name: str) -> Optional[str]:
    """
    Strip outer brackets and lightly sanitize the inner text.
    Returns the inner text if it is a plausible (non-source-junk) artist name,
    or None if the inner text is clearly junk.
    """
    m = _RE_FULL_BRACKET.match(name.strip())
    if not m:
        return None
    inner = _RE_BRACKET_EDGE_JUNK.sub("", m.group(1).strip()).strip()
    return inner if _is_good_artist(inner) else None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileAssignment:
    """Per-file artist recovery result for a pure_camelot folder."""
    source:        Path
    artist:        str    # recovered artist name ("Unknown Artist" if none found)
    target_folder: Path   # absolute path to the destination artist folder
    method:        str    # "tag_artist" | "filename_parse" | "unknown"


@dataclass
class CleanResult:
    """Describes the outcome for one bad artist folder."""
    original_path:   Path
    original_name:   str
    letter:          str
    files:           List[Path]            # audio files directly inside this folder
    detection_rule:  str                   # which rule flagged this folder
    cleaned_name:    Optional[str]         # proposed new/corrected folder name, or None
    reject_reason:   Optional[str]         # why cleaned_name is None (for logging)
    target_path:     Optional[Path]        # full path to the target folder, or None
    target_exists:   bool                  # True ⟹ merge into existing folder
    status:          str                   # "rename"|"merge"|"recover"|"suspicious"|"review"
    file_assignments: List[FileAssignment] = field(default_factory=list)
                                           # populated for status=="recover" only


# ---------------------------------------------------------------------------
# Library scanner helpers
# ---------------------------------------------------------------------------

def _collect_audio_files(folder: Path) -> List[Path]:
    exts = config.AUDIO_EXTENSIONS
    return [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in exts
    ]


def _first_letter_for(artist: str) -> str:
    """Return the index letter (A–Z or #) for an artist name."""
    a = artist.strip().upper()
    if not a:
        return "#"
    for prefix in ("THE ", "A "):
        if a.startswith(prefix):
            a = a[len(prefix):]
            break
    first = a[0] if a else "#"
    return first if first.isalpha() else "#"


def _unknown_artist_folder(sorted_root: Path) -> Path:
    return sorted_root / _UNKNOWN_LETTER / _UNKNOWN_ARTIST_NAME


# ---------------------------------------------------------------------------
# Per-file artist recovery (for pure_camelot folders)
# ---------------------------------------------------------------------------

def _recover_artist_from_file(src: Path, sorted_root: Path) -> FileAssignment:
    """
    Attempt to recover the artist name for a single audio file.

    Strategy (in order):
      1. Read the embedded 'artist' tag via mutagen easy tags.
      2. Fall back to parsing the filename stem with parse_filename_stem().
      3. Fall back to "Unknown Artist".

    A candidate is only accepted if it passes _is_good_artist() (structural
    checks + source-junk rejection).
    """
    artist = ""
    method = "unknown"

    # Step 1: embedded metadata artist tag
    try:
        from mutagen import File as MFile
        audio = MFile(str(src), easy=True)
        if audio is not None:
            raw = (audio.get("artist") or [""])[0].strip()
            if raw and _is_good_artist(raw):
                artist = raw
                method = "tag_artist"
    except Exception:
        pass

    # Step 2: filename stem parse
    if not artist:
        try:
            from modules.parser import parse_filename_stem
            parsed    = parse_filename_stem(src.stem)
            candidate = (parsed.get("artist") or "").strip()
            if candidate and _is_good_artist(candidate):
                artist = candidate
                method = "filename_parse"
        except Exception:
            pass

    # Step 3: fallback
    if not artist:
        artist = _UNKNOWN_ARTIST_NAME
        method = "unknown"

    letter        = _first_letter_for(artist) if artist != _UNKNOWN_ARTIST_NAME else _UNKNOWN_LETTER
    target_folder = sorted_root / letter / artist

    return FileAssignment(
        source=src,
        artist=artist,
        target_folder=target_folder,
        method=method,
    )


# ---------------------------------------------------------------------------
# Library scanner
# ---------------------------------------------------------------------------

def scan_bad_folders(sorted_root: Path) -> List[CleanResult]:
    """
    Walk *sorted_root* and return one CleanResult for each bad artist folder.
    Good folders are silently skipped.

    Expected structure:  sorted_root/<LETTER>/<Artist>/
    """
    results: List[CleanResult] = []

    if not sorted_root.exists():
        log.warning("Sorted root does not exist: %s", sorted_root)
        return []

    for letter_dir in sorted(sorted_root.iterdir()):
        if not letter_dir.is_dir():
            continue
        letter = letter_dir.name
        if letter.startswith("_"):
            continue  # skip _unsorted, _compilations

        for artist_dir in sorted(letter_dir.iterdir()):
            if not artist_dir.is_dir():
                continue

            name = artist_dir.name
            rule = _detect_bad_folder(name)
            if rule is None:
                continue  # folder name is fine

            files         = _collect_audio_files(artist_dir)
            cleaned       = None
            reject_reason = None
            target_path   = None
            target_exists = False
            status        = "review"
            assignments: List[FileAssignment] = []

            if rule == "camelot_prefix":
                cleaned, reject_reason = _clean_camelot_prefix(name)
                if cleaned:
                    target_letter = _first_letter_for(cleaned)
                    target_path   = sorted_root / target_letter / cleaned
                    target_exists = (
                        target_path.exists()
                        and target_path.resolve() != artist_dir.resolve()
                    )
                    status = "merge" if target_exists else "rename"
                else:
                    status = "review"
                    log.info(
                        "FOLDER-CLEAN: camelot_prefix candidate rejected — %s",
                        reject_reason,
                    )
                    log_action(
                        f"FOLDER-CLEAN: REJECTED candidate from {name!r}: {reject_reason}"
                    )

            elif rule == "bracket_junk":
                cleaned = _clean_bracket_inner(name)
                if cleaned:
                    target_letter = _first_letter_for(cleaned)
                    target_path   = sorted_root / target_letter / cleaned
                    target_exists = (
                        target_path.exists()
                        and target_path.resolve() != artist_dir.resolve()
                    )
                    status = "suspicious"
                else:
                    status = "review"

            elif rule == "pure_camelot":
                # Per-file recovery pass
                if files:
                    for f in files:
                        fa = _recover_artist_from_file(f, sorted_root)
                        assignments.append(fa)
                    status = "recover"
                else:
                    # Empty Camelot folder — nothing to recover
                    status = "review"

            log.debug(
                "FOLDER-CLEAN detect: %r  rule=%s  status=%s  cleaned=%r",
                name, rule, status, cleaned,
            )
            results.append(CleanResult(
                original_path=artist_dir,
                original_name=name,
                letter=letter,
                files=files,
                detection_rule=rule,
                cleaned_name=cleaned,
                reject_reason=reject_reason,
                target_path=target_path,
                target_exists=target_exists,
                status=status,
                file_assignments=assignments,
            ))

    return results


# ---------------------------------------------------------------------------
# File-move helpers
# ---------------------------------------------------------------------------

def _unique_path(dest: Path) -> Path:
    """If *dest* exists, append a counter suffix until a free slot is found."""
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    parent = dest.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _move_files_to_target(result: CleanResult, dry_run: bool) -> Dict[str, int]:
    """
    Move audio files from result.original_path to result.target_path (same
    target for all files — used for rename/merge outcomes).
    """
    stats  = {"moved": 0, "collisions": 0, "errors": 0}
    target = result.target_path

    for src in result.files:
        dest = target / src.name

        if dest.exists():
            dest = _unique_path(dest)
            stats["collisions"] += 1
            log.warning(
                "FOLDER-CLEAN: collision — %s → %s (renamed to avoid overwrite)",
                src.name, dest.name,
            )
            log_action(
                f"FOLDER-CLEAN: COLLISION {src.name!r} renamed"
                f" → {dest.name!r} in [{result.cleaned_name}]"
            )

        log.info(
            "FOLDER-CLEAN: %s  %s → %s",
            ("DRY-RUN would move" if dry_run else "moving"),
            src, dest,
        )
        log_action(
            f"FOLDER-CLEAN: {'[DRY] ' if dry_run else ''}move"
            f" [{result.original_name}/{src.name}]"
            f" → [{result.cleaned_name}/{dest.name}]"
        )

        if not dry_run:
            try:
                target.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                stats["moved"] += 1
                _update_db(str(src), str(dest), result.cleaned_name)
            except Exception as exc:
                log.error(
                    "FOLDER-CLEAN: failed to move %s → %s: %s", src, dest, exc,
                )
                log_action(f"FOLDER-CLEAN: ERROR moving {src.name!r}: {exc}")
                stats["errors"] += 1
        else:
            stats["moved"] += 1

    return stats


def _apply_recovery(result: CleanResult, dry_run: bool) -> Dict[str, int]:
    """
    Move each file in a pure_camelot folder to its individually-recovered
    target artist folder.  Each file may go to a different destination.
    """
    stats = {"moved": 0, "collisions": 0, "errors": 0, "folders_removed": 0}

    for fa in result.file_assignments:
        src  = fa.source
        dest = fa.target_folder / src.name

        if dest.exists():
            dest = _unique_path(dest)
            stats["collisions"] += 1
            log.warning(
                "FOLDER-CLEAN: collision — %s → %s (renamed to avoid overwrite)",
                src.name, dest.name,
            )
            log_action(
                f"FOLDER-CLEAN: COLLISION {src.name!r} renamed"
                f" → {dest.name!r} in [{fa.artist}]"
            )

        log.info(
            "FOLDER-CLEAN: %s  %s → %s  (method=%s)",
            ("DRY-RUN would recover" if dry_run else "recovering"),
            src, dest, fa.method,
        )
        log_action(
            f"FOLDER-CLEAN: {'[DRY] ' if dry_run else ''}recover"
            f" [{result.original_name}/{src.name}]"
            f" → [{fa.artist}/{dest.name}]  ({fa.method})"
        )

        if not dry_run:
            try:
                fa.target_folder.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                stats["moved"] += 1
                _update_db(str(src), str(dest), fa.artist)
            except Exception as exc:
                log.error(
                    "FOLDER-CLEAN: failed to recover %s → %s: %s", src, dest, exc,
                )
                log_action(f"FOLDER-CLEAN: ERROR recovering {src.name!r}: {exc}")
                stats["errors"] += 1
        else:
            stats["moved"] += 1

    # Remove the now-vacated Camelot folder
    if not dry_run:
        _remove_if_empty(result.original_path, stats)

    return stats


def _apply_clean(result: CleanResult, dry_run: bool) -> Dict[str, int]:
    """
    Apply one CleanResult (rename or merge only).
    Recover, suspicious, and review results must use the appropriate caller.
    """
    if result.status not in ("rename", "merge"):
        return {"moved": 0, "collisions": 0, "errors": 0, "folders_removed": 0}

    stats = _move_files_to_target(result, dry_run)
    stats["folders_removed"] = 0

    if not dry_run:
        _remove_if_empty(result.original_path, stats)

    return stats


# ---------------------------------------------------------------------------
# DB update + folder cleanup helpers
# ---------------------------------------------------------------------------

def _update_db(old_str: str, new_str: str, artist: Optional[str]) -> None:
    """Re-register a moved file in the database under its new path."""
    try:
        row = db.get_track(old_str)
        if row:
            db.upsert_track(
                new_str,
                artist=artist or row["artist"],
                title=row["title"],
                genre=row["genre"],
                bpm=row["bpm"],
                key_musical=row["key_musical"],
                key_camelot=row["key_camelot"],
                duration_sec=row["duration_sec"],
                bitrate_kbps=row["bitrate_kbps"],
                filesize_bytes=row["filesize_bytes"],
                status=row["status"],
            )
            with db.get_conn() as conn:
                conn.execute("DELETE FROM tracks WHERE filepath=?", (old_str,))
    except Exception as exc:
        log.warning("FOLDER-CLEAN: DB update failed for %s → %s: %s",
                    old_str, new_str, exc)


def _remove_if_empty(folder: Path, stats: Dict[str, int]) -> None:
    """Remove *folder* (and its letter parent if also empty) if vacated."""
    if not folder.exists():
        return
    try:
        remaining = [p for p in folder.iterdir() if not p.name.startswith(".")]
        if not remaining:
            folder.rmdir()
            stats["folders_removed"] = stats.get("folders_removed", 0) + 1
            log.info("FOLDER-CLEAN: removed vacated folder %s", folder)
            log_action(f"FOLDER-CLEAN: removed vacated folder [{folder.name}]")
            parent = folder.parent
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    log.info("FOLDER-CLEAN: removed empty letter dir %s", parent)
            except Exception:
                pass
    except Exception as exc:
        log.warning("FOLDER-CLEAN: could not remove %s: %s", folder, exc)


# ---------------------------------------------------------------------------
# Report serialization
# ---------------------------------------------------------------------------

def _assignment_to_dict(fa: FileAssignment) -> dict:
    return {
        "file":          fa.source.name,
        "artist":        fa.artist,
        "target_folder": str(fa.target_folder),
        "method":        fa.method,
    }


def _result_to_dict(r: CleanResult) -> dict:
    d: dict = {
        "original_name":  r.original_name,
        "original_path":  str(r.original_path),
        "letter":         r.letter,
        "detection_rule": r.detection_rule,
        "cleaned_name":   r.cleaned_name,
        "reject_reason":  r.reject_reason,
        "target_path":    str(r.target_path) if r.target_path else None,
        "target_exists":  r.target_exists,
        "status":         r.status,
        "file_count":     len(r.files),
    }
    if r.file_assignments:
        d["file_assignments"] = [_assignment_to_dict(fa) for fa in r.file_assignments]
    return d


def _write_report(
    report_dir: Path,
    results: List[CleanResult],
    applied_stats: Optional[Dict] = None,
    dry_run: bool = True,
) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    mode = "dry_run" if dry_run else "applied"
    report_path = report_dir / f"artist_folder_clean_{mode}.json"

    renames    = [r for r in results if r.status == "rename"]
    merges     = [r for r in results if r.status == "merge"]
    recovered  = [r for r in results if r.status == "recover"]
    suspicious = [r for r in results if r.status == "suspicious"]
    reviews    = [r for r in results if r.status == "review"]

    payload: dict = {
        "mode": mode,
        "summary": {
            "total_bad_folders": len(results),
            "renames":           len(renames),
            "merges":            len(merges),
            "recover":           len(recovered),
            "suspicious":        len(suspicious),
            "review":            len(reviews),
            "files_affected":    sum(len(r.files) for r in renames + merges + recovered),
        },
        "renames":    [_result_to_dict(r) for r in renames],
        "merges":     [_result_to_dict(r) for r in merges],
        "recover":    [_result_to_dict(r) for r in recovered],
        "suspicious": [_result_to_dict(r) for r in suspicious],
        "review":     [_result_to_dict(r) for r in reviews],
    }
    if applied_stats:
        payload["applied"] = applied_stats

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    return report_path


# ---------------------------------------------------------------------------
# Public entry points (called from pipeline.py)
# ---------------------------------------------------------------------------

def run_dry_run(sorted_root: Path, report_dir: Path) -> int:
    """
    Scan the library, detect bad folder names, write a dry-run report, and
    print a human-readable summary.  No files are moved.
    """
    log.info("FOLDER-CLEAN: scanning %s (dry-run)", sorted_root)
    log_action("FOLDER-CLEAN DRY-RUN START")

    results     = scan_bad_folders(sorted_root)
    report_path = _write_report(report_dir, results, dry_run=True)

    renames    = [r for r in results if r.status == "rename"]
    merges     = [r for r in results if r.status == "merge"]
    recovered  = [r for r in results if r.status == "recover"]
    suspicious = [r for r in results if r.status == "suspicious"]
    reviews    = [r for r in results if r.status == "review"]

    # Also gather rejection logs for display
    rejected_candidates = [
        r for r in results
        if r.status == "review" and r.detection_rule == "camelot_prefix" and r.reject_reason
    ]

    print(f"\n{'─'*66}")
    print(f"  Artist Folder Clean — Dry Run")
    print(f"{'─'*66}")
    print(f"  Bad folders found        : {len(results):>4}")
    print(
        f"  Will rename              : {len(renames):>4}"
        f"  ({sum(len(r.files) for r in renames)} files)"
    )
    print(
        f"  Will merge into          : {len(merges):>4}"
        f"  ({sum(len(r.files) for r in merges)} files)"
    )
    print(
        f"  Pure Camelot → recover   : {len(recovered):>4}"
        f"  ({sum(len(r.files) for r in recovered)} files)"
    )
    print(
        f"  Suspicious (review)      : {len(suspicious):>4}"
        f"  ({sum(len(r.files) for r in suspicious)} files)"
    )
    print(
        f"  Review (no candidate)    : {len(reviews):>4}"
        f"  ({sum(len(r.files) for r in reviews)} files)"
    )

    if renames:
        print(f"\n  Renames  [{len(renames)}]")
        print(f"  {'─'*60}")
        for r in sorted(renames, key=lambda x: x.original_name.lower()):
            print(f"  [camelot_prefix]  {r.original_name!r}")
            print(f"    → {r.cleaned_name!r}  ({len(r.files)} files)")

    if merges:
        print(f"\n  Merges  [{len(merges)}]  (target folder already exists)")
        print(f"  {'─'*60}")
        for r in sorted(merges, key=lambda x: x.original_name.lower()):
            print(f"  [camelot_prefix]  {r.original_name!r}")
            print(f"    → {r.cleaned_name!r}  ({len(r.files)} files, merge into existing)")

    if recovered:
        print(f"\n  Pure Camelot — Per-file Recovery  [{len(recovered)}]")
        print(f"  {'─'*60}")
        for r in sorted(recovered, key=lambda x: x.original_name.lower()):
            tag_count = sum(1 for fa in r.file_assignments if fa.method == "tag_artist")
            fn_count  = sum(1 for fa in r.file_assignments if fa.method == "filename_parse")
            unk_count = sum(1 for fa in r.file_assignments if fa.method == "unknown")
            print(
                f"  [pure_camelot]  {r.original_name!r}  ({len(r.files)} files)"
                f"  tag={tag_count} filename={fn_count} unknown={unk_count}"
            )
            for fa in r.file_assignments:
                arrow  = "→" if fa.method != "unknown" else "?"
                target = fa.target_folder.relative_to(sorted_root.parent) \
                         if fa.target_folder.is_relative_to(sorted_root.parent) \
                         else fa.target_folder
                print(
                    f"    {arrow} [{fa.method:14s}]  {fa.source.name}"
                    f"  →  {fa.artist!r}"
                )

    if rejected_candidates:
        print(
            f"\n  Rejected Source-Junk Candidates  [{len(rejected_candidates)}]"
            f"  (camelot prefix stripped but inner text was promo/source junk)"
        )
        print(f"  {'─'*60}")
        for r in sorted(rejected_candidates, key=lambda x: x.original_name.lower()):
            print(f"  [camelot_prefix]  {r.original_name!r}")
            print(f"    rejected: {r.reject_reason}")

    if suspicious:
        print(
            f"\n  Suspicious  [{len(suspicious)}]"
            f"  (bracket-wrapped — plausible candidate, needs human decision)"
        )
        print(f"  {'─'*60}")
        for r in sorted(suspicious, key=lambda x: x.original_name.lower()):
            print(f"  [bracket_junk]  {r.original_name!r}")
            print(
                f"    candidate: {r.cleaned_name!r}  ({len(r.files)} files)"
                f"{'  [target exists]' if r.target_exists else ''}"
            )

    if reviews:
        print(
            f"\n  Review  [{len(reviews)}]"
            f"  (unrecoverable — no valid artist name extractable)"
        )
        print(f"  {'─'*60}")
        for r in sorted(reviews, key=lambda x: x.original_name.lower()):
            extra = f"  rejected: {r.reject_reason}" if r.reject_reason else ""
            print(
                f"  [{r.detection_rule}]  {r.original_name!r}"
                f"  ({len(r.files)} files){extra}"
            )

    print(f"\n  Report: {report_path}")
    print(f"{'─'*66}\n")

    log.info(
        "FOLDER-CLEAN dry-run: %d bad — %d rename, %d merge,"
        " %d recover, %d suspicious, %d review",
        len(results), len(renames), len(merges),
        len(recovered), len(suspicious), len(reviews),
    )
    log_action(
        f"FOLDER-CLEAN DRY-RUN DONE: {len(results)} bad —"
        f" {len(renames)} rename, {len(merges)} merge,"
        f" {len(recovered)} recover, {len(suspicious)} suspicious,"
        f" {len(reviews)} review → {report_path}"
    )
    return 0


def run_apply(sorted_root: Path, report_dir: Path) -> int:
    """
    Apply all actionable folders:
      rename  / merge   — move all files to the cleaned artist folder
      recover           — move each file individually to its recovered artist folder

    Suspicious and review folders are never touched — they appear in the report.

    Returns 0 on success, 1 if any file move errored.
    """
    log.info("FOLDER-CLEAN: scanning %s (apply)", sorted_root)
    log_action("FOLDER-CLEAN APPLY START")

    results    = scan_bad_folders(sorted_root)
    actionable = [r for r in results if r.status in ("rename", "merge", "recover")]
    suspicious = [r for r in results if r.status == "suspicious"]
    reviews    = [r for r in results if r.status == "review"]

    if not actionable:
        log.info("FOLDER-CLEAN: no actionable folders found")
        print("\n  Artist Folder Clean — nothing to fix automatically.\n")
        _write_report(report_dir, results, dry_run=False)
        return 0

    global_stats: Dict[str, int] = {
        "moved":           0,
        "collisions":      0,
        "errors":          0,
        "folders_removed": 0,
        "renamed":         0,
        "merged":          0,
        "recovered":       0,
    }

    for r in actionable:
        log.info(
            "FOLDER-CLEAN: %s %r  (%d files, rule=%s)",
            r.status, r.original_name, len(r.files), r.detection_rule,
        )

        if r.status == "recover":
            log_action(
                f"FOLDER-CLEAN: recover {r.original_name!r}"
                f"  ({len(r.file_assignments)} file-assignments)"
            )
            stats = _apply_recovery(r, dry_run=False)
            global_stats["recovered"] += 1
        else:
            log_action(
                f"FOLDER-CLEAN: {r.status} {r.original_name!r}"
                f" → {r.cleaned_name!r}  ({len(r.files)} files)"
            )
            stats = _apply_clean(r, dry_run=False)
            if r.status == "rename":
                global_stats["renamed"] += 1
            else:
                global_stats["merged"] += 1

        global_stats["moved"]           += stats["moved"]
        global_stats["collisions"]      += stats["collisions"]
        global_stats["errors"]          += stats["errors"]
        global_stats["folders_removed"] += stats.get("folders_removed", 0)

    for r in suspicious:
        log.info(
            "FOLDER-CLEAN: suspicious (skipped) %r  candidate=%r  files=%d",
            r.original_name, r.cleaned_name, len(r.files),
        )
        log_action(
            f"FOLDER-CLEAN: SUSPICIOUS (skipped) {r.original_name!r}"
            f"  candidate={r.cleaned_name!r}  files={len(r.files)}"
        )

    for r in reviews:
        log.info(
            "FOLDER-CLEAN: review (skipped) %r  rule=%s  files=%d",
            r.original_name, r.detection_rule, len(r.files),
        )
        log_action(
            f"FOLDER-CLEAN: REVIEW (skipped) {r.original_name!r}"
            f"  rule={r.detection_rule}  files={len(r.files)}"
        )

    report_path = _write_report(
        report_dir, results,
        applied_stats=global_stats, dry_run=False,
    )

    print(f"\n{'─'*66}")
    print(f"  Artist Folder Clean — Applied")
    print(f"{'─'*66}")
    print(f"  Folders renamed          : {global_stats['renamed']}")
    print(f"  Folders merged           : {global_stats['merged']}")
    print(f"  Camelot folders recovered: {global_stats['recovered']}")
    print(f"  Files moved              : {global_stats['moved']}")
    print(f"  Collisions renamed       : {global_stats['collisions']}")
    print(f"  Old folders removed      : {global_stats['folders_removed']}")
    print(f"  Errors                   : {global_stats['errors']}")
    print(f"  Suspicious (skipped)     : {len(suspicious)}")
    print(f"  Review (skipped)         : {len(reviews)}")
    print(f"\n  Report: {report_path}")
    print(f"{'─'*66}\n")

    log.info(
        "FOLDER-CLEAN apply done:"
        " %d renamed, %d merged, %d recovered, %d moved,"
        " %d errors, %d suspicious, %d review",
        global_stats["renamed"], global_stats["merged"],
        global_stats["recovered"], global_stats["moved"],
        global_stats["errors"], len(suspicious), len(reviews),
    )
    log_action(
        f"FOLDER-CLEAN APPLY DONE:"
        f" {global_stats['renamed']} renamed,"
        f" {global_stats['merged']} merged,"
        f" {global_stats['recovered']} recovered,"
        f" {global_stats['moved']} files,"
        f" {global_stats['errors']} errors,"
        f" {len(suspicious)} suspicious,"
        f" {len(reviews)} review → {report_path}"
    )
    return 0 if global_stats["errors"] == 0 else 1
