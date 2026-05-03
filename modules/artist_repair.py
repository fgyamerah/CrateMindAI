"""
modules/artist_repair.py — Detect and propose repair for broken concatenated artist tags.

Detects artist strings where two names were merged without a separator, e.g.:
  "Afrikan RootsLebo"       → "Afrikan Roots, Lebo"
  "African RhythmAfrikan Roots" → "African Rhythm, Afrikan Roots"
  "Ante PerryDayne S"       → "Ante Perry, Dayne S"

Detection signal: [a-z][A-Z] boundary NOT immediately preceded by a space.
  - "RootsLebo" → 's' (not word-start) followed by 'L' → merge detected
  - "mOat"      → 'm' preceded by space (word-start) → NOT flagged
  - "McFlare"   → 'c' at offset 1 within word (< 3) → NOT flagged
  - "AVG (IT)"  → no lowercase→uppercase transitions → NOT flagged

Safety rules:
  - Preview by default; no writes without --apply.
  - Only HIGH-confidence splits (both sides confirmed in known-artist dict) are
    write-eligible with --apply.  All others go to review queue only.
  - Country/location suffixes (IT), (De), (UK), (ZA) etc. are stripped before
    analysis and re-attached to the right-side artist after splitting.
  - Prefix-length guard: boundaries within the first 3 chars of a word are
    skipped (protects Mc, De, La, du-style prefixes).
  - --move-artist-review moves review-queue files to .BIN/CHKARTISTNAMES/,
    relative structure preserved.  Requires --apply to execute.

Outputs:
  Review queue : data/intelligence/artist_repair_queue.json
  Log summary  : logs/artist-repair/<timestamp>_artist-repair_summary.json
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import config
import modules.run_logger as _proc

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Match [a-z][A-Z] NOT preceded by a space (lookbehind: preceding char is not space)
_RE_MERGED_BOUNDARY = re.compile(r'(?<=[^ ])([a-z])([A-Z])')

# Country / location suffixes: (IT), (De), (UK), (US), (ZA), (Kz) …
# 2–3 chars, possibly mixed-case, enclosed in parens at end of string.
_RE_COUNTRY_SUFFIX = re.compile(r'\s*\([A-Z][a-zA-Z]{0,2}\)\s*$')

HIGH_CONFIDENCE   = 0.85   # both sides confirmed in known_artists
MEDIUM_CONFIDENCE = 0.65   # one side confirmed
LOW_CONFIDENCE    = 0.45   # neither side confirmed

# ---------------------------------------------------------------------------
# Separator-repair constants
# ---------------------------------------------------------------------------

# Splits on / | \ with optional surrounding whitespace
_SLASH_SEP_RE = re.compile(r'\s*[/|\\]\s*')

# Known artist names that legitimately contain a slash — never split these.
_SLASH_ARTIST_ALLOWLIST: frozenset = frozenset({
    "ac/dc",
})

# Minimum character length for each side of a proposed separator split.
# Protects against splitting abbreviations like "AC/DC" (both sides 2 chars).
_MIN_SEP_SIDE_LEN = 3


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_lookup(s: str) -> str:
    """Lowercase + strip non-alphanumeric for fuzzy dict lookup."""
    return re.sub(r'[^a-z0-9\s]', '', s.lower()).strip()


def _strip_country_suffix(artist: str) -> Tuple[str, str]:
    """
    Strip trailing country/location suffix like (IT), (De), (UK).
    Returns (cleaned_artist, suffix_string).  suffix is "" when none found.
    """
    m = _RE_COUNTRY_SUFFIX.search(artist)
    if m:
        return artist[:m.start()].rstrip(), m.group(0).strip()
    return artist, ""


def _word_has_preceding_context(artist: str, word_start: int) -> bool:
    """
    True if the word at word_start has a sibling word before it in the same
    comma/slash-separated artist token.

    Walks backwards from word_start: skips whitespace, then checks the last
    non-whitespace char.  If it is a token separator (, / & ;) the word is the
    FIRST word of its token → False.  If it is a regular char, a sibling word
    exists → True.

    This distinguishes compound names ("AfricanGroove", "RootedSoul", "AfroZone")
    from true merges ("African RootsLebo") where the boundary word follows another
    word in the same token.
    """
    if word_start == 0:
        return False
    j = word_start - 1
    while j >= 0 and artist[j] == ' ':
        j -= 1
    if j < 0:
        return False
    return artist[j] not in (',', '/', '&', ';')


def _find_merge_positions(artist: str) -> List[int]:
    """
    Return positions (index of the lowercase char) where two artist names appear
    merged without a separator.

    Skips:
      - word-start lowercase (handled by lookbehind: char before is not space)
      - positions within the first 3 chars of a word (Mc, De, La prefix guard)
      - positions where the boundary word is the FIRST word of its artist token
        (compound names: AfricanGroove, RootedSoul, AfroZone — these have no
        preceding sibling word within their comma/slash-separated token)
      - trailing single-uppercase stylizations where the uppercase char is the
        last char of its word (MusiQ, SoulQ, BoyZ-style endings)
    """
    positions: List[int] = []
    for m in _RE_MERGED_BOUNDARY.finditer(artist):
        pos = m.start(1)  # index of the lowercase char
        # Prefix-length guard: how far into the current word are we?
        word_start = artist.rfind(' ', 0, pos) + 1  # 0 when no preceding space
        pos_in_word = pos - word_start
        if pos_in_word < 3:
            continue  # likely Mc/De/La/du prefix — not a merge
        # First-word-of-token guard: compound names (AfricanGroove) have the
        # boundary word as the only/first word of their token — not a merge.
        if not _word_has_preceding_context(artist, word_start):
            continue
        # Trailing-stylization guard: if the character immediately after the
        # uppercase char (pos+2) is non-alphabetic — end-of-string, space, comma,
        # or any other separator — the uppercase is a stylized suffix (MusiQ,
        # SoulQ, BoyZ) rather than the start of a second artist name.
        # Using .isalpha() catches commas ("MusiQ, Naak") that the previous
        # space-only word_end check missed.
        next_pos = pos + 2
        if next_pos >= len(artist) or not artist[next_pos].isalpha():
            continue
        positions.append(pos)
    return positions


def _lookup_artist(name: str, known_artists: Set[str]) -> bool:
    """True if name (or its normalized form) is in known_artists."""
    if not name or len(name.strip()) < 2:
        return False
    return name.lower() in known_artists or _normalize_lookup(name) in known_artists


# ---------------------------------------------------------------------------
# Known-artist dictionary builder
# ---------------------------------------------------------------------------

def _build_known_artists(
    input_path: Path,
    alias_store_path: Optional[Path] = None,
) -> Set[str]:
    """
    Build a fuzzy lookup set of known artist names from:
      1. Subdirectory names under input_path (letter/artist/ hierarchy)
      2. Artist tags sampled from audio files in input_path (≤ 300 files)
      3. Keys and variants from artist_aliases.json

    Returns a set of lowercase strings (both raw and normalized forms).
    """
    known: Set[str] = set()

    # 1. Folder names — depth 1 and 2
    try:
        for item in input_path.iterdir():
            if not item.is_dir() or item.name.startswith("."):
                continue
            _add_artist(item.name, known)
            try:
                for sub in item.iterdir():
                    if sub.is_dir() and not sub.name.startswith("."):
                        _add_artist(sub.name, known)
            except PermissionError:
                pass
    except Exception as exc:
        log.debug("Folder scan error under %s: %s", input_path, exc)

    # 2. Artist tags from audio files — capped sample
    _sample_artist_tags(input_path, known, cap=300)

    # 3. Alias store
    if alias_store_path and alias_store_path.exists():
        try:
            with open(alias_store_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for canonical, variants in data.items():
                    _add_artist(canonical, known)
                    for v in (variants or []):
                        _add_artist(v, known)
        except Exception as exc:
            log.debug("Could not load alias store %s: %s", alias_store_path, exc)

    return known


def _add_artist(name: str, known: Set[str]) -> None:
    name = name.strip()
    if len(name) < 2:
        return
    known.add(name.lower())
    norm = _normalize_lookup(name)
    if norm:
        known.add(norm)


def _sample_artist_tags(input_path: Path, known: Set[str], cap: int) -> None:
    try:
        from mutagen import File as MFile
    except ImportError:
        return

    _COLLAB_SEP = re.compile(
        r'\s*[,/&]\s*|\s+feat\.?\s+|\s+ft\.?\s+|\s+vs\.?\s+',
        re.IGNORECASE,
    )
    collected = 0
    for ext in (".mp3", ".flac", ".aiff", ".aif", ".m4a"):
        for f in input_path.rglob(f"*{ext}"):
            if collected >= cap:
                return
            try:
                audio = MFile(str(f), easy=True)
                if audio is None:
                    continue
                raw = audio.get("artist") or []
                for a in raw:
                    for part in _COLLAB_SEP.split(a):
                        part = part.strip()
                        if len(part) >= 2:
                            _add_artist(part, known)
                collected += 1
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Repair proposal
# ---------------------------------------------------------------------------

@dataclass
class RepairCandidate:
    file: str
    source_field: str        # "artist"
    original: str
    proposed: str
    confidence: float
    reason: str
    apply_blocked: bool      # True when confidence < HIGH_CONFIDENCE
    country_suffix: str = ""


def _propose_repairs(
    artist: str,
    known_artists: Set[str],
) -> List[RepairCandidate]:
    """
    Find merge positions in artist and propose comma-separated splits.
    Returns at most one candidate (the highest-confidence split).
    """
    artist_clean, suffix = _strip_country_suffix(artist)
    positions = _find_merge_positions(artist_clean)
    if not positions:
        return []

    candidates: List[RepairCandidate] = []
    for pos in positions:
        left  = artist_clean[:pos + 1].strip()
        right = artist_clean[pos + 1:].strip()

        if len(left) < 2 or len(right) < 2:
            continue

        left_known  = _lookup_artist(left,  known_artists)
        right_known = _lookup_artist(right, known_artists)

        if left_known and right_known:
            conf          = HIGH_CONFIDENCE
            apply_blocked = False
            known_side    = "both"
        elif left_known:
            conf          = MEDIUM_CONFIDENCE
            apply_blocked = True
            known_side    = "left"
        elif right_known:
            conf          = MEDIUM_CONFIDENCE
            apply_blocked = True
            known_side    = "right"
        else:
            conf          = LOW_CONFIDENCE
            apply_blocked = True
            known_side    = "none"

        proposed = f"{left}, {right}" + (f" {suffix}" if suffix else "")
        reason   = f"camelcase_merge_detected; known_sides={known_side}"

        candidates.append(RepairCandidate(
            file="",
            source_field="artist",
            original=artist,
            proposed=proposed,
            confidence=conf,
            reason=reason,
            apply_blocked=apply_blocked,
            country_suffix=suffix,
        ))

    if not candidates:
        return []
    # Return only the highest-confidence candidate
    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates[:1]


def _propose_separator_repairs(
    artist: str,
    known_artists: Set[str],
) -> List[RepairCandidate]:
    """
    Detect slash, pipe, or backslash separators in an artist field and propose
    a comma-separated normalization.

      "African Roots/Lebo"                  → "African Roots, Lebo"
      "NewTone Major/Steve Univers | Koki"  → "NewTone Major, Steve Univers, Koki"

    Skips:
      - Artists in _SLASH_ARTIST_ALLOWLIST (e.g. AC/DC)
      - Splits where any side is shorter than _MIN_SEP_SIDE_LEN chars

    Confidence mirrors _propose_repairs:
      HIGH  — all sides found in known_artists (write-eligible with --apply)
      MEDIUM — at least one side known
      LOW   — no sides known (review queue only)
    """
    if not _SLASH_SEP_RE.search(artist):
        return []

    # Allowlist check — full lowercased artist string
    if artist.strip().lower() in _SLASH_ARTIST_ALLOWLIST:
        return []

    parts = [p.strip() for p in _SLASH_SEP_RE.split(artist) if p.strip()]
    if len(parts) < 2:
        return []

    # Short-side guard: abbreviations like "AC" (2 chars) are not artist names
    if any(len(p) < _MIN_SEP_SIDE_LEN for p in parts):
        return []

    # Confidence from known-artist lookup
    known_count = sum(1 for p in parts if _lookup_artist(p, known_artists))
    if known_count == len(parts):
        conf, apply_blocked, known_side = HIGH_CONFIDENCE, False, "all"
    elif known_count > 0:
        conf, apply_blocked, known_side = MEDIUM_CONFIDENCE, True, "some"
    else:
        conf, apply_blocked, known_side = LOW_CONFIDENCE, True, "none"

    # Build reason codes for each separator type present
    sep_codes: List[str] = []
    if '/' in artist or '\\' in artist:
        sep_codes.append("slash_separator_repair")
    if '|' in artist:
        sep_codes.append("pipe_separator_repair")
    reason = "; ".join(sep_codes) + f"; known_sides={known_side}"

    return [RepairCandidate(
        file="",
        source_field="artist",
        original=artist,
        proposed=", ".join(parts),
        confidence=conf,
        reason=reason,
        apply_blocked=apply_blocked,
        country_suffix="",
    )]


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def _collect_files(input_path: Path, limit: Optional[int]) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    files: List[Path] = []
    seen: set = set()
    for ext in config.AUDIO_EXTENSIONS:
        for p in sorted(input_path.rglob(f"*{ext}")):
            k = str(p)
            if k not in seen:
                seen.add(k)
                files.append(p)
        for p in sorted(input_path.rglob(f"*{ext.upper()}")):
            k = str(p)
            if k not in seen:
                seen.add(k)
                files.append(p)
    files.sort()
    if limit and limit > 0:
        files = files[:limit]
    return files


def _read_artist_tag(path: Path) -> Optional[str]:
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return None
        raw = audio.get("artist") or []
        return raw[0].strip() if raw else None
    except Exception as exc:
        log.debug("Could not read artist from %s: %s", path.name, exc)
        return None


def _write_artist_tag(path: Path, new_artist: str) -> bool:
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return False
        audio["artist"] = [new_artist]
        audio.save()
        return True
    except Exception as exc:
        log.error("Write failed for %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Review queue
# ---------------------------------------------------------------------------

def _update_review_queue(queue_path: Path, entries: List[dict]) -> None:
    """Merge new entries into the queue, dedup by (file, original_artist)."""
    existing: List[dict] = []
    if queue_path.exists():
        try:
            with open(queue_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                existing = data
        except Exception:
            pass

    key_index: Dict[tuple, int] = {
        (e.get("file"), e.get("original_artist")): i
        for i, e in enumerate(existing)
    }
    for entry in entries:
        key = (entry.get("file"), entry.get("original_artist"))
        if key in key_index:
            existing[key_index[key]] = entry
        else:
            existing.append(entry)

    queue_path.parent.mkdir(parents=True, exist_ok=True)
    with open(queue_path, "w", encoding="utf-8") as fh:
        json.dump(existing, fh, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def run_artist_repair(args) -> int:
    """Called by pipeline.py dispatch for the artist-repair subcommand."""
    from modules.textlog import log_action

    input_path  = Path(args.input).expanduser().resolve()
    apply_mode  = getattr(args, "apply",              False)
    move_review = getattr(args, "move_artist_review", False)
    limit       = getattr(args, "limit",              None)
    verbose     = getattr(args, "verbose",            False)

    if verbose:
        logging.basicConfig(level=logging.DEBUG)

    if not input_path.exists():
        print(f"ERROR: Input path does not exist: {input_path}", file=sys.stderr)
        return 1

    # Locate alias store (optional)
    alias_store_path: Optional[Path] = None
    raw_alias = getattr(config, "ARTIST_ALIAS_STORE", None)
    if raw_alias:
        alias_store_path = Path(raw_alias)

    mode_label = "APPLY" if apply_mode else "PREVIEW"
    print(f"artist-repair [{mode_label}] — {input_path}")

    print("  Building known-artist dictionary...", end="", flush=True)
    known_artists = _build_known_artists(input_path, alias_store_path)
    print(f" {len(known_artists)} entries")
    print()

    files = _collect_files(input_path, limit)
    if not files:
        print(f"No audio files found under {input_path}")
        return 0

    print(f"  Scanning {len(files)} file(s)...\n")

    # Paths
    log_dir_raw = getattr(args, "log_dir", None)
    if log_dir_raw:
        log_dir = Path(log_dir_raw)
    elif hasattr(config, "PIPELINE_LOGS_DIR"):
        log_dir = Path(config.PIPELINE_LOGS_DIR) / "artist-repair"
    else:
        log_dir = Path("logs/artist-repair")
    log_dir.mkdir(parents=True, exist_ok=True)

    _stage  = "artist-repair"
    _force  = getattr(args, "force",       False)
    if getattr(args, "reset_stage", False):
        _proc.clear_stage(_stage)

    # .BIN/CHKARTISTNAMES for quarantine
    chkartist_dir = input_path / ".BIN" / "CHKARTISTNAMES"

    # Review queue path
    repair_queue_path_raw = getattr(config, "ARTIST_REPAIR_QUEUE",
                                    "data/intelligence/artist_repair_queue.json")
    repair_queue_path = Path(repair_queue_path_raw)

    # Counters
    flagged_count    = 0
    high_conf_count  = 0
    applied_count    = 0
    review_count     = 0
    moved_count      = 0
    error_count      = 0
    n_skip_unchanged = 0
    n_no_merge       = 0

    review_entries: List[dict] = []

    for path in files:
        if not _force and _proc.should_skip(_stage, path):
            n_skip_unchanged += 1
            continue

        artist = _read_artist_tag(path)
        if not artist:
            n_no_merge += 1
            _proc.record(_stage, path, "no_change")
            continue

        _all = _propose_repairs(artist, known_artists) + \
               _propose_separator_repairs(artist, known_artists)
        if not _all:
            n_no_merge += 1
            _proc.record(_stage, path, "no_change")
            continue
        _all.sort(key=lambda c: c.confidence, reverse=True)

        cand = _all[0]
        cand.file = str(path)
        flagged_count += 1

        print(f"  {path.name}")
        print(f"    artist   : {artist!r}")
        print(f"    proposed : {cand.proposed!r}")
        print(f"    conf     : {cand.confidence:.2f}  blocked: {cand.apply_blocked}")
        print(f"    reason   : {cand.reason}")

        if cand.apply_blocked:
            review_count += 1
            review_entries.append({
                "file":            str(path),
                "original_artist": artist,
                "proposed_artist": cand.proposed,
                "confidence":      round(cand.confidence, 3),
                "reason":          cand.reason,
                "source_field":    cand.source_field,
                "apply_blocked":   True,
                "run_date":        datetime.now(timezone.utc).isoformat(),
            })
            print("    → QUEUED for review (confidence below apply threshold)")

            if move_review and apply_mode:
                try:
                    rel  = path.relative_to(input_path)
                    dest = chkartist_dir / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(path), str(dest))
                    moved_count += 1
                    print(f"    → MOVED to {dest.relative_to(input_path.parent)}")
                    log_action(f"ARTIST-REPAIR MOVED: {path.name} → CHKARTISTNAMES")
                    _proc.record(_stage, path, "skipped", "moved_to_chkartistnames")
                except Exception as exc:
                    print(f"    [ERROR] Move failed: {exc}", file=sys.stderr)
                    error_count += 1
                    _proc.record(_stage, path, "error", "move_failed")
            else:
                _proc.record(_stage, path, "review", "apply_blocked")
        else:
            # High confidence — eligible for write
            high_conf_count += 1
            if apply_mode:
                ok = _write_artist_tag(path, cand.proposed)
                if ok:
                    applied_count += 1
                    log_action(
                        f"ARTIST-REPAIR: {path.name} | "
                        f"{artist!r} → {cand.proposed!r} | {cand.reason}"
                    )
                    _proc.record(_stage, path, "success", cand.reason)
                    print("    → APPLIED")
                else:
                    error_count += 1
                    _proc.record(_stage, path, "error", "write_failed")
                    print("    [ERROR] write_failed")
            else:
                # Preview-pending: don't record success yet
                print("    → WOULD APPLY (pass --apply to write)")

        print()

    # Write review queue
    if review_entries:
        try:
            _update_review_queue(repair_queue_path, review_entries)
        except Exception as exc:
            log.error("Could not write review queue: %s", exc)

    # Write JSON summary
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary_path = log_dir / f"{ts}_artist-repair_summary.json"
    _summary = {
        "run_date":        datetime.now(timezone.utc).isoformat(),
        "input":           str(input_path),
        "mode":            mode_label,
        "files_scanned":   len(files),
        "skip_unchanged":  n_skip_unchanged,
        "no_merge":        n_no_merge,
        "flagged":         flagged_count,
        "high_confidence": high_conf_count,
        "review_queued":   review_count,
        "applied":         applied_count,
        "moved":           moved_count,
        "errors":          error_count,
    }
    try:
        with open(summary_path, "w", encoding="utf-8") as fh:
            json.dump(_summary, fh, indent=2, ensure_ascii=False)
    except Exception as exc:
        log.warning("Could not write summary: %s", exc)

    print()
    print(f"Files scanned           : {len(files)}")
    print(f"Files skipped unchanged : {n_skip_unchanged}")
    print(f"No merge detected       : {n_no_merge}")
    print(f"Flagged (merge found)   : {flagged_count}")
    print(f"  High-conf (eligible)  : {high_conf_count}")
    print(f"  Queued for review     : {review_count}")
    print(f"Files written           : {applied_count}")
    print(f"Files moved to review   : {moved_count}")
    print(f"Errors                  : {error_count}")

    if not apply_mode and flagged_count:
        print()
        print("Preview mode — no files modified. Pass --apply to write high-confidence repairs.")
    if review_count:
        print(f"\nReview queue: {repair_queue_path} ({review_count} new entries)")
        if not (move_review and apply_mode):
            print("  Pass --apply --move-artist-review to quarantine review files.")

    log_action(
        f"ARTIST-REPAIR {'APPLY' if apply_mode else 'PREVIEW'}: "
        f"{flagged_count} flagged, {applied_count} applied, "
        f"{review_count} queued, {moved_count} moved → {input_path}"
    )
    return 0
