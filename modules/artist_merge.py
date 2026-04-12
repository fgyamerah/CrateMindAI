"""
Artist folder canonicalization and merge.

Scans the sorted library for artist folders that represent the same base
artist under different capitalization, punctuation, feat/collaboration
suffixes, or comma-separated collaborator naming.  Groups them by a
normalized key and, when safe, merges all files into one canonical folder.

Merge categories (new in v1.7.0):
  SAFE_ALIAS          Difference is only formatting / punctuation / casing
                      (no collab suffixes present in the group).
                      Examples: "Heavy-K" / "Heavy K", "H.O.S.H" / "HOSH"
  SAME_PRIMARY_COLLAB Primary artist matches after normalization, but ≥1
                      folder has a feat/ft/collab suffix.
                      Examples: "Heavy-K" / "Heavy-K feat. Davido"
  AMBIGUOUS           Primary artists differ even after full normalization.
                      Never auto-merged; written to review report only.

Both SAFE_ALIAS and SAME_PRIMARY_COLLAB are auto-applied in --apply mode.
AMBIGUOUS groups are always skipped.

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
# Merge category constants
# ---------------------------------------------------------------------------

MERGE_CATEGORY_SAFE_ALIAS          = "SAFE_ALIAS"
MERGE_CATEGORY_SAME_PRIMARY_COLLAB = "SAME_PRIMARY_COLLAB"
MERGE_CATEGORY_AMBIGUOUS           = "AMBIGUOUS"

# ---------------------------------------------------------------------------
# Primary-artist extraction
# ---------------------------------------------------------------------------

# feat / ft / featuring — everything from this word onwards is stripped
_RE_FEAT = re.compile(
    r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$",
    re.IGNORECASE,
)


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
    >>> extract_primary_artist("Culoe De Song")
    'Culoe De Song'
    >>> extract_primary_artist("Black Coffee")
    'Black Coffee'
    >>> extract_primary_artist("&ME")
    '&ME'
    >>> extract_primary_artist("Heavy-K feat. Davido & Tresor")
    'Heavy-K'
    >>> extract_primary_artist("Hosh, 1979, jalja")
    'Hosh'
    """
    s = _RE_FEAT.sub("", name).strip()
    if "," in s:
        s = s.split(",", 1)[0].strip()
    return s


def _has_collab_suffix(name: str) -> bool:
    """
    Return True if *name* contains a feat/ft/featuring or comma-collaborator
    suffix — i.e. it is a collab string rather than a pure artist name.

    >>> _has_collab_suffix("Heavy-K feat. Davido & Tresor")
    True
    >>> _has_collab_suffix("Heavy K ft Naak Musiq")
    True
    >>> _has_collab_suffix("Heavy K, Point 5")
    True
    >>> _has_collab_suffix("Hosh, 1979, jalja")
    True
    >>> _has_collab_suffix("Heavy-K")
    False
    >>> _has_collab_suffix("Mr. Luu & MSK")
    False
    """
    if _RE_FEAT.search(name):
        return True
    primary = extract_primary_artist(name)
    # Has comma suffix only when the comma was stripped by extract_primary_artist
    return "," in name and "," not in primary


# ---------------------------------------------------------------------------
# Normalization key  (grouping + comparison)
# ---------------------------------------------------------------------------

# Periods, apostrophes, quotes, commas stripped before comparison
_RE_KEY_NOISE  = re.compile(r"['\u2018\u2019\u201c\u201d`.,]+")
# Hyphens and underscores → space
_RE_KEY_SEP    = re.compile(r"[-_]+")
# Collapse multiple spaces
_RE_KEY_SPACES = re.compile(r"\s{2,}")


