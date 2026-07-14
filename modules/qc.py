"""
Quality Control — scan files with ffprobe, reject corrupt/low-quality audio.

Results:
    status='rejected' in DB, file moved to REJECTED dir.
    status='pending'  in DB, file remains for further processing.
"""
import json
import logging
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import config
import db

log = logging.getLogger(__name__)


def _probe(path: Path) -> dict:
    """Run ffprobe and return parsed JSON. Raises on ffprobe error."""
    cmd = [
        config.FFPROBE_BIN,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def _check_file(path: Path) -> Tuple[bool, str, dict]:
    """
    Check one audio file.
    Returns (ok, reason, metadata_dict).
    """
    meta: dict = {}

    # 1. File must be non-empty
    size = path.stat().st_size
    if size == 0:
        return False, "empty file", meta

    # 2. ffprobe must succeed
    try:
        probe = _probe(path)
    except (RuntimeError, json.JSONDecodeError, subprocess.TimeoutExpired) as exc:
        return False, f"ffprobe error: {exc}", meta

    fmt = probe.get("format", {})
    streams = probe.get("streams", [])

    # 3. Must have at least one audio stream
    audio_streams = [s for s in streams if s.get("codec_type") == "audio"]
    if not audio_streams:
        return False, "no audio stream", meta

    # 4. Duration check
    try:
        duration = float(fmt.get("duration", 0))
    except (TypeError, ValueError):
        duration = 0.0

    if duration < config.MIN_DURATION_SEC:
        return False, f"too short: {duration:.1f}s < {config.MIN_DURATION_SEC}s", meta
    if duration > config.MAX_DURATION_SEC:
        return False, f"too long: {duration:.1f}s > {config.MAX_DURATION_SEC}s", meta

    # 5. Bitrate check (skip for lossless)
    fmt_name = fmt.get("format_name", "")
    is_lossless = any(x in fmt_name for x in ("flac", "wav", "aiff", "pcm"))
    try:
        bitrate_kbps = int(fmt.get("bit_rate", 0)) // 1000
    except (TypeError, ValueError):
        bitrate_kbps = 0

    if not is_lossless and bitrate_kbps > 0 and bitrate_kbps < config.MIN_BITRATE_KBPS:
        return (
            False,
            f"bitrate too low: {bitrate_kbps}kbps < {config.MIN_BITRATE_KBPS}kbps",
            meta,
        )

    meta = {
        "duration_sec":   round(duration, 2),
        "bitrate_kbps":   bitrate_kbps,
        "filesize_bytes": size,
    }
    return True, "ok", meta


def _reject(path: Path, reason: str, dry_run: bool) -> None:
    dest = config.REJECTED / path.name
    log.warning("REJECT %s — %s", path.name, reason)
    if not dry_run:
        config.REJECTED.mkdir(parents=True, exist_ok=True)
        # Avoid overwriting an existing rejected file with the same name
        if dest.exists():
            dest = config.REJECTED / f"{path.stem}__{path.stat().st_ino}{path.suffix}"
        shutil.move(str(path), str(dest))


def run(files: List[Path], run_id: int, dry_run: bool = False) -> List[Path]:
    """
    QC-check every file in `files`.
    Returns the list of files that passed QC (still in their original location).
    """
    passed: List[Path] = []
    rejected_count = 0

    for path in files:
        if not path.exists():
            log.warning("File disappeared before QC: %s", path)
            continue

        ok, reason, meta = _check_file(path)

        if ok:
            db.upsert_track(
                str(path),
                status="pending",
                **meta,
            )
            passed.append(path)
        else:
            rejected_count += 1
            db.upsert_track(
                str(path),
                status="rejected",
                error_msg=reason,
                **meta,
            )
            _reject(path, reason, dry_run)

    log.info("QC: %d passed, %d rejected", len(passed), rejected_count)
    return passed
