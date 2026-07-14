"""
modules/filename_normalize.py

Deterministic filename normalization for DJ library audio files.

Renames audio files to:
    {artist} - {title} ({version}).ext
    {artist} - {title}.ext            (when version is absent or already in title)

Rules:
  - Preview by default; pass apply=True to commit renames.
  - Tags are never modified. BPM, key, and cues are untouched.
  - Skips files missing artist or title tags.
  - No overwrite: collisions get a safe suffix ' (1)', ' (2)', etc.
  - Operational folders are excluded automatically.
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config
import modules.run_logger as _proc

# ---------------------------------------------------------------------------
# Artist review constants
# ---------------------------------------------------------------------------
# Files whose artist tags look like concatenated names are not renamed.
# Instead an entry is written to the JSONL queue for manual triage.
_ARTIST_REVIEW_DIR   = config.BIN_DIR / "ARTIST_REVIEW"   # under .BIN → auto-excluded
_ARTIST_REVIEW_QUEUE = Path(__file__).parent.parent / "data" / "review" / "artist_review_queue.jsonl"

# ---------------------------------------------------------------------------
# Directories to skip
# ---------------------------------------------------------------------------
_SKIP_DIRS: frozenset = config.FILENAME_NORMALIZE_SKIP_DIRS

# ---------------------------------------------------------------------------
# Filename character sanitization
# ---------------------------------------------------------------------------
_INVALID_RE   = re.compile(r'[/\\:*?"<>|]')
_MULTISPC_RE  = re.compile(r" {2,}")
_MULTIDASH_RE = re.compile(r"-{2,}")

# ---------------------------------------------------------------------------
# Safety: junk version patterns
# ---------------------------------------------------------------------------
# Match watermark/promo/URL text that must never appear in a filename.
_VERSION_JUNK_RE = re.compile(
    r'(?i)(\.com\b|www\.|https?://|fordjonly|promo|download|\.mp3\b|zippy|traxcrate)'
)

# ---------------------------------------------------------------------------
# Safety: concatenated artist names (no separator)
# ---------------------------------------------------------------------------
# Detects a lowercase→uppercase boundary within a single word — the signature
# of names pasted together without a separator (e.g. "KaybeeShimzaBlack").
# Two or more such transitions in the full artist string (with no explicit
# separator present) is considered unsafe.
_CAMEL_TRANSITION_RE = re.compile(r'[a-z][A-Z]')
_COLLAB_SEP_RE       = re.compile(
    r'[;,&/]|\bfeat\.|\bft\.|\bvs\.?', re.IGNORECASE
)


def _sanitize(s: str) -> str:
    """Remove invalid filename chars; collapse repeated spaces and hyphens."""
    s = _INVALID_RE.sub("", s)
    s = _MULTISPC_RE.sub(" ", s)
    s = _MULTIDASH_RE.sub("-", s)
    return s.strip().rstrip(".")


# ---------------------------------------------------------------------------
# Tag reading
# ---------------------------------------------------------------------------

def _first(tag) -> str:
    """Extract a plain string from a mutagen tag value."""
    if tag is None:
        return ""
    # Mutagen ID3 text frames expose .text as a list
    if hasattr(tag, "text") and isinstance(tag.text, list):
        return str(tag.text[0]).strip() if tag.text else ""
    if isinstance(tag, list):
        return str(tag[0]).strip() if tag else ""
    return str(tag).strip()


def _read_tags(path: Path) -> dict:
    """
    Return {"artist": str, "title": str, "version": str, "album": str}.
    All values are stripped strings; empty string on any failure.
    Handles MP3 (ID3), FLAC (Vorbis), M4A (MP4), OGG, OPUS, WAV, AIFF.
    """
    try:
        from mutagen import File as _MFile
        mf = _MFile(str(path), easy=False)
    except Exception:
        return {"artist": "", "title": "", "version": "", "album": ""}

    if mf is None or mf.tags is None:
        return {"artist": "", "title": "", "version": "", "album": ""}

    t = mf.tags

    # ID3 (MP3, AIFF, WAV)
    artist  = _first(t.get("TPE1")) or _first(t.get("TPE2"))
    title   = _first(t.get("TIT2"))
    version = _first(t.get("TIT3"))   # TIT3 = Subtitle / Version description
    album   = _first(t.get("TALB"))

    # Vorbis Comment (FLAC, OGG, OPUS) — case-insensitive keys in mutagen
    if not artist:
        artist = _first(t.get("artist") or t.get("ARTIST"))
    if not title:
        title = _first(t.get("title") or t.get("TITLE"))
    if not version:
        version = _first(t.get("version") or t.get("VERSION"))
    if not album:
        album = _first(t.get("album") or t.get("ALBUM"))

    # MP4 / M4A (\xa9 = © prefix)
    if not artist:
        artist = _first(t.get("\xa9ART"))
    if not title:
        title = _first(t.get("\xa9nam"))
    if not album:
        album = _first(t.get("\xa9alb"))

    return {
        "artist":  artist.strip(),
        "title":   title.strip(),
        "version": version.strip(),
        "album":   album.strip(),
    }


# ---------------------------------------------------------------------------
# Version deduplication
# ---------------------------------------------------------------------------

def _version_in_title(title: str, version: str) -> bool:
    """True when version is already present inside title (case-insensitive)."""
    if not version:
        return True
    return version.lower() in title.lower()


# ---------------------------------------------------------------------------
# Target stem builder
# ---------------------------------------------------------------------------

def _build_stem(artist: str, title: str, version: str) -> str:
    a = _sanitize(artist)
    t = _sanitize(title)
    v = _sanitize(version)
    if v and not _version_in_title(t, v):
        return f"{a} - {t} ({v})"
    return f"{a} - {t}"


# ---------------------------------------------------------------------------
# Collision-safe target path
# ---------------------------------------------------------------------------

def _safe_target(directory: Path, stem: str, ext: str, src: Path) -> tuple[Path, bool]:
    """
    Return (target_path, had_collision).

    Treats src as the current occupant of stem+ext: if the canonical path
    resolves to src itself (name already correct), returns (src, False) so
    the caller can detect the no-change case via ``target == src``.
    Otherwise appends ' (1)', ' (2)', … until a free slot is found.
    """
    candidate = directory / (stem + ext)
    if candidate == src or not candidate.exists():
        return candidate, False
    i = 1
    while True:
        candidate = directory / (f"{stem} ({i})" + ext)
        if not candidate.exists():
            return candidate, True
        i += 1


# ---------------------------------------------------------------------------
# Safety helpers
# ---------------------------------------------------------------------------

def _version_is_junk(version: str) -> bool:
    """True when version contains URL, watermark, or promo text."""
    return bool(_VERSION_JUNK_RE.search(version))


def _is_unsafe_artist(artist: str) -> bool:
    """
    True when artist looks like multiple names pasted together without a separator.

    Logic: 2+ lowercase→uppercase transitions in the full string, AND no
    recognised collaboration separator (,  ;  &  /  feat.  ft.  vs.) present.
    Single transitions are tolerated for names like 'McDonald' or 'McCartney'.
    """
    if _COLLAB_SEP_RE.search(artist):
        return False  # separators exist — trust the tag
    return len(_CAMEL_TRANSITION_RE.findall(artist)) >= 2


# ---------------------------------------------------------------------------
# Artist review queue writer
# ---------------------------------------------------------------------------

def _write_review_entry(
    path: Path,
    artist: str,
    title: str,
    album: str,
    moved: bool = False,
    moved_to: "str | None" = None,
) -> None:
    _ARTIST_REVIEW_QUEUE.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_stage":     "filename-normalize",
        "reason":           "unsafe_artist_concat",
        "file_path":        str(path),
        "current_artist":   artist,
        "title":            title,
        "album":            album,
        "current_filename": path.name,
        "suggested_action": "manually correct artist tag, then rerun filename-normalize",
        "moved":            moved,
        "moved_to":         moved_to,
    }
    try:
        with open(_ARTIST_REVIEW_QUEUE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"    [WARN] Could not write review queue: {exc}")


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
# File collection (with limit)
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
# Main runner
# ---------------------------------------------------------------------------

def run(input_dir: Path, *, apply: bool = False, verbose: bool = False,
        force: bool = False, reset_stage: bool = False,
        limit: "int | None" = None,
        move_artist_review: bool = False) -> dict:
    """
    Walk input_dir for audio files and rename them to the canonical pattern.

    Returns a stats dict:
        scanned, candidates, renamed, skipped_no_tags, skipped_unsafe_artist,
        artist_review_count, moved_to_artist_review,
        skipped_no_change, skipped_errors, collisions, stripped_version
    """
    input_dir = Path(input_dir).resolve()
    stats = dict(
        scanned=0, candidates=0, renamed=0,
        skipped_no_tags=0, skipped_unsafe_artist=0,
        artist_review_count=0, moved_to_artist_review=0,
        skipped_no_change=0, skipped_errors=0,
        collisions=0, stripped_version=0,
    )

    _stage = "filename-normalize"
    if reset_stage:
        _proc.clear_stage(_stage)
    n_skip_unchanged = 0

    plan:          list[tuple[Path, Path, bool, str]] = []  # (src, dst, had_collision, note)
    skipped:       list[tuple[Path, str]]             = []
    artist_review: list[tuple[Path, str, str, str]]   = []  # (path, artist, title, album)

    files = _collect_audio_files(input_dir, limit)
    stats["scanned"] = len(files)

    for path in files:
        if not force and _proc.should_skip(_stage, path):
            n_skip_unchanged += 1
            continue

        tags    = _read_tags(path)
        artist  = tags["artist"]
        title   = tags["title"]
        version = tags["version"]
        album   = tags["album"]

        if not artist or not title:
            reason = "missing_artist" if not artist else "missing_title"
            skipped.append((path, reason))
            stats["skipped_no_tags"] += 1
            if apply:
                _proc.record(_stage, path, "skipped", reason)
            continue

        # Artist safety: concatenated names without separators → review queue
        if _is_unsafe_artist(artist):
            artist_review.append((path, artist, title, album))
            stats["skipped_unsafe_artist"] += 1
            if apply:
                _proc.record(_stage, path, "skipped", "unsafe_artist_concat")
            continue

        # Version safety: strip junk watermark/promo/URL content
        version_note = ""
        if version and _version_is_junk(version):
            version_note = f"  (version stripped — junk: {version!r})"
            version = ""
            stats["stripped_version"] += 1

        if not _sanitize(artist) or not _sanitize(title):
            reason = "empty_sanitized_artist" if not _sanitize(artist) else "empty_sanitized_title"
            skipped.append((path, reason))
            stats["skipped_no_tags"] += 1
            if apply:
                _proc.record(_stage, path, "skipped", reason)
            continue

        stem         = _build_stem(artist, title, version)
        target, coll = _safe_target(path.parent, stem, path.suffix, src=path)

        if target == path:
            stats["skipped_no_change"] += 1
            if apply:
                _proc.record(_stage, path, "no_change")
            if verbose:
                print(f"  OK    {path.name}")
            continue

        stats["candidates"] += 1
        if coll:
            stats["collisions"] += 1
        plan.append((path, target, coll, version_note))

    # ── Output ──────────────────────────────────────────────────────────────
    mode = "APPLY" if apply else "PREVIEW"
    print(f"\n=== filename-normalize {mode} ===")
    print(f"\n  Input : {input_dir}\n")

    for src, dst, coll, version_note in plan:
        print("  RENAME:")
        print(f"    FROM: {src.name}")
        print(f"    TO  : {dst.name}")
        if coll:
            print(f"    Note : collision — suffix appended")
        if version_note:
            print(f"    Note :{version_note}")
        print(f"    Reason: embedded tags → filename")
        print()

        if apply:
            try:
                src.rename(dst)
                stats["renamed"] += 1
                _proc.rename_path(src, dst)
                _proc.record(_stage, dst, "success")
            except Exception as exc:
                print(f"    ERROR: {exc}")
                stats["skipped_errors"] += 1
                _proc.record(_stage, src, "error", str(exc)[:120])

    # ── Artist review queue ─────────────────────────────────────────────────
    if artist_review:
        print(f"  ARTIST REVIEW ({len(artist_review)} file(s)) — artist tag looks like concatenated names:")
        print()
        for path, artist, title, album in artist_review:
            moved    = False
            moved_to = None
            if apply and move_artist_review:
                try:
                    rel      = path.relative_to(input_dir)
                    dest_dir = _ARTIST_REVIEW_DIR / rel.parent
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest     = dest_dir / path.name
                    shutil.move(str(path), str(dest))
                    moved    = True
                    moved_to = str(dest)
                    stats["moved_to_artist_review"] += 1
                    _proc.record(_stage, dest, "skipped", "unsafe_artist_concat")
                except Exception as exc:
                    print(f"    [WARN] Could not move {path.name}: {exc}")
            _write_review_entry(path, artist, title, album, moved=moved, moved_to=moved_to)
            stats["artist_review_count"] += 1
            print(f"  REVIEW  {path.name}")
            print(f"    Artist : {artist}")
            print(f"    Title  : {title}")
            if album:
                print(f"    Album  : {album}")
            if moved:
                print(f"    Moved  → {moved_to}")
            else:
                action = "will move" if (apply and move_artist_review) else "use --move-artist-review --apply to move"
                print(f"    Action : queued for review ({action})")
            print()
        print(f"  Review queue: {_ARTIST_REVIEW_QUEUE}")
        print()

    for path, reason in skipped:
        print(f"  SKIP  {path.name}")
        print(f"    Reason: {reason}")
        print()

    if n_skip_unchanged:
        print(f"  (Skipped unchanged: {n_skip_unchanged}  — use --force to reprocess)")

    return stats
