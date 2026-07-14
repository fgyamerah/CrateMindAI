"""
modules/library_organize.py

Reorganize audio files into:
    <sorted_root>/<first-letter>/<primary-artist>/<filename>

Primary artist = first artist before any explicit collaboration separator
(feat. / ft. / featuring / pres. / & / , / ; / / / x / vs. / with).

Rules:
  - Preview by default; pass apply=True to commit moves.
  - Tags are never modified. BPM, key, and cues are untouched.
  - Skips files missing artist tag.
  - Skips files whose artist string looks like concatenated names without a
    separator (2+ CamelCase transitions → unsafe_primary_artist).
  - No overwrite: collisions get a safe suffix ' (1)', ' (2)', etc.
  - Operational folders (.BIN, QUARANTINE, …) are excluded automatically.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path

import config
import db
import modules.run_logger as _proc

# ---------------------------------------------------------------------------
# Skip dirs
# ---------------------------------------------------------------------------
_SKIP_DIRS: frozenset = config.LIBRARY_ORGANIZE_SKIP_DIRS

# ---------------------------------------------------------------------------
# Collaboration separator split
# ---------------------------------------------------------------------------
# Split on the FIRST explicit collaboration separator only.
# Space-separated multi-word names with no separator are NOT split — use the
# full string as primary (conservative rule).
_COLLAB_SEP_RE = re.compile(
    r"""
    (?:
        \s+feat(?:uring)?\.?   |   # feat / feat. / featuring
        \s+ft\.?               |   # ft / ft.
        \s+pres(?:ents)?\.?    |   # pres / pres. / presents
        \s+vs\.?               |   # vs / vs.
        \s+with\b              |   # with (word-bounded)
        \s+x\b                 |   # standalone x (e.g. "A x B")
        \s+&\s*                |   # & — requires leading space so &ME / &lez are not split
        \s*/\s*                |   # / — common DJ tag separator; also prevents pathlib nesting
        \s*[,;]\s*                 # , ;
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Characters that Python's pathlib would interpret as path separators or that
# are illegal in directory names.  Strip them from the primary artist before
# using it as a folder name.
_DIR_UNSAFE_RE = re.compile(r'[/\\:*?"<>|]')


def _sanitize_dirname(name: str) -> str:
    """Remove path-unsafe characters from a proposed directory name."""
    return _DIR_UNSAFE_RE.sub("", name).strip().rstrip(".")

# ---------------------------------------------------------------------------
# Unsafe artist detection (CamelCase concatenation without separator)
# ---------------------------------------------------------------------------
# Two or more lowercase→uppercase transitions in a string with no recognized
# separator strongly suggests multiple names pasted together (e.g.
# "KaybeeShimzaBlack").  Single transitions tolerated for names like "McDonald".
_CAMEL_TRANSITION_RE = re.compile(r'[a-z][A-Z]')
# &ALL_CAPS immediately followed by a lowercase letter signals an abbreviated
# artist name (&ME, &NERVO) concatenated onto the start of another name
# (e.g. &MERampa = &ME + Rampa).  &ME and &lez are NOT matched.
_AMPERSAND_CONCAT_RE = re.compile(r'&[A-Z]{2,}[a-z]')

# Single-word compound/project names that contain CamelCase and must never be
# flagged as concatenated artists.  Checked before all rules (case-insensitive).
_SAFE_ARTIST_ALLOWLIST: frozenset = frozenset({
    "africangroove",
})


def is_unsafe_artist_string(s: str) -> bool:
    """
    Return True when *s* appears to be multiple artist names concatenated without
    a recognised separator.  Designed to be called on the extracted primary artist.

    Rules:
      1. 2+ [a-z][A-Z] transitions anywhere (e.g. KaybeeShimzaBlack).
      2. A word inside a multi-word string has a [a-z][A-Z] transition at internal
         position >= 3 (e.g. 'AdilDJ' in 'AdilDJ feat. Sue').  Positions < 3
         tolerate prefixes: Mc, De, La.  Single-word inputs skip this rule —
         a lone CamelCase word is treated as a valid compound project name.
      3. An & followed by 2+ uppercase letters then a lowercase letter signals an
         ALL-CAPS abbreviation glued onto the next name (e.g. &MERampa = &ME+Rampa).

    Not flagged: '&ME', '&lez', 'Black Coffee', 'Da Capo', 'AfricanGroove'
    """
    if s.lower() in _SAFE_ARTIST_ALLOWLIST:
        return False
    # Rule 1
    if len(_CAMEL_TRANSITION_RE.findall(s)) >= 2:
        return True
    # Rule 2 — skipped for single-word strings; a lone CamelCase word (e.g.
    # AfricanGroove, RootedSoul) cannot be confirmed as two separate artists
    # without a known-artists DB, so it is treated as a compound project name.
    if " " in s:
        for word in s.split():
            for m in _CAMEL_TRANSITION_RE.finditer(word):
                if m.start() + 1 >= 3:
                    return True
    # Rule 3
    if _AMPERSAND_CONCAT_RE.search(s):
        return True
    return False


def _extract_primary(artist: str) -> tuple[str, bool]:
    """
    Return (primary_artist, is_unsafe).

    Splits on the first explicit separator; checks the extracted primary for
    concatenation patterns regardless of whether a separator was present (catches
    cases like 'AdilDJ, Sue' where the comma splits cleanly but the left side is
    still a concatenated string).
    """
    parts   = _COLLAB_SEP_RE.split(artist, maxsplit=1)
    primary = parts[0].strip()
    return primary, is_unsafe_artist_string(primary)


# ---------------------------------------------------------------------------
# First-letter folder
# ---------------------------------------------------------------------------

def _first_letter(name: str) -> str:
    """Return the uppercase first character if alphabetic, or '#' otherwise."""
    return name[0].upper() if name and name[0].isalpha() else "#"


# ---------------------------------------------------------------------------
# Sorted root detection
# ---------------------------------------------------------------------------

def _find_sorted_root(input_dir: Path) -> Path:
    """
    Walk up from input_dir looking for a directory named 'sorted'.
    Falls back to input_dir itself if not found within 5 levels.
    Ensures moves always land under the correct library root even when
    --input points at a sub-folder (e.g. sorted/P).
    """
    current = input_dir.resolve()
    for _ in range(5):
        if current.name.lower() == "sorted":
            return current
        parent = current.parent
        if parent == current:   # filesystem root
            break
        current = parent
    return input_dir.resolve()


# ---------------------------------------------------------------------------
# File-hash helper (duplicate detection)
# ---------------------------------------------------------------------------

def _file_hash(path: Path) -> str:
    """Return MD5 hex digest of file contents, or '' on error."""
    h = hashlib.md5()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Case-variant directory resolver
# ---------------------------------------------------------------------------

def _resolve_case_variant_dir(parent: Path, name: str) -> tuple[Path, bool]:
    """
    Return (dest_dir, is_case_variant).

    If a child of *parent* matches *name* case-insensitively but not exactly,
    return that existing directory so files merge into it rather than creating a
    parallel folder that differs only in capitalisation.
    """
    try:
        for child in parent.iterdir():
            if child.is_dir() and child.name != name and child.name.lower() == name.lower():
                return child, True
    except OSError:
        pass
    return parent / name, False


# ---------------------------------------------------------------------------
# Collision-safe target path
# ---------------------------------------------------------------------------

def _safe_target(directory: Path, filename: str, src: Path) -> tuple[Path, str]:
    """
    Return (target_path, status) where status is one of:
      'ok'        – no collision; move proceeds normally
      'collision' – destination occupied by different content; suffix appended
      'duplicate' – destination exists and content matches src; skip the move
    """
    candidate = directory / filename
    if candidate == src or not candidate.exists():
        return candidate, "ok"
    src_hash = _file_hash(src)
    dst_hash = _file_hash(candidate)
    if src_hash and dst_hash and src_hash == dst_hash:
        return candidate, "duplicate"
    stem = Path(filename).stem
    ext  = Path(filename).suffix
    i = 1
    while True:
        candidate = directory / f"{stem} ({i}){ext}"
        if not candidate.exists():
            return candidate, "collision"
        i += 1


# ---------------------------------------------------------------------------
# Skip-dir guard
# ---------------------------------------------------------------------------

def _should_skip(path: Path) -> bool:
    for part in path.parts:
        if part in _SKIP_DIRS:
            return True
        if part.startswith("."):
            return True
    return False


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def _collect_audio_files(input_dir: Path, limit: "int | None") -> "list[Path]":
    """Return sorted, deduplicated audio files under input_dir, up to limit."""
    seen:  set        = set()
    files: list[Path] = []
    for ext in sorted(config.AUDIO_EXTENSIONS):
        for path in sorted(input_dir.rglob(f"*{ext}")):
            if _should_skip(path):
                continue
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
    files.sort()
    if limit is not None and limit > 0:
        files = files[:limit]
    return files


# ---------------------------------------------------------------------------
# Artist tag reading
# ---------------------------------------------------------------------------

def _read_artist(path: Path) -> str:
    """Return the artist tag string (stripped), or '' on failure."""
    try:
        from mutagen import File as _MFile
        mf = _MFile(str(path), easy=False)
    except Exception:
        return ""
    if mf is None or mf.tags is None:
        return ""
    t = mf.tags

    def _first(tag) -> str:
        if tag is None:
            return ""
        if hasattr(tag, "text") and isinstance(tag.text, list):
            return str(tag.text[0]).strip() if tag.text else ""
        if isinstance(tag, list):
            return str(tag[0]).strip() if tag else ""
        return str(tag).strip()

    # ID3 (MP3 / AIFF / WAV)
    artist = _first(t.get("TPE1")) or _first(t.get("TPE2"))
    # Vorbis Comment (FLAC / OGG / OPUS)
    if not artist:
        artist = _first(t.get("artist") or t.get("ARTIST"))
    # MP4 / M4A
    if not artist:
        artist = _first(t.get("\xa9ART"))
    return artist.strip()


def _artist_from_filename(path: Path) -> str:
    """
    Attempt to parse artist from the 'Artist - Title.ext' filename pattern.
    Returns '' when separator ' - ' is absent or candidate looks like a track
    number (all digits → low confidence → skip).
    """
    parts = path.stem.split(" - ", 1)
    if len(parts) == 2:
        candidate = parts[0].strip()
        if candidate and not candidate.isdigit():
            return candidate
    return ""


# ---------------------------------------------------------------------------
# Flatten repair helper
# ---------------------------------------------------------------------------

def _run_flatten(
    sorted_root: Path,
    *,
    apply: bool = False,
    verbose: bool = False,
    limit: "int | None" = None,
) -> dict:
    """
    Detect and repair files nested more than one level inside an artist folder.

    Correct depth  : sorted_root/<letter>/<artist>/<file>   (3 parts relative)
    Nested (bad)   : sorted_root/<letter>/<artist>/<collab>/.../<file>  (> 3 parts)

    The <letter> and <artist> are taken from the existing path — no tag reads.
    Files are moved up to sorted_root/<letter>/<artist>/<file>.
    """
    stats = dict(
        scanned=0, candidates=0, moved=0,
        skipped_already_correct=0, skipped_errors=0,
        collisions=0,
        # unused in flatten mode — kept so pipeline.py summary is uniform
        skipped_unchanged=0, skipped_no_artist=0, skipped_unsafe_artist=0,
        unsafe_artist_count=0, moved_to_chkartistnames=0,
        duplicate_target_matches=0, case_only_moves=0,
    )

    mode = "APPLY" if apply else "PREVIEW"
    print(f"\n=== library-organize --flatten-collab-folders {mode} ===")
    print(f"\n  Sorted root : {sorted_root}\n")

    files = _collect_audio_files(sorted_root, limit)
    stats["scanned"] = len(files)

    plan: list[tuple[Path, Path, bool]] = []

    for path in files:
        try:
            rel = path.relative_to(sorted_root)
        except ValueError:
            continue

        # 3 parts = letter / artist / filename → already correct
        if len(rel.parts) <= 3:
            stats["skipped_already_correct"] += 1
            if verbose:
                print(f"  OK    {path.name}")
            continue

        # Take the first two parts (letter, artist) from the existing structure
        dest_dir        = sorted_root / rel.parts[0] / rel.parts[1]
        target, _status = _safe_target(dest_dir, path.name, path)
        coll            = _status == "collision"

        if target == path or _status == "duplicate":
            stats["skipped_already_correct"] += 1
            continue

        stats["candidates"] += 1
        if coll:
            stats["collisions"] += 1
        plan.append((path, target, coll))

    for src, dst, coll in plan:
        print("  FLATTEN:")
        print(f"    FROM: {src}")
        print(f"    TO  : {dst}")
        if coll:
            print(f"    Note : collision — suffix appended")
        print(f"    Reason: move out of nested collaborator folder")
        print()

        if apply:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                stats["moved"] += 1
                db.update_track_path_references(
                    src,
                    dst,
                    context="library_organize",
                )
            except Exception as exc:
                print(f"    ERROR: {exc}")
                stats["skipped_errors"] += 1

    if not apply and stats["candidates"] > 0:
        print(f"\nRun with --apply to flatten {stats['candidates']} file(s).")

    return stats


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run(
    input_dir: Path,
    *,
    apply: bool = False,
    verbose: bool = False,
    force: bool = False,
    reset_stage: bool = False,
    limit: "int | None" = None,
    flatten_collab_folders: bool = False,
    move_unsafe_artists: bool = False,
) -> dict:
    """
    Reorganize audio files into <sorted_root>/<letter>/<primary-artist>/<filename>.

    With flatten_collab_folders=True: repair mode — move files out of nested
    collaborator sub-directories without reading artist tags.

    Returns a stats dict (keys shared between both modes):
        scanned, candidates, moved, skipped_unchanged, skipped_no_artist,
        skipped_unsafe_artist, skipped_already_correct, skipped_errors, collisions
    """
    input_dir   = Path(input_dir).resolve()
    sorted_root = _find_sorted_root(input_dir)

    if flatten_collab_folders:
        return _run_flatten(sorted_root, apply=apply, verbose=verbose, limit=limit)

    stats = dict(
        scanned=0, candidates=0, moved=0,
        skipped_unchanged=0, skipped_no_artist=0, skipped_unsafe_artist=0,
        skipped_already_correct=0, skipped_errors=0,
        collisions=0,
        unsafe_artist_count=0, moved_to_chkartistnames=0,
        duplicate_target_matches=0, case_only_moves=0,
    )

    _stage = "library-organize"
    if reset_stage:
        _proc.clear_stage(_stage)
    n_skip_unchanged = 0

    plan:            list[tuple[Path, Path, bool, bool]] = []   # (src, dst, had_collision, case_only)
    skipped:         list[tuple[Path, str]]               = []
    unsafe_chk_plan: list[tuple[Path, Path, bool]]        = []  # unsafe files → CHKARTISTNAMES
    _chk_root = sorted_root.parent / ".BIN" / "CHKARTISTNAMES"

    files = _collect_audio_files(input_dir, limit)
    stats["scanned"] = len(files)

    mode = "APPLY" if apply else "PREVIEW"
    print(f"\n=== library-organize {mode} ===")
    print(f"\n  Input       : {input_dir}")
    print(f"  Sorted root : {sorted_root}\n")

    for path in files:
        if not force and _proc.should_skip(_stage, path):
            n_skip_unchanged += 1
            continue

        artist = _read_artist(path)
        if not artist:
            artist = _artist_from_filename(path)   # fallback: "Artist - Title.ext"
        if not artist:
            skipped.append((path, "no_artist_tag"))
            stats["skipped_no_artist"] += 1
            if apply:
                _proc.record(_stage, path, "skipped", "no_artist_tag")
            continue

        primary, is_unsafe = _extract_primary(artist)
        if not is_unsafe and primary:
            primary = _sanitize_dirname(primary)   # strip path-unsafe chars (e.g. residual /)
        if is_unsafe or not primary:
            stats["unsafe_artist_count"] += 1
            if move_unsafe_artists:
                try:
                    rel = path.relative_to(input_dir)
                except ValueError:
                    rel = Path(path.name)
                chk_dest_dir = _chk_root / rel.parent
                chk_target, chk_status = _safe_target(chk_dest_dir, path.name, path)
                if chk_status == "collision":
                    stats["collisions"] += 1
                unsafe_chk_plan.append((path, chk_target, chk_status == "collision"))
            else:
                skipped.append((path, "unsafe_primary_artist"))
            if apply:
                _proc.record(_stage, path, "skipped", "unsafe_primary_artist")
            continue

        letter                 = _first_letter(primary)
        dest_dir, is_case_only = _resolve_case_variant_dir(sorted_root / letter, primary)
        target, status         = _safe_target(dest_dir, path.name, path)

        if status == "duplicate":
            stats["duplicate_target_matches"] += 1
            skipped.append((path, "duplicate_candidate"))
            if apply:
                _proc.record(_stage, path, "skipped", "duplicate_candidate")
            continue

        if target == path:
            stats["skipped_already_correct"] += 1
            if apply:
                _proc.record(_stage, path, "no_change")
            if verbose:
                print(f"  OK    {path.name}")
            continue

        stats["candidates"] += 1
        if status == "collision":
            stats["collisions"] += 1
        if is_case_only:
            stats["case_only_moves"] += 1
        plan.append((path, target, status == "collision", is_case_only))

    # ── Output ──────────────────────────────────────────────────────────────
    for src, dst, coll in unsafe_chk_plan:
        label = "  CHKARTISTNAMES:" if apply else "  WOULD MOVE TO CHKARTISTNAMES:"
        print(label)
        print(f"    FROM: {src}")
        print(f"    TO  : {dst}")
        if coll:
            print(f"    Note : collision — suffix appended")
        print()

        if apply:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                stats["moved_to_chkartistnames"] += 1
                db.update_track_path_references(
                    src,
                    dst,
                    context="library_organize",
                )
                _proc.record(_stage, dst, "ignored", "unsafe_primary_artist")
            except Exception as exc:
                print(f"    ERROR: {exc}")
                stats["skipped_errors"] += 1
                _proc.record(_stage, src, "error", str(exc)[:120])

    for src, dst, coll, case_only in plan:
        print("  MOVE:")
        print(f"    FROM: {src}")
        print(f"    TO  : {dst}")
        if coll:
            print(f"    Note : collision — suffix appended")
        if case_only:
            print(f"    Note : case-only folder merge")
        print(f"    Reason: primary artist folder")
        print()

        if apply:
            try:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                stats["moved"] += 1
                db.update_track_path_references(
                    src,
                    dst,
                    context="library_organize",
                )
                _proc.record(_stage, dst, "success")
            except Exception as exc:
                print(f"    ERROR: {exc}")
                stats["skipped_errors"] += 1
                _proc.record(_stage, src, "error", str(exc)[:120])

    for path, reason in skipped:
        print(f"  SKIP  {path.name}")
        print(f"    Reason: {reason}")
        print()

    stats["skipped_unchanged"]    = n_skip_unchanged
    stats["skipped_unsafe_artist"] = (
        stats["unsafe_artist_count"] - stats["moved_to_chkartistnames"]
    )
    if n_skip_unchanged:
        print(f"  (Skipped unchanged: {n_skip_unchanged}  — use --force to reprocess)")

    return stats
