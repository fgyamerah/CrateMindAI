"""
analyze-missing — scan library for tracks without BPM or Camelot key,
run detection only on those, write results back to DB and audio file tags.

Usage:
    python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/
    python3 pipeline.py analyze-missing --limit 100 --timeout-sec 600
    python3 pipeline.py analyze-missing --dry-run --verbose

Path mode (--path given):
    Scans the filesystem under the given directory. For each audio file found,
    looks up its current DB row to check BPM/key state. Files not in the DB
    are treated as needing full analysis. Stale DB paths are never used as the
    primary source — only real, present files are processed.

DB mode (no --path):
    Queries the DB for status='ok' rows with missing BPM/key. Files that no
    longer exist on disk are skipped and counted as stale.
"""
import concurrent.futures
import logging
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import config
import db
from modules import analyzer

log = logging.getLogger(__name__)

_RE_VALID_CAM = re.compile(r"^(1[0-2]|[1-9])[AB]$")


# ---------------------------------------------------------------------------
# Field-level helpers
# ---------------------------------------------------------------------------

def _needs_bpm(row: dict) -> bool:
    bpm = row.get("bpm")
    return (not bpm) or (float(bpm) <= 0)


def _needs_key(row: dict) -> bool:
    key = (row.get("key_camelot") or "").strip()
    return (not key) or (not _RE_VALID_CAM.match(key))


def _row_for_path(path: Path) -> dict:
    """
    Return a plain-dict DB row for path, or an empty dict if no record exists.
    sqlite3.Row does not support .get(), so always convert immediately.
    """
    row = db.get_track(str(path))
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Candidate selection
# ---------------------------------------------------------------------------

def _select_from_filesystem(root: Path) -> Tuple[List[Tuple[Path, dict]], int]:
    """
    Scan the filesystem under root, look up each file's DB row, and return
    (candidates, total_scanned) where candidates is a list of (path, row) pairs
    for files that need BPM and/or key.
    """
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))

    seen: set = set()
    deduped = []
    for f in sorted(files):
        key = str(f)
        if key not in seen:
            seen.add(key)
            deduped.append(f)

    candidates = []
    for path in deduped:
        row = _row_for_path(path)
        if row.get("status") == "corrupt":
            continue   # already quarantined — do not re-attempt
        if _needs_bpm(row) or _needs_key(row):
            candidates.append((path, row))

    return candidates, len(deduped)


def _select_from_db() -> Tuple[List[Tuple[Path, dict]], int, int]:
    """
    Query DB for status='ok' rows missing BPM/key. Skip files that no longer
    exist on disk (stale rows).

    Returns (candidates, total_rows_checked, stale_count) where candidates is
    a list of (path, row) pairs for files that exist and need analysis.
    """
    all_tracks = db.get_all_ok_tracks()
    stale = 0
    candidates = []
    for raw_row in all_tracks:
        row = dict(raw_row)   # sqlite3.Row → plain dict
        fp = row.get("filepath") or ""
        if not (_needs_bpm(row) or _needs_key(row)):
            continue
        path = Path(fp)
        if not path.is_file():
            stale += 1
            log.debug("Stale DB entry (not a file): %s", fp)
            continue
        candidates.append((path, row))

    return candidates, len(all_tracks), stale


# ---------------------------------------------------------------------------
# Tag writing — format-specific, mirrors tagger.py conventions
# ---------------------------------------------------------------------------

