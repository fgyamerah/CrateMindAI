"""
modules/library_dedupe.py

Standalone duplicate detection and cleanup for the full sorted library.

Detection strategy (in order of certainty):
  Case A — Exact duplicate    : SHA-256 hash matches
                                → keep one, quarantine the rest (safe to auto-apply)
  Case B — Quality duplicate  : same artist + base title + version, different format or bitrate
                                → keep highest quality, quarantine rest (safe to auto-apply)
  Case C — Different versions : same base title, different version string
                                → keep all; reported for information only, never auto-removed

Quality priority (highest score first):
  WAV / AIFF  (100)
  FLAC        (90)
  MP3 ≥ 320   (80)
  MP3 ≥ 256   (70)
  M4A / AAC   (70)
  MP3 ≥ 192   (60)
  OGG / OPUS  (60)
  MP3 ≥ 128   (50)
  MP3 < 128   (30)

Safety rules:
  - Case A is always safe (byte-identical content).
  - Case B is safe only when quality scores differ unambiguously.
  - Case C is NEVER touched automatically — only reported.
  - Any group where "keep" cannot be determined with confidence → skip + log.
  - Dry-run never touches files.
  - Apply mode moves files to quarantine dir (never deletes outright).
"""
import hashlib
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from modules.textlog import log_action

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Quality scoring
# ---------------------------------------------------------------------------

_FORMAT_BASE_SCORE: Dict[str, int] = {
    ".wav":  100,
    ".aiff": 100,
    ".aif":  100,
    ".flac": 90,
    ".m4a":  70,   # AAC, generally good quality
    ".ogg":  60,
    ".opus": 60,
    ".mp3":  0,    # refined by bitrate below
}


def _quality_score(path: Path, bitrate_kbps: int) -> int:
    """Return a numeric quality score. Higher = better."""
    suffix = path.suffix.lower()
    base   = _FORMAT_BASE_SCORE.get(suffix, 50)

    if suffix == ".mp3":
        if bitrate_kbps >= 320:
            base = 80
        elif bitrate_kbps >= 256:
            base = 70
        elif bitrate_kbps >= 192:
            base = 60
        elif bitrate_kbps >= 128:
            base = 50
        else:
            base = 30

    return base


# ---------------------------------------------------------------------------
# Version keyword detection
# ---------------------------------------------------------------------------

_VERSION_KEYWORDS = frozenset({
    "remix", "remixed", "edit", "edited", "extended", "mix", "version",
    "vip", "dub", "instrumental", "acapella", "a cappella", "radio",
    "club", "original", "reprise", "rework", "bootleg", "mashup", "flip",
    "remaster", "remastered", "live", "acoustic", "unplugged", "stripped",
    "demo", "session", "intro", "outro", "short",
})

# Match content inside parentheses/brackets or after a dash at end of title
_RE_PAREN_VERSION = re.compile(
    r'^(.*?)\s*[\(\[]\s*(.+?)\s*[\)\]]\s*$', re.IGNORECASE
)
_RE_DASH_VERSION = re.compile(
    r'^(.*?)\s*[-–]\s*(.+)$', re.IGNORECASE
)


def _extract_version(title: str) -> Tuple[str, str]:
    """
    Split a title into (base_title, version_string).

    Only splits when the candidate version contains a known version keyword,
    so feat./featuring credits and similar are left as part of the base title.

    "Dark Days (Original Mix)"  → ("Dark Days", "Original Mix")
    "Dark Days - Extended Mix"  → ("Dark Days", "Extended Mix")
    "Dark Days"                 → ("Dark Days", "")
    "Dark Days (1)"             → ("Dark Days (1)", "")   # no version keyword; caught by hash
    "Some Song (feat. Artist)"  → ("Some Song (feat. Artist)", "")  # not a version keyword
    """
    # Parenthesis/bracket form — only split if the content contains a version keyword
    m = _RE_PAREN_VERSION.match(title)
    if m:
        candidate_ver = m.group(2).strip()
        if any(kw in candidate_ver.lower() for kw in _VERSION_KEYWORDS):
            return m.group(1).strip(), candidate_ver

    # Dash form — only split if the second part contains a version keyword
    m = _RE_DASH_VERSION.match(title)
    if m:
        candidate = m.group(2).strip()
        if any(kw in candidate.lower() for kw in _VERSION_KEYWORDS):
            return m.group(1).strip(), candidate

    return title, ""


