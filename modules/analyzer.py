"""
Audio analysis — BPM detection (aubio) + key detection (keyfinder-cli).

BPM notes:
    aubiobpm outputs one BPM value per analysis window. We collect all values
    and return the median, which is more robust than the mean.
    Genre-aware correction: if result < 90, double it; if > 160 and genre is
    not DNB/jungle/hardcore, halve it. This catches aubio's common
    halving/doubling errors on house and techno.

Key notes:
    keyfinder-cli outputs a single musical key string (e.g. "Am", "C", "F#").
    We convert to Camelot notation using a complete lookup table.
"""
import logging
import re
import shutil
import statistics
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

import config
import db

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Camelot wheel — covers all enharmonic spellings keyfinder might output
# ---------------------------------------------------------------------------
CAMELOT_MAP: dict = {
    # Major -> B suffix
    "C": "8B",   "C major": "8B",
    "Db": "3B",  "Db major": "3B",  "C#": "3B",  "C# major": "3B",
    "D": "10B",  "D major": "10B",
    "Eb": "5B",  "Eb major": "5B",  "D#": "5B",  "D# major": "5B",
    "E": "12B",  "E major": "12B",
    "F": "7B",   "F major": "7B",
    "Gb": "2B",  "Gb major": "2B",  "F#": "2B",  "F# major": "2B",
    "G": "9B",   "G major": "9B",
    "Ab": "4B",  "Ab major": "4B",  "G#": "4B",  "G# major": "4B",
    "A": "11B",  "A major": "11B",
    "Bb": "6B",  "Bb major": "6B",  "A#": "6B",  "A# major": "6B",
    "B": "1B",   "B major": "1B",
    # Minor -> A suffix
    "Cm": "5A",  "C minor": "5A",
    "C#m": "12A","C# minor": "12A", "Dbm": "12A","Db minor": "12A",
    "Dm": "7A",  "D minor": "7A",
    "D#m": "2A", "D# minor": "2A",  "Ebm": "2A", "Eb minor": "2A",
    "Em": "9A",  "E minor": "9A",
    "Fm": "4A",  "F minor": "4A",
    "F#m": "11A","F# minor": "11A", "Gbm": "11A","Gb minor": "11A",
    "Gm": "6A",  "G minor": "6A",
    "G#m": "1A", "G# minor": "1A",  "Abm": "1A", "Ab minor": "1A",
    "Am": "8A",  "A minor": "8A",
    "A#m": "3A", "A# minor": "3A",  "Bbm": "3A", "Bb minor": "3A",
    "Bm": "10A", "B minor": "10A",
}

# Reverse map: Camelot -> canonical musical key
CAMELOT_TO_MUSICAL: dict = {
    "1A": "Ab minor", "1B": "B major",
    "2A": "Eb minor", "2B": "F# major",
    "3A": "Bb minor", "3B": "Db major",
    "4A": "F minor",  "4B": "Ab major",
    "5A": "C minor",  "5B": "Eb major",
    "6A": "G minor",  "6B": "Bb major",
    "7A": "D minor",  "7B": "F major",
    "8A": "A minor",  "8B": "C major",
    "9A": "E minor",  "9B": "G major",
    "10A": "B minor", "10B": "D major",
    "11A": "F# minor","11B": "A major",
    "12A": "C# minor","12B": "E major",
}

# Genres where 170+ BPM is expected — don't halve these
_HIGH_BPM_GENRES = {"drum and bass", "dnb", "jungle", "hardcore", "gabber", "speedcore"}


# ---------------------------------------------------------------------------
# Shared BPM correction helper (genre-aware halving/doubling + range clamp)
# Applied identically to results from both aubio and librosa.
# ---------------------------------------------------------------------------
def _apply_bpm_correction(raw_bpm: float, genre: str, name: str, source: str) -> Optional[float]:
    genre_lower = genre.lower()
    if raw_bpm < 90:
        corrected = raw_bpm * 2
        log.debug("BPM doubled (%s): %.1f → %.1f (%s)", source, raw_bpm, corrected, name)
        raw_bpm = corrected
    elif raw_bpm > 160 and not any(g in genre_lower for g in _HIGH_BPM_GENRES):
        corrected = raw_bpm / 2
        log.debug("BPM halved (%s): %.1f → %.1f (%s)", source, raw_bpm, corrected, name)
        raw_bpm = corrected

    if not (config.BPM_MIN <= raw_bpm <= config.BPM_MAX):
        log.warning("BPM out of range (%.1f) via %s for %s — discarding", raw_bpm, source, name)
        return None

    return round(raw_bpm, 2)


