# TrackIQ — DJ Library Automation Toolkit

> Raw audio downloads → clean, tagged, BPM/key-analysed library → Rekordbox-ready export.

TrackIQ is a local-first pipeline toolkit for automating DJ library preparation on Ubuntu Studio 24. It produces a fully-tagged library ready for transfer to a Windows DJ drive and import into Rekordbox, and ships a **FastAPI backend + React web UI** for managing jobs, reviewing tracks, and monitoring sync state from a browser.

---

## Current Status

| Area | Status |
|---|---|
| Core pipeline (ingest → tag → playlists) | Stable |
| Audit quality, artist merge, dedupe | Stable |
| M4A → AIFF conversion | Stable |
| FastAPI backend (jobs, tracks, BPM, export, sync) | Stable |
| React web UI (all pages) | Stable |
| SSD sync (rsync-based, no `--delete` by default) | Working |
| Set Builder UI | Working |
| BPM Review UI | Working |
| Rekordbox hot-cue write-back | Planned |
| Discogs / Beatport label provider | Planned |

---

## Full Pipeline Workflow

```
inbox/ (new downloads)
  → [1] QC           ffprobe — validate bitrate, duration, codec
  → [2] Dedupe       rmlint — detect duplicates against existing library
  → [3] Organize     Beets (MusicBrainz) + Python filename fallback
  → [4] Sanitize     Strip URLs, promo phrases, DJ-pool junk from all tags
  → [5] Analyze      BPM (aubio) + key (keyfinder-cli) — MIK-first: fills gaps only
  → [6] Write tags   mutagen — ID3v2.3 / FLAC / M4A
  → [7] Playlists    M3U (genre, energy, key, route) + optional Rekordbox XML
  → [8] Report       Terminal summary + pipeline.log
```

**MIK-first policy:** Mixed In Key is the authoritative source for BPM, key, and cue data. The pipeline never overwrites existing values. Cue suggestion and Rekordbox XML export are disabled by default.

---

## Repository Structure

```
djtoolkit/
├── pipeline.py              # CLI entry point — all subcommands
├── config.py                # All paths and tunables
├── config_local.py          # Local overrides (git-ignored)
├── db.py                    # SQLite pipeline database layer
│
├── modules/                 # Pipeline step implementations
│   ├── sanitizer.py
│   ├── analyzer.py
│   ├── tagger.py
│   ├── playlists.py
│   ├── set_builder.py
│   ├── audit_quality.py
│   ├── artist_merge.py
│   ├── convert_audio.py
│   └── ...                  # 24 modules total
│
├── label_intel/             # Label intelligence sub-package
├── utils/                   # LLM client, prompt logger
├── tests/                   # pytest suites
├── scripts/                 # rollback.py, transfer.sh, watch_inbox.sh
│
├── backend/                 # FastAPI web server
│   ├── app/
│   │   ├── main.py          # Server entry point
│   │   ├── core/            # Config, DB, pipeline_db
│   │   ├── api/routes/      # HTTP route handlers
│   │   ├── services/        # Business logic
│   │   ├── models/          # DB dataclasses
│   │   └── schemas/         # Pydantic request/response models
│   ├── data/                # jobs.db + logs/ (git-ignored)
│   └── requirements.txt
│
└── frontend/                # React + Vite + TypeScript web UI
    ├── src/
    │   ├── pages/           # Dashboard, Jobs, Tracks, BpmReview,
    │   │                    # SetBuilder, Export, SsdSync, Settings
    │   ├── components/      # Sidebar, PageHeader, LogModal, …
    │   ├── api/             # Typed fetch wrappers per domain
    │   ├── hooks/           # useJobs, useTracks
    │   └── types/           # TypeScript interfaces
    ├── index.html
    ├── vite.config.ts       # Dev server on :5173, proxy /api → :8000
    └── package.json
```

---

## Quick Start — Pipeline CLI

```bash
# 1. Create and activate virtualenv
python3 -m venv .venv
source .venv/bin/activate

# 2. Install runtime dependencies
pip install -r requirements.txt

# 3. (Dev/test) Install dev dependencies
pip install -r requirements-dev.txt

# 4. Run the full pipeline on whatever is in inbox/
python3 pipeline.py

# 5. Dry run — preview without writing anything
python3 pipeline.py --dry-run
```

---

## Running the Web App

The web app requires two processes: the FastAPI backend and the Vite dev server.
Run them in separate terminals from the project root.

