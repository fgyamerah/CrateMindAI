"""
modules/set_builder.py — Energy-curve auto set builder.

Builds a DJ set from the library database, arranging tracks across
configurable phases (warmup → build → peak → release → outro) using
harmonic transition scoring from harmonic.py.

Outputs (per run):
  - M3U8 playlist     → SET_BUILDER_OUTPUT_DIR/<name>.m3u8
  - CSV summary       → SET_BUILDER_OUTPUT_DIR/<name>.csv
  - DB record         → set_playlists + set_playlist_tracks tables

Vibes:
  warm     — extended warmup/build, light peak section
  peak     — strong peak section; high BPM focus
  deep     — melodic/organic genres preferred; relaxed pacing
  driving  — sustained mid-to-peak energy throughout
"""
from __future__ import annotations

import csv
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
import db
from modules.harmonic import (
    _classify_energy,
    _ENERGY_RANK,
    score_transition,
    camelot_score,
    bpm_score,
    _DEFAULT_WEIGHTS,
    _is_unknown_artist,
    _dedupe_candidates,
    _normalize_artist,
    _bpm_step_multiplier,
)
from modules.textlog import log_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Route exclusivity guard
# Acapella and Tool tracks must not enter the DJ candidate pool.
# ---------------------------------------------------------------------------
def _is_exclusive_route(row) -> bool:
    """Return True for Acapella and Tool tracks."""
    fp = str(row["filepath"] or "")
    if fp.startswith(str(config.ACAPELLA)) or fp.startswith(str(config.DJ_TOOLS)):
        return True
    combined = f"{row['genre'] or ''} {row['title'] or ''}".lower()
    if "acapella" in combined or "a cappella" in combined:
        return True
    if any(kw in combined for kw in ("dj tool", "drum tool", "fx tool", "percussion tool")):
        return True
    return False


# ---------------------------------------------------------------------------
# Phase definitions
# ---------------------------------------------------------------------------

# Each phase specifies which energy tiers and BPM range are preferred.
# bpm_range is used as a soft filter — tracks outside range are still
# included if the pool is small, but penalised during selection.
_PHASE_CONFIG: Dict[str, Dict] = {
    "warmup":  {"energies": ["Chill", "Mid"],      "bpm_min": 100, "bpm_max": 125},
    "build":   {"energies": ["Mid", "Peak"],        "bpm_min": 118, "bpm_max": 130},
    "peak":    {"energies": ["Peak"],               "bpm_min": 124, "bpm_max": 150},
    "release": {"energies": ["Mid", "Chill"],       "bpm_min": 110, "bpm_max": 128},
    "outro":   {"energies": ["Chill", "Mid"],       "bpm_min": 95,  "bpm_max": 125},
}

# Phase order is always the same; vibe presets control time allocation.
_PHASE_ORDER = ["warmup", "build", "peak", "release", "outro"]

# ---------------------------------------------------------------------------
# Vibe presets — fraction of total set duration allocated to each phase
# ---------------------------------------------------------------------------

_VIBE_WEIGHTS: Dict[str, Dict[str, float]] = {
    "warm": {
        "warmup":  0.30,
        "build":   0.30,
        "peak":    0.15,
        "release": 0.15,
        "outro":   0.10,
    },
    "peak": {
        "warmup":  0.12,
        "build":   0.20,
        "peak":    0.40,
        "release": 0.18,
        "outro":   0.10,
    },
    "deep": {
        "warmup":  0.25,
        "build":   0.30,
        "peak":    0.15,
        "release": 0.20,
        "outro":   0.10,
    },
    "driving": {
        "warmup":  0.15,
        "build":   0.25,
        "peak":    0.35,
        "release": 0.15,
        "outro":   0.10,
    },
}

