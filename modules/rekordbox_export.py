"""
modules/rekordbox_export.py — Rekordbox export profile for Windows (M: drive).

Converts Linux paths  /mnt/music_ssd/KKDJ/...
to Windows paths      M:\\KKDJ\\...
and XML locations     file://localhost/M:/KKDJ/...

Genre normalization
-------------------
All genres are normalized into a controlled canonical set (≤25).
Unknown variants are mapped via exact alias → fuzzy phrase → modifier-stripped
phrase → "Other".  Junk genres (store names, URLs) return "" and cause
the track to be excluded.

Pre-export validation
---------------------
Every track is resolved before export:
  1. Missing artist/title → parse from filename ("Artist - Title.ext")
  2. Missing BPM          → trigger aubio analysis + persist to DB
  3. Missing Camelot key  → trigger keyfinder-cli analysis + persist to DB
  4. Tracks that still fail are excluded and logged to:
       logs/rekordbox_export/invalid_tracks.txt

Output structure
----------------
_REKORDBOX_XML_EXPORT/
    rekordbox_library.xml       — Rekordbox-importable, full collection + playlists

_PLAYLISTS_M3U_EXPORT/
    Genre/<genre>.m3u8          — Windows-absolute paths
    Energy/<level>.m3u8
    Combined/<name>.m3u8
    Key/<camelot>.m3u8
    Route/<route>.m3u8
"""
from __future__ import annotations

import html
import logging
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote

import config
import db
from modules.playlists import (
    normalize_genre,
    _is_junk_genre,
    _classify_energy,
    _classify_route,
    _camelot_sort_key,
    _RE_VALID_CAMELOT,
    _RE_UNSAFE_FILENAME,
    _ENERGY_LEVELS,
    _kind_from_path,
    _build_comment,
    _read_label_from_file,
)
from modules.textlog import log_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Genre normalization — controlled canonical set
# ---------------------------------------------------------------------------

# Exact lowercased alias map — checked first, highest confidence.
_GENRE_ALIAS_EXACT: Dict[str, str] = {
    # Acapella (route type — kept so route playlists still work)
    "acapella":                 "Acapella",
    "a cappella":               "Acapella",
    "accapella":                "Acapella",
    "a capella":                "Acapella",
    # Afro House family
    "afro house":               "Afro House",
    "afrohouse":                "Afro House",
    "afro deep house":          "Afro House",
    "deep afro house":          "Afro House",
    "afro house amapiano":      "Afro House",
    "amapiano afro house":      "Afro House",
    "afrohouse amapiano":       "Afro House",
    "tribal house":             "Afro House",
    "afro tribal house":        "Afro House",
    # Afro Tech
    "afro tech":                "Afro Tech",
    "afrotech":                 "Afro Tech",
    "afro tech house":          "Afro Tech",
    # Amapiano
    "amapiano":                 "Amapiano",
    "ama piano":                "Amapiano",
    # Deep House
    "deep house":               "Deep House",
    "deephouse":                "Deep House",
    "uk deep house":            "Deep House",
    "deep soulful house":       "Deep House",
    # Tech House
    "tech house":               "Tech House",
    "techhouse":                "Tech House",
    "minimal tech house":       "Tech House",
    # Melodic House / Melodic Techno
    "melodic house":            "Melodic House",
    "melodic house techno":     "Melodic House",
    "melodic techno":           "Melodic House",
    # Organic House
    "organic house":            "Organic House",
    # Soulful House
    "soulful house":            "Soulful House",
    "latin house":              "Soulful House",
    "gospel house":             "Soulful House",
    # Funky House
    "funky house":              "Funky House",
    "jackin house":             "Funky House",
    "jackin' house":            "Funky House",
    # Garage House
    "garage house":             "Garage House",
    "uk garage":                "Garage House",
    # Progressive House
    "progressive house":        "Progressive House",
    "prog house":               "Progressive House",
    # Nu Disco
    "nu disco":                 "Nu Disco",
    "nu-disco":                 "Nu Disco",
    "afro disco":               "Nu Disco",
    # Disco
    "disco":                    "Disco",
    "classic disco":            "Disco",
    # Techno
    "techno":                   "Techno",
    "hard techno":              "Techno",
    "industrial techno":        "Techno",
    "peak time techno":         "Techno",
    "afro techno":              "Techno",
    # Dance
    "dance":                    "Dance",
    "edm":                      "Dance",
    "pop dance":                "Dance",
    "electronic dance music":   "Dance",
    # R&B
    "r&b":                      "R&B",
    "rnb":                      "R&B",
    "r 'n' b":                  "R&B",
    "rhythm and blues":         "R&B",
    "contemporary r&b":         "R&B",
    # Hip Hop
    "hip hop":                  "Hip Hop",
    "hip-hop":                  "Hip Hop",
    "rap":                      "Hip Hop",
    "trap":                     "Hip Hop",
}