### Step 1 — Start the backend

```bash
# With the .venv active:
pip install -r backend/requirements.txt   # first time only
uvicorn backend.app.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/api/health`

### Step 2 — Start the frontend

```bash
cd frontend
npm install          # first time only
npm run dev          # starts Vite on http://localhost:5173
```

Open `http://localhost:5173` in a browser. All `/api/*` requests are proxied to `localhost:8000`.

### After a reboot

```bash
# Terminal 1 — backend
source .venv/bin/activate
uvicorn backend.app.main:app --reload --port 8000

# Terminal 2 — frontend
cd frontend && npm run dev
```

---

## Web App Pages

| Page | URL | What it does |
|---|---|---|
| Dashboard | `/` | Job counts, recent job history, backend version |
| Jobs | `/jobs` | Submit pipeline commands; view history, logs, cancel running jobs |
| Tracks | `/tracks` | Browse library with search, sort, BPM/key/quality filters; slide-in detail panel |
| BPM Review | `/bpm-review` | Detect BPM anomalies (halved, doubled, missing); approve or requeue per track |
| Set Builder | `/set-builder` | Build energy-curve DJ sets; view saved sets with track list and phase badges |
| Export | `/export` | Validate library, view excluded tracks, run Rekordbox M3U export |
| SSD Sync | `/ssd-sync` | Preview and run rsync to external SSD; live progress bar; cancel support |
| Settings | `/settings` | Placeholder — configuration planned |

### Job system

Jobs are pipeline commands (e.g. `audit-quality`, `analyze-missing`) dispatched as asyncio background tasks. Each job has a UUID, status (`pending → running → succeeded/failed/cancelled`), stdout+stderr log file, and optional progress fields (`progress_current`, `progress_total`, `progress_percent`, `progress_message`).

- **Cancel:** `POST /api/jobs/{id}/cancel` — sends SIGTERM, marks status `cancelled`
- **Logs:** `GET /api/jobs/{id}/logs?tail=N` — returns log tail; polled live in the UI at 2s intervals
- **Progress:** rsync jobs parse `--info=progress2` output; toolkit jobs expose percent when the subprocess reports it

---

## Backend API Reference

All routes are prefixed with `/api`.

### Health

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/version` | Backend version, toolkit version, pipeline.py path |

### Jobs

| Method | Path | Description |
|---|---|---|
| POST | `/api/jobs` | Submit a pipeline job |
| GET | `/api/jobs` | List all jobs (newest first) |
| GET | `/api/jobs/{id}` | Get job by ID |
| GET | `/api/jobs/{id}/logs` | Full log (`?tail=N` for last N lines) |
| POST | `/api/jobs/{id}/cancel` | SIGTERM the subprocess; marks `cancelled` |

### Tracks (read-only from pipeline DB)

| Method | Path | Description |
|---|---|---|
| GET | `/api/tracks` | Paginated track list with filters (q, status, quality_tier, bpm_min/max, sort) |
| GET | `/api/tracks/{id}` | Single track detail |
| GET | `/api/tracks/stats` | Counts by status, missing BPM/key |
| GET | `/api/tracks/issues` | Tracks with active issues |

### BPM Analysis

| Method | Path | Description |
|---|---|---|
| POST | `/api/analysis/bpm-check` | Run BPM anomaly scan; stores results in `bpm_anomalies` table |
| GET | `/api/analysis/bpm-anomalies` | List anomalies with optional status/reason filter |
| PATCH | `/api/analysis/bpm-anomalies/{id}` | Update review status (reviewed / ignored / requeued) |
| POST | `/api/analysis/reanalyze` | Dispatch `analyze-missing` job for pending anomalies |

### Playlists / Set Builder

| Method | Path | Description |
|---|---|---|
| POST | `/api/playlists/set-builder` | Start a set-builder job with full parameter control |
| GET | `/api/playlists` | List saved playlists |
| GET | `/api/playlists/{id}` | Playlist detail with ordered track list |

### Export

| Method | Path | Description |
|---|---|---|
| POST | `/api/exports/validate` | In-process validation — returns stats, excluded tracks, warnings |
| POST | `/api/exports/run` | Dispatch `rekordbox-export` job; returns job ID |
| GET | `/api/exports` | List past export jobs |
| GET | `/api/exports/{id}` | Single export job by ID |

### SSD Sync

| Method | Path | Description |
|---|---|---|
| GET | `/api/sync/config` | Source paths, SSD destination, mount status |
| POST | `/api/sync/preview` | Dry-run rsync; returns file list (up to 500 entries) |
| POST | `/api/sync/run` | Start live rsync job; returns job ID |
| GET | `/api/sync` | List past sync jobs |
| GET | `/api/sync/{id}` | Single sync job |

**Swagger UI:** `http://localhost:8000/docs`

