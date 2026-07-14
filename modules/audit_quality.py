"""
modules/audit_quality.py

Audit library audio files for codec/bitrate quality.

Quality tiers (in descending order):
  LOSSLESS  — FLAC, ALAC, WAV, AIFF (codec-based, bitrate irrelevant)
  HIGH      — lossy codec (MP3, AAC …) >= 256 kbps
  MEDIUM    — lossy codec 192–255 kbps  (lower bound configurable via min_lossy_kbps)
  LOW       — lossy codec < 192 kbps    (below min_lossy_kbps)
  UNKNOWN   — ffprobe could not read the file or codec/bitrate not recognized

Design:
  - classify_tier() is a pure function with no I/O (unit testable)
  - _probe_file() wraps ffprobe; returns (codec, bitrate_kbps) or (None, None)
  - run() orchestrates: scan → probe → classify → report → optional move / tag
  - Default behavior is completely non-destructive (report-only)
  - Actions (move / write-tag) are opt-in via parameters
  - dry_run=True logs intended actions but makes no file changes

Quality tag locations (when --write-tags is active):
  MP3  : TXXX:QUALITY  (ID3v2.3 custom text frame)
  FLAC : QUALITY       (Vorbis comment)
  M4A  : ----:com.apple.iTunes:QUALITY  (MP4 freeform atom)
  AIFF/WAV : skipped safely (AIFF/WAV tagging is unreliable; logged, not failed)

Future use:
  quality_tier is stored in the tracks DB table for downstream features
  (set-builder LOW exclusion, quality-tier playlists, cleanup workflows).
"""
from __future__ import annotations

import csv
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality tier enum
# ---------------------------------------------------------------------------

class QualityTier(str, Enum):
    LOSSLESS = "LOSSLESS"
    HIGH     = "HIGH"
    MEDIUM   = "MEDIUM"
    LOW      = "LOW"
    UNKNOWN  = "UNKNOWN"


# ---------------------------------------------------------------------------
# Codec classification sets
# ---------------------------------------------------------------------------

# Codec names (as reported by ffprobe) that are always lossless
_LOSSLESS_CODECS: frozenset = frozenset({
    "flac",
    "alac",
    # PCM variants — WAV and AIFF container formats
    "pcm_s8",  "pcm_u8",
    "pcm_s16be", "pcm_s16le",
    "pcm_s24be", "pcm_s24le",
    "pcm_s32be", "pcm_s32le",
    "pcm_f32be", "pcm_f32le",
    "pcm_f64be", "pcm_f64le",
})

# Codec names that are lossy — quality is determined by bitrate
_LOSSY_CODECS: frozenset = frozenset({
    "mp3", "aac", "vorbis", "opus", "mp2", "ac3", "eac3", "wma",
})

# HIGH tier bitrate threshold (fixed — not configurable per spec)
_HIGH_KBPS = 256


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class AuditResult:
    """Per-file audit result."""
    filepath:     Path
    codec:        Optional[str]   # ffprobe codec_name, e.g. "mp3", "flac", "aac"
    bitrate_kbps: Optional[int]   # None for lossless or when bitrate unavailable
    quality_tier: QualityTier
    action_taken: str             # "none" | "moved" | "tag_written" | "skipped" | "unreadable"
    error:        Optional[str]   # human-readable reason for failures


# ---------------------------------------------------------------------------
# Pure classification function (no I/O — unit testable)
# ---------------------------------------------------------------------------

def classify_tier(
    codec:          Optional[str],
    bitrate_kbps:   Optional[int],
    min_lossy_kbps: int = 192,
) -> QualityTier:
    """
    Classify a track into a QualityTier.

    Pure function — no I/O, no side effects.

    Args:
        codec:          Codec name as returned by ffprobe (e.g. "mp3", "flac", "aac").
                        None → UNKNOWN.
        bitrate_kbps:   Bitrate in kbps.  Ignored for lossless codecs.
                        None for a lossy codec → UNKNOWN.
        min_lossy_kbps: Bitrate threshold that separates LOW from MEDIUM.
                        Tracks below this value are LOW; >= this value are MEDIUM
                        (unless >= _HIGH_KBPS, in which case HIGH).
                        Default: 192.

    Returns:
        QualityTier enum member.

    >>> classify_tier("flac", None)
    <QualityTier.LOSSLESS: 'LOSSLESS'>
    >>> classify_tier("alac", None)
    <QualityTier.LOSSLESS: 'LOSSLESS'>
    >>> classify_tier("pcm_s16le", None)
    <QualityTier.LOSSLESS: 'LOSSLESS'>
    >>> classify_tier("mp3", 320)
    <QualityTier.HIGH: 'HIGH'>
    >>> classify_tier("aac", 256)
    <QualityTier.HIGH: 'HIGH'>
    >>> classify_tier("mp3", 192)
    <QualityTier.MEDIUM: 'MEDIUM'>
    >>> classify_tier("aac", 128)
    <QualityTier.LOW: 'LOW'>
    >>> classify_tier(None, None)
    <QualityTier.UNKNOWN: 'UNKNOWN'>
    >>> classify_tier("mp3", None)
    <QualityTier.UNKNOWN: 'UNKNOWN'>
    """
    if not codec:
        return QualityTier.UNKNOWN

    c = codec.lower()

    # Lossless — codec alone is sufficient, bitrate irrelevant
    if c in _LOSSLESS_CODECS or c.startswith("pcm_"):
        return QualityTier.LOSSLESS

    # Lossy — need bitrate to decide tier
    if c in _LOSSY_CODECS:
        if bitrate_kbps is None:
            return QualityTier.UNKNOWN
        if bitrate_kbps >= _HIGH_KBPS:
            return QualityTier.HIGH
        if bitrate_kbps >= min_lossy_kbps:
            return QualityTier.MEDIUM
        return QualityTier.LOW

    # Unrecognized codec
    return QualityTier.UNKNOWN