def normalize_artist_key(name: str) -> str:
    """
    Return a normalized grouping key for an artist folder name.

    Extracts the primary artist first (strips feat/collab suffixes), then:
      - NFC unicode normalisation
      - Lowercases
      - Strips periods, apostrophes, and typographic quotes
      - Replaces hyphens / underscores with spaces
      - Collapses runs of whitespace

    This is the KEY used to *group* folders together.  Two folder names that
    produce the same key are considered merge candidates.

    >>> normalize_artist_key("Culoe De Song ft. Thandiswa Mazwai")
    'culoe de song'
    >>> normalize_artist_key("Black-Coffee")
    'black coffee'
    >>> normalize_artist_key("H.O.S.H")
    'hosh'
    >>> normalize_artist_key("Heavy-K feat. Davido & Tresor")
    'heavy k'
    >>> normalize_artist_key("Hosh, 1979, jalja")
    'hosh'
    >>> normalize_artist_key("Mr. Luu & MSK")
    'mr luu & msk'
    """
    primary = extract_primary_artist(name)
    s = unicodedata.normalize("NFC", primary).lower()
    s = _RE_KEY_NOISE.sub("", s)
    s = _RE_KEY_SEP.sub(" ", s)
    s = _RE_KEY_SPACES.sub(" ", s).strip()
    return s


def _normalize_primary_for_compare(primary: str) -> str:
    """
    Normalize a primary artist name for identity comparison *after*
    extract_primary_artist() has already been called.

    Applies the same transformations as normalize_artist_key() but skips
    the collab-stripping step (the caller provides an already-stripped name).

    >>> _normalize_primary_for_compare("Heavy-K")
    'heavy k'
    >>> _normalize_primary_for_compare("Heavy K")
    'heavy k'
    >>> _normalize_primary_for_compare("H.O.S.H")
    'hosh'
    >>> _normalize_primary_for_compare("HOSH")
    'hosh'
    >>> _normalize_primary_for_compare("K.E.E.N.E")
    'keene'
    >>> _normalize_primary_for_compare("K.E.E.N.E.")
    'keene'
    >>> _normalize_primary_for_compare("Mr. Luu & MSK")
    'mr luu & msk'
    >>> _normalize_primary_for_compare("Mr Luu & MSK")
    'mr luu & msk'
    >>> _normalize_primary_for_compare("Rosalie.")
    'rosalie'
    >>> _normalize_primary_for_compare("VA")
    'va'
    >>> _normalize_primary_for_compare("V.A")
    'va'
    >>> _normalize_primary_for_compare("Villager S.A")
    'villager sa'
    >>> _normalize_primary_for_compare("Villager SA")
    'villager sa'
    >>> _normalize_primary_for_compare("Steve 'Silk' Hurley")
    'steve silk hurley'
    >>> _normalize_primary_for_compare("Steve Silk Hurley")
    'steve silk hurley'
    """
    s = unicodedata.normalize("NFC", primary).lower()
    s = _RE_KEY_NOISE.sub("", s)
    s = _RE_KEY_SEP.sub(" ", s)
    s = _RE_KEY_SPACES.sub(" ", s).strip()
    return s


# ---------------------------------------------------------------------------
# Alias difference description helper
# ---------------------------------------------------------------------------