def _write_tags_bpm_key(
    path: Path,
    bpm: Optional[float],
    musical_key: Optional[str],
    camelot: Optional[str],
    dry_run: bool,
) -> bool:
    """
    Write newly-detected BPM and/or key to audio file tags.
    Only writes fields that were actually detected (not None).
    Uses format-specific mutagen APIs — no easy-tag wrapper — to match the
    tag schema that Rekordbox and the rest of the pipeline expect:

      MP3/ID3 : TBPM (BPM integer string), TKEY (Camelot, e.g. "8A")
      FLAC    : BPM (integer string), INITIALKEY (Camelot), KEY (musical)
      M4A     : tmpo (BPM int), ----:com.apple.iTunes:initialkey (Camelot bytes)
      other   : unsupported — logged and skipped (returns True, not a failure)

    Returns True on success, dry_run, or unsupported format.
    Returns False only on a write error.
    """
    if dry_run:
        return True

    suffix = path.suffix.lower()

    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3, ID3NoHeaderError, TBPM, TKEY
            try:
                audio = ID3(str(path))
            except ID3NoHeaderError:
                audio = ID3()
            if bpm is not None:
                audio["TBPM"] = TBPM(encoding=3, text=[str(int(round(bpm)))])
            if camelot:
                audio["TKEY"] = TKEY(encoding=3, text=[camelot])
            audio.save(str(path), v2_version=3)

        elif suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            if bpm is not None:
                audio["BPM"] = [str(int(round(bpm)))]
            if camelot:
                audio["INITIALKEY"] = [camelot]
            if musical_key:
                audio["KEY"] = [musical_key]
            audio.save()

        elif suffix in {".m4a", ".mp4", ".aac"}:
            from mutagen.mp4 import MP4
            audio = MP4(str(path))
            if bpm is not None:
                audio["tmpo"] = [int(round(bpm))]
            if camelot:
                audio["----:com.apple.iTunes:initialkey"] = [camelot.encode("utf-8")]
            audio.save()

        else:
            log.debug("Tag write: unsupported format %s — skipping %s", suffix, path.name)
            return True  # not a failure

        return True

    except Exception as exc:
        log.warning("Tag write failed for %s: %s", path.name, exc)
        return False


# Default per-file wall-clock limit (seconds). Prevents corrupt files from
# causing multi-hour resync loops in aubio or hanging librosa/ffmpeg decoders.
_DEFAULT_PER_FILE_TIMEOUT: float = 10.0


# ---------------------------------------------------------------------------
# Per-file analysis with hard timeout
# ---------------------------------------------------------------------------

def _analyse_file(
    path_obj: Path,
    genre: str,
    need_bpm: bool,
    need_key: bool,
    per_file_timeout: float,
) -> Tuple[Optional[float], Optional[str], Optional[str], bool]:
    """
    Run BPM and/or key detection with a hard per-file wall-clock timeout.

    Runs detection in a worker thread. If the thread does not return within
    per_file_timeout seconds the file is abandoned and timed_out=True is
    returned. The worker thread continues running in the background until its
    own subprocess timeout fires (typically 120 s in analyzer.py), which is
    acceptable for a CLI tool — leaked threads are bounded and eventually die.

    Returns: (bpm, musical_key, camelot, timed_out)
      bpm, musical_key, camelot — None if not detected or not needed
      timed_out                 — True if the file exceeded per_file_timeout
    """
    def _work() -> Tuple[Optional[float], Optional[str], Optional[str]]:
        bpm: Optional[float] = None
        musical: Optional[str] = None
        camelot: Optional[str] = None

        if need_bpm:
            bpm = analyzer.detect_bpm(path_obj, genre)

        if need_key:
            musical, camelot = analyzer.detect_key(path_obj)
            # Discard key if it didn't produce a valid Camelot value
            if not (camelot and _RE_VALID_CAM.match(camelot)):
                musical = None
                camelot = None

        return bpm, musical, camelot

    ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = ex.submit(_work)
    try:
        bpm, musical, camelot = future.result(timeout=per_file_timeout)
        ex.shutdown(wait=False)
        return bpm, musical, camelot, False
    except concurrent.futures.TimeoutError:
        ex.shutdown(wait=False)
        log.warning("Per-file timeout (%.0fs) exceeded — skipping: %s", per_file_timeout, path_obj.name)
        return None, None, None, True
    except Exception as exc:
        ex.shutdown(wait=False)
        log.warning("Analysis error for %s: %s", path_obj.name, exc)
        return None, None, None, False


# ---------------------------------------------------------------------------
# Corrupt-file isolation
# ---------------------------------------------------------------------------

# Persistent log for all moves and bad-path events — append across runs.
_CORRUPT_LOG_PATH = config.LOGS_DIR / "analyze_missing" / "corrupt_moves.txt"


