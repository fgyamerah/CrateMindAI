"""
modules/cue_suggest.py — Production cue point suggestion engine (v2)

DISCLAIMER: These are SUGGESTED cue positions only.
Native Rekordbox hot-cues are NOT written by this tool.
Final review and placement in Rekordbox is recommended.

Detection pipeline:
  Full mode (numpy + ffmpeg):
    1. Decode audio to mono float32 at 11025 Hz via ffmpeg subprocess
    2. Extract per-frame: RMS energy, low-frequency energy (< 250 Hz
       — bass/kick proxy), spectral flux (onset strength indicator)
    3. Smooth with 4-second moving average
    4. Aggregate all features to bar resolution using BPM-derived bar grid
    5. Detect 6 cue positions with multi-feature threshold logic
    6. Score per-cue confidence from feature agreement + distinctiveness
    7. Generate human-readable note explaining each detection

  Estimate mode (BPM + duration, fallback):
    - Used when ffmpeg decode fails or numpy is unavailable
    - Conventional club-music structure heuristics (house / afro house)
    - All cues marked source='bpm_estimate', confidence ≤ 0.50

Cue types:
  intro_start   Always bar 1 (confidence 1.0)
  mix_in        First stable beat-phrase for DJ entry
  groove_start  First sustained full-arrangement section (bass + energy in)
  drop          Main energy arrival / impact
  breakdown     Significant energy/density reduction after peak
  outro_start   Start of mix-out section

Signal features used:
  - RMS energy (overall loudness per frame)
  - Low-frequency energy (bass/kick proxy, 1–250 Hz via FFT)
  - Spectral flux (onset strength, sum of positive spectral differences)

Bar alignment:
  All cue times are snapped to the nearest bar boundary (4 beats at detected BPM).

Output files (per run):
  CUE_SUGGEST_OUTPUT_DIR/cue_suggestions.json  — master, all tracks in DB
  CUE_SUGGEST_OUTPUT_DIR/cue_suggestions.csv   — master, wide format (1 row/track)
  CUE_SUGGEST_OUTPUT_DIR/runs/cues_YYYYMMDD_HHmmss.csv  — per-run detail log
  <audio_file>.cues.json                        — sidecar (opt-in via config)
  DB table cue_points                           — queryable by set_builder
"""
from __future__ import annotations

import csv
import json
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.textlog import log_action

log = logging.getLogger(__name__)

try:
    import numpy as np
    from numpy.lib.stride_tricks import as_strided
    _NUMPY_OK = True
except ImportError:
    _NUMPY_OK = False
    log.warning("cue_suggest: numpy not available — BPM-estimate mode only")

try:
    import librosa as _librosa  # noqa: F401
    _LIBROSA_OK = True
except ImportError:
    _LIBROSA_OK = False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CUE_TYPES = [
    "intro_start",
    "mix_in",
    "groove_start",
    "drop",
    "breakdown",
    "outro_start",
]

_ANALYSIS_SR  = 11025    # Hz — fast decode, adequate for bass (< 5.5 kHz)
_HOP_SEC      = 0.1      # frame hop in seconds
_N_FFT        = 1024     # FFT window; freq resolution ≈ 10.8 Hz/bin at 11025 Hz
_SMOOTH_SEC   = 4.0      # moving-average window (seconds)
_LF_MAX_HZ    = 250.0    # bass/kick proxy ceiling (Hz)