# Ordered phrase list for fuzzy matching when exact alias fails.
# More-specific phrases come FIRST so "afro deep house" beats "house".
_CANONICAL_PHRASES: Tuple[Tuple[str, str], ...] = (
    ("progressive house",   "Progressive House"),
    ("afro deep house",     "Afro House"),
    ("deep afro house",     "Afro House"),
    ("afro tech house",     "Afro Tech"),
    ("afro tech",           "Afro Tech"),
    ("afro house",          "Afro House"),
    ("melodic house",       "Melodic House"),
    ("melodic techno",      "Melodic House"),
    ("organic house",       "Organic House"),
    ("soulful house",       "Soulful House"),
    ("funky house",         "Funky House"),
    ("garage house",        "Garage House"),
    ("deep house",          "Deep House"),
    ("tech house",          "Tech House"),
    ("nu disco",            "Nu Disco"),
    ("nu-disco",            "Nu Disco"),
    ("afro disco",          "Nu Disco"),
    ("amapiano",            "Amapiano"),
    ("hip hop",             "Hip Hop"),
    ("hip-hop",             "Hip Hop"),
    ("r&b",                 "R&B"),
    ("techno",              "Techno"),
    ("disco",               "Disco"),
    ("dance",               "Dance"),
    ("house",               "Deep House"),   # bare "house" → Deep House
)

# Words that modify genre names without changing the core identity.
# These are stripped before fuzzy matching so:
#   "Deep House Classic"  → "Deep House"
#   "Afro Gospel House"   → "Afro House"   (gospel stripped, afro house matched)
#   "House Reprise"       → "House"        → "Deep House"
_MODIFIER_WORDS: frozenset = frozenset({
    "classic", "classics", "gospel", "reprise", "trendy",
    "underground", "old", "school", "oldskool", "modern",
    "vintage", "collection", "selection", "sessions",
    "special", "edition", "revisited", "reissue",
    "presents", "feat", "featuring", "volume", "vol",
})


def _normalize_genre_for_export(genre: Optional[str]) -> str:
    """
    Normalize a raw genre tag to a member of the canonical export set.

    Pipeline:
      1. Basic normalize_genre() — split multi-values, collapse hyphens, title-case
      2. Junk check — return "" if store name / URL / placeholder
      3. Exact alias on original
      4. Strip modifier words (classic, gospel, reprise, …) → exact alias on cleaned
      5. Fuzzy phrase match on cleaned (longest/most-specific first)
      6. Fuzzy phrase match on original (catches unmodified non-aliased phrases)
      7. Fallback → "Other"

    Modifier stripping happens BEFORE fuzzy phrases so "Afro Gospel House" hits
    "afro house" in the alias map rather than the bare "house" phrase.

    Returns:
      ""        — junk or empty input  (caller excludes track)
      canonical — a member of the controlled set, or "Other"
    """
    base = normalize_genre(genre)
    if not base:
        return ""
    if _is_junk_genre(base):
        return ""

    key = base.lower()

    # 1. Exact alias on original
    exact = _GENRE_ALIAS_EXACT.get(key)
    if exact:
        return exact

    # 2. Strip modifier words
    words   = key.split()
    cleaned = " ".join(w for w in words if w not in _MODIFIER_WORDS) or key

    # 3. Exact alias on stripped form (handles "Deep House Classic" → "deep house")
    if cleaned != key:
        exact = _GENRE_ALIAS_EXACT.get(cleaned)
        if exact:
            return exact

    # 4. Fuzzy phrase match on stripped form
    for phrase, canonical in _CANONICAL_PHRASES:
        if phrase in cleaned:
            return canonical

    # 5. Fuzzy phrase match on original (in case stripping removed a relevant word)
    if cleaned != key:
        for phrase, canonical in _CANONICAL_PHRASES:
            if phrase in key:
                return canonical

    # 6. Valid but unmapped → "Other" (included in export, no named genre playlist)
    return "Other"


# ---------------------------------------------------------------------------
# Analysis fallback helpers (BPM + key)
# ---------------------------------------------------------------------------

def _try_detect_bpm(filepath: str, genre: str) -> Optional[float]:
    """
    Attempt BPM detection via aubio. Persists result to DB on success.
    Returns detected BPM float, or None if analysis fails / binary unavailable.
    """
    try:
        from modules.analyzer import detect_bpm
        bpm = detect_bpm(Path(filepath), genre=genre)
        if bpm is not None:
            db.upsert_track(filepath, bpm=bpm)
            log.debug("rekordbox-export: BPM recovered for %s → %.1f",
                      Path(filepath).name, bpm)
        return bpm
    except Exception as exc:
        log.debug("rekordbox-export: BPM analysis error for %s: %s",
                  Path(filepath).name, exc)
        return None


