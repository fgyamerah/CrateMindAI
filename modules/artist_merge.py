"""
Artist folder canonicalization and merge.

Scans the sorted library for artist folders that represent the same base
artist under different capitalization, feat/collaboration suffixes, or
comma-separated collaborator naming.  Groups them by a normalized key and,
when safe, merges all files into one canonical folder.

Safe merges (auto-applied in --apply mode):
  - Case differences only:
      "culoe de song" / "Culoe De Song" / "Culoe de Song"
  - Feat / featuring suffix variations:
      "Culoe De Song ft. Thandiswa Mazwai" → canonical "Culoe De Song"
  - Comma-separated collaborator suffix:
      "Cee ElAssaad, Jackie Queens" → canonical "Cee ElAssaad"

Uncertain merges (written to review report, never auto-applied):
  - Primary artist names differ beyond case (different characters, accents,
    or punctuation that cannot be explained by the above rules).

Storage structure is always preserved:
  $SORTED/<LETTER>/<Artist>/TrackName.mp3
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.textlog import log_action

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Primary-artist extraction
# ---------------------------------------------------------------------------

# feat / ft / featuring — everything from this word onwards is stripped
_RE_FEAT = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$",
    re.IGNORECASE,
)

# After stripping feat, if the remaining string contains a comma, take only
# the part before the first comma (first artist in a collaborator list).
# A trailing " &" or " and" before a comma is also treated as a collaborator
# separator, but we never split on bare "&" alone — "&ME" is a real DJ name.


def extract_primary_artist(name: str) -> str:
    """
    Return the base/primary artist from a folder name that may include
    collaboration suffixes.

    Steps:
      1. Strip feat / ft / featuring and everything after it.
      2. If the remainder contains a comma, return only the text before the
         first comma (first artist in a collaborator list).
      3. Strip surrounding whitespace.

    >>> extract_primary_artist("Culoe De Song ft. Thandiswa Mazwai")
    'Culoe De Song'
    >>> extract_primary_artist("Cee ElAssaad, Jackie Queens")
    'Cee ElAssaad'
    >>> extract_primary_artist("Cee ElAssaad, Mario Bianco")
    'Cee ElAssaad'
    >>> extract_primary_artist("Culoe De Song")
    'Culoe De Song'
    >>> extract_primary_artist("Black Coffee")
    'Black Coffee'
    >>> extract_primary_artist("&ME")
    '&ME'
    """
    s = _RE_FEAT.sub("", name).strip()
    if "," in s:
        s = s.split(",", 1)[0].strip()
    return s


# ---------------------------------------------------------------------------
# Normalization key
# ---------------------------------------------------------------------------

# Typographic noise stripped before key comparison (apostrophes, periods)
_RE_KEY_NOISE  = re.compile(r"['\u2018\u2019\u201c\u201d`.,]+")
# Hyphens and underscores → space (catches "Black-Coffee" == "Black Coffee")
_RE_KEY_SEP    = re.compile(r"[-_]+")
# Collapse multiple spaces to one
_RE_KEY_SPACES = re.compile(r"\s{2,}")


def normalize_artist_key(name: str) -> str:
    """
    Return a normalized comparison key for an artist folder name.

    Extracts the primary artist first, then:
      - Lowercases
      - NFC unicode normalization (does NOT strip accents — "Söhne" stays distinct)
      - Strips apostrophes and periods (typographic noise)
      - Replaces hyphens/underscores with a space
      - Collapses multiple spaces

    >>> normalize_artist_key("Culoe De Song ft. Thandiswa Mazwai")
    'culoe de song'
    >>> normalize_artist_key("Culoe de song")
    'culoe de song'
    >>> normalize_artist_key("Culoe De Song")
    'culoe de song'
    >>> normalize_artist_key("Cee ElAssaad, Jackie Queens")
    'cee elassaad'
    >>> normalize_artist_key("D'Angelo")
    'dangelo'
    >>> normalize_artist_key("Black-Coffee")
    'black coffee'
    """
    primary = extract_primary_artist(name)
    s = unicodedata.normalize("NFC", primary).lower()
    s = _RE_KEY_NOISE.sub("", s)
    s = _RE_KEY_SEP.sub(" ", s)
    s = _RE_KEY_SPACES.sub(" ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FolderInfo:
    """One artist folder on disk."""
    path:           Path        # absolute path to the artist folder
    display_name:   str         # folder name exactly as it exists on disk
    primary_artist: str         # result of extract_primary_artist(display_name)
    letter:         str         # parent letter directory name
    files:          List[Path]  # audio files directly inside this folder


@dataclass
class MergeGroup:
    """A group of artist folders that share the same normalized key."""
    normalized_key:   str
    canonical_name:   str        # chosen display name for the merged folder
    canonical_letter: str        # letter directory for the canonical folder
    canonical_path:   Path       # full path to the target canonical folder
    folders:          List[FolderInfo]
    total_files:      int
    is_safe:          bool
    reason:           str        # human-readable explanation


# ---------------------------------------------------------------------------
# Library scanner
# ---------------------------------------------------------------------------

def _collect_audio_files(folder: Path) -> List[Path]:
    """Return all audio files directly inside *folder* (non-recursive)."""
    exts = config.AUDIO_EXTENSIONS
    return [
        f for f in folder.iterdir()
        if f.is_file() and f.suffix.lower() in exts
    ]


def _first_letter_for(artist: str) -> str:
    """Return the index letter (A-Z or #) for an artist name."""
    a = artist.strip().upper()
    if not a:
        return "#"
    for prefix in ("THE ", "A "):
        if a.startswith(prefix):
            a = a[len(prefix):]
            break
    first = a[0] if a else "#"
    return first if first.isalpha() else "#"


def scan_artist_folders(sorted_root: Path) -> Dict[str, List[FolderInfo]]:
    """
    Walk sorted_root and group artist folders by normalized key.

    Expected structure:  sorted_root/<LETTER>/<Artist>/

    Returns dict: normalized_key → [FolderInfo, ...]
    Only keys with more than one folder are returned (no-op single folders
    are omitted).
    """
    groups: Dict[str, List[FolderInfo]] = {}

    if not sorted_root.exists():
        log.warning("Sorted root does not exist: %s", sorted_root)
        return {}

    for letter_dir in sorted(sorted_root.iterdir()):
        if not letter_dir.is_dir():
            continue
        letter = letter_dir.name
        if letter.startswith("_"):
            continue  # skip _unsorted, _compilations

        for artist_dir in sorted(letter_dir.iterdir()):
            if not artist_dir.is_dir():
                continue

            display_name   = artist_dir.name
            primary        = extract_primary_artist(display_name)
            key            = normalize_artist_key(display_name)
            audio_files    = _collect_audio_files(artist_dir)

            info = FolderInfo(
                path=artist_dir,
                display_name=display_name,
                primary_artist=primary,
                letter=letter,
                files=audio_files,
            )
            groups.setdefault(key, []).append(info)

    # Return only groups with variants (2+ folders)
    return {k: v for k, v in groups.items() if len(v) > 1}


# ---------------------------------------------------------------------------
# Canonical name selection
# ---------------------------------------------------------------------------

def _pick_canonical(folders: List[FolderInfo]) -> str:
    """
    Choose the best display name for the merged folder.

    Priority:
      1. Primary artist form with the most total files (most common in library).
      2. On a file-count tie: prefer the title-cased form.
      3. Final tie-break: alphabetical order (deterministic).
    """
    # Accumulate file counts per distinct primary artist name
    counts: Dict[str, int] = {}
    for fi in folders:
        counts[fi.primary_artist] = counts.get(fi.primary_artist, 0) + len(fi.files)

    def sort_key(item: Tuple[str, int]) -> Tuple[int, int, str]:
        name, count = item
        # title-case match is 0 (preferred), non-match is 1
        tc_penalty = 0 if name == name.title() else 1
        return (-count, tc_penalty, name)

    ranked = sorted(counts.items(), key=sort_key)
    return ranked[0][0]


# ---------------------------------------------------------------------------
# Safety classification
# ---------------------------------------------------------------------------

def _classify_merge(folders: List[FolderInfo]) -> Tuple[bool, str]:
    """
    Determine whether a merge group is safe for automatic application.

    Safe when: after extract_primary_artist(), all primary artist names are
    identical except for capitalization — i.e., they all lowercase to the
    same string.  This ensures the only differences between folders are:
      - case variants          ("culoe de song" / "Culoe De Song")
      - feat/collab suffixes   ("Culoe De Song ft. X" → "Culoe De Song")
      - comma-collab suffixes  ("Cee ElAssaad, Jackie Queens" → "Cee ElAssaad")

    Uncertain when: primary artist names differ in actual characters beyond
    case (different letters, accented variants, punctuation differences that
    survived normalization).

    Returns (is_safe, reason_string).
    """
    primaries_lower = {fi.primary_artist.lower() for fi in folders}

    if len(primaries_lower) == 1:
        # All primaries are case-equivalent — determine the type of difference
        has_feat   = any(_RE_FEAT.search(fi.display_name) for fi in folders)
        has_collab = any(
            "," in fi.display_name
            and "," not in extract_primary_artist(fi.display_name)
            for fi in folders
        )
        has_case   = len({fi.display_name.lower() for fi in folders}) > 1

        reasons = []
        if has_case:
            reasons.append("capitalization variants")
        if has_feat:
            reasons.append("feat/featuring suffix")
        if has_collab:
            reasons.append("comma-collaborator suffix")

        reason = ", ".join(reasons) if reasons else "identical names"
        return True, reason

    # Primary artists differ beyond case — uncertain
    primaries_display = sorted({fi.primary_artist for fi in folders})
    return False, f"primary artists differ: {', '.join(repr(p) for p in primaries_display)}"


# ---------------------------------------------------------------------------
# Group builder
# ---------------------------------------------------------------------------

def build_merge_groups(sorted_root: Path) -> Tuple[List[MergeGroup], List[MergeGroup]]:
    """
    Scan sorted_root and return (safe_groups, uncertain_groups).

    Each group represents a set of artist folders that should be merged into
    one canonical folder.
    """
    raw_groups = scan_artist_folders(sorted_root)
    safe: List[MergeGroup]      = []
    uncertain: List[MergeGroup] = []

    for key, folders in raw_groups.items():
        canonical_name   = _pick_canonical(folders)
        canonical_letter = _first_letter_for(canonical_name)
        canonical_path   = sorted_root / canonical_letter / canonical_name

        is_safe, reason = _classify_merge(folders)

        group = MergeGroup(
            normalized_key=key,
            canonical_name=canonical_name,
            canonical_letter=canonical_letter,
            canonical_path=canonical_path,
            folders=sorted(folders, key=lambda f: f.display_name.lower()),
            total_files=sum(len(f.files) for f in folders),
            is_safe=is_safe,
            reason=reason,
        )

        if is_safe:
            safe.append(group)
        else:
            uncertain.append(group)

    safe.sort(key=lambda g: g.canonical_name.lower())
    uncertain.sort(key=lambda g: g.normalized_key)
    return safe, uncertain


# ---------------------------------------------------------------------------
# Unique path helper
# ---------------------------------------------------------------------------

def _unique_path(dest: Path) -> Path:
    """If dest exists, append a counter suffix until a free slot is found."""
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


# ---------------------------------------------------------------------------
# Apply a single merge group
# ---------------------------------------------------------------------------

def _apply_merge(group: MergeGroup, dry_run: bool) -> Dict[str, int]:
    """
    Move all files from non-canonical folders into the canonical folder.

    Returns stats dict: moved, collisions, errors, folders_removed.
    """
    stats = {"moved": 0, "collisions": 0, "errors": 0, "folders_removed": 0}
    canonical = group.canonical_path

    for fi in group.folders:
        if fi.path.resolve() == canonical.resolve():
            log.debug("ARTIST-MERGE: skipping canonical folder %s", fi.display_name)
            continue

        if not fi.files:
            # No audio files — folder is empty or has non-audio content only
            if not dry_run and fi.path.exists():
                remaining = list(fi.path.iterdir())
                if not remaining:
                    fi.path.rmdir()
                    stats["folders_removed"] += 1
                    log.info("ARTIST-MERGE: removed empty folder %s", fi.path)
                    log_action(f"ARTIST-MERGE: removed empty folder [{fi.path.name}]")
            continue

        for src in fi.files:
            dest = canonical / src.name

            if dest.exists():
                dest = _unique_path(dest)
                stats["collisions"] += 1
                log.warning(
                    "ARTIST-MERGE: collision — %s → %s (renamed to avoid overwrite)",
                    src.name, dest.name,
                )
                log_action(
                    f"ARTIST-MERGE: COLLISION {src.name!r} renamed → {dest.name!r}"
                    f" in [{group.canonical_name}]"
                )

            log.info(
                "ARTIST-MERGE: %s  %s → %s",
                ("DRY-RUN would move" if dry_run else "moving"),
                src,
                dest,
            )
            log_action(
                f"ARTIST-MERGE: {'[DRY] ' if dry_run else ''}move"
                f" [{fi.display_name}/{src.name}]"
                f" → [{group.canonical_name}/{dest.name}]"
            )

            if not dry_run:
                try:
                    canonical.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(src), str(dest))
                    stats["moved"] += 1

                    # Update DB: re-register under new path
                    old_str = str(src)
                    new_str = str(dest)
                    row = db.get_track(old_str)
                    if row:
                        db.upsert_track(
                            new_str,
                            artist=row["artist"],
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
                            conn.execute(
                                "DELETE FROM tracks WHERE filepath=?", (old_str,)
                            )
                except Exception as exc:
                    log.error("ARTIST-MERGE: failed to move %s → %s: %s", src, dest, exc)
                    log_action(
                        f"ARTIST-MERGE: ERROR moving {src.name!r}: {exc}"
                    )
                    stats["errors"] += 1
            else:
                stats["moved"] += 1  # count what would be moved

        # After moving all files, remove the now-empty source folder
        if not dry_run and fi.path.exists():
            try:
                remaining = [p for p in fi.path.iterdir() if not p.name.startswith(".")]
                if not remaining:
                    fi.path.rmdir()
                    stats["folders_removed"] += 1
                    log.info("ARTIST-MERGE: removed vacated folder %s", fi.path)
                    log_action(
                        f"ARTIST-MERGE: removed vacated folder [{fi.display_name}]"
                    )

                    # Also remove the parent letter directory if it is now empty
                    parent_letter_dir = fi.path.parent
                    try:
                        if not any(parent_letter_dir.iterdir()):
                            parent_letter_dir.rmdir()
                            log.info(
                                "ARTIST-MERGE: removed empty letter dir %s",
                                parent_letter_dir,
                            )
                    except Exception:
                        pass  # letter dir still has other artists — not an error
            except Exception as exc:
                log.warning("ARTIST-MERGE: could not remove %s: %s", fi.path, exc)

    return stats


# ---------------------------------------------------------------------------
# Report serialization helpers
# ---------------------------------------------------------------------------

def _group_to_dict(group: MergeGroup) -> dict:
    return {
        "canonical": group.canonical_name,
        "normalized_key": group.normalized_key,
        "total_files": group.total_files,
        "reason": group.reason,
        "folders": [
            {
                "name":    fi.display_name,
                "primary": fi.primary_artist,
                "letter":  fi.letter,
                "files":   len(fi.files),
            }
            for fi in group.folders
        ],
    }


def _write_report(
    report_dir: Path,
    safe: List[MergeGroup],
    uncertain: List[MergeGroup],
    applied_stats: Optional[Dict] = None,
    dry_run: bool = True,
) -> Path:
    """Write the merge report JSON to report_dir. Returns the report path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    mode = "dry_run" if dry_run else "applied"
    report_path = report_dir / f"artist_merge_{mode}.json"

    payload: dict = {
        "mode": mode,
        "summary": {
            "safe_merge_groups":     len(safe),
            "uncertain_merge_groups": len(uncertain),
            "safe_files_affected":   sum(g.total_files for g in safe),
            "uncertain_files":       sum(g.total_files for g in uncertain),
        },
        "safe_merges":     [_group_to_dict(g) for g in safe],
        "uncertain_merges": [_group_to_dict(g) for g in uncertain],
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
    Scan the library, compute merge groups, write a report, and print a
    human-readable summary.  No files are moved.

    Returns 0 on success.
    """
    log.info("ARTIST-MERGE: scanning %s (dry-run)", sorted_root)
    log_action("ARTIST-MERGE DRY-RUN START")

    safe, uncertain = build_merge_groups(sorted_root)

    report_path = _write_report(report_dir, safe, uncertain, dry_run=True)

    # --- Console summary ---
    total_safe_files = sum(g.total_files for g in safe)
    total_unc_files  = sum(g.total_files for g in uncertain)

    print(f"\n{'─'*60}")
    print(f"  Artist Merge — Dry Run")
    print(f"{'─'*60}")
    print(f"  Safe merges ready to apply : {len(safe):>4}  ({total_safe_files} files)")
    print(f"  Uncertain (review required): {len(uncertain):>4}  ({total_unc_files} files)")

    if safe:
        print(f"\n  Safe Merges")
        print(f"  {'─'*54}")
        for g in safe:
            print(f"  → {g.canonical_name!r}  [{g.reason}]")
            for fi in g.folders:
                marker = "★ " if fi.primary_artist == g.canonical_name and fi.display_name == g.canonical_name else "  "
                print(f"    {marker}{fi.display_name!r}  ({len(fi.files)} files)")

    if uncertain:
        print(f"\n  Uncertain Merges — manual review required")
        print(f"  {'─'*54}")
        for g in uncertain:
            print(f"  ? normalized_key={g.normalized_key!r}")
            for fi in g.folders:
                print(f"      {fi.display_name!r}  ({len(fi.files)} files)")
            print(f"    reason: {g.reason}")

    print(f"\n  Report: {report_path}")
    print(f"{'─'*60}\n")

    log.info(
        "ARTIST-MERGE dry-run: %d safe groups (%d files), %d uncertain (%d files)",
        len(safe), total_safe_files, len(uncertain), total_unc_files,
    )
    log_action(
        f"ARTIST-MERGE DRY-RUN DONE: {len(safe)} safe, {len(uncertain)} uncertain"
        f" → {report_path}"
    )
    return 0


def run_apply(sorted_root: Path, report_dir: Path) -> int:
    """
    Apply all safe merges: move files into canonical folders, remove vacated
    folders, update the database.  Uncertain groups are not touched.

    Returns 0 on success, 1 if any individual file move errored.
    """
    log.info("ARTIST-MERGE: scanning %s (apply)", sorted_root)
    log_action("ARTIST-MERGE APPLY START")

    safe, uncertain = build_merge_groups(sorted_root)

    if not safe:
        log.info("ARTIST-MERGE: nothing to merge")
        print("\n  Artist Merge — no safe merges found.\n")
        _write_report(report_dir, safe, uncertain, dry_run=False)
        return 0

    global_stats = {
        "moved":           0,
        "collisions":      0,
        "errors":          0,
        "folders_removed": 0,
        "groups_merged":   0,
    }

    for group in safe:
        log.info(
            "ARTIST-MERGE: merging %d folders → %r  [%s]",
            len(group.folders), group.canonical_name, group.reason,
        )
        log_action(
            f"ARTIST-MERGE: merge → {group.canonical_name!r}"
            f" ({group.total_files} files, {group.reason})"
        )
        stats = _apply_merge(group, dry_run=False)
        global_stats["moved"]           += stats["moved"]
        global_stats["collisions"]      += stats["collisions"]
        global_stats["errors"]          += stats["errors"]
        global_stats["folders_removed"] += stats["folders_removed"]
        global_stats["groups_merged"]   += 1

    report_path = _write_report(
        report_dir, safe, uncertain,
        applied_stats=global_stats, dry_run=False,
    )

    # --- Console summary ---
    print(f"\n{'─'*60}")
    print(f"  Artist Merge — Applied")
    print(f"{'─'*60}")
    print(f"  Groups merged       : {global_stats['groups_merged']}")
    print(f"  Files moved         : {global_stats['moved']}")
    print(f"  Collisions renamed  : {global_stats['collisions']}")
    print(f"  Folders removed     : {global_stats['folders_removed']}")
    print(f"  Errors              : {global_stats['errors']}")
    print(f"  Uncertain (skipped) : {len(uncertain)}")
    print(f"\n  Report: {report_path}")
    print(f"{'─'*60}\n")

    log.info(
        "ARTIST-MERGE apply done: %d merged, %d moved, %d collisions, %d errors",
        global_stats["groups_merged"],
        global_stats["moved"],
        global_stats["collisions"],
        global_stats["errors"],
    )
    log_action(
        f"ARTIST-MERGE APPLY DONE: {global_stats['groups_merged']} groups,"
        f" {global_stats['moved']} files, {global_stats['errors']} errors"
        f" → {report_path}"
    )

    return 0 if global_stats["errors"] == 0 else 1