### Job submission — allowed commands and flags

Only explicitly allowlisted commands and flags are accepted.

**Commands (18):** all `pipeline.py` subcommands — `audit-quality`, `dedupe`, `playlists`,
`analyze-missing`, `set-builder`, `rekordbox-export`, `metadata-clean`, `tag-normalize`,
`convert-audio`, `cue-suggest`, `harmonic-suggest`, `artist-folder-clean`,
`artist-merge`, `label-intel`, `label-clean`, `db-prune-stale`, `generate-docs`, `validate-docs`

**Boolean flags:** `--dry-run`, `--verbose`, `--strict`, `--force-xml`, `--force-cue-suggest`,
`--write-tags`, `--no-progress`, `--overwrite`, `--reanalyze`, `--skip-beets`, `--apply`

**Value flags:** `--report-format`, `--min-lossy-kbps`, `--workers`, `--format`

---

## Main Pipeline Flags

```bash
python3 pipeline.py [FLAGS]

  --dry-run                     Run all steps; write nothing
  --skip-beets                  Use Python filename parser (no Beets/MusicBrainz)
  --skip-analysis               [Legacy] Skip all BPM/key analysis
  --reanalyze                   Re-analyze library tracks missing BPM or key
  --force-cue-suggest           Enable cue suggestion (MIK owns cues by default)
  --label-enrich-from-library   Enrich label DB from library tag data
  --path DIR                    Override music root (use for SSD runs)
  --verbose, -v                 Debug-level logging
```

---

## Subcommands

<!-- COMMANDS:START -->
> Auto-generated from `modules/doc_registry.py`. Run `python3 pipeline.py generate-docs` to refresh.

### Library Maintenance

```bash
# Detect and quarantine duplicate audio files across the library.
python3 pipeline.py dedupe --dry-run

# Strip URL watermarks and promo junk from all metadata fields across the library.
python3 pipeline.py metadata-clean --dry-run

# Standardize MP3 ID3 tag format for Rekordbox (ID3v2.4 → ID3v2.3, remove ID3v1).
python3 pipeline.py tag-normalize --dry-run

# Detect BPM and key for tracks missing that data — writes to DB and audio tags.
python3 pipeline.py analyze-missing

# Audit library for codec/bitrate quality — classify into LOSSLESS/HIGH/MEDIUM/LOW/UNKNOWN.
python3 pipeline.py audit-quality

# Fix bad artist folder names across the library (Camelot prefixes, URL junk, symbols).
python3 pipeline.py artist-folder-clean --dry-run

# Merge artist folder spelling variants into a single canonical folder.
python3 pipeline.py artist-merge --dry-run

# Mark DB rows stale when the file no longer exists on disk.
python3 pipeline.py db-prune-stale --dry-run
```

### Audio Conversion

```bash
# Convert .m4a files to .aiff with parallel ffmpeg, preserving metadata and archiving originals.
python3 pipeline.py convert-audio --src PATH --dst PATH --archive PATH [FLAGS]
```

### Playlists and Export

```bash
# Generate all M3U playlists and Rekordbox XML from the library DB.
python3 pipeline.py playlists --dry-run

# Export library as Rekordbox-ready M3U playlists for Windows (Linux→Windows path mapping).
python3 pipeline.py rekordbox-export --dry-run
```

### Cues and Sets

```bash
# Auto-detect cue points (intro / drop / outro) and store in the DB.
python3 pipeline.py cue-suggest --dry-run

# Build an energy-curve DJ set from the library database and export as M3U + CSV.
python3 pipeline.py set-builder --dry-run

# Suggest the best next tracks using harmonic + BPM + energy scoring.
python3 pipeline.py harmonic-suggest --track "/music/sorted/Artist/track.mp3"
```

### Label Intelligence

```bash
# Scrape label metadata from Beatport / Traxsource and export to JSON/CSV/TXT/SQLite.
python3 pipeline.py label-intel

# Detect, normalize, and optionally write back label metadata (Phase 1: local).
python3 pipeline.py label-clean
```