def _resolve_corrupt_dest(src: Path, corrupt_base: Path) -> Path:
    """
    Return a collision-free destination path directly inside corrupt_base/.
    If the filename already exists, append _1, _2, … before the suffix.
    """
    corrupt_base.mkdir(parents=True, exist_ok=True)
    dest = corrupt_base / src.name
    if not dest.exists():
        return dest
    stem   = src.stem
    suffix = src.suffix
    n = 1
    while True:
        candidate = corrupt_base / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _move_to_corrupt(
    src: Path,
    reason: str,
    dry_run: bool,
    corrupt_fh,
    corrupt_base: Path,
) -> Optional[Path]:
    """
    Move src directly into corrupt_base/ and log the action.
    In dry_run mode logs intent but performs no filesystem operation.
    Returns the destination path (or None on move error).
    """
    dest = _resolve_corrupt_dest(src, corrupt_base)
    tag  = "[DRY-RUN WOULD MOVE]" if dry_run else "[MOVED_TO_CORRUPT]"

    _corrupt_log_line(corrupt_fh, f"{tag} {src}")
    _corrupt_log_line(corrupt_fh, f"       → {dest}")
    _corrupt_log_line(corrupt_fh, f"  reason : {reason}")
    _corrupt_log_line(corrupt_fh, "")

    if dry_run:
        log.info("CORRUPT (dry-run): would move %s → %s  reason=%s", src.name, dest, reason)
        return dest

    try:
        shutil.move(str(src), str(dest))
        log.info("[MOVED_TO_CORRUPT] %s → %s  reason=%s", src, dest, reason)
        return dest
    except Exception as exc:
        log.error("CORRUPT: failed to move %s: %s", src.name, exc)
        return None


def _log_bad_path(path_str: str, reason: str, corrupt_fh) -> None:
    """Log a bad/non-file path. No filesystem operation is performed."""
    _corrupt_log_line(corrupt_fh, f"[BAD_PATH] {path_str}")
    _corrupt_log_line(corrupt_fh, f"  reason : {reason}")
    _corrupt_log_line(corrupt_fh, "")
    log.warning("BAD_PATH: %s  reason=%s", path_str, reason)


def _open_corrupt_log(dry_run: bool):
    """
    Open (append) the persistent corrupt-moves log at _CORRUPT_LOG_PATH.
    Returns None in dry_run mode.
    """
    if dry_run:
        return None
    _CORRUPT_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(_CORRUPT_LOG_PATH, "a", encoding="utf-8")
    fh.write(f"\n{'='*72}\n")
    fh.write(f"analyze-missing run — {datetime.now().isoformat(timespec='seconds')}\n")
    fh.write(f"{'='*72}\n")
    fh.flush()
    return fh


def _corrupt_log_line(fh, line: str) -> None:
    if fh is not None:
        fh.write(line + "\n")
        fh.flush()


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

def _validate_path(path_obj: Path) -> Tuple[Optional[str], str]:
    """
    Validate that path_obj points to a real, regular file before analysis.

    Returns (tag, reason) where tag is None when the path is valid:
      None                   — valid regular file, proceed with analysis
      "MISSING_FILE"         — path does not exist; parent directory is present
                               (file renamed, deleted, or filename truncated)
      "PATH_RESOLUTION_ERROR"— neither path nor its parent directory exists;
                               the stored path is likely truncated or corrupt
                               (e.g. "_compilations" stored as "_compilation")
      "DIR_SKIP"             — path exists but is a directory, not a file
      "NOT_A_FILE"           — path exists but is not a regular file (device
                               node, broken symlink, etc.)
    """
    if not path_obj.exists():
        if not path_obj.parent.exists():
            return (
                "PATH_RESOLUTION_ERROR",
                f"parent directory does not exist: {path_obj.parent}",
            )
        return (
            "MISSING_FILE",
            f"file not found (parent dir OK — possible rename or truncation): {path_obj}",
        )

    if path_obj.is_dir():
        return "DIR_SKIP", f"path is a directory: {path_obj}"

    if not path_obj.is_file():
        return "NOT_A_FILE", f"exists but is not a regular file: {path_obj}"

    return None, ""