# Deep vibe prefers these genre keywords
_DEEP_GENRES = {
    "deep house", "organic house", "melodic house", "afro house",
    "melodic techno", "melodic", "organic",
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class SetTrack:
    filepath:        str
    artist:          str
    title:           str
    bpm:             float
    key_camelot:     str
    genre:           str
    energy:          str
    duration_sec:    float
    phase:           str
    position:        int
    transition_note: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _avg_track_duration(rows: list) -> float:
    """Return mean track duration from DB rows (fallback 6 minutes)."""
    durations = [float(r["duration_sec"] or 0) for r in rows if r["duration_sec"]]
    if not durations:
        return 360.0
    return sum(durations) / len(durations)


def _score_row_for_phase(row, phase: str) -> float:
    """
    Return a 0–1 fitness score for a DB row relative to a phase.
    Used to pre-rank the candidate pool before greedy selection.
    """
    pcfg = _PHASE_CONFIG[phase]
    bpm  = float(row["bpm"] or 0)
    genre = row["genre"] or ""
    energy = _classify_energy(bpm, genre)

    score = 0.0

    # Energy match
    if energy in pcfg["energies"]:
        score += 0.5
    elif _ENERGY_RANK.get(energy, 1) in [
        _ENERGY_RANK.get(e, 1) for e in pcfg["energies"]
    ]:
        score += 0.3

    # BPM range
    if pcfg["bpm_min"] <= bpm <= pcfg["bpm_max"]:
        score += 0.5
    elif bpm > 0:
        # Partial credit for proximity to range
        dist = min(abs(bpm - pcfg["bpm_min"]), abs(bpm - pcfg["bpm_max"]))
        score += max(0.0, 0.3 - dist / 100.0)

    return min(1.0, score)


def _genre_matches_deep(genre: str) -> bool:
    g = (genre or "").strip().lower()
    return any(d in g for d in _DEEP_GENRES)


def _filter_for_phase(
    rows:         list,
    phase:        str,
    genre_filter: Optional[str],
    vibe:         str,
    used_paths:   set,
) -> list:
    """
    Filter and sort candidates for a phase.
    Returns a ranked list (best-fit first), excluding used tracks.
    """
    candidates = []
    genre_lower = genre_filter.strip().lower() if genre_filter else None

    for row in rows:
        if row["filepath"] in used_paths:
            continue
        if not row["bpm"] or not row["key_camelot"]:
            continue  # need both for harmonic scoring

        # Exclude tracks with missing/unknown artist
        if _is_unknown_artist(row["artist"] or ""):
            continue

        row_genre = (row["genre"] or "").strip().lower()

        # Genre filter
        if genre_lower and genre_lower not in row_genre:
            continue

        # Deep vibe: soft-prefer deep genres (don't hard-filter to allow fallback)
        deep_bonus = 0.2 if (vibe == "deep" and _genre_matches_deep(row["genre"])) else 0.0

        fit = _score_row_for_phase(row, phase) + deep_bonus
        candidates.append((fit, row))

    # Sort highest-fit first
    candidates.sort(key=lambda x: x[0], reverse=True)
    return [row for _, row in candidates]


def _pick_next(
    last_row,
    pool:          list,
    strategy:      str,
    used:          set,
    phase:         str,
    last_bpm:      float = 0.0,
    used_artists:  Optional[set] = None,
) -> Optional[object]:
    """
    Greedy pick: choose the highest-scored transition from pool.

    Applies two additional constraints:
    - BPM step penalty: large absolute BPM jumps are penalised via
      _bpm_step_multiplier, preventing 122→150 type leaps.
    - Artist repeat penalty: tracks by an artist already used in this set
      receive a 0.35× multiplier so the same artist doesn't spam the set.
    """
    if not pool:
        return None

    if last_row is None:
        return pool[0]  # first track of the set

    scored = []
    for row in pool:
        if row["filepath"] in used:
            continue
        try:
            ts = score_transition(last_row, row)
            s  = ts.strategies.get(strategy, ts.total_score)

            # BPM step constraint
            if last_bpm > 0:
                s = s * _bpm_step_multiplier(last_bpm, float(row["bpm"] or last_bpm))

            # Artist repeat penalty (soft — does not hard-exclude)
            if used_artists:
                primary = _normalize_artist(row["artist"] or "")
                if primary and primary in used_artists:
                    s *= 0.35

            scored.append((s, row))
        except Exception:
            scored.append((0.0, row))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[0][1]


# ---------------------------------------------------------------------------
# Core set builder
# ---------------------------------------------------------------------------

def build_set(
    target_duration_min: int           = 60,
    genre_filter:        Optional[str] = None,
    vibe:                str           = "peak",
    start_energy:        Optional[str] = None,
    end_energy:          Optional[str] = None,
    strategy:            str           = "safest",
) -> List[SetTrack]:
    """
    Build a set from the library DB.

    Args:
        target_duration_min: Target set duration in minutes.
        genre_filter:        Restrict candidates to this genre (substring match).
        vibe:                Phase-weight preset — warm / peak / deep / driving.
        start_energy:        Override the energy tier for the first track.
        end_energy:          Override the energy tier for the last track.
        strategy:            Harmonic ranking strategy for transitions.

    Returns:
        Ordered list of SetTrack instances.
    """
    rows = db.get_all_ok_tracks()
    if not rows:
        log.warning("set-builder: no OK tracks found in DB")
        return []

    # Exclude Acapella and Tool tracks from DJ candidate pool
    before = len(rows)
    rows = [r for r in rows if not _is_exclusive_route(r)]
    excluded = before - len(rows)
    if excluded:
        log.info("set-builder: excluded %d Acapella/Tool track(s) from candidate pool", excluded)

    # Deduplicate the full library pool before any phase filtering
    rows = _dedupe_candidates(rows)

    vibe = vibe if vibe in _VIBE_WEIGHTS else "peak"
    phase_weights = _VIBE_WEIGHTS[vibe]
    target_sec    = target_duration_min * 60.0
    avg_dur       = _avg_track_duration(rows)

    set_tracks:   List[SetTrack] = []
    used_paths:   set            = set()
    used_artists: set            = set()   # normalized primary artists already in set
    last_row                     = None
    last_bpm:     float          = 0.0
    position                     = 1

    for phase in _PHASE_ORDER:
        phase_target_sec = target_sec * phase_weights[phase]
        phase_sec_used   = 0.0

        pool = _filter_for_phase(rows, phase, genre_filter, vibe, used_paths)

        # If the pool is very thin, relax genre filter
        if len(pool) < 3 and genre_filter:
            pool = _filter_for_phase(rows, phase, None, vibe, used_paths)

        if not pool:
            log.debug("set-builder: no candidates for phase=%s, skipping", phase)
            continue

        while phase_sec_used < phase_target_sec:
            next_row = _pick_next(
                last_row, pool, strategy, used_paths, phase,
                last_bpm=last_bpm, used_artists=used_artists,
            )
            if next_row is None:
                break

            filepath    = str(next_row["filepath"])
            bpm         = float(next_row["bpm"] or 0)
            genre       = next_row["genre"] or ""
            energy      = _classify_energy(bpm, genre)
            dur         = float(next_row["duration_sec"] or avg_dur)

            # Build a brief transition note
            note = ""
            if last_row is not None:
                try:
                    ts   = score_transition(last_row, next_row)
                    note = ts.explanation
                except Exception:
                    pass

            st = SetTrack(
                filepath        = filepath,
                artist          = next_row["artist"] or "Unknown",
                title           = next_row["title"] or Path(filepath).stem,
                bpm             = bpm,
                key_camelot     = next_row["key_camelot"] or "",
                genre           = genre,
                energy          = energy,
                duration_sec    = dur,
                phase           = phase,
                position        = position,
                transition_note = note,
            )
            set_tracks.append(st)
            used_paths.add(filepath)
            primary = _normalize_artist(next_row["artist"] or "")
            if primary:
                used_artists.add(primary)
            pool      = [r for r in pool if r["filepath"] not in used_paths]
            last_row  = next_row
            last_bpm  = bpm if bpm > 0 else last_bpm
            phase_sec_used += dur
            position       += 1

    return set_tracks


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write_m3u(tracks: List[SetTrack], path: Path, dry_run: bool) -> None:
    if dry_run:
        log.info("[DRY-RUN] Would write M3U: %s (%d tracks)", path, len(tracks))
        for t in tracks:
            log.info("  [%s] %s - %s  %.0fbpm %s  %.0fs",
                     t.phase, t.artist, t.title, t.bpm, t.key_camelot, t.duration_sec)
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        fh.write("#EXTM3U\n")
        for t in tracks:
            dur_i = int(t.duration_sec)
            fh.write(f"#EXTINF:{dur_i},{t.artist} - {t.title}\n")
            fh.write(f"#EXT-X-SET-PHASE:{t.phase}\n")
            fh.write(f"{t.filepath}\n")
    log.info("Set M3U written: %s", path)


def _write_csv(tracks: List[SetTrack], path: Path, dry_run: bool) -> None:
    if dry_run:
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "position", "phase", "artist", "title", "bpm", "key",
        "energy", "genre", "duration_sec", "transition_note", "filepath",
    ]
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for t in tracks:
            writer.writerow({
                "position":        t.position,
                "phase":           t.phase,
                "artist":          t.artist,
                "title":           t.title,
                "bpm":             f"{t.bpm:.1f}",
                "key":             t.key_camelot,
                "energy":          t.energy,
                "genre":           t.genre,
                "duration_sec":    f"{t.duration_sec:.0f}",
                "transition_note": t.transition_note,
                "filepath":        t.filepath,
            })
    log.info("Set CSV written: %s", path)


