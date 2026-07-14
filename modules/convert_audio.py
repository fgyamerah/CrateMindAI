"""
convert-audio — Convert .m4a files to .aiff, preserving metadata and folder structure.

Workflow per file:
  1. ffprobe check — skip corrupt / unreadable source files
  2. Build output path under --dst (relative folder structure preserved)
  3. Skip if output already exists, unless --overwrite
  4. Run ffmpeg: decode audio, remap metadata, encode as 16-bit PCM AIFF
  5. Verify output: ffprobe must succeed + duration check vs source
  6. On success  : move original .m4a to --archive (same relative structure)
  7. On failure  : log error, leave original untouched

Parallelism:
  ThreadPoolExecutor — N worker threads run ffmpeg concurrently.
  Results are collected in-order after the pool drains and then
  written to the log in source-path order, so the log is deterministic.

Caveats:
  - Output codec is always pcm_s16be (16-bit big-endian PCM) — standard AIFF
    format understood by all DJ software (Rekordbox, Serato, Traktor).
  - Metadata is mapped with -map_metadata 0 (container-level metadata copy).
  - If source M4A is Apple Lossless (ALAC), downsampling to 16-bit is lossy.
    Use this tool only for AAC-encoded M4A files from DJ pools/Beatport.
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class ConvResult:
    src:       Path
    dst:       Optional[Path]
    archived:  Optional[Path]
    status:    str            # "ok" | "skipped" | "failed" | "corrupt_src"
    reason:    str            # human-readable explanation (empty string on clean ok)
    src_dur:   Optional[float]
    dst_dur:   Optional[float]


# ---------------------------------------------------------------------------
# ffprobe helpers
# ---------------------------------------------------------------------------

def _probe(path: Path, ffprobe_bin: str) -> Tuple[bool, Optional[float]]:
    """
    Return (is_readable, duration_sec).

    Uses -show_format so a single JSON parse gives us everything we need.
    Returns (False, None) when ffprobe cannot read the file.
    """
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        log.error("ffprobe not found at '%s'", ffprobe_bin)
        return False, None
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out for %s", path.name)
        return False, None

    if r.returncode != 0 or not r.stdout.strip():
        return False, None

    try:
        data = json.loads(r.stdout)
        fmt  = data.get("format", {})
        dur  = fmt.get("duration")
        return True, float(dur) if dur else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return False, None


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _build_dst(src: Path, src_root: Path, dst_root: Path) -> Path:
    """Preserve relative folder structure; swap extension to .aiff."""
    return (dst_root / src.relative_to(src_root)).with_suffix(".aiff")


def _build_archive(src: Path, src_root: Path, archive_root: Path) -> Path:
    """Preserve relative folder structure in the archive tree."""
    return archive_root / src.relative_to(src_root)


def _collision_free(path: Path) -> Path:
    """Return path unchanged, or with _1/_2/… inserted before the suffix."""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    n = 1
    while True:
        candidate = parent / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# Single-file worker (runs inside thread pool)
# ---------------------------------------------------------------------------

def _convert_one(
    src:          Path,
    src_root:     Path,
    dst_root:     Path,
    archive_root: Path,
    overwrite:    bool,
    tolerance:    float,
    ffmpeg_bin:   str,
    ffprobe_bin:  str,
    dry_run:      bool,
) -> ConvResult:
    """
    Convert a single .m4a to .aiff.

    Returns a ConvResult describing the outcome.  Never raises — all errors
    are captured and returned as status="failed" or status="corrupt_src".
    """
    dst     = _build_dst(src, src_root, dst_root)
    archive = _build_archive(src, src_root, archive_root)

    # ------------------------------------------------------------------
    # 1. Skip check
    # ------------------------------------------------------------------
    if dst.exists() and not overwrite:
        return ConvResult(src, dst, None, "skipped",
                          "output already exists (pass --overwrite to force)", None, None)

    # ------------------------------------------------------------------
    # 2. Probe source
    # ------------------------------------------------------------------
    src_readable, src_dur = _probe(src, ffprobe_bin)
    if not src_readable:
        return ConvResult(src, dst, None, "corrupt_src",
                          "ffprobe could not read source file", None, None)

    # ------------------------------------------------------------------
    # 3. Dry-run exit
    # ------------------------------------------------------------------
    if dry_run:
        return ConvResult(src, dst, archive, "ok",
                          "[dry-run — no files written]", src_dur, None)

    # ------------------------------------------------------------------
    # 4. Convert with ffmpeg
    # ------------------------------------------------------------------
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_bin,
        "-y",                  # overwrite output silently
        "-loglevel", "error",
        "-i", str(src),
        "-map_metadata", "0",  # copy container-level metadata
        "-c:a", "pcm_s16be",   # 16-bit big-endian PCM — standard AIFF codec
        str(dst),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=600)
    except FileNotFoundError:
        return ConvResult(src, dst, None, "failed",
                          f"ffmpeg not found at '{ffmpeg_bin}'", src_dur, None)
    except subprocess.TimeoutExpired:
        dst.unlink(missing_ok=True)
        return ConvResult(src, dst, None, "failed",
                          "ffmpeg timed out (10-min limit)", src_dur, None)

    if r.returncode != 0:
        err = r.stderr.decode(errors="replace").strip()[:300]
        dst.unlink(missing_ok=True)
        return ConvResult(src, dst, None, "failed",
                          f"ffmpeg rc={r.returncode}: {err}", src_dur, None)

    # ------------------------------------------------------------------
    # 5. Verify output
    # ------------------------------------------------------------------
    dst_readable, dst_dur = _probe(dst, ffprobe_bin)
    if not dst_readable:
        dst.unlink(missing_ok=True)
        return ConvResult(src, dst, None, "failed",
                          "output not readable by ffprobe after conversion", src_dur, None)

    if src_dur is not None and dst_dur is not None:
        delta = abs(dst_dur - src_dur)
        if delta > tolerance:
            dst.unlink(missing_ok=True)
            return ConvResult(
                src, dst, None, "failed",
                f"duration mismatch: src={src_dur:.2f}s dst={dst_dur:.2f}s "
                f"delta={delta:.2f}s > tolerance={tolerance:.2f}s",
                src_dur, dst_dur,
            )

    # ------------------------------------------------------------------
    # 6. Archive original (only after verified conversion)
    # ------------------------------------------------------------------
    archive_dest = _collision_free(archive)
    archive_dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(src), str(archive_dest))
    except Exception as exc:
        # Conversion and verification succeeded; archive move failed.
        # The converted file is safe.  Report ok but surface the archive error.
        return ConvResult(
            src, dst, None, "ok",
            f"converted+verified OK, but archive move failed: {exc}",
            src_dur, dst_dur,
        )

    return ConvResult(src, dst, archive_dest, "ok", "", src_dur, dst_dur)


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def _open_run_log(log_dir: Path, dry_run: bool):
    """Return an open file handle for this run's log, or None in dry-run mode."""
    if dry_run:
        return None
    log_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = log_dir / f"convert_{ts}.log"
    fh = open(path, "w", encoding="utf-8")
    fh._path = path   # type: ignore[attr-defined]   # stash path for caller summary
    return fh