# Multi-feature detection thresholds (fractions of track peak, 0–1)
_MIX_IN_RMS   = 0.22     # min normalised RMS for mix_in
_MIX_IN_LF    = 0.15     # min normalised LF  for mix_in
_GROOVE_RMS   = 0.48     # min normalised RMS for groove_start (sustained)
_GROOVE_LF    = 0.30     # min normalised LF  for groove_start
_GROOVE_BARS  = 2        # bars that must stay above threshold
_DROP_DWELL   = 4        # bars before drop must have included at least one dip
_BREAK_RMS    = 0.40     # max normalised RMS for breakdown valley
_OUTRO_RMS    = 0.58     # RMS must fall below this in the outro zone
_OUTRO_ZONE   = 0.78     # search outro from this fraction of track duration


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CuePoint:
    cue_type:    str
    time_sec:    float
    bar:         int
    beat_in_bar: int   = 1
    confidence:  float = 0.5
    source:      str   = "auto"
    note:        str   = ""   # human-readable explanation

    @property
    def time_fmt(self) -> str:
        m = int(self.time_sec // 60)
        s = self.time_sec - m * 60
        return f"{m}:{s:04.1f}"

    def to_dict(self) -> dict:
        return {
            "cue_type":    self.cue_type,
            "time_sec":    round(self.time_sec, 2),
            "bar":         self.bar,
            "beat_in_bar": self.beat_in_bar,
            "confidence":  round(self.confidence, 3),
            "source":      self.source,
            "note":        self.note,
        }

    def to_db_dict(self) -> dict:
        """Subset accepted by db.save_cue_points (note not in schema)."""
        return {
            "cue_type":    self.cue_type,
            "time_sec":    self.time_sec,
            "bar":         self.bar,
            "beat_in_bar": self.beat_in_bar,
            "confidence":  self.confidence,
            "source":      self.source,
        }


@dataclass
class TrackCues:
    filepath:     str
    title:        str
    artist:       str
    bpm:          float
    camelot:      str
    duration_sec: float
    cues:         List[CuePoint] = field(default_factory=list)
    analyzed_at:  str            = ""
    method:       str            = "unknown"

    def cue_map(self) -> Dict[str, CuePoint]:
        return {c.cue_type: c for c in self.cues}

    def to_dict(self) -> dict:
        return {
            "filepath":     self.filepath,
            "title":        self.title,
            "artist":       self.artist,
            "bpm":          round(self.bpm, 2) if self.bpm else None,
            "key":          self.camelot,
            "duration_sec": round(self.duration_sec, 1),
            "analyzed_at":  self.analyzed_at,
            "method":       self.method,
            "cues":         {c.cue_type: c.to_dict() for c in self.cues},
        }


# ---------------------------------------------------------------------------
# Audio decoding
# ---------------------------------------------------------------------------

def _ffmpeg_bin() -> str:
    ffprobe = getattr(config, "FFPROBE_BIN", "ffprobe")
    return str(ffprobe).replace("ffprobe", "ffmpeg")


def _load_audio_numpy(path: Path) -> Optional[Tuple["np.ndarray", int]]:
    """Decode audio to mono float32 at _ANALYSIS_SR. Returns (y, sr) or None."""
    if not _NUMPY_OK:
        return None
    cmd = [
        _ffmpeg_bin(), "-y", "-loglevel", "error",
        "-i", str(path),
        "-ac", "1", "-ar", str(_ANALYSIS_SR),
        "-f", "f32le", "-",
    ]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=180)
        if proc.returncode != 0 or not proc.stdout:
            log.debug("ffmpeg decode failed %s: %s",
                      path.name, proc.stderr.decode(errors="replace")[:200])
            return None
        y = np.frombuffer(proc.stdout, dtype=np.float32).copy()
        if len(y) < _ANALYSIS_SR:
            return None
        return y, _ANALYSIS_SR
    except Exception as exc:
        log.debug("Audio load error %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Multi-feature extraction
# ---------------------------------------------------------------------------

@dataclass
class _Features:
    rms:    "np.ndarray"  # normalised (0–1) per-bar mean RMS
    lf:     "np.ndarray"  # normalised (0–1) per-bar mean LF energy
    flux:   "np.ndarray"  # normalised (0–1) per-bar mean spectral flux
    n_bars: int


def _smooth_ma(arr: "np.ndarray", window: int) -> "np.ndarray":
    w  = max(1, window)
    cs = np.cumsum(np.insert(arr, 0, 0.0))
    raw = (cs[w:] - cs[:-w]) / w
    pad_l = (len(arr) - len(raw)) // 2
    pad_r = len(arr) - len(raw) - pad_l
    return np.pad(raw, (pad_l, pad_r), mode="edge")


def _normalise(arr: "np.ndarray") -> "np.ndarray":
    peak = float(arr.max())
    return arr / peak if peak > 1e-9 else np.zeros_like(arr)


def _extract_features(
    y: "np.ndarray",
    sr: int,
    bar_times: List[float],
) -> Optional["_Features"]:
    """
    Return per-bar normalised (RMS, LF energy, spectral flux).
    Returns None if audio is too short.
    """
    hop   = max(1, int(sr * _HOP_SEC))
    n_fft = _N_FFT

    n_frames = max(0, (len(y) - n_fft) // hop + 1)
    if n_frames < 8:
        return None

    window = np.hanning(n_fft).astype(np.float32)

    # Build frame matrix via stride_tricks (zero-copy view then copy)
    try:
        y_slice  = y[: n_fft + (n_frames - 1) * hop]
        frames   = as_strided(
            y_slice,
            shape=(n_frames, n_fft),
            strides=(y_slice.itemsize * hop, y_slice.itemsize),
        )
        frames = np.array(frames, dtype=np.float32)
    except Exception:
        # Safe fallback: explicit slicing
        frames = np.stack(
            [y[i * hop: i * hop + n_fft] for i in range(n_frames)
             if i * hop + n_fft <= len(y)],
            axis=0,
        ).astype(np.float32)
        n_frames = len(frames)

    if n_frames < 4:
        return None

    # RMS (no FFT)
    rms_frames = np.sqrt(np.mean(frames ** 2 + 1e-12, axis=1))

    # Magnitude spectra
    specs = np.abs(np.fft.rfft(frames * window, n=n_fft, axis=1))

    # LF energy: bins 1 Hz … LF_MAX_HZ
    lf_max_bin = max(2, int(_LF_MAX_HZ * n_fft / sr))
    lf_frames  = np.sqrt(np.mean(specs[:, 1:lf_max_bin] ** 2 + 1e-12, axis=1))

    # Spectral flux (onset strength): sum of positive spectral differences
    flux_raw   = np.sum(np.maximum(0.0, np.diff(specs, axis=0)), axis=1)
    flux_frames = np.concatenate([[0.0], flux_raw])

    # Smooth
    smooth_w = max(1, int(_SMOOTH_SEC / _HOP_SEC))
    rms_s    = _smooth_ma(rms_frames,  smooth_w)
    lf_s     = _smooth_ma(lf_frames,   smooth_w)
    flux_s   = _smooth_ma(flux_frames, max(1, smooth_w // 2))

    # Aggregate to bar resolution
    def _to_bars(feat: "np.ndarray") -> "np.ndarray":
        n = len(feat)
        vals = []
        for i, t in enumerate(bar_times):
            f0 = min(n - 1, int(t / _HOP_SEC))
            f1 = min(n, int(bar_times[i + 1] / _HOP_SEC) if i + 1 < len(bar_times) else n)
            chunk = feat[f0:f1]
            vals.append(float(chunk.mean()) if len(chunk) > 0 else 0.0)
        return np.array(vals, dtype=float)

    return _Features(
        rms    = _normalise(_to_bars(rms_s)),
        lf     = _normalise(_to_bars(lf_s)),
        flux   = _normalise(_to_bars(flux_s)),
        n_bars = len(bar_times),
    )


# ---------------------------------------------------------------------------
# Bar grid
# ---------------------------------------------------------------------------

def _make_bar_grid(bpm: float, duration_sec: float) -> List[float]:
    if not bpm or bpm <= 0:
        bpm = 128.0
    bar_sec = 4.0 * 60.0 / bpm
    times: List[float] = []
    t = 0.0
    while t < duration_sec - bar_sec * 0.5:
        times.append(t)
        t += bar_sec
    return times


# ---------------------------------------------------------------------------
# Cue detection — full energy analysis
# ---------------------------------------------------------------------------

def _detect_cues_full(
    feat: "_Features",
    bar_times: List[float],
    duration_sec: float,
) -> List[CuePoint]:
    """Detect 6 cue positions from multi-feature bar profile."""
    n    = feat.n_bars
    rms  = feat.rms
    lf   = feat.lf
    flux = feat.flux

    if n < 4 or not bar_times:
        return []

    cues: List[CuePoint] = []

    def bt(i: int) -> float:
        return bar_times[min(i, len(bar_times) - 1)]

    # ----- 1. intro_start — always bar 1 -----
    cues.append(CuePoint(
        cue_type="intro_start", time_sec=bar_times[0], bar=1,
        confidence=1.0, source="energy_analysis",
        note="Bar 1; track start. Always present.",
    ))

    # ----- 2. mix_in — first stable DJ entry point -----
    mix_in_bar: Optional[int] = None
    for i in range(2, min(n, int(n * 0.45) + 2)):
        if rms[i] >= _MIX_IN_RMS and lf[i] >= _MIX_IN_LF:
            mix_in_bar = i
            break
    if mix_in_bar is None:
        for i in range(2, n):
            if rms[i] >= _MIX_IN_RMS:
                mix_in_bar = i
                break

    if mix_in_bar is not None:
        lf_ok  = lf[mix_in_bar] >= _MIX_IN_LF
        conf   = 0.72 if lf_ok else 0.55
        # bonus: stays above threshold for 2+ bars
        if mix_in_bar + 1 < n and rms[mix_in_bar + 1] >= _MIX_IN_RMS:
            conf = min(0.85, conf + 0.08)
        note = (
            f"Energy rises to {rms[mix_in_bar]:.0%} of peak at bar {mix_in_bar + 1}"
            f"{'; bass/kick audible' if lf_ok else ' — bass not yet prominent'}."
        )
        if conf < 0.60:
            note += " LOW CONFIDENCE — verify in Rekordbox."
        cues.append(CuePoint(
            cue_type="mix_in", time_sec=bt(mix_in_bar), bar=mix_in_bar + 1,
            confidence=conf, source="energy_analysis", note=note,
        ))

    # ----- 3. groove_start — first sustained full-arrangement section -----
    groove_bar: Optional[int] = None
    run_len = 0
    start_search = max(2, mix_in_bar or 2)
    for i in range(start_search, n):
        if rms[i] >= _GROOVE_RMS and lf[i] >= _GROOVE_LF:
            run_len += 1
            if run_len >= _GROOVE_BARS and groove_bar is None:
                groove_bar = i - _GROOVE_BARS + 1
        else:
            run_len = 0
    # fallback: RMS only
    if groove_bar is None:
        run_len = 0
        for i in range(start_search, n):
            if rms[i] >= _GROOVE_RMS:
                run_len += 1
                if run_len >= _GROOVE_BARS and groove_bar is None:
                    groove_bar = i - _GROOVE_BARS + 1
            else:
                run_len = 0

    if groove_bar is not None:
        lf_ok = lf[groove_bar] >= _GROOVE_LF
        conf  = 0.75 if lf_ok else 0.60
        note  = (
            f"Full groove from bar {groove_bar + 1}; "
            f"RMS at {rms[groove_bar]:.0%} sustained for 2+ bars"
            f"{'; bass/kick fully in' if lf_ok else ''}."
        )
        if conf < 0.65:
            note += " LOW CONFIDENCE — verify."
        cues.append(CuePoint(
            cue_type="groove_start", time_sec=bt(groove_bar), bar=groove_bar + 1,
            confidence=conf, source="energy_analysis", note=note,
        ))

    # ----- 4. drop — main energy arrival -----
    # Novelty = spectral flux + positive LF change (kick/bass arrival)
    lf_diff   = np.maximum(0.0, np.diff(np.concatenate([[0.0], lf])))
    novelty   = 0.6 * flux + 0.4 * lf_diff

    drop_bar: Optional[int] = None
    had_dip   = False
    search_to = max(_DROP_DWELL + 2, int(n * 0.75))

    for i in range(_DROP_DWELL, search_to):
        if not had_dip and rms[i] < 0.65:
            had_dip = True
        if had_dip and novelty[i] > 0.45 and rms[i] > 0.55:
            drop_bar = i
            break

    if drop_bar is None:
        # No dip found; take the novelty peak in first 70%
        candidates = [(novelty[i], i) for i in range(4, search_to) if rms[i] > 0.52]
        if candidates:
            drop_bar = max(candidates, key=lambda x: x[0])[1]

    if drop_bar is not None:
        is_flux  = flux[drop_bar] > 0.50
        is_lf    = lf_diff[drop_bar] > 0.28
        conf     = 0.65
        if is_flux:  conf += 0.12
        if is_lf:    conf += 0.08
        if had_dip:  conf += 0.05
        conf = min(0.90, conf)
        reasons = []
        if is_flux:  reasons.append("spectral flux peak")
        if is_lf:    reasons.append("bass/kick jump")
        if had_dip:  reasons.append("preceded by dip")
        note = (
            f"Drop at bar {drop_bar + 1}; energy {rms[drop_bar]:.0%} of peak"
            f"{'; ' + ', '.join(reasons) if reasons else ''}."
        )
        if conf < 0.70:
            note += " LOW CONFIDENCE — verify in Rekordbox."
        cues.append(CuePoint(
            cue_type="drop", time_sec=bt(drop_bar), bar=drop_bar + 1,
            confidence=conf, source="energy_analysis", note=note,
        ))

    # ----- 5. breakdown — deepest valley after drop -----
    breakdown_bar: Optional[int] = None
    bd_start = (drop_bar + 4) if drop_bar is not None else n // 3
    bd_end   = max(bd_start + 4, int(n * 0.85))

    if bd_start < n - 4:
        window = rms[bd_start:bd_end]
        if len(window) > 0 and window.min() < _BREAK_RMS:
            breakdown_bar = int(np.argmin(window)) + bd_start

    if breakdown_bar is not None:
        bd_rms = rms[breakdown_bar]
        bd_lf  = lf[breakdown_bar]
        conf   = 0.60
        if bd_lf < 0.25:  conf += 0.10
        if bd_rms < 0.30: conf += 0.08
        conf = min(0.82, conf)
        note = (
            f"Energy dips to {bd_rms:.0%} of peak at bar {breakdown_bar + 1}"
            f"{'; bass/kick largely absent' if bd_lf < 0.25 else ''}."
        )
        if conf < 0.65:
            note += " LOW CONFIDENCE — verify."
        cues.append(CuePoint(
            cue_type="breakdown", time_sec=bt(breakdown_bar), bar=breakdown_bar + 1,
            confidence=conf, source="energy_analysis", note=note,
        ))

    # ----- 6. outro_start — energy simplification near end -----
    outro_bar: Optional[int] = None
    ou_start = max(1, int(n * _OUTRO_ZONE))
    for i in range(ou_start, n):
        remaining = rms[i:]
        if rms[i] < _OUTRO_RMS and len(remaining) > 1 and remaining.mean() < _OUTRO_RMS:
            outro_bar = i
            break
    # fallback
    if outro_bar is None and ou_start < n:
        outro_bar = int(np.argmin(rms[ou_start:])) + ou_start

    if outro_bar is not None:
        ou_rms = rms[outro_bar]
        ou_lf  = lf[outro_bar]
        conf   = 0.68
        if ou_lf < 0.35:           conf += 0.07
        if outro_bar >= int(n * 0.82): conf += 0.05
        conf = min(0.82, conf)
        time_left = duration_sec - bt(outro_bar)
        note = (
            f"Outro from bar {outro_bar + 1}; ~{time_left:.0f}s remaining; "
            f"energy {ou_rms:.0%} of peak"
            f"{'; bass tapering' if ou_lf < 0.35 else ''}."
        )
        cues.append(CuePoint(
            cue_type="outro_start", time_sec=bt(outro_bar), bar=outro_bar + 1,
            confidence=conf, source="energy_analysis", note=note,
        ))

    return cues


# ---------------------------------------------------------------------------
# BPM-only fallback
# ---------------------------------------------------------------------------

def _detect_cues_estimate(bpm: float, duration_sec: float) -> List[CuePoint]:
    """
    Estimate cue points from BPM + duration only.
    Uses conventional club/house track structure.
    All confidence values are low (≤ 0.50).
    """
    if not bpm or bpm <= 0:
        bpm = 128.0
    bar_sec  = 4.0 * 60.0 / bpm

    def at_bar(n: int) -> float:
        return (n - 1) * bar_sec

    drop_bar  = max(25, int(duration_sec * 0.26 / bar_sec) + 1)
    bd_bar    = max(drop_bar + 8, int(duration_sec * 0.58 / bar_sec) + 1)
    outro_sec = max(duration_sec - 110.0, duration_sec * 0.80)
    outro_bar = max(bd_bar + 4, int(outro_sec / bar_sec) + 1)

    cues = [
        CuePoint("intro_start",  0.0,              1,
                 confidence=1.0,  source="bpm_estimate",
                 note="Bar 1. Audio analysis unavailable; BPM-estimate mode."),
        CuePoint("mix_in",       at_bar(9),  9,
                 confidence=0.50, source="bpm_estimate",
                 note="Bar 9 (conventional 2-bar intro end). LOW CONFIDENCE — verify."),
        CuePoint("groove_start", at_bar(17), 17,
                 confidence=0.40, source="bpm_estimate",
                 note="Bar 17 (conventional build end). LOW CONFIDENCE — verify."),
        CuePoint("drop",         at_bar(drop_bar), drop_bar,
                 confidence=0.35, source="bpm_estimate",
                 note=f"~26% through track (bar {drop_bar}). BPM estimate only. LOW CONFIDENCE."),
        CuePoint("breakdown",    at_bar(bd_bar),   bd_bar,
                 confidence=0.30, source="bpm_estimate",
                 note=f"~58% through track (bar {bd_bar}). BPM estimate only. LOW CONFIDENCE."),
        CuePoint("outro_start",  outro_sec, outro_bar,
                 confidence=0.45, source="bpm_estimate",
                 note=f"Last ~{int(duration_sec - outro_sec)}s (bar {outro_bar}). BPM estimate."),
    ]
    return [c for c in cues if c.time_sec < duration_sec - bar_sec * 0.5]


# ---------------------------------------------------------------------------
# File-level metadata fallback (no DB required)
# ---------------------------------------------------------------------------

def _meta_from_file(path: Path) -> Optional[dict]:
    """
    Read track metadata directly from audio tags via mutagen.
    Used when a file is not registered in the DB (e.g. --path mode).
    Returns a dict with keys matching db.get_track() output, or None if unreadable.
    """
    try:
        from mutagen import File as _MutagenFile
        audio = _MutagenFile(str(path), easy=True)
        if audio is None:
            return None
        duration_sec = float(getattr(audio.info, "length", 0) or 0)
        bpm = 0.0
        try:
            bpm_raw = audio.get("bpm") or audio.get("TBPM") or []
            if bpm_raw:
                bpm = float(str(bpm_raw[0]).strip())
        except (ValueError, IndexError):
            pass
        artist = ""
        try:
            a = audio.get("artist") or []
            if a:
                artist = str(a[0])
        except (ValueError, IndexError):
            pass
        title = ""
        try:
            t = audio.get("title") or []
            if t:
                title = str(t[0])
        except (ValueError, IndexError):
            pass
        key = ""
        try:
            k = audio.get("initialkey") or audio.get("TKEY") or []
            if k:
                key = str(k[0])
        except (ValueError, IndexError):
            pass
        return {
            "filepath":     str(path),
            "artist":       artist,
            "title":        title,
            "bpm":          bpm,
            "key_camelot":  key,
            "duration_sec": duration_sec,
        }
    except Exception as exc:
        log.debug("_meta_from_file: could not read %s: %s", path.name, exc)
        return None


# ---------------------------------------------------------------------------
# Per-track analysis entry point
# ---------------------------------------------------------------------------

def analyze_track(
    path: Path, bpm: float, duration_sec: float
) -> Tuple[List[CuePoint], str]:
    """
    Analyze one track. Returns (cues, method_label).
    Falls back to BPM estimation if audio decode or feature extraction fails.
    """
    bar_times = _make_bar_grid(bpm, duration_sec)

    if _NUMPY_OK:
        audio = _load_audio_numpy(path)
        if audio is not None:
            y, sr = audio
            try:
                feat = _extract_features(y, sr, bar_times)
                if feat is not None:
                    cues = _detect_cues_full(feat, bar_times, duration_sec)
                    if cues:
                        return cues, "energy_analysis"
            except Exception as exc:
                log.debug("Feature extraction failed for %s: %s", path.name, exc)

    return _detect_cues_estimate(bpm, duration_sec), "bpm_estimate"


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log_cues(tc: TrackCues) -> None:
    low = sum(1 for c in tc.cues if c.confidence < 0.50)
    log.info(
        "CUE  %-38s  BPM=%-6.1f  %-5s  method=%-16s  low_conf=%d",
        f"{tc.artist[:16]} — {tc.title[:18]}",
        tc.bpm, tc.camelot, tc.method, low,
    )
    for cue in tc.cues:
        marker = "!" if cue.confidence < 0.50 else " "
        log.info("  %s %-14s  %s  bar=%-3d  conf=%.2f",
                 marker, cue.cue_type, cue.time_fmt, cue.bar, cue.confidence)


# ---------------------------------------------------------------------------
# Output: per-run detail CSV
# ---------------------------------------------------------------------------

def _write_run_csv(results: List[TrackCues], runs_dir: Path) -> Path:
    runs_dir.mkdir(parents=True, exist_ok=True)
    ts   = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    path = runs_dir / f"cues_{ts}.csv"
    fields = [
        "filepath", "artist", "title", "bpm", "key", "duration_sec",
        "cue_type", "time_sec", "time_fmt", "bar", "confidence",
        "source", "method", "note",
    ]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for tc in results:
            for cue in tc.cues:
                w.writerow({
                    "filepath":     tc.filepath,
                    "artist":       tc.artist,
                    "title":        tc.title,
                    "bpm":          round(tc.bpm, 1),
                    "key":          tc.camelot,
                    "duration_sec": round(tc.duration_sec, 1),
                    "cue_type":     cue.cue_type,
                    "time_sec":     round(cue.time_sec, 2),
                    "time_fmt":     cue.time_fmt,
                    "bar":          cue.bar,
                    "confidence":   round(cue.confidence, 3),
                    "source":       cue.source,
                    "method":       tc.method,
                    "note":         cue.note,
                })
    return path


# ---------------------------------------------------------------------------
# Output: master files (from DB — full library snapshot)
# ---------------------------------------------------------------------------

def _build_db_records() -> List[dict]:
    """
    Pull all stored cue points from the DB and join with track metadata.
    Returns a list of per-track dicts (all cue types as nested keys).
    """
    records = []
    for fp in db.get_tracks_with_cues():
        row  = db.get_track(fp)
        cues = db.get_cue_points(fp)
        if not row:
            continue
        entry: dict = {
            "filepath":     fp,
            "artist":       row["artist"] or "Unknown",
            "title":        row["title"]  or Path(fp).stem,
            "bpm":          round(float(row["bpm"] or 0), 2),
            "key":          row["key_camelot"] or "",
            "duration_sec": round(float(row["duration_sec"] or 0), 1),
            "cues":         {},
        }
        for cue in cues:
            ct = cue["cue_type"]
            entry["cues"][ct] = {
                "time_sec":   round(float(cue["time_sec"]), 2),
                "bar":        cue["bar"],
                "confidence": round(float(cue["confidence"]), 3),
                "source":     cue["source"],
                "note":       "",   # notes not persisted to DB
            }
        records.append(entry)
    return records


_DISCLAIMER = (
    "These are SUGGESTED cue positions only. "
    "Native Rekordbox hot-cues are NOT written by this tool. "
    "Review and confirm all positions in Rekordbox before use in a live set."
)


def _write_master_json(out_dir: Path) -> Path:
    records = _build_db_records()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cue_suggestions.json"
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer":   _DISCLAIMER,
        "track_count":  len(records),
        "cue_types":    CUE_TYPES,
        "tracks":       records,
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def _write_master_csv(out_dir: Path) -> Path:
    """
    Wide-format CSV: one row per track.
    Columns: filepath, artist, title, bpm, key, duration_sec,
    then per cue_type: <type>_sec, <type>_conf, <type>_source, <type>_note.
    """
    records = _build_db_records()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "cue_suggestions.csv"

    base   = ["filepath", "artist", "title", "bpm", "key", "duration_sec"]
    cue_cols = []
    for ct in CUE_TYPES:
        cue_cols += [f"{ct}_sec", f"{ct}_conf", f"{ct}_source", f"{ct}_note"]

    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=base + cue_cols, extrasaction="ignore")
        w.writeheader()
        for rec in records:
            row: dict = {
                "filepath":     rec["filepath"],
                "artist":       rec["artist"],
                "title":        rec["title"],
                "bpm":          rec["bpm"],
                "key":          rec["key"],
                "duration_sec": rec["duration_sec"],
            }
            for ct in CUE_TYPES:
                c = rec["cues"].get(ct)
                row[f"{ct}_sec"]    = round(c["time_sec"], 2)    if c else ""
                row[f"{ct}_conf"]   = round(c["confidence"], 3)  if c else ""
                row[f"{ct}_source"] = c["source"]                 if c else ""
                row[f"{ct}_note"]   = c.get("note", "")           if c else ""
            w.writerow(row)
    return path


# ---------------------------------------------------------------------------
# Optional sidecar JSON
# ---------------------------------------------------------------------------

def _write_sidecar_json(tc: TrackCues) -> None:
    sidecar = Path(tc.filepath).with_suffix(".cues.json")
    try:
        data = {"disclaimer": _DISCLAIMER, **tc.to_dict()}
        sidecar.write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    except Exception as exc:
        log.warning("Could not write sidecar %s: %s", sidecar, exc)


# ---------------------------------------------------------------------------
# Public run() entry point
# ---------------------------------------------------------------------------

def run(
    paths:          List[Path],
    dry_run:        bool                = False,
    min_conf:       Optional[float]     = None,
    track_filter:   Optional[str]       = None,  # substring match: artist / title / filename
    limit:          Optional[int]       = None,  # max tracks to process
    export_formats: Optional[List[str]] = None,  # ["json", "csv"] — default: both
) -> Tuple[int, int]:
    """
    Analyse tracks and store cue point suggestions.

    Args:
        paths:          Audio file paths to consider.
        dry_run:        Print results only; no DB writes or file output.
        min_conf:       Minimum confidence to store a cue in DB.
        track_filter:   Case-insensitive substring filter on artist/title/filename.
        limit:          Stop after this many tracks are analysed.
        export_formats: Output format list. Options: "json", "csv".

    Returns:
        (tracks_analyzed, cues_stored)
    """
    if min_conf is None:
        min_conf = float(getattr(config, "CUE_SUGGEST_MIN_CONFIDENCE", 0.4))
    if export_formats is None:
        export_formats = ["json", "csv"]

    write_sidecars = getattr(config, "CUE_SUGGEST_WRITE_SIDECARS", False)
    out_dir        = config.CUE_SUGGEST_OUTPUT_DIR
    runs_dir       = out_dir / "runs"
    now_str        = datetime.now(timezone.utc).isoformat()

    results:      List[TrackCues] = []
    analyzed      = 0
    cues_stored   = 0
    skipped       = 0
    low_conf_trks = 0

    for path in paths:
        if limit is not None and analyzed >= limit:
            break
        if not path.exists():
            continue

        row = db.get_track(str(path))
        if row is None:
            row = _meta_from_file(path)
            if row is None:
                log.debug("cue_suggest: skip %s — not in DB and tags unreadable", path.name)
                skipped += 1
                continue

        # Track filter
        if track_filter:
            haystack = " ".join(filter(None, [
                row["artist"] or "", row["title"] or "", path.stem,
            ])).lower()
            if track_filter.lower() not in haystack:
                log.debug("cue_suggest: skip %s — filtered by --track", path.name)
                continue

        bpm  = float(row["bpm"] or 0)
        dur  = float(row["duration_sec"] or 0)

        if dur < 30:
            log.debug("cue_suggest: skip %s — too short (%.1fs)", path.name, dur)
            skipped += 1
            continue

        cues, method = analyze_track(path, bpm, dur)

        tc = TrackCues(
            filepath     = str(path),
            title        = row["title"]  or path.stem,
            artist       = row["artist"] or "Unknown",
            bpm          = bpm,
            camelot      = row["key_camelot"] or "",
            duration_sec = dur,
            cues         = cues,
            analyzed_at  = now_str,
            method       = method,
        )

        _log_cues(tc)
        results.append(tc)
        analyzed += 1

        if any(c.confidence < 0.50 for c in cues):
            low_conf_trks += 1

        if not dry_run:
            storable = [c for c in cues if c.confidence >= min_conf]
            if storable:
                db.save_cue_points(str(path), [c.to_db_dict() for c in storable])
                cues_stored += len(storable)
            if write_sidecars:
                _write_sidecar_json(tc)

    # Per-run CSV (written even on dry-run — read-only output for review)
    if results:
        run_csv = _write_run_csv(results, runs_dir)
        log.info("cue-suggest: run log → %s", run_csv)

    # Master outputs: rebuild from DB (full library snapshot)
    if not dry_run and results:
        if "json" in export_formats:
            p = _write_master_json(out_dir)
            log.info("cue-suggest: master JSON → %s", p)
        if "csv" in export_formats:
            p = _write_master_csv(out_dir)
            log.info("cue-suggest: master CSV  → %s", p)

    log.info(
        "cue-suggest: %d analysed | %d stored | %d skipped | %d low-confidence",
        analyzed, cues_stored, skipped, low_conf_trks,
    )
    log_action(
        f"CUE-SUGGEST {'DRY-RUN' if dry_run else 'DONE'}: "
        f"{analyzed} analysed, {cues_stored} stored, {low_conf_trks} low-conf"
    )
    return analyzed, cues_stored