# ---------------------------------------------------------------------------
# ffprobe probe helper
# ---------------------------------------------------------------------------

def _probe_file(
    path:        Path,
    ffprobe_bin: str = "ffprobe",
) -> Tuple[Optional[str], Optional[int]]:
    """
    Probe `path` with ffprobe and return (codec_name, bitrate_kbps).

    Returns (None, None) when:
    - ffprobe binary is not found
    - ffprobe returns a non-zero exit code
    - JSON output cannot be parsed
    - No audio stream is detected

    Bitrate preference order:
    1. Audio stream bit_rate (most accurate for lossy)
    2. Format-level bit_rate (container total — less accurate but available for FLAC/WAV)
    """
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(path),
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except FileNotFoundError:
        log.error("ffprobe not found at '%s' — install ffprobe or set FFPROBE_BIN", ffprobe_bin)
        return None, None
    except subprocess.TimeoutExpired:
        log.warning("ffprobe timed out probing: %s", path.name)
        return None, None
    except Exception as exc:
        log.warning("ffprobe unexpected error for %s: %s", path.name, exc)
        return None, None

    if r.returncode != 0:
        return None, None

    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None, None

    # Find the first audio stream
    codec = None
    stream_bitrate = None
    for stream in data.get("streams", []):
        if stream.get("codec_type") == "audio":
            codec = stream.get("codec_name")
            raw   = stream.get("bit_rate")
            if raw:
                try:
                    stream_bitrate = int(raw) // 1000
                except (ValueError, TypeError):
                    pass
            break

    if codec is None:
        return None, None

    # Prefer stream-level bitrate; fall back to container-level
    bitrate_kbps = stream_bitrate
    if bitrate_kbps is None:
        raw = data.get("format", {}).get("bit_rate")
        if raw:
            try:
                bitrate_kbps = int(raw) // 1000
            except (ValueError, TypeError):
                pass

    return codec, bitrate_kbps


# ---------------------------------------------------------------------------
# Quality tag writing
# ---------------------------------------------------------------------------

