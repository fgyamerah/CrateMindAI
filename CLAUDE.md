# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Session Housekeeping (REQUIRED)

At the end of every session where you changed code, fixed a bug, added a feature, or completed a task, you MUST update these three files before finishing:

**`CHANGELOG.txt`** — Add an entry at the top of the log (under the header) using the format:
```
[YYYY-MM-DD] — Short title describing what changed
- What changed and why (not just the diff — the reason)
- Files affected
- Any migration notes (DB schema changes, config renames, etc.)
```

**`NEXT_TASKS.txt`** — Mark any completed tasks `[x]`, add any new tasks or follow-ups discovered during the session, update `[~]` for anything now in progress.

**`DJToolkit_CONTEXT.txt`** — Update any section where architecture, CLI behaviour, DB schema, config keys, or known issues changed. Keep it accurate as a reference for future sessions.

Do NOT update these files if the session was read-only (questions, explanations, no code changed).

## Project Overview

**TrackIQ** — a local-first, pipeline-based DJ library automation toolkit. Takes raw audio downloads (inbox folder) and produces a clean, fully-tagged, BPM/key-analysed music library with Rekordbox-compatible XML exports. Runs on Ubuntu Studio 24 (Linux) and outputs files ready for transfer to a Windows DJ drive for Rekordbox.

## Mixed In Key — Authoritative Source (HARD RULE)

**Mixed In Key (MIK) is the authoritative source of BPM, key, and cue data.**
This is a non-negotiable design constraint, not a suggestion.

Rules that must never be violated:
- **BPM**: Do NOT run analysis if BPM already exists in the DB or audio file tags
- **Key**: Do NOT run analysis if Camelot key already exists in DB or file tags (TKEY/INITIALKEY)
- **Cue points**: Cue suggest is DISABLED by default; never overwrite existing cues
- **Rekordbox XML**: XML export is DISABLED by default; use `--force-xml` only when MIK is not in use
- **M3U playlists**: Always safe to generate — they do not affect MIK/Rekordbox state

When writing code that touches BPM, key, or cue data:
1. Always check if the value already exists before computing/writing it
2. If it exists, preserve it — even if you think you have a better value
3. The analyzer's `_read_existing_analysis()` helper reads from file tags for this purpose

Any future XML writes must preserve existing cue data and MIK-added structures.

## Commands

```bash
# Run the full pipeline (MIK-first: analysis only fills missing values)
python3 pipeline.py

# Dry run (no writes)
python3 pipeline.py --dry-run

# Skip Beets metadata lookup (use Python fallback parser only)
python3 pipeline.py --skip-beets

# [legacy] Force-skip all analysis (normally not needed — pipeline is MIK-first)
python3 pipeline.py --skip-analysis

# Enable cue point suggestion (disabled by default — MIK owns cues)
python3 pipeline.py --force-cue-suggest

# Run against a custom library root (overrides all config paths)
python3 pipeline.py --path /mnt/music_ssd/KKDJ

# Re-analyze all library tracks missing BPM or key
python3 pipeline.py --reanalyze

# Run tests
python3 -m pytest tests/ -v
python3 -m pytest tests/test_sanitizer.py -v

# Run a single test class
python3 -m pytest tests/test_sanitizer.py::TestSanitizeText -v

# Run without pytest
python3 -m unittest tests.test_sanitizer -v

# Install dependencies
pip install -r requirements.txt
# For dev/test:
pip install -r requirements.txt pytest
```

### Subcommands

```bash
# Generate playlists only (without re-running the full pipeline)
python3 pipeline.py playlists

# Detect and quarantine duplicates in the existing library
python3 pipeline.py dedupe

# Suggest cue points — DISABLED by default (MIK owns cues); explicit subcommand still works
python3 pipeline.py cue-suggest [PATH ...]

# Build an energy-curve-aware set
python3 pipeline.py set-builder

# Harmonic mixing suggestions for next-track
python3 pipeline.py harmonic-suggest

# Artist folder cleanup (fix bad folder names)
python3 pipeline.py artist-folder-clean

# Merge artist spelling variants
python3 pipeline.py artist-merge

# Retroactive global junk removal across all tags in library
python3 pipeline.py metadata-clean

# Scrape Beatport/Traxsource label metadata
python3 pipeline.py label-intel

# Export M3U playlists for the SSD (XML is disabled by default — MIK owns XML)
python3 pipeline.py rekordbox-export

# Export with XML (NOT recommended when using MIK)
python3 pipeline.py rekordbox-export --force-xml

# Analyze library tracks that are missing BPM/key tags (MIK-first: only fills gaps)
python3 pipeline.py analyze-missing

# Convert .m4a files to .aiff (parallel, preserves metadata, archives originals)
python3 pipeline.py convert-audio \
    --src /downloads/m4a \
    --dst /mnt/music_ssd/KKDJ/inbox \
    --archive /mnt/music_ssd/originals_m4a

# Audit library for codec/bitrate quality (non-destructive by default)
python3 pipeline.py audit-quality
python3 pipeline.py audit-quality --path /mnt/music_ssd/KKDJ/
python3 pipeline.py audit-quality --dry-run --verbose
python3 pipeline.py audit-quality --move-low-quality /music/_low_quality
python3 pipeline.py audit-quality --write-tags
python3 pipeline.py audit-quality --report-format csv,json

# Roll back tag changes for a track
python3 scripts/rollback.py
```

