"""
tests/create_sanitize_fixtures.py

Creates minimal audio fixtures for metadata-sanitize torture tests.

Requirements:
  ffmpeg   — generates 1-second silent audio containers
  mutagen  — writes metadata (already a project dependency)

Output directory: tests/fixtures/metadata_sanitize/
Usage:  python3 tests/create_sanitize_fixtures.py
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "fixtures" / "metadata_sanitize"

# ---------------------------------------------------------------------------
# Fixture definitions
# Each entry: (filename, tags_dict)
# Special keys handled outside the easy-tag path:
#   _multi_value_artist  — list of strings; written as Vorbis list (FLAC only)
#   _id3_tsrc            — string; written as raw TSRC frame (AIFF)
# ---------------------------------------------------------------------------

FIXTURES = [
    # --- Album junk ---
    ("tc01_album_url.mp3", {
        "title": "Summer Feeling", "artist": "DJ Koze",
        "album": "https://djcity.com/download/summer-feeling",
        "organization": "Pampa Records", "isrc": "DEAM10000001",
    }),
    ("tc02_album_domain.flac", {
        "title": "Burning", "artist": "Enoo Napa",
        "album": "traxsource.com", "organization": "Afro Warriors",
    }),
    ("tc03_album_path.m4a", {
        "title": "Ritual", "artist": "Black Coffee",
        "album": "/home/user/downloads/2024/ritual", "organization": "Silo",
    }),
    ("tc04_album_dots.mp3", {
        "title": "Deep Inside", "artist": "Larry Heard",
        "album": "......DJPOOL........", "organization": "Alleviated",
    }),
    ("tc05_clean_album.flac", {
        "title": "Strings of Life", "artist": "Rhythim Is Rhythim",
        "album": "The Classic Collection", "organization": "Transmat",
        "isrc": "USAT20000001",
    }),

    # --- Title cleanup ---
    ("tc06_title_prefix.mp3", {
        "title": "03 | Pressure (Original Mix)", "artist": "Bicep",
        "album": "Isles", "organization": "Ninja Tune", "isrc": "GBARL2000001",
    }),
    ("tc07_title_no_prefix.mp3", {
        "title": "01 Luftballons", "artist": "Nena",
        "album": "99 Luftballons", "organization": "CBS",
    }),
    ("tc08_title_unclosed_paren.flac", {
        "title": "Body Music (Original Mix", "artist": "AlunaGeorge",
        "album": "Body Music", "organization": "Island",
    }),
    ("tc09_title_multi_transform.m4a", {
        "title": "Move - - It(Club Mix)", "artist": "Reel 2 Real",
        "album": "Move It", "organization": "Positiva",
    }),
    ("tc10_title_remix_safe.mp3", {
        "title": "Strings of Life (Derrick May Remix)", "artist": "Rhythim Is Rhythim",
        "organization": "Transmat",
    }),

    # --- Artist cleanup ---
    ("tc11_artist_url.mp3", {
        "title": "Overdrive", "artist": "Selena Gomez www.selenagomez.com",
        "album": "Stars Dance", "organization": "Interscope", "isrc": "USUM71300001",
    }),
    ("tc12_artist_ft_norm.flac", {
        "title": "Blinding Lights", "artist": "The Weeknd ft. Daft Punk",
        "album": "After Hours", "organization": "XO",
    }),
    ("tc13_artist_io_safe.mp3", {
        "title": "Late Night Drive", "artist": "Basement.io",
    }),
    # TC-14: multi-value artist — handled separately below
    ("tc14_artist_multival.flac", {
        "title": "1. Debris (Original Mix)",
        "organization": "Innervisions",
        "_multi_value_artist": ["Solomun", "Dixon"],
    }),

    # --- Label cleanup ---
    ("tc15a_label_unknown.mp3", {
        "title": "Promised Land", "artist": "Joe Smooth", "organization": "unknown",
    }),
    ("tc15b_label_na.mp3", {
        "title": "Promised Land", "artist": "Joe Smooth", "organization": "n/a",
    }),
    ("tc15c_label_none.mp3", {
        "title": "Promised Land", "artist": "Joe Smooth", "organization": "None",
    }),
    ("tc16_label_whitespace.flac", {
        "title": "Strings of Life", "artist": "Rhythim Is Rhythim",
        "organization": "Toolroom  Records",
    }),
    ("tc17_label_clean.mp3", {
        "title": "Mind Games", "artist": "Redlight", "organization": "Rekids",
    }),

    # --- ISRC validation ---
    ("tc18_isrc_valid_bare.mp3", {
        "title": "Opus", "artist": "Eric Prydz",
        "organization": "Pryda", "isrc": "GBAYE0000001",
    }),
    ("tc19_isrc_valid_dashes.flac", {
        "title": "Opus", "artist": "Eric Prydz",
        "organization": "Pryda", "isrc": "GB-AYE-00-00001",
    }),
    ("tc20a_isrc_bad_word.mp3", {
        "title": "Pyramid", "artist": "Visible Cloaks",
        "album": "Reassemblage", "organization": "RVNG Intl.", "isrc": "BADISRC",
    }),
    ("tc20b_isrc_digit_wrong_pos.mp3", {
        "title": "Pyramid", "artist": "Visible Cloaks",
        "album": "Reassemblage", "organization": "RVNG Intl.", "isrc": "GBAYE000000X",
    }),
    ("tc20c_isrc_too_short.mp3", {
        "title": "Pyramid", "artist": "Visible Cloaks",
        "album": "Reassemblage", "organization": "RVNG Intl.", "isrc": "GB-BAD-99",
    }),
    # TC-21: AIFF with ISRC in ID3 TSRC frame — handled separately below
    ("tc21_aiff_isrc_id3.aiff", {
        "title": "Movement", "artist": "Four Tet",
        "album": "There Is Love In You", "organization": "Domino",
        "_id3_tsrc": "GBDOM1000001X",  # invalid — letter at position 12
    }),

    # --- Safety edge cases ---
    ("tc22_artist_hyphen_safe.mp3", {
        "title": "22-Pistepirkko (Live at Tavastia)", "artist": "22-Pistepirkko",
        "album": "Big Friendly Family",
    }),
    ("tc23_fully_clean.flac", {
        "title": "Watergate (John Daly Remix)", "artist": "Patrice Bäumel",
        "album": "Watergate 26", "organization": "Watergate Records",
        "isrc": "DEUM71800001",
    }),
]

# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------

_FFMPEG_CMD = {
    ".mp3":  ["-c:a", "libmp3lame", "-q:a", "9"],
    ".flac": ["-c:a", "flac"],
    ".m4a":  ["-c:a", "aac", "-b:a", "32k"],
    ".aiff": ["-c:a", "pcm_s16be"],
}


def _make_audio(path: Path) -> None:
    """Generate a 1-second silent audio file via ffmpeg."""
    ext = path.suffix.lower()
    codec_args = _FFMPEG_CMD.get(ext)
    if codec_args is None:
        raise ValueError(f"Unsupported format: {ext}")
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
        "-t", "1",
        *codec_args,
        str(path),
    ]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# Tag writers
# ---------------------------------------------------------------------------

_EASY_ISRC_FORMATS = {".flac", ".m4a"}  # easy mode exposes isrc for these


def _write_easy_tags(path: Path, tags: dict) -> None:
    from mutagen import File as MFile
    audio = MFile(str(path), easy=True)
    if audio is None:
        raise RuntimeError(f"mutagen could not open {path}")
    _EASY_KEY_MAP = {
        "title": "title", "artist": "artist", "album": "album",
        "organization": "organization", "isrc": "isrc",
    }
    for key, easy_key in _EASY_KEY_MAP.items():
        val = tags.get(key)
        if val:
            try:
                audio[easy_key] = [val]
            except Exception:
                pass
    audio.save()


def _write_mp3_isrc(path: Path, value: str) -> None:
    """MP3: write ISRC as TSRC frame (not exposed via easy mode)."""
    from mutagen.id3 import ID3, TSRC
    id3 = ID3(str(path))
    id3["TSRC"] = TSRC(encoding=3, text=[value])
    id3.save(str(path))


def _write_flac_multi_artist(path: Path, artists: list) -> None:
    """FLAC: write artist as a genuine multi-value Vorbis comment list."""
    from mutagen.flac import FLAC
    audio = FLAC(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags["artist"] = artists
    audio.save()


def _write_aiff_id3_tsrc(path: Path, value: str) -> None:
    """AIFF: write ISRC as a TSRC frame inside the AIFF container via mutagen.aiff.AIFF."""
    from mutagen.aiff import AIFF
    from mutagen.id3 import TSRC
    audio = AIFF(str(path))
    if audio.tags is None:
        audio.add_tags()
    audio.tags.add(TSRC(encoding=3, text=[value]))
    audio.save()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    ok = 0
    errors = []

    for filename, tags in FIXTURES:
        path = OUTPUT_DIR / filename
        print(f"  creating {filename} ...", end=" ", flush=True)
        try:
            _make_audio(path)

            # Separate special keys from easy-tag keys
            easy_tags = {k: v for k, v in tags.items() if not k.startswith("_")}
            _write_easy_tags(path, easy_tags)

            # MP3: ISRC must go via raw TSRC frame
            if path.suffix.lower() == ".mp3" and "isrc" in easy_tags:
                _write_mp3_isrc(path, easy_tags["isrc"])

            # FLAC multi-value artist
            if "_multi_value_artist" in tags:
                _write_flac_multi_artist(path, tags["_multi_value_artist"])

            # AIFF TSRC via ID3
            if "_id3_tsrc" in tags:
                _write_aiff_id3_tsrc(path, tags["_id3_tsrc"])

            print("ok")
            ok += 1
        except Exception as exc:
            print(f"FAILED: {exc}")
            errors.append((filename, exc))

    print(f"\n{ok}/{len(FIXTURES)} fixtures created in {OUTPUT_DIR}")
    if errors:
        for name, err in errors:
            print(f"  ERROR: {name}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