def _write_quality_tag(
    path:    Path,
    tier:    QualityTier,
    dry_run: bool = False,
) -> bool:
    """
    Write a QUALITY tag to the audio file.

    Tag locations:
      MP3  : TXXX:QUALITY  (ID3v2.3 custom text frame)
      FLAC : QUALITY       (Vorbis comment)
      M4A  : ----:com.apple.iTunes:QUALITY  (MP4 freeform atom)
      AIFF/WAV : skipped — tagging unreliable; returns True (no-op, not error)

    Returns True on success / skip; False on write failure.
    Never raises — errors are logged and False is returned.
    """
    if dry_run:
        log.debug("[DRY-RUN] Would write QUALITY=%s to %s", tier.value, path.name)
        return True

    suffix = path.suffix.lower()
    value  = tier.value

    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3, TXXX, ID3NoHeaderError
            try:
                tags = ID3(str(path))
            except ID3NoHeaderError:
                tags = ID3()
            tags.delall("TXXX:QUALITY")
            tags.add(TXXX(encoding=3, desc="QUALITY", text=[value]))
            tags.save(str(path), v2_version=config.ID3_VERSION)
            log.debug("Wrote QUALITY=%s (TXXX) → %s", value, path.name)
            return True

        elif suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            audio["QUALITY"] = [value]
            audio.save()
            log.debug("Wrote QUALITY=%s (Vorbis) → %s", value, path.name)
            return True

        elif suffix in {".m4a", ".mp4"}:
            from mutagen.mp4 import MP4, MP4FreeForm
            audio  = MP4(str(path))
            mp4key = "----:com.apple.iTunes:QUALITY"
            audio[mp4key] = [MP4FreeForm(value.encode("utf-8"))]
            audio.save()
            log.debug("Wrote QUALITY=%s (MP4 freeform) → %s", value, path.name)
            return True

        elif suffix in {".aiff", ".aif", ".wav"}:
            # AIFF and WAV tagging is not consistently supported across tools;
            # skip silently to avoid breaking files.
            log.debug(
                "Skipping QUALITY tag write for %s "
                "(AIFF/WAV tagging not reliably supported — no change made)",
                path.name,
            )
            return True

        else:
            log.debug("Skipping QUALITY tag write for unsupported format %s", path.suffix)
            return True

    except Exception as exc:
        log.warning("Could not write QUALITY tag to %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# File-move helper
# ---------------------------------------------------------------------------

def _move_file(
    path:            Path,
    scan_root:       Path,
    low_quality_dir: Path,
    dry_run:         bool = False,
) -> Optional[Path]:
    """
    Move `path` to `low_quality_dir`, preserving its path relative to `scan_root`.

    Example:
        path        = /music/sorted/Artist/Album/track.mp3
        scan_root   = /music/sorted
        dest        = /music/_low_quality/Artist/Album/track.mp3

    Returns the destination Path on success / dry-run; None on failure.
    Never raises — errors are logged and None is returned.
    """
    try:
        rel = path.relative_to(scan_root)
    except ValueError:
        log.warning(
            "Cannot compute relative path for %s vs root %s — skipping move",
            path, scan_root,
        )
        return None

    dest = low_quality_dir / rel

    if dry_run:
        log.info("[DRY-RUN] Would move LOW: %s → %s", path, dest)
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(path), str(dest))
        log.info("Moved LOW quality: %s → %s", path.name, dest)
        return dest
    except Exception as exc:
        log.warning("Move failed for %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------

_REPORT_FIELDS = [
    "filepath", "codec", "bitrate_kbps", "quality_tier", "action_taken", "error",
]


def _write_reports(
    results:    List[AuditResult],
    report_dir: Path,
    formats:    List[str],
    dry_run:    bool = False,
) -> Dict[str, Path]:
    """
    Write CSV and/or JSON reports to `report_dir`.

    Returns a dict mapping format string → written Path.
    In dry-run mode nothing is written; returns empty dict.
    """
    if dry_run:
        return {}

    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    written: Dict[str, Path] = {}

    if "csv" in formats:
        csv_path = report_dir / f"audit_quality_{timestamp}.csv"
        with csv_path.open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=_REPORT_FIELDS)
            w.writeheader()
            for r in results:
                w.writerow({
                    "filepath":     str(r.filepath),
                    "codec":        r.codec or "",
                    "bitrate_kbps": "" if r.bitrate_kbps is None else r.bitrate_kbps,
                    "quality_tier": r.quality_tier.value,
                    "action_taken": r.action_taken,
                    "error":        r.error or "",
                })
        written["csv"] = csv_path
        log.info("Quality report (CSV): %s", csv_path)

    if "json" in formats:
        json_path = report_dir / f"audit_quality_{timestamp}.json"
        data = [
            {
                "filepath":     str(r.filepath),
                "codec":        r.codec,
                "bitrate_kbps": r.bitrate_kbps,
                "quality_tier": r.quality_tier.value,
                "action_taken": r.action_taken,
                "error":        r.error,
            }
            for r in results
        ]
        json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        written["json"] = json_path
        log.info("Quality report (JSON): %s", json_path)

    return written


# ---------------------------------------------------------------------------
# Terminal summary
# ---------------------------------------------------------------------------