## Architecture

### Entry Point

`pipeline.py` is the single entry point for all functionality. It parses subcommands/flags and delegates to modules. The pipeline runs 9 ordered steps: QC → dedupe → organize → sanitize → BPM+key analysis → tag write → cue suggest → playlist generation → report.

### Configuration

`config.py` defines all paths and tunables. **Never hardcode paths** — always reference `config.*`. Local overrides go in `config_local.py` (git-ignored), which is imported with `from config_local import *` at the end of `config.py`. All paths are also overridable via environment variables (e.g. `DJ_MUSIC_ROOT`, `RB_LINUX_ROOT`, `RB_WIN_DRIVE`).

The `--path` CLI flag calls `_override_music_root()` in `pipeline.py`, which rewrites all derived `config.*` paths at runtime — use this when working with the SSD mount instead of the default `/music` tree.

### Database

`db.py` is the SQLite persistence layer. Tables:
- `tracks` — one row per known file, carries status (`pending`, `ok`, `rejected`, `duplicate`, `needs_review`, `error`) plus BPM, key, bitrate
- `track_history` — immutable audit log: snapshots original + cleaned metadata as JSON before any tag write; used by `scripts/rollback.py`
- `pipeline_runs` — one row per pipeline invocation with counters

All writes go through `db.upsert_track()` and `db.mark_status()`. The idempotency guard is `db.is_processed()` — tracks with `status='ok'` and `TXXX:PROCESSED=1` in their ID3 tags are skipped.

### Modules

Each module in `modules/` exposes a `run()` function with signature `run(files, run_id, dry_run) → files`. Modules are stateless between calls and do not import each other.

| Module | Responsibility |
|---|---|
| `qc.py` | ffprobe validation — bitrate, duration, codec |
| `dedupe.py` | rmlint duplicate detection against existing library |
| `organizer.py` | Beets (MusicBrainz) file organization with Python fallback |
| `sanitizer.py` | Strip URL watermarks, promo phrases, DJ-pool junk from all tag fields |
| `analyzer.py` | BPM (aubio → librosa fallback) + musical key (keyfinder-cli → Camelot) |
| `tagger.py` | Write final tags: ID3v2.3 for MP3, FLAC, M4A via mutagen |
| `playlists.py` | Generate M3U and Rekordbox XML playlists |
| `parser.py` | Parse filenames/tags; detect label names, Camelot prefixes, junk |
| `sanitizer.py` | `sanitize_text()` + `sanitize_metadata()` are the core junk-removal functions |
| `cue_suggest.py` | Multi-feature audio analysis for cue point suggestions |
| `set_builder.py` | Energy-curve-aware automatic set generation |
| `harmonic.py` | Camelot-wheel + BPM + energy + genre scoring |
| `library_dedupe.py` | Post-pipeline exact/quality duplicate quarantine |
| `rekordbox_export.py` | Export library to Rekordbox XML, mapping Linux paths to Windows drive letter |
| `analyze_missing.py` | Find and re-analyze tracks missing BPM/key |
| `convert_audio.py` | Convert .m4a → .aiff with parallel ffmpeg, metadata preservation, archive |
| `audit_quality.py` | Codec/bitrate quality audit; classify files into LOSSLESS/HIGH/MEDIUM/LOW/UNKNOWN; CSV/JSON reports; optional move + tag write |
| `metadata_clean.py` | Retroactive tag sanitization across entire library |
| `artist_merge.py` | Consolidate artist spelling variants |
| `artist_folder_clean.py` | Remove bad artist folder names |

### Label Intelligence (`label_intel/`)

A separate sub-package (also mirrored in `djtoolkit_label_intelligence_feature/` — treat `label_intel/` as the active copy). Scrapes Beatport and Traxsource for label metadata, caches results, and exports to JSON/CSV/TXT/SQLite. The `labels.txt` output feeds back into `known_labels.txt` which `modules/parser.py` uses as a blocklist for label-name detection in artist/title fields.

### Windows/Rekordbox Path Mapping

All playlist and XML generation translates Linux paths to Windows-compatible paths using `config.WINDOWS_DRIVE_LETTER` (default `E`) and `config.RB_WINDOWS_DRIVE` (default `M`). The `rekordbox-export` subcommand maps `RB_LINUX_ROOT` → `RB_WINDOWS_DRIVE:\` for direct SSD use.

### Utils

`utils/llm_client.py` wraps Claude API calls (optional, requires `anthropic` package). `utils/prompt_logger.py` auto-logs all prompts to `./last-prompts/`.

## Key Conventions

- **Idempotent by design**: re-running is always safe. The `TXXX:PROCESSED=1` ID3 tag + `status='ok'` DB row is the idempotency gate.
- **Conservative tag writes**: junk removal is explicit pattern-matching in `modules/junk_patterns.py` and `modules/sanitizer.py`. Safe pass-through is the default — never overwrite with a lower-confidence guess.
- **Windows-safe output**: all generated folder names and XML paths must use only Windows-safe characters.
- **ID3v2.3 only**: Rekordbox compatibility requires ID3v2.3, never ID3v2.4. `config.ID3_VERSION = 3`.
- **External binaries**: ffprobe, rmlint, aubio/aubiobpm, keyfinder-cli, beet. All configurable via env vars or `config_local.py`.