<!-- COMMANDS:END -->

---

## Audit Quality — Quality Tiers

```
LOSSLESS   FLAC / ALAC / WAV / AIFF         (codec-based; bitrate irrelevant)
HIGH       lossy (MP3/AAC) ≥ 256 kbps
MEDIUM     lossy 192–255 kbps
LOW        lossy < 192 kbps                  (threshold: --min-lossy-kbps, default 192)
UNKNOWN    ffprobe failure or unrecognized codec
```

```bash
python3 pipeline.py audit-quality                                    # probe + classify + report
python3 pipeline.py audit-quality --move-low-quality /music/_low     # move LOW files
python3 pipeline.py audit-quality --write-tags                       # tag each file with tier
python3 pipeline.py audit-quality --dry-run --verbose                # preview only
```

---

## Recommended Workflows

### Standard ingest
```bash
# Drop files into inbox/, then:
python3 pipeline.py

# Refresh playlists after library changes
python3 pipeline.py playlists
```

### M4A → AIFF before ingest
```bash
python3 pipeline.py convert-audio \
    --src /downloads/m4a \
    --dst /music/inbox \
    --archive /mnt/music_ssd/originals_m4a
python3 pipeline.py
```

### SSD gig prep
```bash
python3 pipeline.py --path /mnt/music_ssd/KKDJ/
python3 pipeline.py rekordbox-export
```

### Fill missing BPM/key (after MIK analysis)
```bash
python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/
python3 pipeline.py rekordbox-export
```

### Library cleanup
```bash
python3 pipeline.py metadata-clean
python3 pipeline.py artist-folder-clean --apply
python3 pipeline.py artist-merge --apply
python3 pipeline.py tag-normalize
python3 pipeline.py db-prune-stale --path /mnt/music_ssd/KKDJ/
python3 pipeline.py playlists
```

### Set building
```bash
# 90-minute peak set (via CLI)
python3 pipeline.py set-builder --vibe peak --duration 90

# Deep house set with genre filter
python3 pipeline.py set-builder --vibe deep --genre "deep house" --start-energy Chill

# Or use the Set Builder page in the web UI
```

---

## Rollback

```bash
python3 scripts/rollback.py list               # list rollback-eligible tracks
python3 scripts/rollback.py rollback 42 --dry-run
python3 scripts/rollback.py rollback 42        # restore original tags
python3 scripts/rollback.py rollback 42 --restore-path  # also move file back to inbox
```

---

## Configuration

All paths are defined in `config.py`. Create `config_local.py` (git-ignored) to override any value:

```python
# config_local.py
MUSIC_ROOT = Path("/mnt/ssd/music")
WINDOWS_DRIVE_LETTER = "D"
```

### Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `DJ_MUSIC_ROOT` | `/music` | Music root directory |
| `RB_LINUX_ROOT` | `/mnt/music_ssd` | Linux path of the Windows DJ SSD |
| `RB_WIN_DRIVE` | `M` | Windows drive letter for SSD |
| `FFPROBE_BIN` | `ffprobe` | ffprobe binary path |
| `FFMPEG_BIN` | `ffmpeg` | ffmpeg binary path |
| `AUBIOBPM_BIN` | `aubiobpm` | aubio BPM binary |
| `KEYFINDER_BIN` | `keyfinder-cli` | Key detection binary |
| `RMLINT_BIN` | `rmlint` | Duplicate detection binary |
| `BEET_BIN` | `beet` | Beets music organizer binary |

---

## Data and File Locations

### Pipeline outputs

| Path | Contents |
|---|---|
| `/music/library/sorted/` | Processed, organized library |
| `/music/playlists/m3u/` | M3U playlists (genre, energy, key, route) |
| `/music/playlists/xml/` | Rekordbox XML |
| `/music/logs/processed.db` | Pipeline SQLite database (tracks, history, runs) |
| `/music/logs/pipeline.log` | Full pipeline run log |
| `/music/logs/reports/audit_quality/` | Quality audit reports (CSV/JSON) |

### Backend

| Path | Contents |
|---|---|
| `backend/data/jobs.db` | Job-tracking SQLite database |
| `backend/data/logs/<job_id>.log` | stdout+stderr for each dispatched job |

Both `backend/data/` paths are git-ignored and created on first run.

### SSD export paths