def _normalize(s: str) -> str:
    """Lowercase + collapse whitespace + strip for stable comparison."""
    return re.sub(r"\s+", " ", s.strip().lower())


def _is_version_variant(title_a: str, title_b: str) -> bool:
    """
    Return True when two titles represent different versions of the same track
    (same base, different version strings that each contain a version keyword).
    """
    base_a, ver_a = _extract_version(title_a)
    base_b, ver_b = _extract_version(title_b)

    if _normalize(base_a) != _normalize(base_b):
        return False   # different base titles entirely

    # Both have version strings with meaningful keywords → different versions
    ver_a_lower = ver_a.lower()
    ver_b_lower = ver_b.lower()

    a_has_kw = any(kw in ver_a_lower for kw in _VERSION_KEYWORDS)
    b_has_kw = any(kw in ver_b_lower for kw in _VERSION_KEYWORDS)

    return (a_has_kw or b_has_kw) and _normalize(ver_a) != _normalize(ver_b)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FileInfo:
    path:         Path
    size:         int       # bytes
    sha256:       str       # hex
    duration_sec: float
    bitrate_kbps: int
    quality:      int       # from _quality_score()
    title:        str       # from tags
    artist:       str       # from tags
    base_title:   str       # title with version stripped
    version:      str       # version string only

    @property
    def size_mb(self) -> float:
        return self.size / (1024 * 1024)

    @property
    def fmt(self) -> str:
        return self.path.suffix.upper().lstrip(".")


@dataclass
class DupeGroup:
    group_type: str             # "exact", "quality", "versions"
    keep:       FileInfo
    remove:     List[FileInfo]  # files that will be quarantined
    report:     List[FileInfo]  # files reported but not touched (Case C)
    reason:     str


# ---------------------------------------------------------------------------
# File reading
# ---------------------------------------------------------------------------