# ---------------------------------------------------------------------------
# Librosa BPM fallback
# Used when aubio is unavailable or returns no usable result.
# ---------------------------------------------------------------------------
def _detect_bpm_librosa(path: Path, genre: str = "") -> Optional[float]:
    """
    Estimate BPM via librosa beat tracking.
    Returns None if librosa is not installed or analysis fails.
    """
    try:
        import librosa  # type: ignore
    except ImportError:
        log.debug("BPM: librosa not installed — cannot use as fallback (pip install librosa)")
        return None

    try:
        log.debug("BPM: loading %s via librosa", path.name)
        y, sr = librosa.load(str(path), sr=None, mono=True)
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        raw_bpm = float(tempo)
    except Exception as exc:
        log.warning("BPM: librosa failed on %s: %s", path.name, exc)
        return None

    if not (20 < raw_bpm < 400):
        log.debug("BPM: librosa out-of-range %.1f for %s — discarding", raw_bpm, path.name)
        return None

    return _apply_bpm_correction(raw_bpm, genre, path.name, "librosa")


# ---------------------------------------------------------------------------
# BPM binary resolution
#
# Cached once on first call. Priority:
#   1. config.AUBIO_BIN  (explicit override, e.g. "/usr/local/bin/aubio")
#   2. aubio             → command: aubio tempo <file>
#   3. aubiotrack        → command: aubiotrack <file>   (legacy fallback)
# ---------------------------------------------------------------------------

# Module-level cache: None = not yet resolved, "" = resolved but not found.
_AUBIO_BIN:   Optional[str] = None   # resolved binary path
_AUBIO_STYLE: str            = "tempo"


def _resolve_aubio_binary() -> Tuple[Optional[str], str]:
    """
    Return (binary_path, style) where style is "tempo" or "track".
    Resolves on first call; subsequent calls return the cached result.
    Logs at INFO level exactly once so the chosen binary is visible in runs.
    """
    global _AUBIO_BIN, _AUBIO_STYLE

    if _AUBIO_BIN is not None:
        # Already resolved — "" means not found, any other string is the path.
        return _AUBIO_BIN or None, _AUBIO_STYLE

    # 1. Explicit override from config (AUBIO_BIN env var or config_local.py)
    override = (getattr(config, "AUBIO_BIN", "") or "").strip()
    if override:
        resolved = shutil.which(override)
        if resolved:
            style = "track" if "track" in Path(resolved).name.lower() else "tempo"
            log.info("BPM: using configured AUBIO_BIN=%s (style=%s)", resolved, style)
            _AUBIO_BIN, _AUBIO_STYLE = resolved, style
            return _AUBIO_BIN, _AUBIO_STYLE
        log.warning(
            "BPM: configured AUBIO_BIN=%r not found in PATH — falling back to auto-detect",
            override,
        )

    # 2. Auto-detect: aubio (modern single binary, Ubuntu Studio 24+)
    found = shutil.which("aubio")
    if found:
        log.info("BPM: auto-detected %s → using 'aubio tempo <file>'", found)
        _AUBIO_BIN, _AUBIO_STYLE = found, "tempo"
        return _AUBIO_BIN, _AUBIO_STYLE

    # 3. aubiotrack (legacy fallback — older aubio-tools packages)
    found = shutil.which("aubiotrack")
    if found:
        log.info("BPM: auto-detected %s (legacy fallback) → using 'aubiotrack <file>'", found)
        _AUBIO_BIN, _AUBIO_STYLE = found, "track"
        return _AUBIO_BIN, _AUBIO_STYLE

    # Nothing found
    log.error(
        "BPM: no aubio binary found (tried: aubio, aubiotrack). "
        "Install with: sudo apt install aubio-tools"
    )
    _AUBIO_BIN = ""  # cache the "not found" result so we don't re-probe every file
    return None, _AUBIO_STYLE


# ---------------------------------------------------------------------------
# BPM detection
# ---------------------------------------------------------------------------
def _librosa_fallback(path: Path, genre: str, reason: str) -> Optional[float]:
    """Log reason aubio was skipped/failed, then attempt librosa."""
    log.info("BPM: %s for %s — trying librosa fallback", reason, path.name)
    result = _detect_bpm_librosa(path, genre)
    if result is not None:
        log.info("BPM recovered via librosa: %.1f for %s", result, path.name)
    else:
        log.debug("BPM: librosa also returned no result for %s", path.name)
    return result


