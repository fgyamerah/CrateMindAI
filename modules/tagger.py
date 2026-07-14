"""
Tag writer — writes the final metadata schema to audio files using mutagen.

Always writes ID3v2.3 (Rekordbox's preferred version).
Handles MP3 (ID3), FLAC (VorbisComment), and M4A (MP4 atoms).
Skips formats it cannot handle rather than crashing the pipeline.

Tag schema written:
    TIT2  — Title           (from existing tags / DB)
    TPE1  — Artist          (from existing tags / DB)
    TPE2  — Album Artist    (from existing tags / DB)
    TCON  — Genre           (from existing tags / DB)
    TBPM  — BPM             (from DB, integer string)
    TKEY  — Key (Camelot)   (from DB, e.g. "8A")
    COMM  — Comment         (human-readable summary)
    TXXX:CAMELOT   — Camelot code (redundant for other software)
    TXXX:PROCESSED — pipeline flag, value "1"
    TIT1  — Grouping        (left blank, user fills in Rekordbox)
"""
import logging
from pathlib import Path
from typing import List, Optional

import config
import db
from modules.textlog import log_action

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy imports — mutagen is required but may not be installed
# ---------------------------------------------------------------------------
try:
    from mutagen.id3 import (
        ID3, ID3NoHeaderError, TBPM, TKEY, TCON, COMM, TXXX, TIT1,
        TPE1, TPE2, TIT2,
    )
    from mutagen.flac import FLAC
    from mutagen.mp4 import MP4
    _MUTAGEN_OK = True
except ImportError:
    _MUTAGEN_OK = False
    log.error("mutagen not installed. Run: pip install mutagen")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _safe_str(val) -> str:
    return str(val).strip() if val is not None else ""


def _write_mp3(path: Path, row: dict, dry_run: bool) -> bool:
    """Write ID3v2.3 tags to an MP3 file. Returns True on success."""
    try:
        try:
            audio = ID3(str(path))
        except ID3NoHeaderError:
            audio = ID3()

        bpm      = row.get("bpm")
        camelot  = _safe_str(row.get("key_camelot"))
        musical  = _safe_str(row.get("key_musical"))
        genre    = _safe_str(row.get("genre"))
        artist   = _safe_str(row.get("artist"))
        title    = _safe_str(row.get("title"))

        # --- Core music tags (only write if non-empty and not already set) ---
        if artist and "TPE1" not in audio:
            audio["TPE1"] = TPE1(encoding=3, text=[artist])
        if artist and "TPE2" not in audio:
            audio["TPE2"] = TPE2(encoding=3, text=[artist])
        if title and "TIT2" not in audio:
            audio["TIT2"] = TIT2(encoding=3, text=[title])
        if genre:
            audio["TCON"] = TCON(encoding=3, text=[genre])

        # --- BPM (always overwrite — we calculated it) ---
        if bpm is not None:
            audio["TBPM"] = TBPM(encoding=3, text=[str(int(round(bpm)))])

        # --- Key: write Camelot in TKEY (displayed verbatim in Rekordbox) ---
        if camelot:
            audio["TKEY"] = TKEY(encoding=3, text=[camelot])

        # --- Comment: human-readable summary ---
        comment_parts = []
        if camelot:
            comment_parts.append(camelot)
        if musical:
            comment_parts.append(musical)
        if bpm is not None:
            comment_parts.append(f"{int(round(bpm))} BPM")
        if comment_parts:
            audio["COMM::eng"] = COMM(
                encoding=3, lang="eng", desc="", text=[" | ".join(comment_parts)]
            )

        # --- Custom frames ---
        if camelot:
            audio["TXXX:CAMELOT"] = TXXX(encoding=3, desc="CAMELOT", text=[camelot])
        audio["TXXX:PROCESSED"] = TXXX(encoding=3, desc="PROCESSED", text=["1"])

        # --- Grouping placeholder (Rekordbox reads TIT1 as Grouping) ---
        if "TIT1" not in audio:
            audio["TIT1"] = TIT1(encoding=3, text=[""])

        if not dry_run:
            # Save as ID3v2.3 — critical for Rekordbox compatibility
            audio.save(str(path), v2_version=3)
        return True

    except Exception as exc:
        log.error("Failed to write ID3 tags to %s: %s", path.name, exc)
        return False


