# CrateMindAI

A local-first DJ library automation toolkit. Transforms messy audio downloads into a clean, structured, Rekordbox-ready library using a hybrid approach: deterministic metadata cleaning, AI-assisted normalization, and multi-source enrichment.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-orange?style=flat-square)](https://ubuntu.com/)
[![Ollama](https://img.shields.io/badge/AI-Ollama-black?style=flat-square)](https://ollama.com/)

---

## Pipeline

```
Raw audio downloads
        │
        ▼
┌─────────────────────┐
│  metadata-sanitize  │  offline, deterministic cleaning
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│    ai-normalize     │  local AI proposals via Ollama
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ artist-intelligence │  alias + identity resolution
└─────────────────────┘
        │
        ▼
┌──────────────────────────┐
│  metadata-enrich-online  │  Spotify / Deezer scoring
└──────────────────────────┘
        │
        ▼
┌─────────────────────┐
│ label-intelligence  │  label parsing + enrichment (evolving)
└─────────────────────┘
        │
        ▼
  Rekordbox-ready output
```

Each stage is standalone. Run one, or compose the full pipeline.

---

## Safety Philosophy

**Preview by default.** Nothing writes without `--apply`.

| Principle | Detail |
|---|---|
| Deterministic before AI | Rules clean first; AI fills gaps |
| AI before enrichment | Identity normalized before online lookup |
| Enrichment only when proven | No guessing on artist mismatches or version conflicts |
| MIK-first | BPM, key, and cue data are never modified (Mixed In Key owns these) |
| Idempotent | Safe to re-run at any stage |
| Full audit log | Every change logged with before/after values |

### Enrichment operational states

| State | Condition | Action |
|---|---|---|
| **APPLY** | conf ≥ 0.80, all safety rules pass | Written with `--apply` |
| **REVIEW** | 0.70 ≤ conf < 0.80 | Added to review queue |
| **SKIP** | Hard safety block fires | Moved to IGNORED with `--move-ignored` |

Hard blocks (always enforced regardless of confidence):
- Artist field: **never proposed**
- Version mismatch: conflicting version tokens → cap at 0.74
- Low artist similarity (< 0.90, no ISRC anchor): cap at 0.74
- ISRC exact match: overrides formula → confidence 0.98

IGNORED quarantine path: `/home/koolkatdj/Music/music/IGNORED/`

---

## Current Status

| Stage | Status |
|---|---|
| metadata-sanitize | Production-ready |
| ai-normalize | Production-ready |
| artist-intelligence | Production-ready |
| metadata-enrich-online | Production-ready |
| label-intelligence | In development |
| review-queue CLI | Production-ready |
| Web app (jobs + library) | Production-ready |
| Web app (Collection workspace) | UI only — CLI is source of truth |

---

## Installation

### Requirements

- Python 3.10+
- Linux (Ubuntu Studio 24 recommended)
- [Ollama](https://ollama.com/) — for AI features
- External tools: `ffprobe`, `ffmpeg`, `aubio`, `keyfinder-cli` (for analysis)

### Setup

```bash
git clone https://github.com/fgyamerah/CrateMindAI.git
cd CrateMindAI

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Ollama (for ai-normalize)

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve
ollama pull qwen2.5-coder:3b
```

### Spotify credentials (for metadata-enrich-online)

```bash
export SPOTIFY_CLIENT_ID=your_id
export SPOTIFY_CLIENT_SECRET=your_secret
```

---

## Core Commands


<!-- COMMANDS:START -->

### 1. metadata-sanitize

Deterministic offline cleaning of all metadata fields.

```bash
# Preview (no writes)
python3 pipeline.py metadata-sanitize --input ~/Music/inbox

# Apply
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

### 2. ai-normalize

Local AI (Ollama) metadata proposals for artist, title, version, label, remixers, and featured artists.

```bash
# Preview
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize

# Apply
python3 pipeline.py ai-normalize --input ~/Music/inbox --pre-sanitize --apply
```

### 3. artist-intelligence

Deterministic artist normalization, alias resolution, and identity consistency across the library.

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
```

### 4. metadata-enrich-online

Fill missing album, label, and ISRC via Spotify + Deezer matching with confidence scoring.

```bash
# Preview
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox

# Apply (with IGNORED quarantine for unresolvable files)
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply --move-ignored
```

### 5. review-queue

Review and resolve medium-confidence enrichment results interactively.

```bash
python3 pipeline.py review-queue
python3 pipeline.py review-queue --list-only
```

> Full reference: [COMMANDS.md](COMMANDS.md) | [COMMANDS.html](COMMANDS.html)

<!-- COMMANDS:END -->

---

## Additional Commands

```bash
# Library maintenance
python3 pipeline.py dedupe --dry-run
python3 pipeline.py analyze-missing
python3 pipeline.py audit-quality
python3 pipeline.py artist-merge --apply
python3 pipeline.py tag-normalize

# Audio conversion
python3 pipeline.py convert-audio --src /downloads/m4a --dst /music/inbox --archive /archive

# Playlists and sets
python3 pipeline.py playlists
python3 pipeline.py set-builder --vibe peak --duration 90
python3 pipeline.py harmonic-suggest --key 8A --bpm 128
```

Full command reference: [COMMANDS.txt](COMMANDS.txt) | [COMMANDS.html](COMMANDS.html)

---

## Project Structure

```
CrateMindAI/
├── pipeline.py
├── config.py
├── db.py
│
├── modules/            Core pipeline modules
├── ai/                 AI normalize (Ollama interface)
│
├── intelligence/
│   ├── artist/         Artist normalization + alias store
│   ├── enrichment/     Online metadata enrichment
│   └── label/          Label intelligence (evolving)
│
├── backend/            FastAPI web backend
├── frontend/           React + Vite web UI
├── data/intelligence/  Dataset logs (JSONL)
└── tests/
```

---

## Testing

```bash
python3 -m pytest tests/ -v
python3 -m pytest tests/test_artist_intelligence.py -v
```

---

## Philosophy

- **Local-first** — your data stays on your machine; no mandatory cloud
- **Deterministic + AI hybrid** — rules run first, AI fills the gaps
- **No silent overwrites** — every change is visible, logged, and reversible
- **Reproducible** — idempotent by design; re-running produces no drift

---

## Author

**fgyamerah** — [github.com/fgyamerah](https://github.com/fgyamerah)

---

## License

[MIT](LICENSE)