def detect_bpm(path: Path, genre: str = "") -> Optional[float]:
    """
    Detect BPM for a file. Tries aubio first; falls back to librosa if aubio
    is unavailable, crashes, or returns no usable output.

    Recovery order:
      1. aubio (if binary is found and works)
      2. librosa (if installed) — used whenever aubio cannot produce a result
    """
    binary, style = _resolve_aubio_binary()
    if not binary:
        return _librosa_fallback(path, genre, "aubio unavailable")

    # Build command based on detected binary style:
    #   aubio     → aubio tempo <file>
    #   aubiotrack → aubiotrack <file>
    if style == "tempo":
        cmd = [binary, "tempo", str(path)]
    else:
        cmd = [binary, str(path)]

    log.debug("BPM cmd: %s", " ".join(cmd))

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except FileNotFoundError:
        log.error("BPM: binary not executable: %s", binary)
        return _librosa_fallback(path, genre, "aubio binary not executable")
    except subprocess.TimeoutExpired:
        log.warning("BPM: aubio timed out on %s", path.name)
        return _librosa_fallback(path, genre, "aubio timed out")

    if result.returncode != 0:
        log.debug(
            "BPM: %s returned rc=%d for %s — stderr: %s",
            Path(binary).name, result.returncode, path.name,
            result.stderr.strip()[:300],
        )

    # Parse output — handles all known aubio output formats:
    #   "128.000000"           aubiobpm / aubiotrack per-window value
    #   "0.371 128.000000"     aubiobpm timestamp + BPM
    #   "123.26 bpm"           aubio tempo single-line summary (Ubuntu 24)
    # Strategy: scan each token left-to-right, take the first float in the
    # BPM-plausible range (20–400).  This is robust to trailing unit strings.
    values: List[float] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        for token in line.split():
            try:
                bpm_val = float(token)
                if 20 < bpm_val < 400:   # sanity filter before median
                    values.append(bpm_val)
                    break  # one BPM value per line is enough
            except ValueError:
                continue

    if not values:
        log.debug("BPM: no valid values from %s for %s", Path(binary).name, path.name)
        if result.stderr.strip():
            log.debug("BPM stderr: %s", result.stderr.strip()[:300])
        return _librosa_fallback(path, genre, "aubio returned no usable output")

    raw_bpm = statistics.median(values)
    return _apply_bpm_correction(raw_bpm, genre, path.name, "aubio")


# ---------------------------------------------------------------------------
# Key detection
# ---------------------------------------------------------------------------
def detect_key(path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Run keyfinder-cli on a file.
    Returns (musical_key, camelot) or (None, None) on failure.
    """
    def _run_keyfinder(target: Path) -> Optional[str]:
        """Run keyfinder-cli on target, return raw key string or None."""
        cmd = [config.KEYFINDER_BIN, str(target)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        except FileNotFoundError:
            log.error(
                "keyfinder-cli not found at '%s'. "
                "Install from: https://github.com/EvanPurkhiser/keyfinder-cli",
                config.KEYFINDER_BIN,
            )
            return None
        except subprocess.TimeoutExpired:
            log.warning("keyfinder-cli timed out on %s", target.name)
            return None

        out = result.stdout.strip()
        if out:
            return out

        stderr = result.stderr.strip()
        if stderr:
            return f"__ERROR__{stderr}"
        return None

    raw = _run_keyfinder(path)

    # If keyfinder-cli reported a decode error, transcode to temp WAV and retry
    if raw is None or raw.startswith("__ERROR__"):
        if raw and raw.startswith("__ERROR__"):
            log.debug(
                "keyfinder-cli decode error on %s (%s) — retrying via ffmpeg WAV",
                path.name, raw[8:]
            )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            ffmpeg_result = subprocess.run(
                [
                    config.FFPROBE_BIN.replace("ffprobe", "ffmpeg"),
                    "-y", "-loglevel", "error",
                    "-i", str(path),
                    "-ac", "2", "-ar", "44100",
                    "-f", "wav", str(tmp_path),
                ],
                capture_output=True, timeout=60,
            )
            if ffmpeg_result.returncode == 0:
                raw = _run_keyfinder(tmp_path)
            else:
                log.warning(
                    "ffmpeg transcode failed for %s: %s",
                    path.name, ffmpeg_result.stderr.decode(errors="replace")[:200],
                )
        except subprocess.TimeoutExpired:
            log.warning("ffmpeg transcode timed out for %s", path.name)
        finally:
            tmp_path.unlink(missing_ok=True)

    if not raw or raw.startswith("__ERROR__"):
        log.debug("keyfinder-cli: no key detected for %s", path.name)
        return None, None

    # keyfinder-cli outputs "Am", "C", "F#", "Dbm", etc.
    musical_key = raw
    camelot = CAMELOT_MAP.get(musical_key) or CAMELOT_MAP.get(musical_key.strip())
    if camelot is None:
        log.warning("Unknown key '%s' from keyfinder-cli for %s", musical_key, path.name)
        return musical_key, None

    return musical_key, camelot


# ---------------------------------------------------------------------------
# Combined run
# ---------------------------------------------------------------------------
def run(files: List[Path], run_id: int, dry_run: bool = False) -> List[Path]:
    """
    Detect BPM + key for each file. Updates DB with results.
    Returns the same file list (analysis doesn't reject files).
    """
    for path in files:
        if not path.exists():
            continue

        row = db.get_track(str(path))
        genre = row["genre"] if row and row["genre"] else ""

        bpm               = detect_bpm(path, genre)
        musical_key, camelot = detect_key(path)

        update: dict = {}
        if bpm is not None:
            update["bpm"] = bpm
        if musical_key is not None:
            update["key_musical"] = musical_key
        if camelot is not None:
            update["key_camelot"] = camelot

        if update and not dry_run:
            db.upsert_track(str(path), **update)

        log.info(
            "ANALYZED %s  BPM=%.1f  Key=%s (%s)",
            path.name,
            bpm or 0.0,
            camelot or "?",
            musical_key or "?",
        )

    return files
