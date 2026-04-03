# TrackIQ

> Automated DJ library preparation — from raw downloads to a Rekordbox-ready collection.

TrackIQ is a local-first, pipeline-based toolkit that takes audio files from an inbox folder and produces a clean, fully-tagged, BPM/key-analysed music library with Rekordbox-compatible XML exports and a full set of energy, genre, key, and route playlists. It runs unattended on Linux (Ubuntu Studio 24), optionally on a timer or inbox-watch trigger, and outputs a library that transfers directly to a DJ drive for use on Windows.

---

## Table of Contents

1. [What TrackIQ Does](#what-trackiq-does)
2. [Design Philosophy](#design-philosophy)
3. [Feature Overview](#feature-overview)
4. [Repository Structure](#repository-structure)
5. [Installation](#installation)
6. [Configuration](#configuration)
7. [Usage](#usage)
   - [Main Pipeline](#main-pipeline)
   - [Standalone Playlist Generation](#standalone-playlist-generation)
   - [Duplicate Detection and Cleanup](#duplicate-detection-and-cleanup-dedupe)
   - [Cue Point Suggestion](#cue-point-suggestion-cue-suggest)
   - [Set Builder](#set-builder-set-builder)
   - [Harmonic Mixing Suggestions](#harmonic-mixing-suggestions-harmonic-suggest)
   - [Artist Folder Clean](#artist-folder-clean-subcommand)
   - [Artist Merge](#artist-merge-subcommand)
   - [Metadata Clean](#metadata-clean-subcommand)
   - [Label Intelligence](#label-intelligence-subcommand)
   - [Label Clean](#label-clean-subcommand)
   - [Library Enrichment](#library-enrichment-flag)
   - [Rollback](#rollback-tool)
   - [Transfer to DJ Drive](#transfer-to-dj-drive)
8. [Playlist Types](#playlist-types)
9. [Tag Cleaning — What Gets Removed](#tag-cleaning--what-gets-removed)
10. [Label Intelligence — Deep Dive](#label-intelligence--deep-dive)
11. [Data Outputs](#data-outputs)
12. [Automation](#automation)
13. [Safety and Limitations](#safety-and-limitations)
14. [Development Notes](#development-notes)
15. [Troubleshooting](#troubleshooting)

---

## What TrackIQ Does

DJs accumulate files from many sources — Beatport, Traxsource, Bandcamp, promo pools, and miscellaneous downloads. These files often arrive with inconsistent, incomplete, or outright junk metadata: URL watermarks in artist fields, catalog numbers where labels should be, missing BPM, wrong key, or no tags at all.

TrackIQ solves this by running each file through a deterministic, idempotent pipeline:

1. **Validates** the file (bitrate, duration, format) using ffprobe
2. **Deduplicates** against the existing library using rmlint
3. **Organises** the file into a clean folder structure using Beets (MusicBrainz) or a pure-Python fallback parser
4. **Sanitises tags globally** — strips URL watermarks, promo phrases, symbol junk, Camelot key prefixes, and DJ-pool watermarks from all text fields including the label (TPUB) field
5. **Detects BPM** using aubio with windowed median averaging
6. **Detects musical key** in Camelot notation using keyfinder-cli
7. **Writes final tags** in ID3v2.3 (MP3), FLAC, or M4A format
8. **Generates playlists** — per-letter, per-genre, energy-tier (Peak/Mid/Chill), combined genre+energy, Camelot key (1A–12B), and route-type (Acapella, Tool, Vocal) M3U playlists, plus a full Rekordbox XML with all playlist hierarchies
9. **Reports** on every run

Standalone post-pipeline tools provide:
- **Library dedupe** — detect and quarantine exact/quality duplicates without inbox re-processing
- **Cue point suggestion** — multi-feature audio analysis (RMS, LF energy, spectral flux) to suggest intro/mix-in/drop/breakdown/outro positions
- **Set building** — energy-curve-aware automatic set generation from your library
- **Harmonic mixing suggestions** — Camelot-wheel + BPM + energy + genre scoring for next-track recommendations
- **Metadata clean** — retroactive global junk removal across all tag fields
- **Artist merge / folder clean** — consolidate artist spelling variants and remove bad folder names

The result is a library that is ready to transfer to a DJ drive and import into Rekordbox without any manual cleanup.

---

## Design Philosophy

- **Local-first.** No mandatory cloud services. All analysis runs on-machine.
- **Idempotent.** Re-running the pipeline on already-processed tracks is safe and fast. Each track carries a `PROCESSED` flag so it is skipped after its first successful pass.
- **Conservative writes.** The pipeline never overwrites good existing metadata with a lower-confidence guess. Junk detection is explicit; safe pass-through is the default.
- **Audit trail.** Every modification is stored in a SQLite database. Original metadata is snapshotted before any tag write. All changes can be rolled back.
- **Composable modules.** Each pipeline stage (`qc`, `dedupe`, `organizer`, `sanitizer`, `analyzer`, `tagger`, `playlists`) is an independent Python module with no side effects between stages. They are individually testable and replaceable.
- **Windows-compatible output.** Folder names, file paths, and the Rekordbox XML all use Windows-safe characters and the configured drive letter for cross-platform portability.

---

## Feature Overview

### Core Pipeline

| Feature | Implementation | Status |
|---|---|---|
| Audio file validation (bitrate, duration, codec) | `modules/qc.py` + ffprobe | ✅ |
| Duplicate detection (inbox) | `modules/dedupe.py` + rmlint | ✅ |
| Smart file organisation | `modules/organizer.py` + Beets / Python parser | ✅ |
| Camelot key / artist / title prefix stripping | `modules/parser.py` | ✅ |
| Global junk removal from all tag fields (incl. label) | `modules/sanitizer.py` | ✅ |
| DJ-pool watermark removal (traxcrate, musicafresca, etc.) | `modules/sanitizer.py` | ✅ |
| Label-name detection in artist/album_artist fields | `modules/parser.py` → `classify_name_candidate()` | ✅ |
| BPM detection with windowed median | `modules/analyzer.py` + aubio | ✅ |
| Musical key detection (Camelot) | `modules/analyzer.py` + keyfinder-cli | ✅ |
| ID3v2.3 / FLAC / M4A tag writing | `modules/tagger.py` + mutagen | ✅ |
| Per-letter M3U playlists | `modules/playlists.py` | ✅ |
| Per-genre M3U playlists | `modules/playlists.py` | ✅ |
| Energy-tier M3U playlists (Peak / Mid / Chill) | `modules/playlists.py` | ✅ |
| Combined genre+energy M3U playlists | `modules/playlists.py` | ✅ |
| **Camelot key playlists (1A–12B)** | `modules/playlists.py` | ✅ |
| **Route playlists (Acapella / Tool / Vocal)** | `modules/playlists.py` | ✅ |
| Rekordbox XML with all playlist hierarchies | `modules/playlists.py` | ✅ |
| Run reports | `modules/reporter.py` | ✅ |
| SQLite audit trail + rollback | `db.py` + `scripts/rollback.py` | ✅ |

### Standalone Subcommands

| Subcommand | Purpose | Module |
|---|---|---|
| `playlists` | Regenerate all M3U playlists and Rekordbox XML without running inbox pipeline | `modules/playlists.py` |
| `dedupe` | Detect and quarantine exact/quality duplicates across the sorted library | `modules/library_dedupe.py` |
| `cue-suggest` | Suggest intro/mix-in/drop/breakdown/outro cue points from audio analysis | `modules/cue_suggest.py` |
| `set-builder` | Build an energy-curve DJ set from the library database | `modules/set_builder.py` |
| `harmonic-suggest` | Rank next-track suggestions by Camelot + BPM + energy + genre | `modules/harmonic.py` |
| `metadata-clean` | Retroactive junk removal from all tag fields across the library | `modules/metadata_clean.py` |
| `artist-folder-clean` | Fix bad artist folder names (Camelot prefixes, URL junk, symbols) | `modules/artist_folder_clean.py` |
| `artist-merge` | Merge artist folder spelling variants into a single canonical folder | `modules/artist_merge.py` |
| `label-clean` | Detect, normalize, and optionally write back label/TPUB tags | `label_intel/cleaner.py` |
| `label-intel` | Scrape label metadata from Beatport and Traxsource | `label_intel/scraper.py` |

### Label Intelligence

| Feature | Command | Status |
|---|---|---|
| Web scraping (Beatport + Traxsource) | `label-intel` | ✅ |
| JSON / CSV / TXT / SQLite export | `label-intel` | ✅ |
| Library enrichment (BPM/genre from local tracks) | `--label-enrich-from-library` | ✅ |
| Label tag detection + normalization + confidence scoring | `label-clean` | ✅ Phase 1 |
| Junk label rejection (Camelot keys, URLs, DJ-pool watermarks) | `label-clean` | ✅ |
| Filename-based label extraction | `label-clean` | ✅ |
| Alias merging across spelling variants | `label-clean` | ✅ |
| Conservative tag write-back | `label-clean --write-tags` | ✅ |
| Discogs provider | `--use-discogs` | 🔲 Phase 2 |
| Beatport single-label lookup | `--use-beatport` | 🔲 Phase 2 |

### Routing

Tracks are automatically routed into specialised library folders based on title keywords and metadata patterns:

| Route | Trigger examples | Destination |
|---|---|---|
| Acapella | `(Acapella)`, `Acap` | `library/acapella/` |
| Instrumental | `(Instrumental)`, `(Instr)` | `library/instrumental/` |
| DJ Tool | `DJ Tool`, `Drum Loop`, `FX` | `library/dj_tools/` |
| Edit | `(Edit)`, `(Re-Edit)` | `library/edits/` |
| Bootleg | `(Bootleg)`, `(Mashup)` | `library/bootlegs/` |
| Live | `(Live)`, `Live@` | `library/live/` |
| Unknown | Missing artist or title | `library/unknown/` |
| Normal | Everything else | `library/sorted/<Artist>/` |

---

## Repository Structure

```
trackiq/
│
├── pipeline.py               Main entry point — orchestrates all stages + all subcommands
├── pipeline.sh               Bash wrapper (locking, env, dependency checks)
├── config.py                 Central configuration and path definitions
├── config_local.py           User-local overrides (git-ignored, created by setup.sh)
├── db.py                     SQLite database layer — all pipeline state + cue/set tables
├── beets_config.yaml         Beets music organizer configuration template
├── setup.sh                  First-time installer (directories, packages, services)
├── known_labels.txt          Label blocklist for parser/label-clean
├── PROJECT_CONTEXT.txt       Detailed technical documentation
│
├── modules/                  Pipeline stage and subcommand modules
│   ├── parser.py             Filename/metadata parsing, prefix removal, validation
│   ├── sanitizer.py          Junk removal from all tag fields including label/TPUB
│   ├── organizer.py          File routing, folder construction, beets integration
│   ├── qc.py                 Quality control (ffprobe — bitrate, duration, codec)
│   ├── dedupe.py             Duplicate detection during pipeline (rmlint)
│   ├── library_dedupe.py     Standalone library-wide dedupe (Case A/B/C, quarantine)
│   ├── analyzer.py           BPM (aubio) and key (keyfinder-cli) analysis
│   ├── tagger.py             Final tag writing (mutagen, ID3v2.3/FLAC/M4A)
│   ├── playlists.py          M3U and Rekordbox XML generation (letter, genre,
│   │                         energy, combined, Camelot key 1A–12B, route types)
│   ├── reporter.py           Human-readable run summary reports
│   ├── textlog.py            Append-only plaintext audit log
│   ├── junk_patterns.py      Centralised junk-pattern loader (from config/junk_patterns.json)
│   ├── metadata_clean.py     Retroactive global junk removal from all tag fields
│   ├── artist_merge.py       Artist folder variant detection and merge
│   ├── artist_folder_clean.py Retroactive bad-name folder repair
│   ├── cue_suggest.py        Audio cue point suggestion (RMS + LF + spectral flux)
│   ├── harmonic.py           Harmonic mixing suggestions (Camelot + BPM + energy + genre)
│   └── set_builder.py        Energy-curve auto set builder
│
├── config/
│   └── junk_patterns.json    Centralised junk pattern definitions (URLs, promo, symbols)
│
├── label_intel/              Label Intelligence package
│   ├── models.py             LabelRecord dataclass
│   ├── store.py              LabelStore — in-memory, deduped by normalized name
│   ├── utils.py              normalize_label_name(), parse_energy(), soft_bpm_hint()
│   ├── scraper.py            scrape_labels() — seed → search → enrich orchestrator
│   ├── exporters.py          export_json/csv/txt/sqlite()
│   ├── enrich_from_library.py  enrich_store_from_tracks() — local library enrichment
│   ├── cleaner.py            Label detection, confidence scoring, junk rejection,
│   │                         write-back (Camelot keys, URLs, DJ-pool watermarks)
│   ├── normalizer.py         normalize_label(), AliasRegistry
│   ├── filename_parser.py    Conservative filename → label extraction
│   ├── reports.py            label-clean report generation
│   ├── cli.py                Standalone CLI entry point (label_intel.cli)
│   ├── sources/
│   │   ├── base.py           HttpClient (robots.txt, rate limiting, disk cache)
│   │   ├── beatport.py       BeatportSource scraper
│   │   └── traxsource.py     TraxsourceSource scraper
│   └── providers/            Phase 2 placeholders
│       ├── discogs.py        DiscogsProvider stub (not yet implemented)
│       └── beatport.py       BeatportCleanProvider stub (not yet implemented)
│
├── scripts/
│   ├── rollback.py           CLI to restore original tags or file paths
│   ├── transfer.sh           rsync library to external DJ drive
│   └── watch_inbox.sh        inotifywait inbox monitor (triggers pipeline)
│
├── systemd/
│   ├── djtoolkit.service     One-shot pipeline service (called by timer)
│   ├── djtoolkit.timer       Runs 5 min after boot, then every 30 min
│   └── djtoolkit-watch.service  Long-running inbox watcher
│
└── tests/
    ├── test_parser.py        Parser unit tests (prefix removal, validation, classify)
    └── test_sanitizer.py     Sanitizer unit tests (URL removal, promo phrases)
```

---

## Installation

### Requirements

**Operating system:** Linux (developed on Ubuntu Studio 24). The pipeline and watcher scripts are Linux-specific. Config generation and Rekordbox XML output are Windows-path-aware.

**Python:** 3.10 or later.

### Step 1 — Clone the repository

```bash
git clone <your-repo-url> trackiq
cd trackiq
```

### Step 2 — Run the installer

`setup.sh` creates the music directory tree, installs system packages, configures Beets, and optionally sets up systemd services.

```bash
# Default: music root at /music, no virtualenv
./setup.sh

# Custom music root + isolated virtualenv
./setup.sh --music-root /mnt/ssd/music --venv

# Skip systemd installation (manual runs only)
./setup.sh --no-systemd
```

The installer installs these system packages via `apt`:

| Package | Provides |
|---|---|
| `ffmpeg` | `ffprobe` + `ffmpeg` — audio validation, metadata extraction, and audio decode for cue analysis |
| `aubio-tools` | `aubiobpm` — BPM detection |
| `rmlint` | `rmlint` — duplicate file detection |
| `inotify-tools` | `inotifywait` — inbox file watcher |
| `kid3` | `kid3-cli` — tag inspection utility |
| `beets` | `beet` — MusicBrainz-powered organizer (optional) |

And these Python packages via `pip`:

| Package | Used for |
|---|---|
| `mutagen` | Tag reading and writing |
| `numpy` | Audio feature extraction for cue-suggest (RMS, spectral flux, LF energy) |
| `beets` | Organizer integration (optional) |
| `requests` | Label Intelligence HTTP scraping |
| `beautifulsoup4` | Label Intelligence HTML parsing |

**Optional:** `librosa` — improves beat tracking for cue-suggest when installed (`pip install librosa`). Not required; the tool falls back to ffmpeg+numpy automatically.

**keyfinder-cli** is not in apt. The installer will prompt for manual installation or an AppImage path.

### Step 3 — Activate virtualenv (if used)

```bash
source .venv/bin/activate
```

### Step 4 — Verify setup

```bash
python3 pipeline.py --dry-run
```

This runs a full simulation pass without moving or modifying any files. Check the output for any missing binary warnings.

---

## Configuration

### `config.py`

All paths, thresholds, and binary names are defined in `config.py`. Override any value by creating `config_local.py` in the project root — it is loaded at the end of `config.py` and is git-ignored.

```python
# config_local.py example
MUSIC_ROOT = Path("/mnt/ssd/music")
WINDOWS_DRIVE_LETTER = "D"
LABEL_CLEAN_THRESHOLD = 0.75
GENERATE_KEY_PLAYLISTS = False    # disable Camelot key playlists if not needed
GENERATE_ROUTE_PLAYLISTS = False  # disable route playlists if not needed
```

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DJ_MUSIC_ROOT` | `/music` | Root of the music library tree |
| `DJ_WIN_DRIVE` | `E` | Drive letter for Windows Rekordbox XML paths |
| `DJ_PYTHON` | `python3` | Python binary (`pipeline.sh`) |
| `DJ_VENV` | _(unset)_ | Path to virtualenv (`pipeline.sh` activates it) |
| `RMLINT_BIN` | `rmlint` | rmlint binary |
| `AUBIO_BIN` | _(auto)_ | aubio binary — probes `aubio` then `aubiotrack` |
| `AUBIOBPM_BIN` | `aubiobpm` | Legacy aubio BPM binary |
| `KEYFINDER_BIN` | `keyfinder-cli` | Key detection binary |
| `FFPROBE_BIN` | `ffprobe` | ffprobe binary (ffmpeg derived from same path) |
| `BEET_BIN` | `beet` | Beets CLI binary |

### Directory Layout

```
$DJ_MUSIC_ROOT/                     (default: /music)
├── inbox/                          Drop new tracks here
├── processing/                     Temporary staging (pipeline use only)
│
├── library/
│   ├── sorted/                     Clean, organized library
│   │   ├── _unsorted/              Tracks Beets could not identify
│   │   ├── _compilations/          Multi-artist albums
│   │   └── _duplicates/            Quarantined duplicates (library dedupe)
│   ├── acapella/
│   ├── instrumental/
│   ├── dj_tools/
│   ├── edits/
│   ├── bootlegs/
│   ├── live/
│   └── unknown/                    Tracks with insufficient metadata
│
├── duplicates/                     Quarantined duplicate files (pipeline dedupe)
├── rejected/                       Failed QC (corrupt, too short, etc.)
│
├── playlists/
│   ├── m3u/
│   │   ├── A.m3u8 … Z.m3u8        Per-letter playlists
│   │   ├── _all_tracks.m3u8       Master playlist (all tracks)
│   │   ├── Genre/                 Per-genre playlists
│   │   │   ├── Afro House.m3u8
│   │   │   └── ...
│   │   ├── Energy/                Energy-tier playlists
│   │   │   ├── Peak.m3u8
│   │   │   ├── Mid.m3u8
│   │   │   └── Chill.m3u8
│   │   ├── Combined/              Genre+energy combined playlists
│   │   │   ├── Peak Afro House.m3u8
│   │   │   └── ...
│   │   ├── Key/                   Camelot key playlists
│   │   │   ├── 1A.m3u8 … 12B.m3u8
│   │   │   └── ...
│   │   └── Route/                 Route-type playlists
│   │       ├── Acapella.m3u8
│   │       ├── Tool.m3u8
│   │       └── Vocal.m3u8
│   └── xml/
│       └── rekordbox_library.xml  Full Rekordbox import (all playlist hierarchies)
│
├── data/
│   └── labels/
│       ├── seeds.txt               Label names for web scraping
│       ├── output/                 label-intel exports (JSON/CSV/TXT/SQLite)
│       └── clean/                  label-clean reports
│
├── .cache/
│   └── label_intel/                HTTP cache for scraper
│
└── logs/
    ├── pipeline.log                Structured pipeline log (appended per run)
    ├── processing_log.txt          Human-readable audit log (appended per run)
    ├── beets_import.log            Beets import log
    ├── processed.db                SQLite database (all pipeline state)
    ├── README.md                   Auto-generated run summary (overwritten)
    ├── reports/                    Per-run pipeline reports
    ├── cue_suggest/                Cue point suggestion outputs
    │   ├── cue_suggestions.json    Master JSON (all tracks, latest DB state)
    │   ├── cue_suggestions.csv     Master wide-format CSV (one row per track)
    │   └── runs/                   Per-run detail logs (one row per cue point)
    ├── set_builder/                Set builder outputs (M3U + CSV per generated set)
    ├── harmonic_suggest/           Harmonic suggestion JSON exports
    ├── metadata_clean/             metadata-clean reports
    ├── artist_merge/               artist-merge reports
    └── artist_folder_clean/        artist-folder-clean reports
```

### Quality Thresholds

```python
MIN_BITRATE_KBPS = 128      # files below this are rejected
MIN_DURATION_SEC = 30       # files shorter than this are rejected
MAX_DURATION_SEC = 7200     # files longer than 2 hours are rejected
BPM_MIN = 60                # BPM outside this range is discarded
BPM_MAX = 200
```

### Playlist Generation Toggles

```python
GENERATE_GENRE_PLAYLISTS    = True   # per-genre playlists
GENERATE_ENERGY_PLAYLISTS   = True   # Peak / Mid / Chill playlists
GENERATE_COMBINED_PLAYLISTS = True   # Genre+Energy combined playlists
GENERATE_KEY_PLAYLISTS      = True   # Camelot key playlists (1A–12B)
GENERATE_ROUTE_PLAYLISTS    = True   # route playlists (Acapella, Tool, Vocal)
PLAYLIST_MIN_TRACKS         = 2      # minimum tracks to write a playlist
```

Set any to `False` in `config_local.py` to skip those playlist types.

### Cue Suggest Configuration

```python
CUE_SUGGEST_OUTPUT_DIR      = LOGS_DIR / "cue_suggest"
CUE_SUGGEST_WRITE_SIDECARS  = False   # write .cues.json next to audio files
CUE_SUGGEST_MIN_CONFIDENCE  = 0.4    # ignore cues below this when writing to DB
```

### Set Builder Configuration

```python
SET_BUILDER_OUTPUT_DIR = LOGS_DIR / "set_builder"
```

### Harmonic Suggest Configuration

```python
HARMONIC_SUGGEST_OUTPUT_DIR = LOGS_DIR / "harmonic_suggest"
```

### Dedupe Configuration

```python
DEDUPE_QUARANTINE_DIR = SORTED / "_duplicates"
```

### Label Intelligence Paths

```python
LABEL_INTEL_SEEDS   = MUSIC_ROOT / "data/labels/seeds.txt"
LABEL_INTEL_OUTPUT  = MUSIC_ROOT / "data/labels/output"
LABEL_INTEL_CACHE   = MUSIC_ROOT / ".cache/label_intel"
LABEL_INTEL_SOURCES = ["beatport", "traxsource"]
LABEL_INTEL_DELAY   = 2.0
LABEL_CLEAN_OUTPUT    = MUSIC_ROOT / "data/labels/clean"
LABEL_CLEAN_THRESHOLD = 0.85
```

---

## Usage

### Main Pipeline

```bash
# Full pipeline run (drop files in /music/inbox/ first)
python3 pipeline.py

# Or via the shell wrapper (handles locking, env, logging)
./pipeline.sh

# Dry run — simulate everything, no file changes
python3 pipeline.py --dry-run

# Skip Beets — use pure-Python organizer only
python3 pipeline.py --skip-beets

# Skip BPM and key analysis (useful for re-tagging only)
python3 pipeline.py --skip-analysis

# Re-run BPM+key on all library tracks missing those values
python3 pipeline.py --reanalyze

# Enable verbose/debug logging
python3 pipeline.py --verbose
```

### Pipeline Steps (in order)

```
[1/8]  Quality control          ffprobe: bitrate, duration, codec
[2/8]  Duplicate detection      rmlint: byte-identical / near-duplicate
[3/8]  Organize                 Beets (MusicBrainz) → Python parser fallback
[4/8]  Sanitize tags            Strip URL watermarks, promo phrases, symbols,
                                Camelot key prefixes, DJ-pool watermarks
[5/8]  BPM + key analysis       aubio → Camelot key via keyfinder-cli
[6/8]  Write tags               mutagen: ID3v2.3 / FLAC / M4A
[7/8]  Playlist generation      Letter + Genre + Energy + Combined + Key + Route M3U
                                Rekordbox XML (all six playlist hierarchies)
[8/8]  Report                   Text report + auto-update README in logs/
```

All steps are idempotent. Tracks with `status='ok'` in the database are skipped.

---

### Standalone Playlist Generation

Regenerate all playlists and Rekordbox XML from the current library database without running the full inbox pipeline. Useful after manual library edits, after running `dedupe`, or any time you want a fresh export.

```bash
# Preview all outputs (no files written)
python3 pipeline.py playlists --dry-run

# Write all M3U playlists and Rekordbox XML
python3 pipeline.py playlists

# M3U only — skip XML
python3 pipeline.py playlists --no-xml

# Skip specific playlist types
python3 pipeline.py playlists --no-key --no-route
python3 pipeline.py playlists --no-genre --no-energy --no-combined

# Use a custom library root
python3 pipeline.py playlists --path /mnt/music_ssd/KKDJ/
```

**Available flags:**

| Flag | Effect |
|---|---|
| `--dry-run` | Show what would be written, create no files |
| `--no-genre` | Skip Genre/ playlists |
| `--no-energy` | Skip Energy/ playlists |
| `--no-combined` | Skip Combined/ playlists |
| `--no-key` | Skip Key/ (Camelot) playlists |
| `--no-route` | Skip Route/ playlists |
| `--no-xml` | Skip Rekordbox XML export |
| `--path DIR` | Override music root directory |

---

### Duplicate Detection and Cleanup (`dedupe`)

Scan the sorted library for duplicate audio files and move them to a quarantine folder. Files are **moved, never deleted** — always recoverable.

```bash
# Preview duplicate groups, no files moved
python3 pipeline.py dedupe --dry-run

# Quarantine detected duplicates
python3 pipeline.py dedupe

# Scan a custom directory
python3 pipeline.py dedupe --path /mnt/music_ssd/KKDJ/

# Quarantine to a custom directory
python3 pipeline.py dedupe --quarantine-dir /music/review/
```

**Detection cases:**

| Case | Condition | Action |
|---|---|---|
| **Case A — Exact duplicate** | Same SHA-256 hash | Keep one, quarantine the rest |
| **Case B — Quality duplicate** | Same track, different format/bitrate | Keep best quality, quarantine rest |
| **Case C — Different versions** | "Extended Mix" vs "Radio Edit" etc. | Report only — never auto-removed |

**Quality priority (highest first):** WAV / AIFF > FLAC > MP3 320 > MP3 256 > M4A > MP3 192 > OGG/OPUS > MP3 128 > MP3 <128

**Safety rules:**
- Duration mismatch guard: if two tracks with similar titles differ by more than 5 seconds in duration, Case B is skipped (different songs, not quality variants)
- Ambiguous quality ties are skipped (manual review)
- Case C is never auto-removed

---

### Cue Point Suggestion (`cue-suggest`)

Analyse audio to detect natural cue point positions. Outputs suggested positions for review — **not native Rekordbox hot-cues**. All positions should be reviewed and confirmed in Rekordbox before use in a live set.

```bash
# Analyse full library (dry run — no DB writes)
python3 pipeline.py cue-suggest --dry-run

# Analyse full library and store results in DB + master output files
python3 pipeline.py cue-suggest

# Analyse first 20 tracks only (useful for testing)
python3 pipeline.py cue-suggest --limit 20

# Analyse only tracks matching an artist or title
python3 pipeline.py cue-suggest --track "Black Coffee"
python3 pipeline.py cue-suggest --track "Enoo Napa"

# Write only JSON output (skip CSV)
python3 pipeline.py cue-suggest --export-format json

# Analyse a custom directory
python3 pipeline.py cue-suggest --path /music/sorted/Afro\ House/

# Lower minimum confidence stored to DB (include more uncertain cues)
python3 pipeline.py cue-suggest --min-confidence 0.30
```

**Cue types detected:**

| Cue | Description | Confidence (typical) |
|---|---|---|
| `intro_start` | Bar 1 — always present | 1.00 |
| `mix_in` | First stable beat-phrase for DJ entry | 0.55–0.85 |
| `groove_start` | First sustained full-arrangement section | 0.60–0.85 |
| `drop` | Main energy arrival / impact | 0.65–0.90 |
| `breakdown` | Significant energy/density reduction after peak | 0.60–0.82 |
| `outro_start` | Start of mix-out section | 0.68–0.82 |

**Signal features used:**

| Feature | Method | Purpose |
|---|---|---|
| RMS energy | `sqrt(mean(frame²))` | Overall loudness / section energy |
| Low-frequency energy | FFT bins 1–250 Hz | Bass/kick presence proxy |
| Spectral flux | Sum of positive spectral differences | Onset strength / novelty |

All features are smoothed with a 4-second moving average and aggregated to bar resolution (bar grid derived from BPM). When audio decode fails, a BPM-only structural heuristic is used as a fallback (all cues marked `source=bpm_estimate`, confidence ≤ 0.50).

**Confidence scoring:** Each cue starts from a type-specific base value and earns bonuses for feature agreement (e.g. LF energy also crosses threshold), distinctiveness (deep valley vs shallow), and position within the track. Cues with confidence < 0.50 include a "LOW CONFIDENCE — verify in Rekordbox" note in the output.

**Output files:**

| File | Description |
|---|---|
| `logs/cue_suggest/cue_suggestions.json` | Master JSON — all tracks in DB, rebuilt each run |
| `logs/cue_suggest/cue_suggestions.csv` | Wide CSV — one row per track, columns per cue type |
| `logs/cue_suggest/runs/cues_TIMESTAMP.csv` | Per-run detail log — one row per cue point |
| `<audio>.cues.json` | Sidecar per track (opt-in via `CUE_SUGGEST_WRITE_SIDECARS = True`) |

---

### Set Builder (`set-builder`)

Automatically build an energy-curve DJ set from the library database, arranging tracks across phases with harmonic transition scoring.

```bash
# Preview set (no files written)
python3 pipeline.py set-builder --dry-run

# Build a 60-minute peak-energy set (default)
python3 pipeline.py set-builder

# Build a 90-minute deep/organic set
python3 pipeline.py set-builder --vibe deep --duration 90

# Build a warm intro set filtered to one genre
python3 pipeline.py set-builder --vibe warm --genre "afro house"

# Build with a specific harmonic transition strategy
python3 pipeline.py set-builder --strategy energy_lift --duration 75

# Name the output files
python3 pipeline.py set-builder --name friday_night_peak --vibe peak --duration 120
```

**Phase structure:**

| Phase | Energy | BPM range | Purpose |
|---|---|---|---|
| `warmup` | Chill / Mid | 100–125 | Gentle intro |
| `build` | Mid / Peak | 118–130 | Rising energy |
| `peak` | Peak | 124–150 | Main high-energy section |
| `release` | Mid / Chill | 110–128 | Brief drop after peak |
| `outro` | Chill / Mid | 95–125 | Wind-down / closing |

**Vibe presets** control how much time each phase gets:

| Vibe | warmup | build | peak | release | outro |
|---|---|---|---|---|---|
| `warm` | 30% | 30% | 15% | 15% | 10% |
| `peak` | 12% | 20% | 40% | 18% | 10% |
| `deep` | 25% | 30% | 15% | 20% | 10% |
| `driving` | 15% | 25% | 35% | 15% | 10% |

**Transition strategies:** `safest` (default) · `energy_lift` · `smooth_blend` · `best_warmup` · `best_late_set`

**Output files** (per generated set):

| File | Description |
|---|---|
| `logs/set_builder/<name>.m3u8` | Playable M3U playlist with phase annotations |
| `logs/set_builder/<name>.csv` | Full metadata + transition notes per track |
| DB: `set_playlists` + `set_playlist_tracks` | Persistent record queryable by other tools |

---

### Harmonic Mixing Suggestions (`harmonic-suggest`)

Score every track in the library against a reference track (or a key+BPM pair) and print the best transition candidates.

```bash
# Suggest from a specific track in your library
python3 pipeline.py harmonic-suggest --track "/music/library/sorted/Black Coffee/track.mp3"

# Suggest from a key + BPM (useful mid-set without a specific track path)
python3 pipeline.py harmonic-suggest --key 8A --bpm 124

# Use a different ranking strategy
python3 pipeline.py harmonic-suggest --track "..." --strategy energy_lift

# Get more suggestions
python3 pipeline.py harmonic-suggest --key 5B --bpm 128 --top-n 20

# Export results to JSON
python3 pipeline.py harmonic-suggest --key 8A --bpm 126 --json

# Include current track's energy/genre for better scoring
python3 pipeline.py harmonic-suggest --key 10A --bpm 122 --energy Mid --genre "afro house"
```

**Scoring factors:**

| Factor | Weight | Method |
|---|---|---|
| Camelot compatibility | 35% | Wheel distance (same/±1/mode-switch/±2/far) |
| BPM compatibility | 30% | Halftime/doubletime-aware delta % |
| Energy compatibility | 20% | Peak / Mid / Chill tier match |
| Genre compatibility | 15% | Exact / related / different |

**Camelot scoring:**

| Relationship | Score |
|---|---|
| Same key | 1.00 |
| ±1 position (same mode) | 0.90 |
| Mode switch (A↔B, same root) | 0.85 |
| ±1 + mode switch (diagonal) | 0.80 |
| ±2 same mode | 0.55 |
| ±3 or more | 0.15 |

**Ranking strategies:** `safest` (default) · `energy_lift` · `smooth_blend` · `best_warmup` · `best_late_set`

**Output:** Terminal table with score, BPM delta, key, energy, and a plain-English explanation per suggestion. Optional JSON file in `logs/harmonic_suggest/` with `--json`.

---

### Artist Folder Clean Subcommand

Retroactively fix artist folder names that were created before parsing fixes were in place.

```bash
# Scan and report only (no file moves)
python3 pipeline.py artist-folder-clean --dry-run

# Apply recoverable renames and merges
python3 pipeline.py artist-folder-clean --apply

# Scan a custom directory
python3 pipeline.py artist-folder-clean --path /mnt/music_ssd/KKDJ/ --dry-run
```

**Detection rules:**

| Rule | Example | Outcome |
|---|---|---|
| `camelot_prefix` | `1A - Afrikan Roots/` | Renamed to `Afrikan Roots/` |
| `pure_camelot` | `10B/` | Review only |
| `bracket_junk` | `[HouseGrooveSA]/` | Review only |
| `url_junk` | `djcity.com/` | Review only |
| `symbol_heavy` | < 40% alphanumeric characters | Review only |

Reports written to `logs/artist_folder_clean/`.

---

### Artist Merge Subcommand

Scan the sorted library for artist folders that represent the same base artist (capitalisation / feat / collaborator suffix differences) and merge them into a single canonical folder.

```bash
# Scan and report only (no file moves)
python3 pipeline.py artist-merge --dry-run

# Apply safe merges; uncertain cases go to report only
python3 pipeline.py artist-merge --apply

# Scan a custom directory
python3 pipeline.py artist-merge --path /mnt/music_ssd/KKDJ/
```

Reports written to `logs/artist_merge/`.

---

### Metadata Clean Subcommand

Remove URL/promo junk from all metadata fields retroactively across the entire library. Useful after adding new junk patterns or after importing tracks that bypassed sanitization.

```bash
# Preview all changes (no file writes)
python3 pipeline.py metadata-clean --dry-run

# Apply changes
python3 pipeline.py metadata-clean

# Scan a custom directory
python3 pipeline.py metadata-clean --path /mnt/music_ssd/
```

**Fields cleaned:** `title`, `artist`, `album`, `albumartist`, `genre`, `comment`, `organization` (label/TPUB), `grouping`, catalog number.

Reports written to `logs/metadata_clean/`.

---

### Label Intelligence Subcommand

Scrape label metadata from Beatport and Traxsource for a list of seed labels.

```bash
# Scrape with default seeds file
python3 pipeline.py label-intel

# Custom seeds file
python3 pipeline.py label-intel --label-seeds ~/my_labels.txt

# Single source only
python3 pipeline.py label-intel --label-sources traxsource

# Fast mode — skip enriching individual label pages
python3 pipeline.py label-intel --label-skip-enrich

# Custom output and cache directories
python3 pipeline.py label-intel \
    --label-output /tmp/labels/out \
    --label-cache  /tmp/labels/cache

# Slower rate limiting
python3 pipeline.py label-intel --label-delay 5.0
```

**Seeds file format** — one label name per line:

```text
MoBlack Records
Defected Records
Drumcode
```

**Outputs written to `$DJ_MUSIC_ROOT/data/labels/output/`:**

| File | Contents |
|---|---|
| `labels.json` | Full metadata per label (all fields) |
| `labels.csv` | Spreadsheet-friendly flat export |
| `labels.txt` | One label name per line (usable as `known_labels.txt`) |
| `labels.db` | SQLite database for ad-hoc queries |

---

### Label Clean Subcommand

Scan your processed library for label metadata — detect, normalize, and optionally write back the `organization/TPUB` tag.

```bash
# Scan and report only (default / safe mode)
python3 pipeline.py label-clean

# Write high-confidence labels (≥ 0.85) back to TPUB tag
python3 pipeline.py label-clean --write-tags

# Export only unresolved tracks for manual review
python3 pipeline.py label-clean --review-only

# Lower threshold to include grouping-tag fallbacks
python3 pipeline.py label-clean --write-tags --confidence-threshold 0.75

# Scan a custom directory
python3 pipeline.py label-clean --path /mnt/music_ssd/
```

**Detection order and confidence:**

| Source | Confidence | Written at default threshold? |
|---|---|---|
| Embedded `organization/TPUB` tag (valid) | **0.95** | ✅ Yes |
| `grouping` tag fallback | 0.75 | No |
| `comment` tag fallback | 0.60 | No |
| Filename: `[Label] Artist - Title` | 0.70 | No |
| Filename: `Artist - Title (Label Records)` | 0.65 | No |
| Unresolved | 0.00 | No |

**Outputs written to `$DJ_MUSIC_ROOT/data/labels/clean/`:**

| File | Contents |
|---|---|
| `label_clean_report.json` | Full per-track results |
| `label_clean_report.csv` | Spreadsheet-friendly version |
| `label_clean_review.json` | Only unresolved / low-confidence tracks |
| `label_clean_summary.txt` | Human-readable stats + top labels |

---

### Library Enrichment Flag

Enrich the label database using BPM and genre data from your local library without re-analyzing files.

```bash
python3 pipeline.py --label-enrich-from-library
python3 pipeline.py --label-enrich-from-library --verbose
```

---

### Rollback Tool

Restore original metadata tags (and optionally original file paths) for any previously processed track.

```bash
python3 scripts/rollback.py list
python3 scripts/rollback.py info 42
python3 scripts/rollback.py rollback 42 --dry-run
python3 scripts/rollback.py rollback 42
python3 scripts/rollback.py rollback 42 --restore-path
```

Rollback **never deletes files**. All rollback actions are logged to `processing_log.txt`.

---

### Transfer to DJ Drive

```bash
./scripts/transfer.sh /mnt/djdrive
./scripts/transfer.sh /mnt/djdrive --dry-run
```

Transfers `library/sorted/` and `playlists/` using `rsync --checksum`. Subsequent runs only transfer new or changed files.

**After transfer — Rekordbox import on Windows:**

1. Open Rekordbox
2. **File → Import Library** → select `<drive>:\music\playlists\xml\rekordbox_library.xml`
3. Select all new tracks → right-click → **Analyze**
4. Set cue points as needed (use `cue-suggest` output as a reference)
5. **File → Export to USB**

---

## Playlist Types

TrackIQ generates six complementary playlist types from the same library database.

### Letter playlists

One playlist per first-letter folder (`A.m3u8` through `Z.m3u8`) plus `_all_tracks.m3u8`.

### Genre playlists (`Genre/`)

One playlist per normalized primary genre. Genre strings are normalized so `"Afro-House"`, `"afro house"`, and `"AFRO HOUSE"` all map to `"Afro House"`.

### Energy playlists (`Energy/`)

| Playlist | Typical BPM | Genre signal |
|---|---|---|
| `Peak.m3u8` | ≥ 126 BPM | Afro Tech, Techno, Hard Techno always Peak |
| `Mid.m3u8` | 118–125 BPM | Afro House, Amapiano at moderate BPM |
| `Chill.m3u8` | < 118 BPM | Deep House, Organic House, Melodic House always Chill |

Genre classification takes priority over BPM. Disable with `GENERATE_ENERGY_PLAYLISTS = False`.

### Combined playlists (`Combined/`)

Genre+energy intersection playlists (e.g. `Peak Afro House.m3u8`, `Chill Deep House.m3u8`). Only playlists with at least `PLAYLIST_MIN_TRACKS` tracks are written. Disable with `GENERATE_COMBINED_PLAYLISTS = False`.

### Key playlists (`Key/`)

One playlist per Camelot key position: `1A.m3u8`, `1B.m3u8`, `2A.m3u8` … `12A.m3u8`, `12B.m3u8`. Sorted in Camelot wheel order (1A, 1B, 2A, 2B, …). Only tracks with a detected Camelot key are included. Disable with `GENERATE_KEY_PLAYLISTS = False`.

### Route playlists (`Route/`)

| Playlist | Detection method |
|---|---|
| `Acapella.m3u8` | Filepath in `library/acapella/` OR title keyword: acapella, acap, vocal only |
| `Tool.m3u8` | Filepath in `library/dj_tools/` OR title keyword: dj tool, drum loop, fx, loop, intro tool |
| `Vocal.m3u8` | Title keyword: vocal mix, vox mix, vocal version (not acapella) |

Disable with `GENERATE_ROUTE_PLAYLISTS = False`.

### Rekordbox XML hierarchy

```
ROOT
├── All Tracks
├── A … Z              (letter nodes)
├── Genre/
│   ├── Afro House
│   ├── Amapiano
│   └── …
├── Energy/
│   ├── Peak
│   ├── Mid
│   └── Chill
├── Combined/
│   ├── Peak Afro House
│   └── …
├── Key/
│   ├── 1A
│   ├── 1B
│   └── … 12B
└── Route/
    ├── Acapella
    ├── Tool
    └── Vocal
```

Each track's `Label` attribute is populated from the cleaned `organization/TPUB` tag. URL or domain watermarks are suppressed from the XML even if the tag was not fully cleared on disk.

---

## Tag Cleaning — What Gets Removed

The sanitizer (`modules/sanitizer.py`) runs in step 4 of every pipeline pass and in the `metadata-clean` standalone command. It processes: `title`, `artist`, `album`, `genre`, `comment`, and `organization` (label/TPUB).

### Removed from all fields

| Pattern | Examples |
|---|---|
| Full URLs | `https://fordjonly.com/track` |
| `www.` URLs | `www.djcity.com` |
| Plain domain names (known TLDs) | `fordjonly.com`, `beatsource.net` |
| Trademark and currency symbols | `™ ® © ℗ $ € £` |
| "for DJ only" / "for DJs only" | standard promo watermark |
| "promo only" | promo distribution marker |
| "djcity" / "dj city" | DJCity.com source tag |
| "zipdj" | ZipDJ.com source tag |
| "traxcrate" | TraxCrate.com source tag |
| "musicafresca" | MusicaFresca.com source tag |
| "downloaded from …" | generic download tool tag |
| "official audio / video" | YouTube auto-tag |
| "free download" | promotional label |
| "buy on beatport/traxsource" | sales call-to-action |
| Camelot/key prefix at field start | `8A - My Song` → `My Song` |

### Preserved

- Version info: `Original Mix`, `Extended Mix`, `Dub Mix`, `VIP`
- Remix credits: `(Boddhi Satva Remix)`, `(Kerri Chandler Edit)`
- Exclusive version names: `Exclusive Mix`, `Exclusive Dub`

### Label field — additional behavior

If the entire label field is a URL or watermark (e.g. `"TraxCrate.com"`), the tag is **deleted** from the file — not left as an empty string.

---

## Label Intelligence — Deep Dive

### Architecture

The label intelligence system is built around a **name-first identity model**. A label's canonical identity is its `normalized_name` (lowercased, punctuation-stripped, noise-suffix-removed). Beatport and Traxsource IDs are optional enrichment fields — never the primary key.

`"Defected"`, `"Defected Records"`, and `"Defected Recordings"` all resolve to the same canonical identity (`"defected"`) and are merged automatically.

### `LabelRecord` Fields

```python
label_name, normalized_name, aliases, countries, genres, subgenres,
bpm_min, bpm_max, energy_profile,
beatport_id, traxsource_id, beatport_url, traxsource_url,
verification_score,  # 0.0–1.0 (seed=0.2, scrape=0.7, full enrich=0.95)
notes, discovered_from, last_seen_utc
```

### Energy Profile Heuristics

| Profile | Approximate BPM | Genres |
|---|---|---|
| `warmup` | ≤ 118 avg | Organic/Deep House |
| `groove` | 118–126 avg | Most house |
| `peak` | ≥ 126 avg | Tech House, Afro Tech |
| `closing` | < 122 avg | General catch-all |

### Scraper Behavior

The `HttpClient` enforces robots.txt compliance, per-host rate limiting (default 2 seconds), and SHA256-keyed disk cache (re-running does not re-fetch already-cached pages).

⚠️ **Note:** Beatport and Traxsource scrapers target `a[href*='/label/']` CSS selectors. If either site redesigns its HTML structure, scraping will silently return fewer results. Monitor `labels.json` for records with low `verification_score` and `search_failed` / `enrich_failed` notes.

---

## Data Outputs

### Pipeline Outputs

| Path | Type | Contents |
|---|---|---|
| `logs/pipeline.log` | Text | Structured pipeline log (appended per run) |
| `logs/processing_log.txt` | Text | Human-readable audit log (appended per run) |
| `logs/processed.db` | SQLite | All track state, history, cue points, set playlists |
| `logs/reports/pipeline_<id>.txt` | Text | Per-run summary statistics |
| `playlists/m3u/*.m3u8` | M3U | Per-letter playlists |
| `playlists/m3u/Genre/*.m3u8` | M3U | Per-genre playlists |
| `playlists/m3u/Energy/*.m3u8` | M3U | Peak / Mid / Chill energy playlists |
| `playlists/m3u/Combined/*.m3u8` | M3U | Genre+energy combined playlists |
| `playlists/m3u/Key/*.m3u8` | M3U | Camelot key playlists (1A–12B) |
| `playlists/m3u/Route/*.m3u8` | M3U | Route playlists (Acapella, Tool, Vocal) |
| `playlists/xml/rekordbox_library.xml` | XML | Full Rekordbox import (all six hierarchies) |

### Cue Suggest Outputs

| Path | Type | Contents |
|---|---|---|
| `logs/cue_suggest/cue_suggestions.json` | JSON | Master — all tracks in DB, latest cues |
| `logs/cue_suggest/cue_suggestions.csv` | CSV | Wide format — one row per track, one column per cue type |
| `logs/cue_suggest/runs/cues_TIMESTAMP.csv` | CSV | Per-run detail log — one row per cue point |
| `<audio>.cues.json` | JSON | Sidecar per track (opt-in) |

### Set Builder Outputs

| Path | Type | Contents |
|---|---|---|
| `logs/set_builder/<name>.m3u8` | M3U | Playable playlist with phase annotations |
| `logs/set_builder/<name>.csv` | CSV | Full metadata + transition notes per track |

### Harmonic Suggest Outputs

| Path | Type | Contents |
|---|---|---|
| `logs/harmonic_suggest/<timestamp>.json` | JSON | Suggestion results with scores + explanations |

### Label Intelligence Outputs

| Path | Type | Contents |
|---|---|---|
| `data/labels/output/labels.json` | JSON | Full `LabelRecord` data |
| `data/labels/output/labels.csv` | CSV | Flat spreadsheet export |
| `data/labels/output/labels.txt` | TXT | One label name per line |
| `data/labels/output/labels.db` | SQLite | Queryable label database |

### Label Clean Outputs

| Path | Type | Contents |
|---|---|---|
| `data/labels/clean/label_clean_report.json` | JSON | Per-track detection results |
| `data/labels/clean/label_clean_report.csv` | CSV | Spreadsheet-friendly |
| `data/labels/clean/label_clean_review.json` | JSON | Unresolved / low-confidence only |
| `data/labels/clean/label_clean_summary.txt` | TXT | Stats + top labels |

### SQLite Schema (`processed.db`)

```sql
-- Current state of every track
tracks (filepath, filename, artist, title, genre, bpm, key_musical,
        key_camelot, duration_sec, bitrate_kbps, filesize_bytes,
        status, error_msg, processed_at, pipeline_ver)

-- Rollback snapshots
track_history (filepath, original_path, original_meta, cleaned_meta,
               actions, created_at, rolled_back, rollback_note)

-- Per-run statistics
pipeline_runs (run_at, dry_run, inbox_count, processed, rejected,
               duplicates, errors, duration_sec)

-- Duplicate detection records
duplicate_groups (run_id, original, duplicate, reason, resolved)

-- Cue point suggestions (from cue-suggest)
cue_points (filepath, cue_type, time_sec, bar, beat_in_bar,
            confidence, source, analyzed_at)

-- Generated sets (from set-builder)
set_playlists (name, created_at, config_json, duration_sec, track_count)
set_playlist_tracks (set_id, position, filepath, phase, transition_note)
```

---

## Automation

### systemd Timer (runs every 30 minutes)

```bash
systemctl --user enable --now djtoolkit.timer
systemctl --user status djtoolkit.timer
journalctl --user -u djtoolkit.service -n 50
```

### Inbox Watcher (real-time trigger)

```bash
systemctl --user enable --now djtoolkit-watch.service
```

`watch_inbox.sh` uses `inotifywait` to monitor `/music/inbox/`. When new audio files are detected, it waits 15 seconds for the transfer to settle, then triggers `pipeline.sh`.

### Manual Bash Wrapper

```bash
./pipeline.sh
./pipeline.sh --dry-run
./pipeline.sh --skip-analysis --verbose
```

---

## Safety and Limitations

### What is written automatically

| Action | Condition |
|---|---|
| Move file from inbox to library | Track passes QC and is not a duplicate |
| Write artist/title/genre/BPM/key tags | Track passes all pipeline stages |
| Sanitize junk from tags (incl. label/TPUB) | `SANITIZE_TAGS = True` (default) |
| Delete junk-only label tag from file | Organization field is entirely a URL or watermark |
| Write label tag (`label-clean --write-tags`) | Confidence ≥ threshold (default 0.85) |
| Store cue points to DB | `cue-suggest` run (not dry-run), confidence ≥ `CUE_SUGGEST_MIN_CONFIDENCE` |
| Store set playlist to DB | `set-builder` run (not dry-run) |

### What is never written automatically

- Native Rekordbox hot-cues — cue-suggest outputs suggested positions for review only
- Rollback-only tag restoration (requires explicit command)
- Any external provider data (Discogs, Beatport IDs) — not yet implemented

### Cue suggest limitations

- Suggestions are positions to review, not authoritative hot-cues
- RMS + LF + spectral flux work best for kick-driven music (house, afro house, amapiano); less reliable for very slow or ambient tracks
- Cue `note` field not persisted to DB (no DB column) — present in JSON/CSV outputs only
- When ffmpeg decode fails, BPM-estimate fallback is used (all confidence values ≤ 0.50)

### Scraper limitations

- Beatport and Traxsource scrapers parse live HTML. Site layout changes will break extraction silently.
- HTML cache means stale pages may be served on re-runs. Clear `$DJ_MUSIC_ROOT/.cache/label_intel/` to force fresh fetches.

### Beets dependency

Beets is optional but recommended. If `beet` is not installed or fails for a specific track, the pure-Python organizer fallback is used.

---

## Development Notes

### Running tests

```bash
python3 -m pytest tests/ -v
python3 -m pytest tests/test_parser.py -v
python3 -m pytest tests/test_sanitizer.py -v
python3 -m pytest tests/ --cov=modules --cov-report=term-missing
```

### Adding a new pipeline stage

1. Create `modules/newstage.py` with `run(files, run_id, dry_run) -> list`
2. Import and call it in `run_pipeline()` in `pipeline.py`
3. Add any new config values to `config.py`

### Extension points

| What to extend | Where |
|---|---|
| New routing rules | `modules/organizer.py` route patterns |
| New junk phrases | `config/junk_patterns.json` `promo_phrases` list |
| New label-indicator keywords | `modules/parser.py` `_LABEL_SIGNALS` |
| New filename label patterns | `label_intel/filename_parser.py` `_PATTERNS` |
| Energy BPM thresholds | `modules/playlists.py` `_BPM_PEAK`, `_BPM_MID` |
| Target genres for combined playlists | `modules/playlists.py` `_COMBINED_TARGET_GENRES` |
| Camelot scoring weights | `modules/harmonic.py` `_DEFAULT_WEIGHTS` |
| Cue detection thresholds | `modules/cue_suggest.py` threshold constants |
| Set builder vibe presets | `modules/set_builder.py` `_VIBE_WEIGHTS` |
| Additional known labels | `known_labels.txt` (one per line) |

### Planned / Future

- **Phase 2 providers** — Discogs and Beatport single-label lookup (stubs in `label_intel/providers/`)
- **More audio formats** — WAV/AIFF tag writing support is partial
- **Rekordbox XML hot-cue writing** — safe specification for embedding cue points in XML needs confirmation across Rekordbox versions before implementation

---

## Troubleshooting

### `ffprobe: command not found`
```bash
sudo apt install ffmpeg
```

### `aubiobpm: command not found`
```bash
sudo apt install aubio-tools
# Or override in config_local.py:
# AUBIOBPM_BIN = "/path/to/aubiobpm"
```

### `keyfinder-cli: command not found`
keyfinder-cli is not in apt. Install from the project's AppImage or source.
Override in `config_local.py`: `KEYFINDER_BIN = "/path/to/keyfinder-cli"`

### cue-suggest produces no output / all BPM-estimate

Audio decode requires `ffmpeg` to be installed and `numpy` to be available. Check:
```bash
which ffmpeg
python3 -c "import numpy; print(numpy.__version__)"
```
If ffmpeg is missing, install it. If numpy is missing: `pip install numpy`. Tracks with no BPM in the DB will also fall back to estimate mode — run `python3 pipeline.py --reanalyze` first.

### set-builder produces 0 tracks

The set builder reads from `status='ok'` tracks in the DB. If the DB is empty, run the full pipeline first. If tracks are present but have no BPM or Camelot key, they are excluded from harmonic scoring — run `python3 pipeline.py --reanalyze`.

### harmonic-suggest returns no results

`--track PATH` requires the track to exist in the DB with `status='ok'`. Use `python3 pipeline.py --reanalyze` to populate BPM + key if missing. `--key` + `--bpm` mode does not require a DB entry for the reference track.

### Energy playlists are empty or missing

Energy classification requires BPM data. Run `python3 pipeline.py --reanalyze` to fill in BPM for library tracks. Tracks with no BPM and no genre signal default to Mid.

### Key playlists are empty or missing

Camelot key playlists require `key_camelot` data in the DB. Run `python3 pipeline.py --reanalyze` to re-run key detection on library tracks that are missing this value.

### Label tag was junk but is still in the file

Run `python3 pipeline.py metadata-clean --dry-run` to preview what would be cleaned. If the tag persists, the value was not matched by any junk pattern — add the specific phrase to `config/junk_patterns.json`.

### Tracks end up in `library/unknown/` instead of `library/sorted/`

The track had insufficient metadata. Inspect tags with `kid3 <file>` or `mutagen-inspect <file>`. Fix tags manually, re-drop into inbox, or check `logs/processing_log.txt` for the rejection reason.