def _try_detect_key(filepath: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Attempt key detection via keyfinder-cli. Persists result to DB on success.
    Returns (musical_key, camelot), or (None, None) if analysis fails.
    """
    try:
        from modules.analyzer import detect_key
        musical, camelot = detect_key(Path(filepath))
        if camelot:
            db.upsert_track(filepath, key_musical=musical, key_camelot=camelot)
            log.debug("rekordbox-export: key recovered for %s → %s",
                      Path(filepath).name, camelot)
        return musical, camelot
    except Exception as exc:
        log.debug("rekordbox-export: key analysis error for %s: %s",
                  Path(filepath).name, exc)
        return None, None


# ---------------------------------------------------------------------------
# Filename fallback — parse "Artist - Title" from stem
# ---------------------------------------------------------------------------

_RE_FILENAME_SPLIT = re.compile(r'\s+-\s+')


def _parse_filename_meta(filepath: str) -> Tuple[str, str]:
    """
    Extract (artist, title) from filename stem using "Artist - Title" format.
    Returns ("", "") if the pattern is not found.
    """
    stem  = Path(filepath).stem
    parts = _RE_FILENAME_SPLIT.split(stem, maxsplit=1)
    if len(parts) == 2:
        artist, title = parts[0].strip(), parts[1].strip()
        if artist and title:
            return artist, title
    return "", ""


# ---------------------------------------------------------------------------
# Track resolution — validate, apply fallbacks, return None if unfixable
# ---------------------------------------------------------------------------

_UNKNOWN_ARTISTS_LOWER: frozenset = frozenset({
    "", "unknown", "unknown artist", "va", "various artists",
    "various", "n/a", "none", "-", "--",
})

_RE_VALID_CAM = re.compile(r'^(1[0-2]|[1-9])[AB]$', re.IGNORECASE)


def _is_missing_analysis(row) -> bool:
    """Return True if this row is missing BPM or Camelot key in the DB.

    Fast check — no I/O.  Used to count how many tracks need analysis
    before deciding whether to run it.
    """
    bpm = row["bpm"]
    key = (row["key_camelot"] or "").strip()
    return (not bpm) or (not key) or (not _RE_VALID_CAM.match(key))


def _resolve_row_for_export(row, recover: bool = False) -> Optional[dict]:
    """
    Validate and resolve one DB row for export.

    Always applied:
      artist/title  → filename parse fallback
      genre         → canonical alias / fuzzy map / "Other"

    Only applied when recover=True:
      BPM           → aubio analysis (result written back to DB)
      key           → keyfinder-cli analysis (result written back to DB)

    When recover=False (default) a track with missing BPM or key returns None
    immediately — no subprocess is spawned.

    Returns a plain dict with resolved values + internal flags:
      _norm_genre    — pre-computed canonical genre
      _recovered_bpm — True if BPM came from analysis this run
      _recovered_key — True if key came from analysis this run
    Returns None if the track cannot be fixed.
    """
    fp     = str(row["filepath"])
    artist = (row["artist"] or "").strip()
    title  = (row["title"]  or "").strip()
    bpm    = row["bpm"]
    key    = (row["key_camelot"] or "").strip()
    genre  = row["genre"]

    # --- artist / title: filename fallback (always, no I/O) ---
    if not title or not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER:
        fb_artist, fb_title = _parse_filename_meta(fp)
        if not title and fb_title:
            title = fb_title
        if (not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER) and fb_artist:
            artist = fb_artist

    if not title:
        return None
    if not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER:
        return None

    # --- BPM ---
    recovered_bpm = False
    if not bpm:
        if not recover:
            return None          # fast exit — no subprocess
        bpm = _try_detect_bpm(fp, genre or "")
        if bpm is None:
            return None          # analysis failed
        recovered_bpm = True
    else:
        try:
            bpm_f = float(bpm)
            if not (50.0 <= bpm_f <= 220.0):
                return None
        except (TypeError, ValueError):
            return None

    # --- Camelot key ---
    recovered_key = False
    if not key or not _RE_VALID_CAM.match(key):
        if not recover:
            return None          # fast exit — no subprocess
        _, camelot = _try_detect_key(fp)
        if not camelot:
            return None          # analysis failed
        key = camelot
        recovered_key = True

    # --- Genre: canonical normalization ---
    norm_genre = _normalize_genre_for_export(genre)
    if not norm_genre:           # empty = genuinely junk/missing
        return None

    resolved = dict(row)
    resolved["artist"]         = artist
    resolved["title"]          = title
    resolved["bpm"]            = bpm
    resolved["key_camelot"]    = key
    resolved["_norm_genre"]    = norm_genre
    resolved["_recovered_bpm"] = recovered_bpm
    resolved["_recovered_key"] = recovered_key
    return resolved


def _get_exclusion_reasons(row, recover_attempted: bool = False) -> List[str]:
    """
    Return human-readable reasons why a track is excluded from export.

    recover_attempted controls the wording for missing BPM/key:
      False — "missing BPM — run analyze-missing to fix"
      True  — "missing BPM — analysis attempted but failed"
    """
    fp     = str(row["filepath"])
    artist = (row["artist"] or "").strip()
    title  = (row["title"]  or "").strip()
    bpm    = row["bpm"]
    key    = (row["key_camelot"] or "").strip()
    genre  = row["genre"]

    if not title or not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER:
        fb_artist, fb_title = _parse_filename_meta(fp)
        if not title and fb_title:
            title = fb_title
        if (not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER) and fb_artist:
            artist = fb_artist

    issues: List[str] = []

    if not title:
        issues.append("missing title (filename fallback failed)")
    if not artist or artist.lower() in _UNKNOWN_ARTISTS_LOWER:
        issues.append(f"missing/unknown artist: '{artist}' (filename fallback failed)")

    if not bpm:
        if recover_attempted:
            issues.append("missing BPM — analysis attempted but failed (is aubio installed?)")
        else:
            issues.append("missing BPM — use --recover-missing-analysis or run analyze-missing")
    else:
        try:
            bpm_f = float(bpm)
            if not (50.0 <= bpm_f <= 220.0):
                issues.append(f"BPM out of range: {bpm_f:.1f}")
        except (TypeError, ValueError):
            issues.append(f"non-numeric BPM: '{bpm}'")

    if not key or not _RE_VALID_CAM.match(key):
        if recover_attempted:
            issues.append(
                f"missing/invalid Camelot key: '{key}' — "
                "analysis attempted but failed (is keyfinder-cli installed?)"
            )
        else:
            issues.append(
                f"missing/invalid Camelot key: '{key}' — "
                "use --recover-missing-analysis or run analyze-missing"
            )

    norm_genre = _normalize_genre_for_export(genre)
    if not norm_genre:
        issues.append(f"junk/missing genre: '{genre}'")

    return issues


# ---------------------------------------------------------------------------
# Pre-export resolution pass
# ---------------------------------------------------------------------------

def _resolve_tracks(
    all_tracks: list,
    recover: bool = False,
    recover_limit: Optional[int] = None,
    recover_timeout_sec: Optional[float] = None,
) -> Tuple[List[dict], List[Tuple[str, List[str]]], int, int, int]:
    """
    Resolve all DB rows for export in a single pass.

    Args:
        all_tracks          — raw DB rows
        recover             — if True, spawn aubio/keyfinder for missing BPM/key
        recover_limit       — max number of tracks to attempt analysis on
        recover_timeout_sec — stop attempting analysis after this many seconds

    Returns:
        valid               — resolved dicts ready for XML/M3U generation
        invalid             — list of (filepath, [reason, ...]) for excluded tracks
        recovered_bpm       — tracks where BPM was filled in by analysis
        recovered_key       — tracks where key was filled in by analysis
        needs_analysis      — tracks skipped only because BPM/key missing
                              (0 when recover=True, since we attempted them all)
    """
    valid:   List[dict]                   = []
    invalid: List[Tuple[str, List[str]]] = []
    recovered_bpm    = 0
    recovered_key    = 0
    needs_analysis   = 0

    # Analysis budget tracking (only meaningful when recover=True)
    budget_remaining = recover_limit          # None = unlimited
    deadline         = (time.monotonic() + recover_timeout_sec
                        if recover and recover_timeout_sec
                        else None)

    for row in all_tracks:
        fp              = str(row["filepath"])
        missing_now     = _is_missing_analysis(row)

        # Decide whether to attempt analysis for this specific row
        should_recover = recover and missing_now
        if should_recover:
            if budget_remaining is not None and budget_remaining <= 0:
                should_recover = False
                log.info(
                    "rekordbox-export: --recover-limit %d reached — "
                    "remaining tracks with missing analysis will be excluded",
                    recover_limit,
                )
                # Avoid repeating this message; set budget to sentinel
                budget_remaining = -1
            elif deadline is not None and time.monotonic() >= deadline:
                should_recover = False
                log.info(
                    "rekordbox-export: --recover-timeout-sec %.0f reached — "
                    "remaining tracks with missing analysis will be excluded",
                    recover_timeout_sec,
                )
                deadline = None   # suppress further timeout messages

        resolved = _resolve_row_for_export(row, recover=should_recover)

        if resolved is not None:
            if resolved.pop("_recovered_bpm", False):
                recovered_bpm += 1
            if resolved.pop("_recovered_key", False):
                recovered_key += 1
            # Consume one analysis slot if we actually ran analysis
            if should_recover and budget_remaining is not None and budget_remaining > 0:
                budget_remaining -= 1
            valid.append(resolved)
        else:
            # Track whether this exclusion was purely due to missing analysis
            if missing_now and not should_recover:
                needs_analysis += 1
            invalid.append((
                fp,
                _get_exclusion_reasons(row, recover_attempted=should_recover),
            ))

    return valid, invalid, recovered_bpm, recovered_key, needs_analysis


def _write_invalid_log(
    invalid: List[Tuple[str, List[str]]],
    path: Path,
    total_scanned: int,
    dry_run: bool = False,
) -> None:
    """Write the invalid-tracks exclusion log to disk."""
    if not invalid:
        return

    if dry_run:
        for fp, reasons in invalid[:20]:
            log.info("  [EXCLUDED] %s: %s", Path(fp).name, "; ".join(reasons))
        if len(invalid) > 20:
            log.info("  ... and %d more excluded (run without --dry-run for full log)",
                     len(invalid) - 20)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "Rekordbox Export — Excluded Tracks",
        f"Generated : {now}",
        f"Scanned   : {total_scanned}",
        f"Excluded  : {len(invalid)}",
        f"Exported  : {total_scanned - len(invalid)}",
        "",
    ]
    for fp, reasons in invalid:
        lines.append(f"  {Path(fp).name}")
        for r in reasons:
            lines.append(f"    - {r}")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info("rekordbox-export: %d tracks excluded → %s", len(invalid), path.name)


# ---------------------------------------------------------------------------
# Path conversion — Linux → Windows
# ---------------------------------------------------------------------------

def _to_windows_location(linux_path: str) -> str:
    """
    Convert a Linux absolute path to a Rekordbox XML Location attribute.

    /mnt/music_ssd/KKDJ/library/sorted/A/ATFC/track.mp3
    → file://localhost/M:/KKDJ/library/sorted/A/ATFC/track.mp3
    """
    drive      = getattr(config, "RB_WINDOWS_DRIVE", "M")
    linux_root = Path(getattr(config, "RB_LINUX_ROOT", "/mnt/music_ssd"))
    try:
        rel = Path(linux_path).relative_to(linux_root)
    except ValueError:
        log.debug("rekordbox-export: path not under RB_LINUX_ROOT: %s", linux_path)
        rel = Path(linux_path)
    encoded_parts = [quote(part, safe="") for part in rel.parts]
    return f"file://localhost/{drive}:/" + "/".join(encoded_parts)


def _to_windows_path(linux_path: str) -> str:
    """
    Convert a Linux absolute path to a Windows-absolute path for M3U files.

    /mnt/music_ssd/KKDJ/library/sorted/A/ATFC/track.mp3
    → M:/KKDJ/library/sorted/A/ATFC/track.mp3
    """
    drive      = getattr(config, "RB_WINDOWS_DRIVE", "M")
    linux_root = Path(getattr(config, "RB_LINUX_ROOT", "/mnt/music_ssd"))
    try:
        rel = Path(linux_path).relative_to(linux_root)
    except ValueError:
        rel = Path(linux_path)
    return f"{drive}:/" + "/".join(rel.parts)


# ---------------------------------------------------------------------------
# XML helpers
# ---------------------------------------------------------------------------

def _xe(s) -> str:
    """XML-escape a value."""
    return html.escape(str(s or ""), quote=True)


def _fmt_bpm(bpm) -> str:
    try:
        return f"{float(bpm):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_dur(dur) -> str:
    try:
        return str(int(float(dur or 0)))
    except (TypeError, ValueError):
        return "0"


def _leaf_node(name: str, tids: List[int], indent: str) -> str:
    refs = "\n".join(f'{indent}    <TRACK Key="{t}"/>' for t in tids)
    return (
        f'{indent}<NODE Name="{_xe(name)}" Type="1" KeyType="0" Entries="{len(tids)}">\n'
        f'{refs}\n'
        f'{indent}</NODE>'
    )


# ---------------------------------------------------------------------------
# M3U export (Windows-absolute paths)
# ---------------------------------------------------------------------------

def _write_rb_m3u(playlist_path: Path, tracks: list, dry_run: bool) -> int:
    """Write a single M3U8 playlist with Windows-absolute paths."""
    if not tracks:
        return 0
    if dry_run:
        log.info("[DRY-RUN] Would write %s (%d tracks)", playlist_path.name, len(tracks))
        return len(tracks)

    playlist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(playlist_path, "w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for row in tracks:
            artist = row["artist"] or "Unknown"
            title  = row["title"]  or Path(row["filepath"]).stem
            dur    = int(row["duration_sec"] or -1)
            win    = _to_windows_path(row["filepath"])
            fh.write(f"#EXTINF:{dur},{artist} - {title}\n")
            fh.write(f"{win}\n")
    return len(tracks)


def export_m3u_playlists(tracks: list, dry_run: bool = False) -> Dict[str, int]:
    """
    Export all playlist types to _PLAYLISTS_M3U_EXPORT/ with Windows-absolute paths.

    Args:
        tracks:  Pre-resolved, pre-filtered list of track dicts.
        dry_run: Preview only — create no files.

    Returns a dict of {playlist_type: track_count}.
    """
    if not tracks:
        log.info("rekordbox-export M3U: no valid tracks to export")
        return {}

    base       = config.REKORDBOX_M3U_EXPORT_DIR
    min_tracks = getattr(config, "PLAYLIST_MIN_TRACKS", 2)
    totals: Dict[str, int] = {}

    # --- Genre ---
    by_genre: Dict[str, list] = {}
    for row in tracks:
        genre = row.get("_norm_genre") or _normalize_genre_for_export(row["genre"])
        if not genre or _is_junk_genre(genre):
            genre = "_Unknown Genre"
        by_genre.setdefault(genre, []).append(row)

    genre_dir = base / "Genre"
    n_genre = 0
    for gname, gtracks in sorted(by_genre.items()):
        if len(gtracks) < min_tracks and gname != "_Unknown Genre":
            continue
        safe = _RE_UNSAFE_FILENAME.sub("", gname).strip() or "_Unknown"
        n = _write_rb_m3u(genre_dir / f"{safe}.m3u8", gtracks, dry_run)
        n_genre += n
    totals["genre"] = n_genre
    log.info("rekordbox-export: Genre M3U — %d tracks across %d playlists",
             n_genre, len(by_genre))

    # --- Energy ---
    by_energy: Dict[str, list] = {level: [] for level in _ENERGY_LEVELS}
    for row in tracks:
        by_energy[_classify_energy(row["bpm"], row["genre"])].append(row)

    energy_dir = base / "Energy"
    n_energy = 0
    for level in _ENERGY_LEVELS:
        etracks = by_energy[level]
        if not etracks:
            continue
        n = _write_rb_m3u(energy_dir / f"{level}.m3u8", etracks, dry_run)
        n_energy += n
    totals["energy"] = n_energy

    # --- Combined (genre + energy) ---
    combined: Dict[Tuple[str, str], list] = {}
    for row in tracks:
        g = row.get("_norm_genre") or _normalize_genre_for_export(row["genre"])
        if not g or _is_junk_genre(g) or g == "Other":
            continue
        e = _classify_energy(row["bpm"], row["genre"])
        combined.setdefault((g, e), []).append(row)

    combined_dir = base / "Combined"
    n_combined = 0
    for (gname, energy), ctracks in sorted(combined.items()):
        if len(ctracks) < min_tracks:
            continue
        name = f"{energy} {gname}"
        safe = _RE_UNSAFE_FILENAME.sub("", name).strip() or "_Combined"
        n = _write_rb_m3u(combined_dir / f"{safe}.m3u8", ctracks, dry_run)
        n_combined += n
    totals["combined"] = n_combined

    # --- Key ---
    by_key: Dict[str, list] = {}
    for row in tracks:
        key = (row["key_camelot"] or "").strip().upper()
        if key and _RE_VALID_CAMELOT.match(key):
            by_key.setdefault(key, []).append(row)

    key_dir = base / "Key"
    n_key = 0
    for kname in sorted(by_key, key=_camelot_sort_key):
        ktracks = by_key[kname]
        if len(ktracks) < min_tracks:
            continue
        n = _write_rb_m3u(key_dir / f"{kname}.m3u8", ktracks, dry_run)
        n_key += n
    totals["key"] = n_key

    # --- Route ---
    by_route: Dict[str, list] = {}
    for row in tracks:
        route = _classify_route(
            row["filepath"], genre=row["genre"] or "", title=row["title"] or ""
        )
        if route:
            by_route.setdefault(route, []).append(row)

    route_dir    = base / "Route"
    _ROUTE_ORDER = ("Acapella", "Tool", "Vocal")
    ordered      = list(_ROUTE_ORDER) + sorted(r for r in by_route if r not in _ROUTE_ORDER)
    n_route = 0
    for rname in ordered:
        rtracks = by_route.get(rname)
        if not rtracks or len(rtracks) < min_tracks:
            continue
        safe = _RE_UNSAFE_FILENAME.sub("", rname).strip() or "_Route"
        n = _write_rb_m3u(route_dir / f"{safe}.m3u8", rtracks, dry_run)
        n_route += n
    totals["route"] = n_route

    log.info(
        "rekordbox-export: M3U — genre=%d energy=%d combined=%d key=%d route=%d",
        n_genre, n_energy, n_combined, n_key, n_route,
    )
    return totals


# ---------------------------------------------------------------------------
# XML export
# ---------------------------------------------------------------------------

def export_xml(tracks: list, dry_run: bool = False) -> Tuple[Path, int]:
    """
    Generate a Rekordbox-importable XML file with Windows M: drive paths.

    Args:
        tracks:  Pre-resolved, pre-filtered list of track dicts.
        dry_run: Preview only — create no files.

    Returns:
        (output_path, track_count)
    """
    out_dir     = config.REKORDBOX_XML_EXPORT_DIR
    output_path = out_dir / "rekordbox_library.xml"

    if dry_run:
        log.info("[DRY-RUN] Would write Rekordbox XML with %d tracks → %s",
                 len(tracks), output_path)
        return output_path, len(tracks)

    out_dir.mkdir(parents=True, exist_ok=True)

    min_tracks   = getattr(config, "PLAYLIST_MIN_TRACKS", 2)
    today        = date.today().isoformat()

    track_entries:  List[str]                        = []
    genre_nodes:    Dict[str, List[int]]             = {}
    energy_nodes:   Dict[str, List[int]]             = {}
    combined_nodes: Dict[Tuple[str, str], List[int]] = {}
    key_nodes:      Dict[str, List[int]]             = {}
    route_nodes:    Dict[str, List[int]]             = {}
    all_tids:       List[int]                        = []
    track_id = 1

    for row in tracks:
        linux_path = str(row["filepath"])
        location   = _xe(_to_windows_location(linux_path))
        name       = _xe(row["title"]  or Path(linux_path).stem)
        artist     = _xe(row["artist"] or "")
        raw_genre  = row["genre"] or ""
        norm_genre = row.get("_norm_genre") or _normalize_genre_for_export(raw_genre)
        genre_attr = _xe(norm_genre or raw_genre)
        bpm        = _fmt_bpm(row["bpm"])
        key        = _xe(row["key_camelot"] or "")
        comment    = _xe(_build_comment(row))
        total_time = _fmt_dur(row["duration_sec"])
        bitrate    = str(row["bitrate_kbps"] or 0)
        kind       = _kind_from_path(linux_path)
        size       = str(row["filesize_bytes"] or 0)
        label      = _xe(_read_label_from_file(linux_path))

        tempo_node = (
            f'            <TEMPO Inizio="0.000" Bpm="{bpm}" Metro="4/4" Battito="1"/>'
        )
        track_entries.append(
            f'        <TRACK TrackID="{track_id}"'
            f' Name="{name}"'
            f' Artist="{artist}"'
            f' Composer=""'
            f' Album=""'
            f' Grouping=""'
            f' Genre="{genre_attr}"'
            f' Kind="{kind}"'
            f' Size="{size}"'
            f' TotalTime="{total_time}"'
            f' DiscNumber="0"'
            f' TrackNumber="0"'
            f' Year=""'
            f' AverageBpm="{bpm}"'
            f' DateAdded="{today}"'
            f' BitRate="{bitrate}"'
            f' SampleRate="44100"'
            f' Comments="{comment}"'
            f' PlayCount="0"'
            f' Rating="0"'
            f' Location="{location}"'
            f' Remixer=""'
            f' Tonality="{key}"'
            f' Label="{label}"'
            f' Mix="">\n'
            f'{tempo_node}\n'
            f'        </TRACK>'
        )
        all_tids.append(track_id)

        # Genre playlists — exclude "Other" from genre folder (too broad)
        if norm_genre and not _is_junk_genre(norm_genre) and norm_genre != "Other":
            genre_nodes.setdefault(norm_genre, []).append(track_id)

        # Energy
        energy = _classify_energy(row["bpm"], raw_genre)
        energy_nodes.setdefault(energy, []).append(track_id)

        # Combined — exclude "Other" from combined folder
        if norm_genre and not _is_junk_genre(norm_genre) and norm_genre != "Other":
            combined_nodes.setdefault((norm_genre, energy), []).append(track_id)

        # Key
        camelot = (row["key_camelot"] or "").strip().upper()
        if camelot and _RE_VALID_CAMELOT.match(camelot):
            key_nodes.setdefault(camelot, []).append(track_id)

        # Route
        route = _classify_route(linux_path, genre=raw_genre, title=row["title"] or "")
        if route:
            route_nodes.setdefault(route, []).append(track_id)

        track_id += 1

    collection_count = track_id - 1

    # All Tracks node
    all_refs = "\n".join(f'                <TRACK Key="{t}"/>' for t in all_tids)
    all_tracks_node = (
        f'            <NODE Name="All Tracks" Type="1" KeyType="0"'
        f' Entries="{collection_count}">\n'
        f'{all_refs}\n'
        f'            </NODE>'
    )

    def _folder(name, parts):
        if not parts:
            return ""
        return (
            f'            <NODE Type="0" Name="{name}" Count="{len(parts)}">\n'
            + "\n".join(parts) + "\n"
            + '            </NODE>'
        )

    genre_parts = [
        _leaf_node(gname, tids, "                ")
        for gname, tids in sorted(genre_nodes.items())
        if len(tids) >= min_tracks
    ]
    energy_parts = [
        _leaf_node(level, energy_nodes[level], "                ")
        for level in _ENERGY_LEVELS
        if energy_nodes.get(level)
    ]
    combined_parts = [
        _leaf_node(f"{energy} {gname}", tids, "                ")
        for (gname, energy), tids in sorted(combined_nodes.items())
        if len(tids) >= min_tracks
    ]
    key_parts = [
        _leaf_node(k, key_nodes[k], "                ")
        for k in sorted(key_nodes, key=_camelot_sort_key)
        if len(key_nodes[k]) >= min_tracks
    ]
    _ROUTE_ORDER = ("Acapella", "Tool", "Vocal")
    route_order  = list(_ROUTE_ORDER) + sorted(r for r in route_nodes if r not in _ROUTE_ORDER)
    route_parts  = [
        _leaf_node(rname, route_nodes[rname], "                ")
        for rname in route_order
        if rname in route_nodes and len(route_nodes[rname]) >= min_tracks
    ]

    folder_nodes = [n for n in [
        _folder("Genre",    genre_parts),
        _folder("Energy",   energy_parts),
        _folder("Combined", combined_parts),
        _folder("Key",      key_parts),
        _folder("Route",    route_parts),
    ] if n]

    playlist_xml = [all_tracks_node] + folder_nodes
    root_count   = 1 + len(folder_nodes)

    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<DJ_PLAYLISTS Version="1.0.0">\n'
        '    <PRODUCT Name="rekordbox" Version="6.0.0" Company="Pioneer DJ"/>\n'
        f'    <COLLECTION Entries="{collection_count}">\n'
        + "\n".join(track_entries) + "\n"
        + '    </COLLECTION>\n'
        + '    <PLAYLISTS>\n'
        + f'        <NODE Type="0" Name="ROOT" Count="{root_count}">\n'
        + "\n".join(playlist_xml) + "\n"
        + '        </NODE>\n'
        + '    </PLAYLISTS>\n'
        + '</DJ_PLAYLISTS>\n'
    )

    with open(output_path, "w", encoding="utf-8") as fh:
        fh.write(xml)

    log.info(
        "rekordbox-export: XML → %s  (%d tracks, %d genre playlists, "
        "%d combined, %d key, %d route)",
        output_path.name, collection_count,
        len(genre_parts), len(combined_parts), len(key_parts), len(route_parts),
    )
    return output_path, collection_count


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    dry_run:              bool            = False,
    skip_xml:             bool            = False,
    skip_m3u:             bool            = False,
    recover_missing:      bool            = False,
    recover_limit:        Optional[int]   = None,
    recover_timeout_sec:  Optional[float] = None,
) -> int:
    """
    Run the full Rekordbox export profile.

    Args:
        dry_run:             Preview only — create no files.
        skip_xml:            Skip Rekordbox XML generation.
        skip_m3u:            Skip M3U playlist generation.
        recover_missing:     If True, run aubio/keyfinder on tracks missing
                             BPM or Camelot key before deciding to exclude them.
                             Off by default — export stays fast and predictable.
                             Use --recover-missing-analysis flag to enable.
        recover_limit:       Maximum number of tracks to attempt analysis on
                             (only used when recover_missing=True).
        recover_timeout_sec: Stop analysis after this many seconds
                             (only used when recover_missing=True).

    Returns:
        0 on success.
    """
    drive      = getattr(config, "RB_WINDOWS_DRIVE", "M")
    linux_root = getattr(config, "RB_LINUX_ROOT", "/mnt/music_ssd")
    log.info(
        "rekordbox-export: mapping %s → %s:\\ (dry_run=%s)",
        linux_root, drive, dry_run,
    )
    log_action(
        f"REKORDBOX-EXPORT {'DRY-RUN' if dry_run else 'START'}: "
        f"Linux root={linux_root}  Windows drive={drive}:"
    )

    if recover_missing:
        log.info(
            "rekordbox-export: analysis recovery ENABLED "
            "(limit=%s, timeout=%ss)",
            recover_limit or "unlimited",
            recover_timeout_sec or "none",
        )
    else:
        log.info(
            "rekordbox-export: analysis recovery DISABLED "
            "(pass --recover-missing-analysis to enable)"
        )

    # --- Fetch all tracks ---
    all_tracks    = db.get_all_ok_tracks()
    total_scanned = len(all_tracks)

    # Count distinct raw genres BEFORE normalization (for summary)
    raw_genre_set = {
        (row["genre"] or "").strip()
        for row in all_tracks
        if (row["genre"] or "").strip()
    }

    # --- Resolve: validate, apply fallbacks, filter ---
    valid_tracks, invalid_tracks, recovered_bpm, recovered_key, needs_analysis = (
        _resolve_tracks(
            all_tracks,
            recover=recover_missing,
            recover_limit=recover_limit,
            recover_timeout_sec=recover_timeout_sec,
        )
    )
    valid_count   = len(valid_tracks)
    invalid_count = len(invalid_tracks)

    # Count genre distribution after normalization
    export_genre_counts: Dict[str, int] = {}
    for row in valid_tracks:
        g = row.get("_norm_genre") or _normalize_genre_for_export(row.get("genre"))
        if g:
            export_genre_counts[g] = export_genre_counts.get(g, 0) + 1

    # --- Write invalid-tracks log ---
    invalid_log_path = config.LOGS_DIR / "rekordbox_export" / "invalid_tracks.txt"
    _write_invalid_log(invalid_tracks, invalid_log_path, total_scanned, dry_run)

    # --- XML ---
    xml_path    = config.REKORDBOX_XML_EXPORT_DIR / "rekordbox_library.xml"
    track_count = 0
    if not skip_xml:
        xml_path, track_count = export_xml(valid_tracks, dry_run)

    # --- M3U ---
    m3u_totals: Dict[str, int] = {}
    if not skip_m3u:
        m3u_totals = export_m3u_playlists(valid_tracks, dry_run)

    # --- Summary ---
    excl_pct  = 100.0 * invalid_count / max(total_scanned, 1)
    total_m3u = sum(m3u_totals.values())

    print()
    print(f"=== Rekordbox Export {'(DRY-RUN) ' if dry_run else ''}===")
    print(f"  Drive mapping   : {linux_root}  →  {drive}:\\")
    print(f"  Tracks scanned  : {total_scanned}")
    print(f"  Valid exported  : {valid_count}")
    print(f"  Excluded        : {invalid_count}  ({excl_pct:.1f}%)"
          + ("" if dry_run else "  → logs/rekordbox_export/invalid_tracks.txt"))

    if needs_analysis:
        print(f"  Needs analysis  : {needs_analysis} tracks excluded due to missing BPM/key")
        print( "                    → run analyze-missing first:")
        print( "                      python3 pipeline.py analyze-missing "
               "--path /mnt/music_ssd/KKDJ/")
        print( "                    → or add --recover-missing-analysis to this command")
        if recover_limit or recover_timeout_sec:
            parts = []
            if recover_limit:
                parts.append(f"--recover-limit {recover_limit}")
            if recover_timeout_sec:
                parts.append(f"--recover-timeout-sec {recover_timeout_sec:.0f}")
            print(f"                      (active limits: {', '.join(parts)})")

    if recovered_bpm or recovered_key:
        print(f"  BPM recovered   : {recovered_bpm} tracks (inline analysis)")
        print(f"  Key recovered   : {recovered_key} tracks (inline analysis)")

    print()
    print(f"  Genres (raw)    : {len(raw_genre_set)} distinct values")
    print(f"  Genres (export) : {len(export_genre_counts)} after normalization")
    if export_genre_counts:
        print("  Genre breakdown :")
        for gname, cnt in sorted(export_genre_counts.items(), key=lambda x: -x[1]):
            print(f"    {gname:<22}: {cnt}")
    print()
    if not skip_xml:
        print(f"  XML             : {config.REKORDBOX_XML_EXPORT_DIR.name}/"
              f"rekordbox_library.xml  ({track_count} tracks)")
    if not skip_m3u:
        print(f"  M3U playlists   : {config.REKORDBOX_M3U_EXPORT_DIR.name}/")
        for ptype, cnt in m3u_totals.items():
            print(f"    {ptype:<14}: {cnt} tracks")
        print(f"  M3U total       : {total_m3u} track entries")
    print()

    log_action(
        f"REKORDBOX-EXPORT {'DRY-RUN' if dry_run else 'DONE'}: "
        f"{valid_count}/{total_scanned} exported, {invalid_count} excluded "
        f"({excl_pct:.1f}%), {needs_analysis} need analysis, "
        f"{len(export_genre_counts)} genres, "
        f"BPM+key recovered inline: {recovered_bpm}+{recovered_key}"
    )
    return 0