def _print_summary(tracks: List[SetTrack], name: str) -> None:
    total_min = sum(t.duration_sec for t in tracks) / 60.0
    phases    = {}
    for t in tracks:
        phases.setdefault(t.phase, []).append(t)

    print(f"\n=== Set Builder: {name} ===")
    print(f"  Total tracks : {len(tracks)}")
    print(f"  Total duration: {total_min:.1f} min")
    print()
    for phase in _PHASE_ORDER:
        pts = phases.get(phase, [])
        if not pts:
            continue
        pdur = sum(t.duration_sec for t in pts) / 60.0
        print(f"  {phase.upper():<10}  {len(pts):>3} tracks  {pdur:.1f} min")
    print()


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run(
    target_duration_min: int           = 60,
    genre_filter:        Optional[str] = None,
    vibe:                str           = "peak",
    start_energy:        Optional[str] = None,
    end_energy:          Optional[str] = None,
    strategy:            str           = "safest",
    name:                Optional[str] = None,
    dry_run:             bool          = False,
) -> Tuple[int, Optional[Path]]:
    """
    Build a set and write all outputs.

    Returns:
        (track_count, m3u_path)  — m3u_path is None on dry_run or empty set
    """
    if not name:
        ts   = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        name = f"set_{ts}_{vibe}_{target_duration_min}min"

    log.info(
        "set-builder: vibe=%s  duration=%dmin  genre=%s  strategy=%s",
        vibe, target_duration_min, genre_filter or "any", strategy,
    )
    log_action(
        f"SET-BUILDER START: vibe={vibe} duration={target_duration_min}min "
        f"genre={genre_filter or 'any'} strategy={strategy}"
    )

    tracks = build_set(
        target_duration_min = target_duration_min,
        genre_filter        = genre_filter,
        vibe                = vibe,
        start_energy        = start_energy,
        end_energy          = end_energy,
        strategy            = strategy,
    )

    if not tracks:
        log.warning("set-builder: no tracks selected — check your library DB")
        return 0, None

    out_dir   = config.SET_BUILDER_OUTPUT_DIR
    m3u_path  = out_dir / f"{name}.m3u8"
    csv_path  = out_dir / f"{name}.csv"

    _write_m3u(tracks, m3u_path, dry_run)
    _write_csv(tracks, csv_path, dry_run)
    _print_summary(tracks, name)

    if not dry_run:
        total_sec = sum(t.duration_sec for t in tracks)
        db_tracks = [
            {
                "filepath":        t.filepath,
                "phase":           t.phase,
                "transition_note": t.transition_note,
            }
            for t in tracks
        ]
        import json as _json
        cfg_json = _json.dumps({
            "vibe":                 vibe,
            "target_duration_min":  target_duration_min,
            "genre_filter":         genre_filter,
            "strategy":             strategy,
        })
        db.save_set_playlist(name, db_tracks, cfg_json, total_sec)
        log.info("Set saved to DB: %s", name)

    log_action(
        f"SET-BUILDER DONE: {len(tracks)} tracks → {m3u_path.name if not dry_run else '[dry-run]'}"
    )
    return len(tracks), m3u_path if not dry_run else None
