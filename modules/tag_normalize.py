"""
modules/tag_normalize.py

Normalize ID3 tags on MP3 files for Rekordbox compatibility:
  • Convert ID3v2.4 → ID3v2.3  (Rekordbox reads v2.3 correctly on all platforms)
  • Remove ID3v1 block completely  (ID3v1 is 128 bytes at end-of-file; never needed)

Only MP3 files are touched.  FLAC, WAV, AIFF, M4A, OGG, OPUS are untouched.

Usage:
    python pipeline.py tag-normalize --dry-run      # preview, no writes
    python pipeline.py tag-normalize                # apply to whole sorted library
    python pipeline.py tag-normalize --path /mnt/music_ssd/KKDJ/sorted/

Log tags emitted per file:
    [ID3V23_NORMALIZED]   — file saved as ID3v2.3 (may combine with tags below)
    [ID3V24_DOWNGRADED]   — was ID3v2.4, converted to ID3v2.3
    [ID3V1_REMOVED]       — ID3v1 128-byte block stripped from end-of-file

Safety:
    • Never rewrites a file unless something actually needs fixing
    • Never touches tag content — this is format-only normalization
    • Preserve every frame that mutagen can carry across version conversion
    • v1=0 on save means mutagen will NOT write ID3v1 (existing block is gone)
"""
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

import config
from modules.textlog import log_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-file result
# ---------------------------------------------------------------------------

@dataclass
class NormalizeResult:
    filepath:   str
    was_v24:    bool           # file had ID3v2.4 tags
    had_v1:     bool           # file had ID3v1 block
    normalized: bool           # a change was made (or would be in dry-run)
    error:      Optional[str]  # None on success


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------

def _has_id3v1(path: Path) -> bool:
    """
    Return True if the file has an ID3v1 block.

    ID3v1 is exactly 128 bytes at end-of-file, starting with the ASCII
    bytes "TAG".  When APEv2 tags are also present at end-of-file, their
    32-byte footer can follow ID3v1, so we also check 160 bytes from the end.
    """
    try:
        size = path.stat().st_size
        if size < 128:
            return False
        with open(str(path), "rb") as fh:
            # Standard position: last 128 bytes
            fh.seek(size - 128)
            if fh.read(3) == b"TAG":
                return True
            # Before a 32-byte APEv2 footer
            if size >= 160:
                fh.seek(size - 160)
                if fh.read(3) == b"TAG":
                    return True
        return False
    except Exception:
        return False


def _get_id3_version(path: Path) -> Optional[Tuple[int, int]]:
    """
    Return (major, minor) of the ID3v2 header, e.g. (2, 3) or (2, 4).
    Returns None if the file has no ID3v2 tags or cannot be read.
    """
    try:
        from mutagen.id3 import ID3
        tags = ID3(str(path))
        return tags.version[:2]   # version is (2, minor, patch)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-file normalizer
# ---------------------------------------------------------------------------

def normalize_file(path: Path, dry_run: bool = False) -> NormalizeResult:
    """
    Inspect one MP3 and normalize its ID3 tags.

    Steps:
      1. Load ID3v2 tags with mutagen — detect version (2.3 vs 2.4).
      2. Check for ID3v1 block using raw file seek.
      3. If either condition is non-ideal: save with v2_version=3, v1=0.
         mutagen handles frame-level conversion automatically.
    """
    if path.suffix.lower() != ".mp3":
        return NormalizeResult(
            filepath=str(path), was_v24=False, had_v1=False,
            normalized=False, error="not an MP3",
        )

    # --- Detect current state ---
    try:
        from mutagen.id3 import ID3
        tags = ID3(str(path))
    except Exception as exc:
        return NormalizeResult(
            filepath=str(path), was_v24=False, had_v1=False,
            normalized=False, error=str(exc),
        )

    version = tags.version[:2]   # e.g. (2, 4)
    was_v24 = (version == (2, 4))
    had_v1  = _has_id3v1(path)

    needs_work = was_v24 or had_v1

    if not needs_work:
        return NormalizeResult(
            filepath=str(path), was_v24=False, had_v1=False,
            normalized=False, error=None,
        )

    if dry_run:
        return NormalizeResult(
            filepath=str(path), was_v24=was_v24, had_v1=had_v1,
            normalized=True, error=None,
        )

    # --- Apply: save as ID3v2.3, strip ID3v1 ---
    try:
        tags.save(str(path), v2_version=config.ID3_VERSION, v1=0)
        return NormalizeResult(
            filepath=str(path), was_v24=was_v24, had_v1=had_v1,
            normalized=True, error=None,
        )
    except Exception as exc:
        return NormalizeResult(
            filepath=str(path), was_v24=was_v24, had_v1=had_v1,
            normalized=False, error=str(exc),
        )