| Path | Contents |
|---|---|
| `/mnt/music_ssd/KKDJ/_PLAYLISTS_M3U_EXPORT/` | SSD M3U export |
| `/mnt/music_ssd/KKDJ/_REKORDBOX_XML_EXPORT/` | SSD XML export |
| `/mnt/music_ssd/KKDJ/_SETS/` | DJ set M3U files |

All paths derive from `DJ_MUSIC_ROOT`. Override globally with `--path DIR`.

---

## Developer Setup

### direnv (auto-activate virtualenv)

```bash
sudo apt install direnv
echo 'eval "$(direnv hook bash)"' >> ~/.bashrc
source ~/.bashrc
direnv allow .   # once per repo clone
```

After setup, `cd`-ing into the project activates `.venv` automatically.

### Tab-completion for subcommands

```bash
# argcomplete is in requirements.txt — install if needed
pip install argcomplete

# Per-command (no root required):
eval "$(register-python-argcomplete pipeline.py)"

# Add to ~/.bashrc to persist across sessions
```

### Frontend dev

```bash
cd frontend
npm install
npm run dev          # Vite on :5173
npm run typecheck    # tsc --noEmit (no emit, just type errors)
npm run build        # production build to frontend/dist/
```

---

## Running Tests

```bash
# All tests
python3 -m pytest tests/ -v

# Specific file
python3 -m pytest tests/test_sanitizer.py -v
python3 -m pytest tests/test_audit_quality.py -v
python3 -m pytest tests/test_artist_merge.py -v

# Specific class
python3 -m pytest tests/test_sanitizer.py::TestSanitizeText -v
```

---

## MIK-First Safety Rules

| Data | Policy |
|---|---|
| BPM | Never written if already present in DB or file tags |
| Key (Camelot) | Never written if already present in DB or file tags |
| Cue points | Cue-suggest disabled by default; never overwrites existing cues |
| Rekordbox XML | Disabled by default; `--force-xml` required |
| M3U playlists | Always safe — does not affect MIK or Rekordbox state |

---

## Troubleshooting

**Backend not responding / frontend shows "Failed to fetch"**
The FastAPI server is not running.
```bash
source .venv/bin/activate
uvicorn backend.app.main:app --reload --port 8000
```
Then reload the browser.

**Vite dev server shows `ECONNREFUSED` in the network tab**
Same cause as above — the backend proxy target (`:8000`) is unreachable.
Start the backend first, then start or reload the frontend.

**`ModuleNotFoundError: No module named 'mutagen'` (or other package)**
The virtualenv is not active or `requirements.txt` hasn't been installed.
```bash
source .venv/bin/activate
pip install -r requirements.txt
pip install -r backend/requirements.txt
```

**Empty or null artist/BPM/key in Tracks page**
These fields are populated by the pipeline. If they show `—`:
- The track was never processed through the full pipeline, or
- BPM/key were skipped because MIK hasn't analysed the file yet.
Run `python3 pipeline.py analyze-missing` (or submit it via the Jobs page) to fill gaps.

**`backend/data/jobs.db` not found on first run**
Normal — the file is created automatically when the backend starts for the first time.
The `backend/data/` directory must be writable. It is git-ignored.

**Log file path confusion (`/music/logs/pipeline.log` vs `backend/data/logs/`)**
There are two separate log stores:
- `/music/logs/pipeline.log` — written by `pipeline.py` directly; covers full ingest runs
- `backend/data/logs/<job_id>.log` — written by the backend when you submit a job via the web UI or API; one file per job

**SSD mount warnings in Sync page**
The SSD path (`/mnt/music_ssd/KKDJ`) is checked on every preview/run request.
If the badge shows "not mounted", connect the drive and mount it:
```bash
# Check what's available
ls /mnt/
# Mount the drive (adjust device name as needed)
sudo mount /dev/sdb1 /mnt/music_ssd
```

---

## Next Steps

- Rekordbox hot-cue write-back (blocked by MIK-first policy; requires mapping cues from DB to XML without overwriting existing POSITION_MARK entries)
- Settings page (wire up config.py overrides via the web UI)
- SIGKILL fallback for jobs that ignore SIGTERM
- SSE / WebSocket streaming for live log tails (currently polled every 2s)
- Discogs and Beatport label data providers
- Per-run config snapshots in `pipeline_runs` table
- Quality-tier playlists (LOW.m3u8 / HIGH.m3u8 under `M3U_DIR/Quality/`)
