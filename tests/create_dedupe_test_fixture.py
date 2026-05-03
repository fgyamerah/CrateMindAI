#!/usr/bin/env python3
"""
tests/create_dedupe_test_fixture.py

Standalone fixture generator for the library_dedupe engine.

Builds valid short silent audio files (via ffmpeg) under:
    ~/Music/music/tmp_dedupe_test/inbox/

No pipeline imports. No new pip dependencies — only stdlib + subprocess.
Generated files are real audio that any player, tagger, or dedupe engine
can read.  Supported formats: mp3, flac, m4a.

Usage
-----
    python tests/create_dedupe_test_fixture.py            # create
    python tests/create_dedupe_test_fixture.py --cleanup  # remove
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Destination
# ---------------------------------------------------------------------------
FIXTURE_ROOT = Path.home() / "Music" / "music" / "tmp_dedupe_test"
INBOX        = FIXTURE_ROOT / "inbox"

# ---------------------------------------------------------------------------
# ffmpeg bootstrap
# ---------------------------------------------------------------------------
FFMPEG: str = ""   # resolved once at startup via _require_ffmpeg()


def _require_ffmpeg() -> str:
    """Locate the ffmpeg binary or exit with a clear, actionable error."""
    path = shutil.which("ffmpeg")
    if not path:
        print("ERROR: ffmpeg not found in PATH.")
        print("Install it with:  sudo apt install ffmpeg")
        print("Then re-run this script.")
        sys.exit(1)
    return path


# ---------------------------------------------------------------------------
# Audio file generator
# ---------------------------------------------------------------------------

def make_audio(
    path: Path,
    *,
    title:        str   = "",
    artist:       str   = "",
    album:        str   = "",
    fmt:          str   = "mp3",
    bitrate_kbps: int   = 128,
    duration:     float = 0.3,
) -> Path:
    """
    Generate a short silent audio file with embedded metadata using ffmpeg.

    Supported formats
    -----------------
    mp3   — encoded with libmp3lame at bitrate_kbps
    flac  — lossless, compression level 5
    m4a   — AAC in an MP4 container at bitrate_kbps

    Returns the path on success; prints error and exits on ffmpeg failure.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        FFMPEG, "-y", "-loglevel", "error",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
    ]

    # Metadata tags
    for key, val in [("title", title), ("artist", artist), ("album", album)]:
        if val:
            cmd += ["-metadata", f"{key}={val}"]

    # Format / codec / bitrate
    if fmt == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrate_kbps}k"]
    elif fmt == "flac":
        cmd += ["-c:a", "flac", "-compression_level", "5"]
    elif fmt == "m4a":
        cmd += ["-c:a", "aac", "-b:a", f"{bitrate_kbps}k", "-f", "mp4"]
    else:
        print(f"ERROR: unsupported format '{fmt}'. Choices: mp3, flac, m4a")
        sys.exit(1)

    cmd.append(str(path))

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"\nERROR: ffmpeg failed while generating {path}")
        # Show the tail of stderr — ffmpeg outputs the useful part last
        print(result.stderr[-800:])
        sys.exit(1)

    return path