# ---------------------------------------------------------------------------
# Dry-run output
# ---------------------------------------------------------------------------

def print_dry_run_summary(
    results: List[NormalizeResult],
    scanned: int,
) -> None:
    to_fix   = [r for r in results if r.normalized]
    v24_list = [r for r in to_fix if r.was_v24]
    v1_list  = [r for r in to_fix if r.had_v1]
    errors   = [r for r in results if r.error and not r.normalized]
    already  = scanned - len(to_fix) - len(errors)

    print(
        f"\n=== tag-normalize DRY RUN ===\n"
        f"  MP3 files scanned  : {scanned}\n"
        f"  Already OK (v2.3, no v1): {already}\n"
        f"  Would normalize    : {len(to_fix)}\n"
        f"    ID3v2.4 → v2.3   : {len(v24_list)}\n"
        f"    ID3v1 to remove  : {len(v1_list)}\n"
        f"  Errors / unreadable: {len(errors)}\n"
        f"\nNo files modified. Run without --dry-run to apply.\n"
    )

    if errors:
        print("Unreadable files:")
        for r in errors:
            print(f"  [ERROR] {Path(r.filepath).name} — {r.error}")
        print()

    if to_fix:
        print("Files that would be changed:")
        for r in to_fix:
            tags = []
            if r.was_v24:
                tags.append("[ID3V24_DOWNGRADED]")
            if r.had_v1:
                tags.append("[ID3V1_REMOVED]")
            tags.append("[ID3V23_NORMALIZED]")
            tag_str = " ".join(tags)
            print(f"  {tag_str}  {Path(r.filepath).name}")
        print()

    print(f"Run without --dry-run to apply to {len(to_fix)} file(s).")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    paths: List[Path],
    dry_run: bool = False,
    verbose: bool = False,
) -> Tuple[int, int, int, int]:
    """
    Normalize all MP3 files in the given path list.

    Args:
        paths:   list of audio file Paths (non-MP3 entries are silently skipped)
        dry_run: if True, detect but don't write
        verbose: emit per-file log lines at INFO level

    Returns:
        (scanned, normalized, v24_downgraded, v1_removed)
    """
    mp3_paths = [p for p in paths if p.suffix.lower() == ".mp3" and p.is_file()]
    scanned   = len(mp3_paths)

    mode = "DRY-RUN" if dry_run else "APPLY"
    log_action(f"TAG-NORMALIZE {mode} START: {scanned} MP3(s)")
    log.info("tag-normalize: scanning %d MP3 file(s) [%s]", scanned, mode)

    results:       List[NormalizeResult] = []
    normalized     = 0
    v24_downgraded = 0
    v1_removed     = 0
    errors         = 0

    for path in mp3_paths:
        result = normalize_file(path, dry_run=dry_run)
        results.append(result)

        if result.error and not result.normalized:
            log.warning("TAG-NORMALIZE ERROR: %s — %s", path.name, result.error)
            errors += 1
            continue

        if not result.normalized:
            continue

        normalized += 1
        action_tags = []

        if result.was_v24:
            v24_downgraded += 1
            action_tags.append("[ID3V24_DOWNGRADED]")
            if not dry_run:
                log_action(f"ID3V24_DOWNGRADED: {path}")

        if result.had_v1:
            v1_removed += 1
            action_tags.append("[ID3V1_REMOVED]")
            if not dry_run:
                log_action(f"ID3V1_REMOVED: {path}")

        action_tags.append("[ID3V23_NORMALIZED]")
        if not dry_run:
            log_action(f"ID3V23_NORMALIZED: {path}")

        prefix = "[DRY-RUN] " if dry_run else ""
        log.info(
            "%s%s  %s",
            prefix,
            " ".join(action_tags),
            path.name,
        )

    if dry_run:
        print_dry_run_summary(results, scanned)
    else:
        log.info(
            "tag-normalize done: scanned=%d normalized=%d "
            "v24_downgraded=%d v1_removed=%d errors=%d",
            scanned, normalized, v24_downgraded, v1_removed, errors,
        )

    log_action(
        f"TAG-NORMALIZE {mode} DONE: "
        f"{scanned} scanned, {normalized} normalized, "
        f"{v24_downgraded} v2.4→v2.3, {v1_removed} v1 removed, {errors} errors"
    )

    return scanned, normalized, v24_downgraded, v1_removed
