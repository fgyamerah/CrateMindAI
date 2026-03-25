# TrackIQ

> Automated DJ library preparation — from raw downloads to a Rekordbox-ready collection.

TrackIQ is a local-first, pipeline-based toolkit that takes audio files from an inbox folder and produces a clean, fully-tagged, BPM/key-analysed music library with Rekordbox-compatible XML exports. It runs unattended on Linux (Ubuntu Studio 24), optionally on a timer or inbox-watch trigger, and outputs a library that transfers directly to a DJ drive for use on Windows.

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
   - [Label Intelligence](#label-intelligence-subcommand)
   - [Label Clean](#label-clean-subcommand)
   - [Library Enrichment](#library-enrichment-flag)
   - [Rollback](#rollback-tool)
   - [Transfer to DJ Drive](#transfer-to-dj-drive)
8. [Label Intelligence — Deep Dive](#label-intelligence--deep-dive)
9. [Data Outputs](#data-outputs)
10. [Automation](#automation)
11. [Safety and Limitations](#safety-and-limitations)
12. [Development Notes](#development-notes)
13. [Troubleshooting](#troubleshooting)

---

## What TrackIQ Does

DJs accumulate files from many sources — Beatport, Traxsource, Bandcamp, promo pools, and miscellaneous downloads. These files often arrive with inconsistent, incomplete, or outright junk metadata: URL watermarks in artist fields, catalog numbers where labels should be, missing BPM, wrong key, or no tags at all.

TrackIQ solves this by running each file through a deterministic, idempotent pipeline:

1. **Validates** the file (bitrate, duration, format) using ffprobe
2. **Deduplicates** against the existing library using rmlint
3. **Organises** the file into a clean folder structure using Beets (MusicBrainz) or a pure-Python fallback parser
4. **Sanitises** tags — strips URL watermarks, promo phrases, and symbol junk
5. **Detects BPM** using aubio with windowed median averaging
6. **Detects musical key** in Camelot notation using keyfinder-cli
7. **Writes final tags** in ID3v2.3 (MP3), FLAC, or M4A format
8. **Generates playlists** — per-genre M3U files and a full Rekordbox XML
9. **Reports** on every run

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
| Audio file validation (bitrate, duration, codec) | `modules/qc.py` + ffprobe | ✅ Implemented |
| Duplicate detection | `modules/dedupe.py` + rmlint | ✅ Implemented |
| Smart file organisation | `modules/organizer.py` + Beets / Python parser | ✅ Implemented |
| Camelot key / artist / title prefix stripping | `modules/parser.py` | ✅ Implemented |
| URL and promo junk removal from tags | `modules/sanitizer.py` | ✅ Implemented |
| Label-name detection in artist/album_artist fields | `modules/parser.py` → `classify_name_candidate()` | ✅ Implemented |
| BPM detection with windowed median | `modules/analyzer.py` + aubio | ✅ Implemented |
| Musical key detection (Camelot) | `modules/analyzer.py` + keyfinder-cli | ✅ Implemented |
| ID3v2.3 / FLAC / M4A tag writing | `modules/tagger.py` + mutagen | ✅ Implemented |
| Per-genre M3U playlist generation | `modules/playlists.py` | ✅ Implemented |
| Rekordbox XML export | `modules/playlists.py` | ✅ Implemented |
| Run reports | `modules/reporter.py` | ✅ Implemented |
| SQLite audit trail + rollback | `db.py` + `scripts/rollback.py` | ✅ Implemented |
| Metadata rollback CLI | `scripts/rollback.py` | ✅ Implemented |
| Inbox-watch trigger | `scripts/watch_inbox.sh` + inotifywait | ✅ Implemented |
| Transfer to DJ drive | `scripts/transfer.sh` + rsync | ✅ Implemented |
| systemd timer + watch service | `systemd/` | ✅ Implemented |

### Label Intelligence

| Feature | Command | Status |
|---|---|---|
| Web scraping (Beatport + Traxsource) | `label-intel` | ✅ Implemented |
| JSON / CSV / TXT / SQLite export | `label-intel` | ✅ Implemented |
| Library enrichment (BPM/genre from local tracks) | `--label-enrich-from-library` | ✅ Implemented |
| Label tag detection + normalization + confidence scoring | `label-clean` | ✅ Implemented (Phase 1) |
| Filename-based label extraction | `label-clean` | ✅ Implemented |
| Alias merging across spelling variants | `label-clean` | ✅ Implemented |
| Conservative tag write-back | `label-clean --write-tags` | ✅ Implemented |
| Discogs provider | `--use-discogs` | 🔲 Placeholder (Phase 2) |
| Beatport single-label lookup | `--use-beatport` | 🔲 Placeholder (Phase 2) |

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
├── pipeline.py               Main entry point — orchestrates all stages
├── pipeline.sh               Bash wrapper (locking, env, dependency checks)
├── config.py                 Central configuration and path definitions
├── config_local.py           User-local overrides (git-ignored, created by setup.sh)
├── db.py                     SQLite database layer — all pipeline state
├── beets_config.yaml         Beets music organizer configuration template
├── setup.sh                  First-time installer (directories, packages, services)
├── known_labels.txt          Label blocklist for parser/label-clean
├── PROJECT_CONTEXT.txt       Detailed technical documentation
│
├── modules/                  Pipeline stage modules
│   ├── parser.py             Filename/metadata parsing, prefix removal, validation,
│   │                         classify_name_candidate() for label vs artist detection
│   ├── sanitizer.py          URL/promo junk removal from tag text
│   ├── organizer.py          File routing, folder construction, beets integration
│   ├── qc.py                 Quality control (ffprobe — bitrate, duration, codec)
│   ├── dedupe.py             Duplicate detection (rmlint)
│   ├── analyzer.py           BPM (aubio) and key (keyfinder-cli) analysis
│   ├── tagger.py             Final tag writing (mutagen, ID3v2.3/FLAC/M4A)
│   ├── playlists.py          M3U and Rekordbox XML generation
│   ├── reporter.py           Human-readable run summary reports
│   └── textlog.py            Append-only plaintext audit log
│
├── label_intel/              Label Intelligence package
│   ├── models.py             LabelRecord dataclass
│   ├── store.py              LabelStore — in-memory, deduped by normalized name
│   ├── utils.py              normalize_label_name(), parse_energy(), soft_bpm_hint()
│   ├── scraper.py            scrape_labels() — seed → search → enrich orchestrator
│   ├── exporters.py          export_json/csv/txt/sqlite()
│   ├── enrich_from_library.py  enrich_store_from_tracks() — local library enrichment
│   ├── cleaner.py            Label detection, confidence scoring, write-back
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
| `ffmpeg` | `ffprobe` — audio validation and metadata extraction |
| `aubio-tools` | `aubiobpm` — BPM detection |
| `rmlint` | `rmlint` — duplicate file detection |
| `inotify-tools` | `inotifywait` — inbox file watcher |
| `kid3` | `kid3-cli` — tag inspection utility |
| `beets` | `beet` — MusicBrainz-powered organizer (optional) |

And these Python packages via `pip`:

| Package | Used for |
|---|---|
| `mutagen` | Tag reading and writing |
| `beets` | Organizer integration (optional) |
| `requests` | Label Intelligence HTTP scraping |
| `beautifulsoup4` | Label Intelligence HTML parsing |

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
```

### Environment Variables

All key paths can also be set via environment variables, which take priority:

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
| `FFPROBE_BIN` | `ffprobe` | ffprobe binary |
| `BEET_BIN` | `beet` | Beets CLI binary |

### Directory Layout

After `setup.sh` runs, the music directory tree looks like this:

```
$DJ_MUSIC_ROOT/                     (default: /music)
│
├── inbox/                          Drop new tracks here
├── processing/                     Temporary staging (pipeline use only)
│
├── library/
│   ├── sorted/                     Clean, organized library
│   │   ├── _unsorted/              Tracks Beets could not identify
│   │   └── _compilations/          Multi-artist albums
│   ├── acapella/
│   ├── instrumental/
│   ├── dj_tools/
│   ├── edits/
│   ├── bootlegs/
│   ├── live/
│   └── unknown/                    Tracks with insufficient metadata
│
├── duplicates/                     Quarantined duplicate files
├── rejected/                       Failed QC (corrupt, too short, etc.)
│
├── playlists/
│   ├── m3u/                        Per-genre M3U playlists
│   │   └── Genre/                  Genre-based M3U subdirectory
│   └── xml/                        Rekordbox XML
│       └── rekordbox_library.xml
│
├── data/
│   └── labels/
│       ├── seeds.txt               Label names for web scraping
│       ├── output/                 label-intel exports (JSON/CSV/TXT/SQLite)
│       └── clean/                  label-clean reports
│
├── .cache/
│   └── label_intel/                HTTP cache for scraper (SHA256-keyed HTML files)
│
└── logs/
    ├── pipeline.log                Structured pipeline log (appended per run)
    ├── processing_log.txt          Human-readable audit log (appended per run)
    ├── beets_import.log            Beets import log
    ├── processed.db                SQLite database (all pipeline state)
    ├── README.md                   Auto-generated run summary (overwritten)
    └── reports/
        └── pipeline_<run_id>.txt   Per-run reports
```

### Quality Thresholds

```python
MIN_BITRATE_KBPS = 128      # files below this are rejected
MIN_DURATION_SEC = 30       # files shorter than this are rejected
MAX_DURATION_SEC = 7200     # files longer than 2 hours are rejected
BPM_MIN = 60                # BPM outside this range is discarded
BPM_MAX = 200
```

### Label Intelligence Paths

```python
LABEL_INTEL_SEEDS   = MUSIC_ROOT / "data/labels/seeds.txt"
LABEL_INTEL_OUTPUT  = MUSIC_ROOT / "data/labels/output"
LABEL_INTEL_CACHE   = MUSIC_ROOT / ".cache/label_intel"
LABEL_INTEL_SOURCES = ["beatport", "traxsource"]
LABEL_INTEL_DELAY   = 2.0         # seconds between requests per host
```

### Label Clean Paths

```python
LABEL_CLEAN_OUTPUT    = MUSIC_ROOT / "data/labels/clean"
LABEL_CLEAN_THRESHOLD = 0.85     # minimum confidence for automatic write-back
```

### Known Labels File

`known_labels.txt` at the project root is a plain text blocklist loaded by the parser to classify names as labels rather than artist names. One name per line, `#` comments supported.

```text
# known_labels.txt
drumcode
hot creations
elrow music
fabric
```

Override the path in `config_local.py`:

```python
from modules.parser import _DEFAULT_KNOWN_LABELS_PATH
KNOWN_LABELS_FILE = Path("/custom/path/known_labels.txt")
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
[4/8]  Sanitize tags            Strip URL watermarks, promo phrases, symbols
[5/8]  BPM + key analysis       aubiobpm → Camelot key via keyfinder-cli
[6/8]  Write tags               mutagen: ID3v2.3 / FLAC / M4A
[7/8]  Playlist generation      M3U (per genre) + Rekordbox XML
[8/8]  Report                   Text report + auto-update README in logs/
```

All steps are idempotent. Tracks with `status='ok'` in the database are skipped.

---

### Label Intelligence Subcommand

Scrape label metadata from Beatport and Traxsource for a list of seed labels.

```bash
# Scrape with default seeds file ($DJ_MUSIC_ROOT/data/labels/seeds.txt)
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

# Slower rate limiting (increase delay if getting throttled)
python3 pipeline.py label-intel --label-delay 5.0
```

**Seeds file format** — one label name per line:

```text
MoBlack Records
Defected Records
Drumcode
Kerri Chandler
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
# Scan and report only — no file changes (default / safe mode)
python3 pipeline.py label-clean

# Explicit dry run (same as above)
python3 pipeline.py label-clean --dry-run

# Write high-confidence labels (≥ 0.85) back to TPUB tag
python3 pipeline.py label-clean --write-tags

# Export only unresolved tracks for manual review
python3 pipeline.py label-clean --review-only

# Lower threshold to include grouping-tag fallbacks (0.75)
python3 pipeline.py label-clean --write-tags --confidence-threshold 0.75

# Verbose debug output
python3 pipeline.py label-clean --verbose
```

**Detection order and confidence:**

| Source | Confidence | Written at default threshold? |
|---|---|---|
| Embedded `organization/TPUB` tag (valid) | **0.95** | ✅ Yes |
| `grouping` tag fallback | 0.75 | No (lower `--confidence-threshold`) |
| `comment` tag fallback | 0.60 | No |
| `albumartist` with label-indicator word | 0.50 | No |
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
# Read label (TPUB) + genre + BPM from all OK tracks; update labels.json
python3 pipeline.py --label-enrich-from-library

# With verbose logging
python3 pipeline.py --label-enrich-from-library --verbose
```

This reads the `organization` tag from each processed audio file (no re-analysis of BPM or key), then:
- Creates new label records for labels not yet in the database (score `0.1`)
- Enriches existing records with `bpm_min`, `bpm_max`, `genres`, `subgenres`, `energy_profile`
- Never overwrites scraped metadata (Beatport/Traxsource IDs, URLs, higher verification scores)

**Typical workflow:**

```bash
# 1. Scrape web sources for known labels
python3 pipeline.py label-intel

# 2. Enrich with your actual library data
python3 pipeline.py --label-enrich-from-library

# 3. Inspect the result
sqlite3 /music/data/labels/output/labels.db \
    "SELECT label_name, bpm_min, bpm_max, energy_profile FROM labels ORDER BY label_name"
```

---

### Rollback Tool

Restore original metadata tags (and optionally original file paths) for any previously processed track.

```bash
# List all rollback-eligible records
python3 scripts/rollback.py list

# Include already-rolled-back records
python3 scripts/rollback.py list --all

# Inspect a specific history record
python3 scripts/rollback.py info 42

# Dry-run rollback (preview only)
python3 scripts/rollback.py rollback 42 --dry-run

# Restore original tags
python3 scripts/rollback.py rollback 42

# Restore original tags AND move file back to original inbox path
python3 scripts/rollback.py rollback 42 --restore-path
```

Rollback **never deletes files**. It only overwrites tags or moves files. All rollback actions are logged to `processing_log.txt`.

---

### Transfer to DJ Drive

```bash
# Mount your drive first, then:
./scripts/transfer.sh /mnt/djdrive

# Dry run (shows what would be transferred)
./scripts/transfer.sh /mnt/djdrive --dry-run
```

Transfers `library/sorted/` and `playlists/` to the drive using `rsync --checksum` (reliable on exFAT). Subsequent runs only transfer new or changed files.

**After transfer — Rekordbox import on Windows:**

1. Open Rekordbox
2. **File → Import Library** → select `<drive>:\music\playlists\xml\rekordbox_library.xml`
3. Select all new tracks → right-click → **Analyze**
4. Set cue points as needed
5. **File → Export to USB**

---

## Label Intelligence — Deep Dive

### Architecture

The label intelligence system is built around a **name-first identity model**. A label's canonical identity is its `normalized_name` (lowercased, punctuation-stripped, noise-suffix-removed). Beatport and Traxsource IDs are optional enrichment fields — never the primary key.

This means `"Defected"`, `"Defected Records"`, and `"Defected Recordings"` all resolve to the same canonical identity (`"defected"`) and are merged automatically.

### `LabelRecord` Fields

```python
label_name       str      # best display name
normalized_name  str      # deduplication key
aliases          list     # all observed spellings
countries        list     # e.g. ["UK"]
genres           list     # e.g. ["Tech House", "Deep House"]
subgenres        list
bpm_min          int      # BPM range hint
bpm_max          int
energy_profile   str      # "warmup" | "groove" | "peak" | "closing"
beatport_id      str?     # nullable — from web scraping
traxsource_id    str?     # nullable — from web scraping
beatport_url     str?
traxsource_url   str?
verification_score float  # 0.0 – 1.0 (seed=0.2, scrape=0.7, full enrich=0.95)
notes            list
discovered_from  list     # ["seed", "beatport", "library"]
last_seen_utc    str
```

### Scraper Behavior

1. Seeds are loaded from the seeds file and inserted into the store at score `0.2`
2. For each seed, each configured source (Beatport, Traxsource) is searched
3. Candidate label pages that fuzzy-match the seed name are enriched
4. The `HttpClient` in `sources/base.py` enforces:
   - robots.txt compliance (per host, with graceful fallback)
   - Per-host rate limiting (configurable delay, default 2 seconds)
   - SHA256-keyed disk cache (re-running does not re-fetch already-cached pages)

### HTML Selector Fragility

⚠️ **Important:** The Beatport and Traxsource scrapers use BeautifulSoup CSS selectors to extract label links from search result pages. These selectors target `a[href*='/label/']` elements. If either site redesigns its HTML structure, scraping will silently return fewer or no results. The `notes` field on affected records will contain `search_failed` or `enrich_failed` entries.

Monitor `labels.json` for records with low `verification_score` and non-empty `notes` to detect scraper drift.

### Energy Profile Heuristics

BPM ranges are used to assign a rough energy label:

| Profile | Approximate BPM | Genres |
|---|---|---|
| `warmup` | ≤ 118 avg | Organic/Deep House |
| `groove` | 118–126 avg | Most house |
| `peak` | ≥ 126 avg | Tech House, Afro Tech |
| `closing` | < 122 avg | General catch-all |

These are genre-aware heuristics, not authoritative. They provide a quick sorting hint for set planning.

---

## Data Outputs

### Pipeline Outputs

| Path | Type | Contents |
|---|---|---|
| `logs/pipeline.log` | Text | Structured pipeline log (appended per run) |
| `logs/processing_log.txt` | Text | Human-readable audit log (appended per run) |
| `logs/processed.db` | SQLite | All track state, history, run metadata |
| `logs/reports/pipeline_<id>.txt` | Text | Per-run summary statistics |
| `logs/README.md` | Markdown | Latest run summary (overwritten) |
| `playlists/m3u/*.m3u` | M3U | Per-genre playlists |
| `playlists/xml/rekordbox_library.xml` | XML | Full Rekordbox library import |

### Label Intelligence Outputs

| Path | Type | Contents |
|---|---|---|
| `data/labels/output/labels.json` | JSON | Full `LabelRecord` data, all fields |
| `data/labels/output/labels.csv` | CSV | Flat spreadsheet export |
| `data/labels/output/labels.txt` | TXT | One label name per line (blocklist-ready) |
| `data/labels/output/labels.db` | SQLite | Queryable label database |

### Label Clean Outputs

| Path | Type | Contents |
|---|---|---|
| `data/labels/clean/label_clean_report.json` | JSON | Per-track detection results |
| `data/labels/clean/label_clean_report.csv` | CSV | Spreadsheet-friendly |
| `data/labels/clean/label_clean_review.json` | JSON | Unresolved / low-confidence tracks only |
| `data/labels/clean/label_clean_summary.txt` | TXT | Stats + top labels |

### SQLite Schema (`processed.db`)

```sql
-- Current state of every track
tracks (filepath, filename, artist, title, genre, bpm, key_musical,
        key_camelot, duration_sec, bitrate_kbps, filesize_bytes,
        status, error_msg, processed_at, pipeline_ver)

-- Rollback snapshots (original + cleaned metadata as JSON)
track_history (filepath, original_path, original_meta, cleaned_meta,
               actions, created_at, rolled_back, rollback_note)

-- Per-run statistics
pipeline_runs (run_at, dry_run, inbox_count, processed, rejected,
               duplicates, errors, duration_sec)

-- Duplicate detection records
duplicate_groups (run_id, original, duplicate, reason, resolved)
```

---

## Automation

### systemd Timer (runs every 30 minutes)

```bash
# Install and enable
systemctl --user enable --now djtoolkit.timer

# Check status
systemctl --user status djtoolkit.timer
journalctl --user -u djtoolkit.service -n 50
```

The timer fires 5 minutes after boot, then every 30 minutes (±2 min jitter). If the inbox is empty, the pipeline exits immediately without doing any work.

### Inbox Watcher (real-time trigger)

```bash
# Enable the file watcher service
systemctl --user enable --now djtoolkit-watch.service
```

`watch_inbox.sh` uses `inotifywait` to monitor `/music/inbox/`. When new audio files are detected, it waits 15 seconds for the transfer to settle, then triggers `pipeline.sh`. The service auto-restarts on failure with a 10-second delay.

### Manual Bash Wrapper

`pipeline.sh` is the recommended way to run the pipeline manually:
- Prevents concurrent runs with a file lock
- Activates virtualenv if `DJ_VENV` is set
- Checks that required binaries exist
- Skips early if inbox is empty
- Logs timing and exit code

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
| Sanitize junk from tags | `SANITIZE_TAGS = True` (default) |
| Write label tag (`label-clean --write-tags`) | Confidence ≥ threshold (default 0.85) |

### What is never written automatically

- Rollback-only tag restoration (always requires explicit command)
- Any external provider data (Discogs, Beatport IDs) — not yet implemented
- Labels found via fallback fields or filename patterns — these appear in reports only (unless `--write-tags` with a lowered `--confidence-threshold`)

### Conservative metadata behavior

- Tags are only written when confidence is high or the source is authoritative (embedded tag, BPM analysis, key analysis)
- The pipeline stores original metadata snapshots before any write — the rollback tool can restore them at any time
- `--dry-run` on every command simulates the full run without modifying files or writing to the database
- Junk label detection explicitly rejects: empty strings, single characters, `unknown`, `n/a`, catalog codes (e.g., `ABC001`), and genre words masquerading as labels

### Scraper limitations

- Beatport and Traxsource scrapers parse live HTML. Site layout changes will break extraction silently. Check `notes` fields in `labels.json` for `search_failed` or `enrich_failed` markers.
- The scraper respects `robots.txt` and enforces a per-host delay (default 2 seconds). Aggressive rate reduction or IP-level throttling by either site is possible.
- HTML cache on disk means stale pages may be served on re-runs. Clear `$DJ_MUSIC_ROOT/.cache/label_intel/` to force fresh fetches.
- Beatport search results may include false-positive label matches (similar names). The scraper uses a fuzzy substring match (`seed_norm in cand_norm or cand_norm in seed_norm`) which may over-match for short or common label names.

### Beets dependency

Beets is optional but recommended. If `beet` is not installed or fails for a specific track, the pure-Python organizer fallback is used. The fallback relies on filename parsing and existing tags, so metadata quality for unidentified tracks will be lower.

---

## Development Notes

### Running tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Run specific test file
python3 -m pytest tests/test_parser.py -v
python3 -m pytest tests/test_sanitizer.py -v

# With coverage (if pytest-cov is installed)
python3 -m pytest tests/ --cov=modules --cov-report=term-missing
```

Current test coverage focuses on `modules/parser.py` (prefix removal, separator normalization, artist validation, `classify_name_candidate`) and `modules/sanitizer.py` (URL removal, promo phrase detection, symbol removal).

### Adding a new pipeline stage

1. Create `modules/newstage.py` following the existing module pattern
2. Add a `run(files, run_id, dry_run) -> list` function
3. Import and call it in the appropriate position in `pipeline.py`'s `run_pipeline()` function
4. Add any new config values to `config.py`

### Adding a new label source

1. Create `label_intel/sources/newsource.py` implementing `search_url()`, `extract_candidates()`, and `enrich_label_page()`
2. Register it in `label_intel/scraper.py`'s `_sources()` function
3. Add the source name to `config.LABEL_INTEL_SOURCES` if it should be default-on
4. Add it as a valid choice in the `--label-sources` argparse argument in `pipeline.py`

### Implementing a Phase 2 provider (Discogs / Beatport clean)

The stubs in `label_intel/providers/discogs.py` and `label_intel/providers/beatport.py` define the expected interface: a `match(label_name: str) -> dict | None` method. The `run_label_clean()` function in `pipeline.py` already checks for `--use-discogs` and `--use-beatport` flags and will call these providers once implemented.

### Extension points

| What to extend | Where |
|---|---|
| New routing rules (acapella, etc.) | `modules/organizer.py` route patterns |
| New junk phrases | `modules/sanitizer.py` `_PROMO_PHRASES` list |
| New label-indicator keywords | `modules/parser.py` `_LABEL_SIGNALS` |
| New filename label patterns | `label_intel/filename_parser.py` `_PATTERNS` |
| New genre → BPM hint mappings | `label_intel/utils.py` `soft_bpm_hint()` |
| Additional known labels | `known_labels.txt` (one per line) |

### Planned / Future

The following directions are suggested by the current codebase but not yet implemented:

- **Phase 2 providers** — Discogs and Beatport single-label lookup for the `label-clean` flow (stubs exist in `label_intel/providers/`)
- **Persistent `LabelStore` load/save** — `LabelStore` is currently in-memory only; deserialization from `labels.json` is handled inline in `pipeline.py`. A `LabelStore.load(path)` / `LabelStore.save(path)` class method would clean this up.
- **Post-organize enrichment hook** — a comment in `run_pipeline()` marks the point where `enrich_store_from_tracks()` could be called automatically after each pipeline run, once a persistent store is available.
- **More audio formats** — the tag writer currently handles MP3/FLAC/M4A. WAV/AIFF tagging support is partial (format-dependent mutagen behavior).

---

## Troubleshooting

### `ffprobe: command not found`
Install ffmpeg: `sudo apt install ffmpeg`

### `aubiobpm: command not found`
Install aubio tools: `sudo apt install aubio-tools`
Or override the binary path in `config_local.py`: `AUBIOBPM_BIN = "/path/to/aubiobpm"`

### `keyfinder-cli: command not found`
keyfinder-cli is not in apt. Install from the project's AppImage or source.
Override in `config_local.py`: `KEYFINDER_BIN = "/path/to/keyfinder-cli"`

### BPM analysis returns nothing / all tracks skipped
Check that `aubiobpm` or `aubio` is on `$PATH`. Run `which aubiobpm` to verify.
The analyzer probes for `aubio` and `aubiobpm` — if neither is found, the analysis step is skipped and logged as a warning.

### Tracks end up in `library/unknown/` instead of `library/sorted/`
The track had insufficient metadata for the organizer to determine an artist and title. Inspect tags with `kid3 <file>` or `mutagen-inspect <file>`. Either fix the tags manually and re-drop into inbox, or check `logs/processing_log.txt` for the rejection reason.

### Label detection misses / everything is "unresolved"
- Run `python3 pipeline.py label-clean --verbose` to see per-track debug output
- Most unresolved tracks are genuinely missing a label tag. Check with `kid3 <file>` what TPUB/organization contains.
- Add known labels to `known_labels.txt` to improve `classify_name_candidate()` accuracy in the organizer.
- If the label is in a non-standard field, lower `--confidence-threshold` to 0.60–0.70 to include comment/grouping fallbacks.

### Web scraper returns empty results
- Verify the seeds file has one label name per line and no BOM encoding issues
- Check `labels.json` for records with `notes` containing `search_failed`
- The HTML cache may contain stale (empty) pages from a previous failed run. Clear it: `rm -rf $DJ_MUSIC_ROOT/.cache/label_intel/`
- Beatport and Traxsource may have updated their HTML structure. Inspect `label_intel/sources/beatport.py` and `traxsource.py` selectors against the live site.

### `PermissionError: Blocked by robots.txt`
The target site's robots.txt disallows the scraper's user-agent for the requested URL. The scraper honors this by design. There is no workaround within the current implementation.

### Pipeline runs again immediately after completing (timer)
Check `systemctl --user status djtoolkit.timer`. If `Persistent=true` causes immediate re-runs after a long suspension, set `Persistent=false` in the timer file and reload: `systemctl --user daemon-reload`.

### `config_local.py` changes not taking effect
`config_local.py` is imported at the **end** of `config.py`. Make sure you are not referencing a symbol that is defined after the import in `config.py` — the override will work but earlier symbols derived from the overridden value will not be updated. To override a derived path (e.g., `LABEL_INTEL_OUTPUT`), override it explicitly:

```python
# config_local.py
from pathlib import Path
MUSIC_ROOT = Path("/mnt/ssd/music")
LABEL_INTEL_OUTPUT = MUSIC_ROOT / "data/labels/output"   # re-derive manually
```

### `duplicate_groups` table grows indefinitely
`duplicate_groups` records are never auto-deleted. After reviewing duplicates in `$DJ_MUSIC_ROOT/duplicates/`, manually mark them resolved:

```sql
sqlite3 /music/logs/processed.db \
    "UPDATE duplicate_groups SET resolved=1 WHERE resolved=0"
```

---

## License

_Not yet specified. Add a LICENSE file to clarify distribution terms._

---

*TrackIQ — built for DJs, by a DJ.*