def print_summary(results: List[AuditResult]) -> None:
    """Print a formatted quality summary to stdout."""
    from collections import Counter

    tier_counts   = Counter(r.quality_tier.value for r in results)
    action_counts = Counter(r.action_taken        for r in results)

    total = len(results)
    line  = "─" * 44

    print()
    print("╔══════════════════════════════════════════╗")
    print("║         AUDIT-QUALITY SUMMARY            ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  Files scanned  : {total}")
    print()
    print("  Quality breakdown:")
    for tier in ("LOSSLESS", "HIGH", "MEDIUM", "LOW", "UNKNOWN"):
        n = tier_counts.get(tier, 0)
        if total > 0:
            pct = f"{n/total*100:4.0f}%"
        else:
            pct = "  0%"
        bar = "█" * min(n, 25)
        print(f"    {tier:<10} {n:>5}  {pct}  {bar}")

    print()
    print("  Actions taken:")
    for action in ("none", "moved", "tag_written", "skipped", "unreadable"):
        n = action_counts.get(action, 0)
        if n:
            print(f"    {action:<14} {n}")

    print(line)
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(
    scan_root:       Path,
    dry_run:         bool           = False,
    move_low_dir:    Optional[Path] = None,
    write_tags:      bool           = False,
    report_formats:  List[str]      = None,
    min_lossy_kbps:  int            = 192,
    verbose:         bool           = False,
    ffprobe_bin:     str            = "ffprobe",
    report_dir:      Optional[Path] = None,
    store_in_db:     bool           = True,
) -> Tuple[List[AuditResult], Dict[str, Path]]:
    """
    Audit audio files under `scan_root` for codec/bitrate quality.

    Default behavior (no optional flags) is non-destructive:
      - Probes every audio file with ffprobe
      - Classifies each into a QualityTier
      - Writes CSV + JSON reports to report_dir
      - Prints a terminal summary
      - No files are moved or modified

    Optional actions (all off by default):
      move_low_dir   — move LOW files to this directory (structure preserved)
      write_tags     — write QUALITY tag to each file
      store_in_db    — update tracks.quality_tier in the pipeline DB

    dry_run=True logs all intended actions but makes no changes.

    Returns:
        (results, report_paths) where report_paths maps format → Path.
    """
    if report_formats is None:
        report_formats = ["csv", "json"]

    if report_dir is None:
        report_dir = config.REPORTS_DIR / "audit_quality"

    # --- Collect audio files ---
    files: List[Path] = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(scan_root.rglob(f"*{ext}"))
        files.extend(scan_root.rglob(f"*{ext.upper()}"))

    # Deduplicate (rglob can match the same path twice on case-insensitive FS)
    seen:         set       = set()
    unique_files: List[Path] = []
    for f in sorted(files):
        key = str(f)
        if key not in seen:
            seen.add(key)
            unique_files.append(f)

    log.info(
        "audit-quality: %d file(s) found under %s  "
        "dry_run=%s  move_low=%s  write_tags=%s  min_lossy_kbps=%d",
        len(unique_files), scan_root,
        dry_run, move_low_dir or "off", write_tags, min_lossy_kbps,
    )

    results: List[AuditResult] = []

    for path in unique_files:
        if not path.exists():
            continue

        # --- Probe ---
        codec, bitrate_kbps = _probe_file(path, ffprobe_bin=ffprobe_bin)

        if codec is None:
            result = AuditResult(
                filepath=path,
                codec=None,
                bitrate_kbps=None,
                quality_tier=QualityTier.UNKNOWN,
                action_taken="unreadable",
                error="ffprobe could not read file",
            )
            results.append(result)
            log.debug("Unreadable / no audio stream: %s", path.name)
            continue

        # --- Classify ---
        tier   = classify_tier(codec, bitrate_kbps, min_lossy_kbps=min_lossy_kbps)
        action = "none"
        error  = None

        if verbose:
            log.info(
                "  %s  codec=%-8s  bitrate=%-6s  tier=%s",
                path.name,
                codec,
                f"{bitrate_kbps}k" if bitrate_kbps else "n/a",
                tier.value,
            )

        # --- Optional: move LOW files ---
        if move_low_dir is not None and tier == QualityTier.LOW:
            dest = _move_file(path, scan_root, move_low_dir, dry_run=dry_run)
            if dest is not None:
                action = "moved"
            else:
                action = "skipped"
                error  = "move failed"

        # --- Optional: write quality tag ---
        if write_tags and tier != QualityTier.UNKNOWN:
            if suffix_supports_tags(path):
                wrote = _write_quality_tag(path, tier, dry_run=dry_run)
                if wrote and action == "none":
                    action = "tag_written"
            else:
                log.debug("Tag write skipped for unsupported format: %s", path.suffix)

        # --- Optional: persist to DB ---
        if store_in_db and not dry_run:
            try:
                import db as _db
                _db.upsert_track(str(path), quality_tier=tier.value)
            except Exception as exc:
                log.debug("DB quality_tier update skipped for %s: %s", path.name, exc)

        results.append(AuditResult(
            filepath=path,
            codec=codec,
            bitrate_kbps=bitrate_kbps,
            quality_tier=tier,
            action_taken=action,
            error=error,
        ))

    # --- Reports ---
    report_paths = _write_reports(results, report_dir, report_formats, dry_run=dry_run)

    # --- Terminal summary ---
    print_summary(results)

    if dry_run:
        print("  (dry-run: no files were moved or modified)")
        print()

    return results, report_paths


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def suffix_supports_tags(path: Path) -> bool:
    """
    Return True if the file format has supported tag-write handling.
    AIFF/WAV return True but _write_quality_tag() skips them gracefully.
    """
    return path.suffix.lower() in {
        ".mp3", ".flac", ".m4a", ".mp4",
        ".aiff", ".aif", ".wav",
    }