def _write_flac(path: Path, row: dict, dry_run: bool) -> bool:
    """Write VorbisComment tags to a FLAC file."""
    try:
        audio = FLAC(str(path))
        bpm     = row.get("bpm")
        camelot = _safe_str(row.get("key_camelot"))
        musical = _safe_str(row.get("key_musical"))
        genre   = _safe_str(row.get("genre"))

        if bpm is not None:
            audio["BPM"] = [str(int(round(bpm)))]
        if camelot:
            audio["INITIALKEY"] = [camelot]
        if musical:
            audio["KEY"] = [musical]
        if genre:
            audio["GENRE"] = [genre]
        audio["CAMELOT"]   = [camelot or ""]
        audio["PROCESSED"] = ["1"]

        comment_parts = [p for p in [camelot, musical, f"{int(round(bpm))} BPM" if bpm else ""] if p]
        if comment_parts:
            audio["COMMENT"] = [" | ".join(comment_parts)]

        if not dry_run:
            audio.save()
        return True
    except Exception as exc:
        log.error("Failed to write FLAC tags to %s: %s", path.name, exc)
        return False


def _write_m4a(path: Path, row: dict, dry_run: bool) -> bool:
    """Write MP4/AAC atoms (M4A). Rekordbox reads these on import."""
    try:
        audio = MP4(str(path))
        bpm     = row.get("bpm")
        camelot = _safe_str(row.get("key_camelot"))
        musical = _safe_str(row.get("key_musical"))

        if bpm is not None:
            audio["tmpo"] = [int(round(bpm))]  # tmpo is the BPM atom
        # Rekordbox reads ©key for key in M4A — non-standard but works
        if camelot:
            audio["----:com.apple.iTunes:initialkey"] = [
                camelot.encode("utf-8")
            ]
        audio["----:com.apple.iTunes:PROCESSED"] = [b"1"]

        if not dry_run:
            audio.save()
        return True
    except Exception as exc:
        log.error("Failed to write M4A tags to %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------
def run(files: List[Path], run_id: int, dry_run: bool = False) -> List[Path]:
    """
    Write final metadata tags to every file in `files`.
    Returns the file list unchanged (tagging doesn't reject files).
    """
    if not _MUTAGEN_OK:
        log.error("Skipping tag writing — mutagen not available")
        return files

    success = 0
    failed  = 0

    for path in files:
        if not path.exists():
            continue

        row = db.get_track(str(path))
        if row is None:
            log.warning("No DB record for %s — skipping tag write", path.name)
            continue

        row = dict(row)  # sqlite3.Row → plain dict for easier handling
        suffix = path.suffix.lower()

        if suffix == ".mp3":
            ok = _write_mp3(path, row, dry_run)
        elif suffix == ".flac":
            ok = _write_flac(path, row, dry_run)
        elif suffix in {".m4a", ".mp4", ".aac"}:
            ok = _write_m4a(path, row, dry_run)
        else:
            log.debug("No tag writer for %s — skipping", path.suffix)
            ok = True  # not a failure, just unsupported format

        if ok:
            success += 1
            log.debug("TAGGED %s", path.name)
            bpm_val = row.get("bpm")
            bpm_str = str(int(round(bpm_val))) if bpm_val is not None else "?"
            key_str = row.get("key_camelot") or "?"
            log_action(f"TAGGED: BPM={bpm_str}, KEY={key_str} [{path.name}]")
        else:
            failed += 1
            db.mark_status(str(path), "error", "tag write failed")
            log_action(f"ERROR: tag write failed [{path.name}]")

    log.info("Tagger: %d tagged, %d failed", success, failed)
    return files