# ---------------------------------------------------------------------------
# Log writer
# ---------------------------------------------------------------------------

def _open_log(dry_run: bool):
    """
    Create log directory and return an open file handle for the run log.
    Returns None in dry_run mode.
    """
    if dry_run:
        return None

    log_dir = config.LOGS_DIR / "analyze_missing"
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = log_dir / f"{ts}.log"
    return open(log_path, "w", encoding="utf-8")


def _log_line(fh, line: str) -> None:
    if fh is not None:
        fh.write(line + "\n")
        fh.flush()
    log.info("%s", line)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    path: Optional[Path] = None,
    dry_run: bool = False,
    limit: Optional[int] = None,
    timeout_sec: Optional[float] = None,
    min_confidence: float = 0.0,
    verbose: bool = False,
    per_file_timeout: float = _DEFAULT_PER_FILE_TIMEOUT,
    isolate_corrupt: bool = True,
    corrupt_base_dir: Optional[Path] = None,
) -> int:
    """
    Scan the library for tracks missing BPM or Camelot key, analyse them,
    and write results back to the DB and audio file tags.

    Returns exit code: 0 on success, 1 on fatal error.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Resolve corrupt quarantine directory:
    #   1. explicit --corrupt-dir argument
    #   2. sibling _corrupt/ next to --path when given
    #   3. config.CORRUPT_DIR (config_local.py override or default)
    if corrupt_base_dir is None:
        corrupt_base_dir = (path / "_corrupt") if path is not None else config.CORRUPT_DIR

    # ------------------------------------------------------------------
    # Candidate selection — filesystem scan (--path) or DB query (no --path)
    # ------------------------------------------------------------------
    stale_count = 0
    total_scanned: Optional[int] = None   # only meaningful in path mode

    if path is not None:
        print(f"analyze-missing: scanning {path} ...")
        candidates, total_scanned = _select_from_filesystem(path)
    else:
        candidates, _total_db, stale_count = _select_from_db()

    total_candidates = len(candidates)

    if not candidates:
        if path is not None:
            print(
                f"analyze-missing: scanned {total_scanned} file(s) — "
                "none need BPM/key analysis."
            )
        else:
            msg = "analyze-missing: no tracks need analysis — library is complete."
            if stale_count:
                msg += f" ({stale_count} stale DB entries skipped)"
            print(msg)
        return 0

    if limit is not None:
        candidates = candidates[:limit]

    # Print header
    scanned_str = f"  ({total_scanned} files scanned)" if total_scanned is not None else ""
    stale_str   = f"  ({stale_count} stale DB entries skipped)" if stale_count else ""
    print(
        f"analyze-missing: {total_candidates} file(s) need BPM/key analysis"
        + scanned_str
        + stale_str
        + (f" — processing up to {limit}" if limit and limit < total_candidates else "")
        + (" [DRY RUN]" if dry_run else "")
    )

    deadline: Optional[float] = (time.monotonic() + timeout_sec) if timeout_sec else None

    fh          = _open_log(dry_run)
    corrupt_fh  = _open_corrupt_log(dry_run) if isolate_corrupt else None

    _log_line(fh, f"analyze-missing run started — {datetime.now().isoformat(timespec='seconds')}")
    if path is not None:
        _log_line(fh, f"  mode       : filesystem scan ({path})")
        _log_line(fh, f"  scanned    : {total_scanned}")
    else:
        _log_line(fh, f"  mode       : DB query")
        if stale_count:
            _log_line(fh, f"  stale skip : {stale_count}")
    _log_line(fh, f"  candidates : {total_candidates}")
    _log_line(fh, f"  processing : {len(candidates)}")
    _log_line(fh, f"  dry_run    : {dry_run}")
    if timeout_sec:
        _log_line(fh, f"  timeout    : {timeout_sec}s")
    _log_line(fh, f"  file limit : {per_file_timeout}s per file")
    _log_line(fh, f"  isolate    : {isolate_corrupt}")
    if isolate_corrupt:
        _log_line(fh, f"  corrupt dir: {corrupt_base_dir}")
    if min_confidence > 0:
        _log_line(fh, f"  min_conf   : {min_confidence}")
    _log_line(fh, "")

    analysed_bpm      = 0
    analysed_key      = 0
    skipped_timeout   = 0    # session-level timeout (remaining unprocessed)
    skipped_file_to   = 0    # per-file timeout (corrupt / slow decoder)
    failed            = 0
    moved_corrupt     = 0    # files physically moved to _corrupt/audio_failures/
    tag_errors        = 0
    # Path validation counters (mutually exclusive)
    cnt_bad_path      = 0    # PATH_RESOLUTION_ERROR — parent dir missing
    cnt_missing_file  = 0    # MISSING_FILE — parent OK but file absent
    cnt_dir_skip      = 0    # DIR_SKIP — path is a directory
    cnt_not_file      = 0    # NOT_A_FILE — other non-regular-file

    for i, (path_obj, row) in enumerate(candidates, 1):

        # Session-level timeout — stop the run
        if deadline is not None and time.monotonic() >= deadline:
            skipped_timeout = len(candidates) - (i - 1)
            _log_line(fh, f"  [TIMEOUT] session limit reached after {i-1} track(s) — {skipped_timeout} remaining")
            break

        # Path validation — must be a real, regular file before we touch it
        path_tag, path_reason = _validate_path(path_obj)
        if path_tag is not None:
            _log_line(fh, f"  {path_tag}: {path_obj}  ({path_reason})")
            if path_tag == "PATH_RESOLUTION_ERROR":
                cnt_bad_path += 1
            elif path_tag == "MISSING_FILE":
                cnt_missing_file += 1
            elif path_tag == "DIR_SKIP":
                cnt_dir_skip += 1
            else:
                cnt_not_file += 1
            if isolate_corrupt:
                _log_bad_path(str(path_obj), f"{path_tag}: {path_reason}", corrupt_fh)
            continue

        need_bpm = _needs_bpm(row)
        need_key = _needs_key(row)
        genre    = (row.get("genre") or "").strip()

        log.debug(
            "[%d/%d] %s  need_bpm=%s need_key=%s",
            i, len(candidates), path_obj.name, need_bpm, need_key,
        )

        # Run detection with per-file hard timeout
        new_bpm, new_musical, new_camelot, timed_out = _analyse_file(
            path_obj, genre, need_bpm, need_key, per_file_timeout,
        )

        if timed_out:
            _log_line(fh, f"  SKIP (timeout): {path_obj.name}")
            skipped_file_to += 1
            if isolate_corrupt:
                dest = _move_to_corrupt(
                    path_obj,
                    f"per-file timeout ({per_file_timeout:.0f}s) — likely corrupt/unreadable",
                    dry_run,
                    corrupt_fh,
                    corrupt_base_dir,
                )
                if dest and not dry_run:
                    moved_corrupt += 1
                    db.mark_status(str(path_obj), "corrupt", "moved to _corrupt: per-file timeout")
            continue

        if new_bpm is not None:
            analysed_bpm += 1
        if new_camelot is not None:
            analysed_key += 1

        # Nothing detected — skip DB/tag write
        if new_bpm is None and new_camelot is None:
            status = "FAILED"
            failed += 1
        else:
            status = "OK"

        parts = []
        if new_bpm is not None:
            parts.append(f"BPM={new_bpm:.1f}")
        if new_camelot is not None:
            parts.append(f"Key={new_camelot} ({new_musical})")
        if not parts:
            parts = ["no results"]

        _log_line(fh, f"  [{status}] {path_obj.name}  {' '.join(parts)}")

        if status == "FAILED":
            if isolate_corrupt:
                dest = _move_to_corrupt(
                    path_obj,
                    "no BPM or key recoverable — all decoders failed",
                    dry_run,
                    corrupt_fh,
                    corrupt_base_dir,
                )
                if dest and not dry_run:
                    moved_corrupt += 1
                    db.mark_status(str(path_obj), "corrupt", "moved to _corrupt: decode failure")
            continue

        # Write to DB
        if not dry_run:
            update: dict = {}
            if new_bpm is not None:
                update["bpm"] = new_bpm
            if new_musical is not None:
                update["key_musical"] = new_musical
            if new_camelot is not None:
                update["key_camelot"] = new_camelot
            if update:
                db.upsert_track(str(path_obj), **update)

        # Write to audio file tags
        tag_ok = _write_tags_bpm_key(
            path_obj,
            bpm=new_bpm,
            musical_key=new_musical,
            camelot=new_camelot,
            dry_run=dry_run,
        )
        if not tag_ok:
            tag_errors += 1

    # Summary
    _log_line(fh, "")
    _log_line(fh, f"analyze-missing completed — {datetime.now().isoformat(timespec='seconds')}")
    if total_scanned is not None:
        _log_line(fh, f"  Scanned          : {total_scanned}")
    if stale_count:
        _log_line(fh, f"  Stale skipped    : {stale_count}")
    if cnt_bad_path:
        _log_line(fh, f"  PATH_RES_ERROR   : {cnt_bad_path}  (parent dir missing — likely truncated path)")
    if cnt_missing_file:
        _log_line(fh, f"  Missing file     : {cnt_missing_file}  (parent dir OK, file absent)")
    if cnt_dir_skip:
        _log_line(fh, f"  Dir skipped      : {cnt_dir_skip}  (path is a directory)")
    if cnt_not_file:
        _log_line(fh, f"  Not-a-file       : {cnt_not_file}  (exists but not a regular file)")
    _log_line(fh, f"  BPM recovered    : {analysed_bpm}")
    _log_line(fh, f"  Key recovered    : {analysed_key}")
    _log_line(fh, f"  Failed (decode)  : {failed}")
    if skipped_file_to:
        _log_line(fh, f"  Timeout (file)   : {skipped_file_to}  ({per_file_timeout:.0f}s limit)")
    if moved_corrupt:
        label = "would move" if dry_run else "moved"
        _log_line(fh, f"  Corrupt {label}  : {moved_corrupt}  → {corrupt_base_dir}")
    if tag_errors:
        _log_line(fh, f"  Tag write errs   : {tag_errors}")
    if skipped_timeout:
        _log_line(fh, f"  Skip (session TO): {skipped_timeout}  (session limit reached)")
    total_path_errors = cnt_bad_path + cnt_missing_file + cnt_dir_skip + cnt_not_file
    if isolate_corrupt and not dry_run and (moved_corrupt or total_path_errors):
        _log_line(fh, f"  Corrupt log      : {_CORRUPT_LOG_PATH}")

    print(
        f"\nDone."
        + (f"  Scanned: {total_scanned}" if total_scanned is not None else "")
        + (f"  Stale skipped: {stale_count}" if stale_count else "")
        + (f"  PATH_RESOLUTION_ERROR: {cnt_bad_path}" if cnt_bad_path else "")
        + (f"  Missing files: {cnt_missing_file}" if cnt_missing_file else "")
        + (f"  Dir skipped: {cnt_dir_skip}" if cnt_dir_skip else "")
        + (f"  Not-a-file: {cnt_not_file}" if cnt_not_file else "")
        + f"  BPM recovered: {analysed_bpm}"
        + f"  Key recovered: {analysed_key}"
        + f"  Failed (decode): {failed}"
        + (f"  Timeout (file): {skipped_file_to}" if skipped_file_to else "")
        + (f"  Corrupt {'(dry-run) ' if dry_run else ''}moved: {moved_corrupt}" if moved_corrupt else "")
        + (f"  Skipped (session timeout): {skipped_timeout}" if skipped_timeout else "")
        + (f"  Tag errors: {tag_errors}" if tag_errors else "")
        + (" [DRY RUN — no writes]" if dry_run else "")
    )

    if corrupt_fh is not None:
        corrupt_fh.close()
    if fh is not None:
        fh.close()

    return 0