def _describe_alias_differences(primaries: List[str]) -> str:
    """
    Return a concise human-readable string describing why a set of primary
    artist names are considered safe aliases.

    Used only for SAFE_ALIAS reason strings.

    >>> _describe_alias_differences(["Heavy-K", "Heavy-K"])
    'identical names'
    >>> _describe_alias_differences(["Culoe De Song", "culoe de song"])
    'capitalization variant'
    >>> _describe_alias_differences(["Heavy-K", "Heavy K"])
    'hyphen/space variant'
    >>> _describe_alias_differences(["H.O.S.H", "HOSH"])
    'dotted-initials variant'
    >>> _describe_alias_differences(["Rosalie", "Rosalie."])
    'trailing period variant'
    >>> _describe_alias_differences(["Mr. Luu & MSK", "Mr Luu & MSK"])
    'period variant'
    """
    unique = set(primaries)
    if len(unique) == 1:
        return "identical names"

    tags: list[str] = []

    # Case-only difference (stripped comparison matches)
    lower_set = {p.lower() for p in primaries}
    if len(lower_set) == 1:
        return "capitalization variant"

    # Detect dotted-initials: names where letters are separated by periods
    # e.g. H.O.S.H  V.A  K.E.E.N.E  Villager S.A
    dotted_initials_pat = re.compile(r"(?<![A-Za-z])[A-Za-z](?:\.[A-Za-z])+\.?")
    has_dotted_initials = any(dotted_initials_pat.search(p) for p in primaries)

    has_period       = any("." in p for p in primaries)
    has_hyphen       = any("-" in p for p in primaries)
    has_space        = any(" " in p for p in primaries)
    has_underscore   = any("_" in p for p in primaries)
    has_quotes       = any(
        c in p for p in primaries
        for c in "'\u2018\u2019\u201c\u201d`"
    )
    has_trailing_dot = any(
        p.rstrip().endswith(".") and not dotted_initials_pat.search(p)
        for p in primaries
    )

    if has_dotted_initials:
        tags.append("dotted-initials variant")
    elif has_period and has_trailing_dot:
        tags.append("trailing period variant")
    elif has_period:
        tags.append("period variant")

    if has_hyphen and has_space:
        # Some have hyphen where others have space (or no separator)
        hyphen_forms = [p for p in primaries if "-" in p]
        plain_forms  = [p for p in primaries if "-" not in p]
        if hyphen_forms and plain_forms:
            tags.append("hyphen/space variant")
    elif has_hyphen:
        tags.append("hyphen variant")

    if has_underscore:
        tags.append("underscore/space variant")

    if has_quotes:
        tags.append("quotation style variant")

    # Case differences alongside other differences
    if len({p.lower() for p in primaries}) > 1 and "capitalization variant" not in tags:
        tags.append("capitalization variant")

    if not tags:
        tags.append("formatting variant")

    return ", ".join(dict.fromkeys(tags))  # deduplicated, insertion-ordered


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FolderInfo:
    """One artist folder on disk."""
    path:              Path        # absolute path to the artist folder
    display_name:      str         # folder name exactly as it exists on disk
    primary_artist:    str         # result of extract_primary_artist(display_name)
    letter:            str         # parent letter directory name
    files:             List[Path]  # audio files directly inside this folder
    has_collab_suffix: bool = False  # True if display_name has feat/ft/comma suffix