def copy_exact(src: Path, dst: Path) -> Path:
    """Copy src bytes verbatim → byte-identical file (same SHA-256 as src)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Test case definitions
# ---------------------------------------------------------------------------

Case = tuple[str, str, list[Path]]   # (label, expected_outcome, files)


def create_fixture() -> list[Case]:
    """Create all test cases and return a list of (label, expectation, paths)."""
    cases: list[Case] = []

    # ------------------------------------------------------------------
    # Case 1 — Exact duplicates (byte-identical)
    # Create one valid MP3 with ffmpeg, then copy it verbatim.
    # Identical SHA-256 → Case A detection.
    # Dedupe keeps the higher-priority path, quarantines the copy.
    # ------------------------------------------------------------------
    src1 = make_audio(
        INBOX / "Artist A" / "Track One.mp3",
        title="Track One", artist="Artist A", album="Album A",
        fmt="mp3", bitrate_kbps=128,
    )
    dup1 = copy_exact(src1, INBOX / "Artist A" / "Track One (copy).mp3")
    cases.append((
        "Case 1 — Exact duplicates",
        "Case A: 1 group — 'Track One (copy).mp3' quarantined",
        [src1, dup1],
    ))

    # ------------------------------------------------------------------
    # Case 2 — Same track, different format (MP3 vs FLAC)
    # FLAC quality > MP3/128 → Case B: FLAC kept, MP3 quarantined.
    # ------------------------------------------------------------------
    c2 = [
        make_audio(INBOX / "Artist B" / "Deep Groove.mp3",
                   title="Deep Groove", artist="Artist B", album="Album B",
                   fmt="mp3", bitrate_kbps=128),
        make_audio(INBOX / "Artist B" / "Deep Groove.flac",
                   title="Deep Groove", artist="Artist B", album="Album B",
                   fmt="flac"),
    ]
    cases.append((
        "Case 2 — Quality duplicate: format (MP3 vs FLAC)",
        "Case B: 1 group — FLAC kept (lossless), MP3 quarantined",
        c2,
    ))

    # ------------------------------------------------------------------
    # Case 3 — Same track, different format/bitrate (MP3 128 vs M4A 256)
    # Demonstrates M4A support.  M4A 256 kbps > MP3 128 kbps → Case B.
    # ------------------------------------------------------------------
    c3 = [
        make_audio(INBOX / "Artist C" / "Sunset Ride_128.mp3",
                   title="Sunset Ride", artist="Artist C", album="Album C",
                   fmt="mp3", bitrate_kbps=128),
        make_audio(INBOX / "Artist C" / "Sunset Ride_256.m4a",
                   title="Sunset Ride", artist="Artist C", album="Album C",
                   fmt="m4a", bitrate_kbps=256),
    ]
    cases.append((
        "Case 3 — Quality duplicate: bitrate/format (MP3 128 vs M4A 256)",
        "Case B: 1 group — M4A 256 kbps kept, MP3 128 kbps quarantined",
        c3,
    ))

    # ------------------------------------------------------------------
    # Case 4 — Version protection (different mix types)
    # All three share base title "Tribal Energy" but carry distinct version
    # strings.  _extract_version() splits each → Case C: reported, kept.
    # ------------------------------------------------------------------
    c4_tracks = [
        ("Tribal Energy (Original Mix)",  "Tribal Energy (Original Mix).mp3"),
        ("Tribal Energy (Radio Edit)",    "Tribal Energy (Radio Edit).mp3"),
        ("Tribal Energy (Extended Mix)",  "Tribal Energy (Extended Mix).mp3"),
    ]
    c4 = [
        make_audio(INBOX / "Artist D" / fname,
                   title=title, artist="Artist D", album="Album D",
                   fmt="mp3", bitrate_kbps=256)
        for title, fname in c4_tracks
    ]
    cases.append((
        "Case 4 — Version protection: Original / Radio Edit / Extended",
        "Case C: 1 group — all 3 files kept (no quarantine, report only)",
        c4,
    ))

    # ------------------------------------------------------------------
    # Case 5 — Remix protection (different remixers)
    # Base title "Night Motion", both carry "Remix" but remixer names
    # differ → version strings differ → Case C: both kept.
    # ------------------------------------------------------------------
    c5_tracks = [
        ("Night Motion (Caiiro Remix)",  "Night Motion (Caiiro Remix).mp3"),
        ("Night Motion (Da Capo Remix)", "Night Motion (Da Capo Remix).mp3"),
    ]
    c5 = [
        make_audio(INBOX / "Artist E" / fname,
                   title=title, artist="Artist E", album="Album E",
                   fmt="mp3", bitrate_kbps=128)
        for title, fname in c5_tracks
    ]
    cases.append((
        "Case 5 — Remix protection: different remixers",
        "Case C: 1 group — both files kept (no quarantine, report only)",
        c5,
    ))

    # ------------------------------------------------------------------
    # Case 6 — False positive prevention: same title, different artists
    # title_bins key includes normalized artist → different bins →
    # no grouping of any kind.
    # ------------------------------------------------------------------
    c6 = [
        make_audio(INBOX / "Artist F" / "Energy.mp3",
                   title="Energy", artist="Artist F", album="Album F",
                   fmt="mp3", bitrate_kbps=128),
        make_audio(INBOX / "Artist G" / "Energy.mp3",
                   title="Energy", artist="Artist G", album="Album G",
                   fmt="mp3", bitrate_kbps=128),
    ]
    cases.append((
        "Case 6 — False positive: same title / different artists",
        "No groups — files are unrelated (artist mismatch)",
        c6,
    ))

    # ------------------------------------------------------------------
    # Case 7 — Acapella protection
    # "acapella" is in VERSION_KEYWORDS → _extract_version() splits
    # "Fire Inside (Acapella)" → (base="Fire Inside", version="Acapella").
    # version != "" → Case C: both kept.
    # ------------------------------------------------------------------
    c7 = [
        make_audio(INBOX / "Artist H" / "Fire Inside.mp3",
                   title="Fire Inside", artist="Artist H", album="Album H",
                   fmt="mp3", bitrate_kbps=128),
        make_audio(INBOX / "Artist H" / "Fire Inside (Acapella).mp3",
                   title="Fire Inside (Acapella)", artist="Artist H", album="Album H",
                   fmt="mp3", bitrate_kbps=128),
    ]
    cases.append((
        "Case 7 — Acapella protection",
        "Case C: 1 group — both files kept (no quarantine, report only)",
        c7,
    ))

    # ------------------------------------------------------------------
    # Case 8 — Near-duplicate names (should NOT match)
    # "Ocean Wave" vs "Ocean Waves" normalise to different base titles.
    # Different keys → no grouping of any kind.
    # ------------------------------------------------------------------
    c8 = [
        make_audio(INBOX / "Artist I" / "Ocean Wave.mp3",
                   title="Ocean Wave", artist="Artist I", album="Album I",
                   fmt="mp3", bitrate_kbps=128),
        make_audio(INBOX / "Artist I" / "Ocean Waves.mp3",
                   title="Ocean Waves", artist="Artist I", album="Album I",
                   fmt="mp3", bitrate_kbps=128),
    ]
    cases.append((
        "Case 8 — Near-duplicate names: 'Ocean Wave' vs 'Ocean Waves'",
        "No groups — base titles differ after normalisation",
        c8,
    ))

    # ------------------------------------------------------------------
    # Case 9 — Nested folder structure: exact duplicates
    # Same as Case 1 but two levels deep (Label X / Artist J).
    # When quarantined with source_root=INBOX, the relative path
    # Label X/Artist J/ must be preserved inside the quarantine dir.
    # ------------------------------------------------------------------
    src9 = make_audio(
        INBOX / "Label X" / "Artist J" / "Deep Ritual.mp3",
        title="Deep Ritual", artist="Artist J", album="Album J",
        fmt="mp3", bitrate_kbps=128,
    )
    dup9 = copy_exact(src9, INBOX / "Label X" / "Artist J" / "Deep Ritual (copy).mp3")
    cases.append((
        "Case 9 — Nested folder: exact duplicates in Label X/Artist J/",
        "Case A: 1 group — quarantine preserves Label X/Artist J/ sub-path",
        [src9, dup9],
    ))

    return cases


# ---------------------------------------------------------------------------
# Summary printer
# ---------------------------------------------------------------------------

_COL = 62


def _hr(char: str = "─") -> str:
    return char * _COL


def print_summary(cases: list[Case]) -> None:
    total_files = sum(len(paths) for _, _, paths in cases)

    print()
    print(_hr("═"))
    print("  Dedupe fixture ready")
    print(_hr("═"))
    print(f"  Root   : {FIXTURE_ROOT}")
    print(f"  Inbox  : {INBOX}")
    print(f"  Cases  : {len(cases)}")
    print(f"  Files  : {total_files}")
    print()

    for label, expectation, paths in cases:
        print(f"  {label}")
        print(f"  Expected → {expectation}")
        for p in paths:
            rel  = p.relative_to(INBOX)
            size = p.stat().st_size
            fmt_tag = p.suffix.lstrip(".")
            print(f"    [{fmt_tag:4s}] {rel!s:<50}  {size:>7} B")
        print()

    print(_hr("═"))
    print()
    print("  Suggested test commands")
    print(_hr())
    print()
    print("  # 1. Preview (dry-run, no files moved):")
    print(f"  python pipeline.py dedupe --path {INBOX}")
    print()
    print("  # 2. Apply quarantine to a scoped destination:")
    print(f"  python pipeline.py dedupe --path {INBOX} --apply \\")
    print(f"    --quarantine-dir {FIXTURE_ROOT}/quarantine_out")
    print()
    print("  # 3. Verify nested path preservation (Case 9):")
    print(f"  find {FIXTURE_ROOT}/quarantine_out -type f")
    print()
    print("  # 4. Remove fixture when done:")
    print(f"  python tests/create_dedupe_test_fixture.py --cleanup")
    print()
    print(_hr("═"))
    print()


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

def cleanup() -> None:
    if not FIXTURE_ROOT.exists():
        print(f"Nothing to remove — {FIXTURE_ROOT} does not exist.")
        return
    answer = input(f"Remove {FIXTURE_ROOT}? [y/N] ").strip().lower()
    if answer != "y":
        print("Aborted.")
        return
    shutil.rmtree(FIXTURE_ROOT)
    print(f"Removed {FIXTURE_ROOT}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create or remove the dedupe engine test fixture.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Generates valid short silent MP3/FLAC/M4A files via ffmpeg\n"
            "for 9 controlled dedupe test scenarios.\n"
            "No pipeline modules imported. No extra pip packages needed.\n"
        ),
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help=f"Remove {FIXTURE_ROOT} and exit.",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup()
        return

    global FFMPEG
    FFMPEG = _require_ffmpeg()

    if FIXTURE_ROOT.exists():
        print(f"Fixture already exists at {FIXTURE_ROOT}")
        print("Run with --cleanup first, then re-create.")
        sys.exit(1)

    print(f"ffmpeg  : {FFMPEG}")
    print(f"Inbox   : {INBOX}")
    print(f"Creating fixture ...", flush=True)
    print()

    cases = create_fixture()

    print()
    print_summary(cases)


if __name__ == "__main__":
    main()