def _wlog(fh, line: str) -> None:
    """Write line to log file (if open) and emit via module logger."""
    log.info("%s", line)
    if fh is not None:
        fh.write(line + "\n")
        fh.flush()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    src:           Path,
    dst:           Path,
    archive:       Path,
    workers:       int            = 4,
    overwrite:     bool           = False,
    tolerance:     float          = 1.0,
    dry_run:       bool           = False,
    verbose:       bool           = False,
    show_progress: bool           = True,
    log_dir:       Optional[Path] = None,
    ffmpeg_bin:    str            = "ffmpeg",
    ffprobe_bin:   str            = "ffprobe",
) -> int:
    """
    Scan src for .m4a files, convert each to .aiff under dst, archive originals.

    Returns 0 on full success, 1 if any files failed or had corrupt sources.
    """
    if verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # ------------------------------------------------------------------
    # Discover .m4a files
    # ------------------------------------------------------------------
    files: List[Path] = []
    seen: set = set()
    for f in sorted(src.rglob("*")):
        if f.suffix.lower() == ".m4a" and f.is_file():
            key = str(f)
            if key not in seen:
                seen.add(key)
                files.append(f)

    if not files:
        msg = f"convert-audio: no .m4a files found under {src}"
        log.info("%s", msg)
        print(msg)
        return 0

    print(
        f"convert-audio: {len(files)} .m4a file(s) found under {src}"
        + ("  [DRY RUN]" if dry_run else "")
    )

    # ------------------------------------------------------------------
    # Open log file
    # ------------------------------------------------------------------
    if log_dir is None:
        log_dir = config.LOGS_DIR / "convert_audio"
    fh = _open_run_log(log_dir, dry_run)

    _wlog(fh, f"convert-audio — {datetime.now().isoformat(timespec='seconds')}")
    _wlog(fh, f"  src       : {src}")
    _wlog(fh, f"  dst       : {dst}")
    _wlog(fh, f"  archive   : {archive}")
    _wlog(fh, f"  files     : {len(files)}")
    _wlog(fh, f"  workers   : {workers}")
    _wlog(fh, f"  overwrite : {overwrite}")
    _wlog(fh, f"  tolerance : {tolerance}s")
    _wlog(fh, f"  dry_run   : {dry_run}")
    _wlog(fh, f"  ffmpeg    : {ffmpeg_bin}")
    _wlog(fh, f"  ffprobe   : {ffprobe_bin}")
    _wlog(fh, "")

    # ------------------------------------------------------------------
    # tqdm (optional progress bar)
    # ------------------------------------------------------------------
    _tqdm = None
    if show_progress:
        try:
            from tqdm import tqdm as _tqdm_import  # type: ignore
            _tqdm = _tqdm_import
        except ImportError:
            print("  (install tqdm for a progress bar: pip install tqdm)")

    # ------------------------------------------------------------------
    # Parallel conversion
    # ------------------------------------------------------------------
    conv_kwargs = dict(
        src_root=src, dst_root=dst, archive_root=archive,
        overwrite=overwrite, tolerance=tolerance,
        ffmpeg_bin=ffmpeg_bin, ffprobe_bin=ffprobe_bin,
        dry_run=dry_run,
    )

    results: List[ConvResult] = []
    t_start = time.monotonic()

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        future_map = {pool.submit(_convert_one, f, **conv_kwargs): f for f in files}

        completed_iter = concurrent.futures.as_completed(future_map)
        if _tqdm is not None:
            completed_iter = _tqdm(completed_iter, total=len(files),
                                   unit="file", desc="Converting")

        for fut in completed_iter:
            try:
                res = fut.result()
            except Exception as exc:
                src_path = future_map[fut]
                res = ConvResult(src_path, None, None, "failed",
                                 f"unhandled worker error: {exc}", None, None)
            results.append(res)

    elapsed = time.monotonic() - t_start

    # ------------------------------------------------------------------
    # Write detailed per-file log (sorted by source path — deterministic)
    # ------------------------------------------------------------------
    results.sort(key=lambda r: str(r.src))
    for res in results:
        dst_str     = str(res.dst)      if res.dst      else "N/A"
        archive_str = str(res.archived) if res.archived else "N/A"

        dur_info = ""
        if res.src_dur is not None and res.dst_dur is not None:
            dur_info = f"src={res.src_dur:.2f}s  dst={res.dst_dur:.2f}s"
        elif res.src_dur is not None:
            dur_info = f"src={res.src_dur:.2f}s"

        _wlog(fh, f"[{res.status.upper()}]  {res.src.name}")
        _wlog(fh, f"  src     : {res.src}")
        _wlog(fh, f"  dst     : {dst_str}")
        _wlog(fh, f"  archive : {archive_str}")
        if dur_info:
            _wlog(fh, f"  duration: {dur_info}")
        if res.reason:
            _wlog(fh, f"  note    : {res.reason}")
        _wlog(fh, "")

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    ok_count      = sum(1 for r in results if r.status == "ok")
    skipped_count = sum(1 for r in results if r.status == "skipped")
    failed_count  = sum(1 for r in results if r.status == "failed")
    corrupt_count = sum(1 for r in results if r.status == "corrupt_src")

    log_path_str = str(getattr(fh, "_path", "N/A")) if fh else "[dry-run]"

    _wlog(fh, "=" * 64)
    _wlog(fh, f"SUMMARY")
    _wlog(fh, f"  Converted    : {ok_count}")
    _wlog(fh, f"  Skipped      : {skipped_count}  (already existed)")
    _wlog(fh, f"  Failed       : {failed_count}   (conversion or verification error)")
    _wlog(fh, f"  Corrupt src  : {corrupt_count}  (ffprobe could not read source)")
    _wlog(fh, f"  Total found  : {len(files)}")
    _wlog(fh, f"  Elapsed      : {elapsed:.1f}s")
    if not dry_run:
        _wlog(fh, f"  Log          : {log_path_str}")
    _wlog(fh, "=" * 64)

    if fh is not None:
        fh.close()

    # Terminal summary
    summary = (
        f"\nconvert-audio complete:"
        f"  converted={ok_count}"
        f"  skipped={skipped_count}"
        f"  failed={failed_count}"
        f"  corrupt_src={corrupt_count}"
        f"  elapsed={elapsed:.1f}s"
    )
    if not dry_run:
        summary += f"  log={log_path_str}"
    else:
        summary += "  [DRY RUN — nothing written]"
    print(summary)

    return 0 if (failed_count == 0 and corrupt_count == 0) else 1