@dataclass
class MergeGroup:
    """A group of artist folders that share the same normalized key."""
    normalized_key:   str
    canonical_name:   str        # chosen display name for the merged folder
    canonical_letter: str        # letter directory for the canonical folder
    canonical_path:   Path       # full path to the target canonical folder
    folders:          List[FolderInfo]
    total_files:      int
    merge_category:   str        # SAFE_ALIAS | SAME_PRIMARY_COLLAB | AMBIGUOUS
    is_safe:          bool       # True for SAFE_ALIAS and SAME_PRIMARY_COLLAB
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
    Only keys with more than one folder are returned (single-folder keys
    are omitted — nothing to merge).
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

            display_name = artist_dir.name
            primary      = extract_primary_artist(display_name)
            key          = normalize_artist_key(display_name)
            audio_files  = _collect_audio_files(artist_dir)

            info = FolderInfo(
                path=artist_dir,
                display_name=display_name,
                primary_artist=primary,
                letter=letter,
                files=audio_files,
                has_collab_suffix=_has_collab_suffix(display_name),
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

    Considers only pure (non-collab) primary artist names when possible.
    Folder names with collab suffixes are never chosen as the canonical.

    Priority (ascending sort key — lower = more preferred):
      1. Most total files across all folders sharing this primary artist form
      2. Not all-uppercase alpha content (avoids "HOSH" over "H.O.S.H")
      3. Not all-lowercase alpha content
      4. Alphabetical tie-break (deterministic)
    """
    # Prefer non-collab primary artists as the canonical name
    pure_folders  = [fi for fi in folders if not fi.has_collab_suffix]
    source_folders = pure_folders if pure_folders else folders

    counts: Dict[str, int] = {}
    for fi in source_folders:
        counts[fi.primary_artist] = counts.get(fi.primary_artist, 0) + len(fi.files)

    def sort_key(item: Tuple[str, int]) -> Tuple[int, int, int, str]:
        name, count = item
        alpha = [c for c in name if c.isalpha()]
        all_upper = bool(alpha) and all(c.isupper() for c in alpha)
        all_lower = bool(alpha) and all(c.islower() for c in alpha)
        return (-count, int(all_upper), int(all_lower), name)

    ranked = sorted(counts.items(), key=sort_key)
    return ranked[0][0]


# ---------------------------------------------------------------------------
# Safety classification
# ---------------------------------------------------------------------------

def _classify_merge(folders: List[FolderInfo]) -> Tuple[str, bool, str]:
    """
    Classify a group of artist folders and determine merge safety.

    Returns (merge_category, is_safe, reason_string).

    Algorithm:
      1. Normalize each folder's primary_artist with _normalize_primary_for_compare().
      2. If the normalized forms differ → AMBIGUOUS (unsafe).
      3. If all match and ≥1 folder has a collab suffix → SAME_PRIMARY_COLLAB (safe).
      4. If all match and no collab suffixes → SAFE_ALIAS (safe).

    The key insight vs. the old code: comparison uses the normalized form of
    the primary artist, so "Heavy-K" and "Heavy K" (and "H.O.S.H" / "HOSH")
    are correctly identified as the same primary artist.
    """
    # Compute normalized comparison key for each folder's primary artist
    norm_keys = {
        _normalize_primary_for_compare(fi.primary_artist) for fi in folders
    }

    if len(norm_keys) > 1:
        # Primary artists still differ after full normalization — ambiguous
        primaries_display = sorted({fi.primary_artist for fi in folders})
        return (
            MERGE_CATEGORY_AMBIGUOUS,
            False,
            "ambiguous: normalized primary artists differ: "
            + ", ".join(repr(p) for p in primaries_display),
        )

    # All primaries share the same normalized key
    collab_folders = [fi for fi in folders if fi.has_collab_suffix]
    pure_folders   = [fi for fi in folders if not fi.has_collab_suffix]

    if collab_folders:
        collab_examples = [fi.display_name for fi in collab_folders[:2]]
        if pure_folders:
            reason = (
                "same primary artist after normalization; "
                "collab variants: "
                + ", ".join(repr(c) for c in collab_examples)
            )
        else:
            reason = (
                "same primary artist after normalization (all collab variants): "
                + ", ".join(repr(c) for c in collab_examples)
            )
        return MERGE_CATEGORY_SAME_PRIMARY_COLLAB, True, reason

    # No collab suffixes — pure formatting/punctuation/casing alias group
    primaries = [fi.primary_artist for fi in folders]
    diff_desc = _describe_alias_differences(primaries)
    return MERGE_CATEGORY_SAFE_ALIAS, True, f"safe alias: {diff_desc}"


# ---------------------------------------------------------------------------
# Group builder
# ---------------------------------------------------------------------------

def build_merge_groups(sorted_root: Path) -> Tuple[List[MergeGroup], List[MergeGroup]]:
    """
    Scan sorted_root and return (safe_groups, ambiguous_groups).

    safe_groups contains both SAFE_ALIAS and SAME_PRIMARY_COLLAB groups.
    ambiguous_groups contains only AMBIGUOUS groups (written to review report,
    never auto-applied).
    """
    raw_groups = scan_artist_folders(sorted_root)
    safe:      List[MergeGroup] = []
    ambiguous: List[MergeGroup] = []

    for key, folders in raw_groups.items():
        canonical_name   = _pick_canonical(folders)
        canonical_letter = _first_letter_for(canonical_name)
        canonical_path   = sorted_root / canonical_letter / canonical_name

        category, is_safe, reason = _classify_merge(folders)

        group = MergeGroup(
            normalized_key=key,
            canonical_name=canonical_name,
            canonical_letter=canonical_letter,
            canonical_path=canonical_path,
            folders=sorted(folders, key=lambda f: f.display_name.lower()),
            total_files=sum(len(f.files) for f in folders),
            merge_category=category,
            is_safe=is_safe,
            reason=reason,
        )

        if is_safe:
            safe.append(group)
        else:
            ambiguous.append(group)

    safe.sort(key=lambda g: g.canonical_name.lower())
    ambiguous.sort(key=lambda g: g.normalized_key)
    return safe, ambiguous


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
                    log_action(f"ARTIST-MERGE: ERROR moving {src.name!r}: {exc}")
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
        "canonical":       group.canonical_name,
        "normalized_key":  group.normalized_key,
        "merge_category":  group.merge_category,
        "total_files":     group.total_files,
        "reason":          group.reason,
        "folders": [
            {
                "name":             fi.display_name,
                "primary":          fi.primary_artist,
                "letter":           fi.letter,
                "files":            len(fi.files),
                "has_collab_suffix": fi.has_collab_suffix,
            }
            for fi in group.folders
        ],
    }


def _write_report(
    report_dir: Path,
    safe: List[MergeGroup],
    ambiguous: List[MergeGroup],
    applied_stats: Optional[Dict] = None,
    dry_run: bool = True,
) -> Path:
    """Write the merge report JSON to report_dir. Returns the report path."""
    report_dir.mkdir(parents=True, exist_ok=True)
    mode = "dry_run" if dry_run else "applied"
    report_path = report_dir / f"artist_merge_{mode}.json"

    safe_alias  = [g for g in safe if g.merge_category == MERGE_CATEGORY_SAFE_ALIAS]
    collab      = [g for g in safe if g.merge_category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB]

    payload: dict = {
        "mode": mode,
        "summary": {
            "safe_alias_groups":         len(safe_alias),
            "same_primary_collab_groups": len(collab),
            "ambiguous_groups":           len(ambiguous),
            "safe_files_affected":        sum(g.total_files for g in safe),
            "ambiguous_files":            sum(g.total_files for g in ambiguous),
        },
        "safe_alias_merges":         [_group_to_dict(g) for g in safe_alias],
        "same_primary_collab_merges": [_group_to_dict(g) for g in collab],
        "ambiguous_merges":           [_group_to_dict(g) for g in ambiguous],
    }

    if applied_stats:
        payload["applied"] = applied_stats

    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    return report_path


# ---------------------------------------------------------------------------
# Console summary helpers
# ---------------------------------------------------------------------------

_CATEGORY_LABELS = {
    MERGE_CATEGORY_SAFE_ALIAS:          "safe alias",
    MERGE_CATEGORY_SAME_PRIMARY_COLLAB: "same primary artist",
    MERGE_CATEGORY_AMBIGUOUS:           "ambiguous",
}


def _print_merge_group(group: MergeGroup, verbose: bool = True) -> None:
    """Print a single merge group to stdout."""
    cat_label = _CATEGORY_LABELS.get(group.merge_category, group.merge_category)
    print(f"  → {group.canonical_name!r}  [{cat_label}]")
    print(f"    {group.reason}")
    if verbose:
        for fi in group.folders:
            is_canonical = (
                fi.primary_artist == group.canonical_name
                and fi.display_name == group.canonical_name
            )
            role  = "canonical" if is_canonical else (
                "collab" if fi.has_collab_suffix else "alias"
            )
            marker = "★ " if is_canonical else "  "
            print(
                f"    {marker}{fi.display_name!r}  "
                f"({len(fi.files)} files, {role})"
            )


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

    safe, ambiguous = build_merge_groups(sorted_root)

    report_path = _write_report(report_dir, safe, ambiguous, dry_run=True)

    # Separate safe into sub-categories for display
    alias_groups  = [g for g in safe if g.merge_category == MERGE_CATEGORY_SAFE_ALIAS]
    collab_groups = [g for g in safe if g.merge_category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB]

    total_safe_files = sum(g.total_files for g in safe)
    total_amb_files  = sum(g.total_files for g in ambiguous)

    print(f"\n{'─'*60}")
    print(f"  Artist Merge — Dry Run")
    print(f"{'─'*60}")
    print(f"  Safe alias merges ready   : {len(alias_groups):>4}  "
          f"({sum(g.total_files for g in alias_groups)} files)")
    print(f"  Same-primary collab merges: {len(collab_groups):>4}  "
          f"({sum(g.total_files for g in collab_groups)} files)")
    print(f"  Ambiguous (review needed) : {len(ambiguous):>4}  "
          f"({total_amb_files} files)")

    if alias_groups:
        print(f"\n  Safe Alias Merges  (auto-applied with --apply)")
        print(f"  {'─'*54}")
        for g in alias_groups:
            _print_merge_group(g)

    if collab_groups:
        print(f"\n  Same Primary Artist — Collab Variants  (auto-applied with --apply)")
        print(f"  {'─'*54}")
        for g in collab_groups:
            _print_merge_group(g)

    if ambiguous:
        print(f"\n  Ambiguous Merges — manual review required")
        print(f"  {'─'*54}")
        for g in ambiguous:
            print(f"  ? normalized_key={g.normalized_key!r}")
            for fi in g.folders:
                print(f"      {fi.display_name!r}  ({len(fi.files)} files)")
            print(f"    {g.reason}")

    print(f"\n  Report: {report_path}")
    print(f"{'─'*60}\n")

    log.info(
        "ARTIST-MERGE dry-run: %d safe (%d alias, %d collab, %d files),"
        " %d ambiguous (%d files)",
        len(safe), len(alias_groups), len(collab_groups), total_safe_files,
        len(ambiguous), total_amb_files,
    )
    log_action(
        f"ARTIST-MERGE DRY-RUN DONE: {len(safe)} safe "
        f"({len(alias_groups)} alias, {len(collab_groups)} collab), "
        f"{len(ambiguous)} ambiguous → {report_path}"
    )
    return 0


def run_apply(sorted_root: Path, report_dir: Path) -> int:
    """
    Apply all safe merges (both SAFE_ALIAS and SAME_PRIMARY_COLLAB): move
    files into canonical folders, remove vacated folders, update the database.
    AMBIGUOUS groups are never touched.

    Returns 0 on success, 1 if any individual file move errored.
    """
    log.info("ARTIST-MERGE: scanning %s (apply)", sorted_root)
    log_action("ARTIST-MERGE APPLY START")

    safe, ambiguous = build_merge_groups(sorted_root)

    if not safe:
        log.info("ARTIST-MERGE: nothing to merge")
        print("\n  Artist Merge — no safe merges found.\n")
        _write_report(report_dir, safe, ambiguous, dry_run=False)
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
            f" ({group.total_files} files, {group.merge_category})"
        )
        stats = _apply_merge(group, dry_run=False)
        global_stats["moved"]           += stats["moved"]
        global_stats["collisions"]      += stats["collisions"]
        global_stats["errors"]          += stats["errors"]
        global_stats["folders_removed"] += stats["folders_removed"]
        global_stats["groups_merged"]   += 1

    report_path = _write_report(
        report_dir, safe, ambiguous,
        applied_stats=global_stats, dry_run=False,
    )

    # --- Console summary ---
    alias_count  = sum(1 for g in safe if g.merge_category == MERGE_CATEGORY_SAFE_ALIAS)
    collab_count = sum(1 for g in safe if g.merge_category == MERGE_CATEGORY_SAME_PRIMARY_COLLAB)

    print(f"\n{'─'*60}")
    print(f"  Artist Merge — Applied")
    print(f"{'─'*60}")
    print(f"  Groups merged       : {global_stats['groups_merged']}")
    print(f"    Safe alias        : {alias_count}")
    print(f"    Same-primary collab:{collab_count}")
    print(f"  Files moved         : {global_stats['moved']}")
    print(f"  Collisions renamed  : {global_stats['collisions']}")
    print(f"  Folders removed     : {global_stats['folders_removed']}")
    print(f"  Errors              : {global_stats['errors']}")
    print(f"  Ambiguous (skipped) : {len(ambiguous)}")
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