def _hash_file(path: Path) -> str:
    """Return hex SHA-256 digest of the file content."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):  # 1 MB chunks
            h.update(chunk)
    return h.hexdigest()


def _read_file_info(path: Path) -> Optional[FileInfo]:
    """
    Read metadata from an audio file using mutagen.
    Returns None if the file cannot be read.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return None

        size         = path.stat().st_size
        duration_sec = getattr(audio.info, "length",   0.0) or 0.0
        bitrate_kbps = int(getattr(audio.info, "bitrate", 0) or 0) // 1000

        title  = (audio.get("title")  or [""])[0].strip()
        artist = (audio.get("artist") or [""])[0].strip()

        # Fallback: parse from filename if tags are empty
        if not title:
            stem  = path.stem
            parts = stem.split(" - ", 1)
            title = parts[-1].strip() if len(parts) > 1 else stem

        base_title, version = _extract_version(title)

        sha256 = _hash_file(path)

        return FileInfo(
            path         = path,
            size         = size,
            sha256       = sha256,
            duration_sec = duration_sec,
            bitrate_kbps = bitrate_kbps,
            quality      = _quality_score(path, bitrate_kbps),
            title        = title,
            artist       = artist,
            base_title   = base_title,
            version      = version,
        )

    except Exception as exc:
        log.debug("Could not read %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Group building
# ---------------------------------------------------------------------------

def _build_groups(infos: List[FileInfo]) -> List[DupeGroup]:
    """
    Analyse FileInfo objects and return duplicate groups.

    Pass 1 — hash grouping     → Case A (exact duplicates)
    Pass 2 — title grouping    → Case B (quality) or Case C (versions)
    """
    groups:    List[DupeGroup] = []
    used_hashes: Dict[str, FileInfo] = {}  # sha256 → first seen
    remaining: List[FileInfo] = []

    # ----- Pass 1: exact hash matches (Case A) -----
    hash_bins: Dict[str, List[FileInfo]] = {}
    for info in infos:
        hash_bins.setdefault(info.sha256, []).append(info)

    for sha, group_infos in hash_bins.items():
        if len(group_infos) == 1:
            remaining.append(group_infos[0])
            continue

        # Keep the highest-quality file; tie-break on larger size then path
        keep   = max(group_infos, key=lambda i: (i.quality, i.size, str(i.path)))
        remove = [i for i in group_infos if i is not keep]

        groups.append(DupeGroup(
            group_type = "exact",
            keep       = keep,
            remove     = remove,
            report     = [],
            reason     = f"byte-identical (SHA-256: {sha[:12]}…)",
        ))
        log.debug("Case A: %d exact copies — keeping %s", len(group_infos), keep.path.name)

        # The kept file re-enters Pass 2 so lower-quality format siblings
        # (different hash, same title) can still be caught as Case B.
        remaining.append(keep)

    # ----- Pass 2: title-based grouping (Case B / C) -----
    # Key: (normalized_artist, normalized_base_title, normalized_version)
    title_bins: Dict[Tuple[str, str, str], List[FileInfo]] = {}
    for info in remaining:
        key = (
            _normalize(info.artist),
            _normalize(info.base_title),
            _normalize(info.version),
        )
        title_bins.setdefault(key, []).append(info)

    # Collect all bins that have duplicates for Case B consideration
    # Also collect files by (artist, base_title) to detect Case C
    base_bins: Dict[Tuple[str, str], List[FileInfo]] = {}
    for info in remaining:
        key = (_normalize(info.artist), _normalize(info.base_title))
        base_bins.setdefault(key, []).append(info)

    processed_paths: set = set()

    # Duration tolerance for Case B: files with matching title but durations
    # more than this many seconds apart are different tracks, not quality
    # variants of the same track.
    _DURATION_GUARD_SEC = 5.0

    for (artist_n, base_n, ver_n), bin_infos in title_bins.items():
        if len(bin_infos) <= 1:
            continue  # no duplicates in this group

        if any(str(i.path) in processed_paths for i in bin_infos):
            continue

        # Duration guard — skip if files have materially different durations.
        # Same track re-encoded in different formats should have matching duration;
        # a large spread indicates these are actually different tracks with the
        # same base title.
        if len(bin_infos) > 1:
            durations = [i.duration_sec for i in bin_infos if i.duration_sec > 0]
            if durations and (max(durations) - min(durations)) > _DURATION_GUARD_SEC:
                log.info(
                    "SKIP (duration mismatch): %s — duration spread %.1fs exceeds %.1fs "
                    "threshold; likely different tracks with matching title",
                    [i.path.name for i in bin_infos],
                    max(durations) - min(durations),
                    _DURATION_GUARD_SEC,
                )
                continue

        # All files in this bin have the same (artist, base_title, version)
        # and similar duration → quality comparison (Case B)
        keep   = max(bin_infos, key=lambda i: (i.quality, i.size, str(i.path)))
        remove = [i for i in bin_infos if i is not keep]

        # Safety: only remove if quality difference is unambiguous
        # (i.e., keep.quality > ALL remove[i].quality)
        min_remove_quality = min(i.quality for i in remove)
        if keep.quality <= min_remove_quality:
            # Tie in quality score — cannot determine which to keep safely
            log.info(
                "SKIP (ambiguous quality): %s vs %s — manual review needed",
                keep.path.name, [r.path.name for r in remove],
            )
            continue

        quality_str = ", ".join(
            f"{r.path.name} ({r.fmt} {r.bitrate_kbps}kbps q={r.quality})"
            for r in remove
        )
        groups.append(DupeGroup(
            group_type = "quality",
            keep       = keep,
            remove     = remove,
            report     = [],
            reason     = (
                f"same track, lower quality: {quality_str} "
                f"→ keeping {keep.fmt} (q={keep.quality})"
            ),
        ))
        for i in bin_infos:
            processed_paths.add(str(i.path))
        log.debug("Case B: %d quality variants — keeping %s", len(bin_infos), keep.path.name)

    # ----- Detect Case C: different versions of the same base track -----
    for (artist_n, base_n), bin_infos in base_bins.items():
        unique_versions = {_normalize(i.version) for i in bin_infos}
        if len(unique_versions) <= 1:
            continue  # same version, already handled above
        # Multiple distinct version strings → different versions → report only
        # Only report files not already covered by Case A or B
        not_handled = [
            i for i in bin_infos if str(i.path) not in processed_paths
        ]
        if len(not_handled) > 1:
            # Use first as "keep" (placeholder — nothing is removed)
            sorted_by_quality = sorted(not_handled, key=lambda i: (-i.quality, str(i.path)))
            version_list = ", ".join(
                f'"{i.title}"' for i in not_handled
            )
            groups.append(DupeGroup(
                group_type = "versions",
                keep       = sorted_by_quality[0],  # informational only
                remove     = [],
                report     = not_handled,
                reason     = f"different versions — keeping all: {version_list}",
            ))
            log.debug(
                "Case C: %d versions of '%s' — no action",
                len(not_handled), bin_infos[0].base_title,
            )

    return groups


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------

def scan_library(paths: List[Path]) -> Tuple[int, List[DupeGroup]]:
    """
    Read metadata from all paths and build duplicate groups.
    Returns (files_scanned, groups).
    """
    infos: List[FileInfo] = []

    for path in paths:
        if not path.exists():
            continue
        info = _read_file_info(path)
        if info is not None:
            infos.append(info)
        else:
            log.debug("Skipped unreadable file: %s", path)

    log.info("Dedupe scan: read %d of %d file(s)", len(infos), len(paths))
    groups = _build_groups(infos)
    return len(infos), groups


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------

def print_dry_run_summary(scanned: int, groups: List[DupeGroup]) -> None:
    """Print a structured preview of what would be done."""
    exact    = [g for g in groups if g.group_type == "exact"]
    quality  = [g for g in groups if g.group_type == "quality"]
    versions = [g for g in groups if g.group_type == "versions"]

    total_remove = sum(len(g.remove) for g in exact + quality)
    space_saved  = sum(
        sum(r.size for r in g.remove) for g in exact + quality
    )

    print(f"\n=== dedupe DRY RUN — {scanned} file(s) scanned ===\n")
    print(f"  Exact duplicates (Case A) : {len(exact)} group(s)")
    print(f"  Quality duplicates (Case B): {len(quality)} group(s)")
    print(f"  Different versions (Case C): {len(versions)} group(s)  [never removed]")
    print(f"\n  Files that would be quarantined : {total_remove}")
    print(f"  Space that would be freed       : {space_saved / (1024*1024):.1f} MB")

    if not groups:
        print("\n  No duplicates found.")
        return

    if exact:
        print(f"\n── Case A: Exact Duplicates ──────────────────────────────────")
        for g in exact:
            dur = f"{g.keep.duration_sec:.0f}s" if g.keep.duration_sec else "?s"
            print(f"\n  KEEP   {g.keep.path.name}  ({g.keep.fmt}, {g.keep.size_mb:.1f} MB, {dur})")
            for r in g.remove:
                rdur = f"{r.duration_sec:.0f}s" if r.duration_sec else "?s"
                print(f"  REMOVE {r.path.name}  ({r.fmt}, {r.size_mb:.1f} MB, {rdur})")
            print(f"  Reason: {g.reason}")

    if quality:
        print(f"\n── Case B: Quality Duplicates ────────────────────────────────")
        for g in quality:
            dur = f"{g.keep.duration_sec:.0f}s" if g.keep.duration_sec else "?s"
            print(f"\n  KEEP   {g.keep.path.name}  ({g.keep.fmt} q={g.keep.quality}, {g.keep.size_mb:.1f} MB, {dur})")
            for r in g.remove:
                rdur = f"{r.duration_sec:.0f}s" if r.duration_sec else "?s"
                print(f"  REMOVE {r.path.name}  ({r.fmt} {r.bitrate_kbps}kbps q={r.quality}, {r.size_mb:.1f} MB, {rdur})")
            print(f"  Reason: {g.reason}")

    if versions:
        print(f"\n── Case C: Different Versions (no action) ────────────────────")
        for g in versions:
            print(f'\n  INFO: {len(g.report)} version(s) of "{g.keep.base_title}"')
            for i in g.report:
                print(f"    • {i.path.name}  ({i.fmt})")
            print(f"  Reason: {g.reason}")

    print(f"\nRun without --dry-run to quarantine {total_remove} file(s).")


# ---------------------------------------------------------------------------
# Apply mode
# ---------------------------------------------------------------------------

def _quarantine_file(info: FileInfo, quarantine_dir: Path) -> Optional[int]:
    """
    Move info.path to quarantine_dir.
    Returns file size in bytes on success, None on failure.
    """
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dest = quarantine_dir / info.path.name

    # Avoid silently overwriting a different file in quarantine
    if dest.exists():
        dest = quarantine_dir / f"{info.path.stem}__{info.sha256[:8]}{info.path.suffix}"

    try:
        shutil.move(str(info.path), str(dest))
        return info.size
    except Exception as exc:
        log.error("Could not move %s → %s: %s", info.path, dest, exc)
        return None


def apply_changes(
    groups: List[DupeGroup],
    quarantine_dir: Path,
    dry_run: bool,
) -> Tuple[int, int]:
    """
    Move duplicate files to quarantine_dir.
    Returns (files_quarantined, bytes_freed).
    """
    quarantined = 0
    bytes_freed = 0

    for g in groups:
        if g.group_type == "versions":
            continue  # never touch version variants

        for r in g.remove:
            if not r.path.exists():
                log.warning("File already gone: %s", r.path)
                continue

            log.info(
                "QUARANTINE [%s]  %s  (%s %.1f MB)  reason: %s",
                g.group_type.upper(), r.path.name, r.fmt, r.size_mb, g.reason,
            )
            log.info("  KEEP: %s", g.keep.path.name)
            log_action(
                f"DEDUPE-{g.group_type.upper()}: quarantine {r.path.name} "
                f"| keep {g.keep.path.name} | {g.reason}"
            )

            if not dry_run:
                freed = _quarantine_file(r, quarantine_dir)
                if freed is not None:
                    quarantined += 1
                    bytes_freed += freed
            else:
                quarantined += 1
                bytes_freed += r.size

    return quarantined, bytes_freed


# ---------------------------------------------------------------------------
# Summary print (apply mode)
# ---------------------------------------------------------------------------

def print_apply_summary(
    scanned: int,
    groups: List[DupeGroup],
    quarantined: int,
    bytes_freed: int,
    quarantine_dir: Path,
    dry_run: bool,
) -> None:
    exact   = sum(1 for g in groups if g.group_type == "exact")
    quality = sum(1 for g in groups if g.group_type == "quality")
    ver     = sum(1 for g in groups if g.group_type == "versions")

    label = "DRY RUN " if dry_run else ""
    print(f"\n=== dedupe {label}complete ===")
    print(f"  Files scanned            : {scanned}")
    print(f"  Exact duplicate groups   : {exact}")
    print(f"  Quality duplicate groups : {quality}")
    print(f"  Version groups (kept)    : {ver}")
    print(f"  Files quarantined        : {quarantined}")
    print(f"  Space freed              : {bytes_freed / (1024*1024):.1f} MB")
    if quarantined and not dry_run:
        print(f"  Quarantine location      : {quarantine_dir}")
        print(f"\n  Files are in quarantine (not deleted). Review and remove manually.")
    print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    paths: List[Path],
    dry_run: bool = False,
    quarantine_dir: Optional[Path] = None,
) -> Tuple[int, int, int, int]:
    """
    Scan paths for duplicates and optionally quarantine them.

    Args:
        paths:          List of audio file paths to scan.
        dry_run:        If True, report only — do not move any files.
        quarantine_dir: Where to move duplicates (default: config.DEDUPE_QUARANTINE_DIR).

    Returns:
        (files_scanned, groups_found, files_quarantined, bytes_freed)
    """
    if quarantine_dir is None:
        quarantine_dir = config.DEDUPE_QUARANTINE_DIR

    mode = "DRY-RUN" if dry_run else "APPLY"
    log_action(f"DEDUPE {mode} START: {len(paths)} file(s)")
    log.info("Dedupe: scanning %d file(s)  dry_run=%s", len(paths), dry_run)

    scanned, groups = scan_library(paths)

    actionable = [g for g in groups if g.group_type != "versions"]
    total_would_remove = sum(len(g.remove) for g in actionable)

    log.info(
        "Dedupe: found %d group(s) — %d exact, %d quality, %d versions",
        len(groups),
        sum(1 for g in groups if g.group_type == "exact"),
        sum(1 for g in groups if g.group_type == "quality"),
        sum(1 for g in groups if g.group_type == "versions"),
    )

    if dry_run:
        print_dry_run_summary(scanned, groups)
        log_action(
            f"DEDUPE DRY-RUN DONE: {scanned} scanned, "
            f"{len(groups)} group(s), {total_would_remove} would be quarantined"
        )
        return scanned, len(groups), total_would_remove, sum(
            sum(r.size for r in g.remove) for g in actionable
        )

    quarantined, bytes_freed = apply_changes(groups, quarantine_dir, dry_run=False)

    print_apply_summary(scanned, groups, quarantined, bytes_freed, quarantine_dir, dry_run=False)

    log_action(
        f"DEDUPE DONE: {scanned} scanned, {len(groups)} group(s), "
        f"{quarantined} quarantined, {bytes_freed // (1024*1024)} MB freed"
    )
    return scanned, len(groups), quarantined, bytes_freed
