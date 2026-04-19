<div align="center">

# 🎧 CrateMindAI

**Your DJ library, finally under control.**

*A local-first metadata intelligence engine for serious music collections.*

CrateMindAI transforms messy downloads into a **clean, structured, Rekordbox-ready library** using a hybrid approach — deterministic metadata cleaning, AI-assisted normalization, and multi-source enrichment.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Platform: Linux](https://img.shields.io/badge/Platform-Linux-orange?style=flat-square&logo=linux&logoColor=white)](https://ubuntu.com/)
[![Ollama](https://img.shields.io/badge/AI-Ollama-black?style=flat-square)](https://ollama.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

</div>

---

## ✨ Why CrateMindAI

**Rules handle the obvious. AI handles the rest.**

Before any AI runs, your files are cleaned using strict deterministic rules:

- Remove junk metadata (URLs, emails, DJ pool tags)
- Fix broken tags and malformed ISRCs
- Normalize structure safely

Then AI refines what remains — with strict safeguards. No hallucinated credits, no silent overwrites, no surprise.

---

## 🛡️ Safety First

CrateMindAI is **not** an automation tool. It is a **decision-support system**.

| Safeguard | Detail |
|---|---|
| Preview mode | Default on every command — nothing writes without `--apply` |
| Confidence thresholds | AI suggestions below threshold are skipped, not applied |
| Audit log | Every change is logged with before/after values |
| Idempotent | Re-running produces no drift — safe to run repeatedly |

---

## ⚙️ Pipeline

```
Raw audio downloads
        │
        ▼
┌─────────────────────┐
│  metadata-sanitize  │  ← offline, deterministic cleaning
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│    ai-normalize     │  ← local AI suggestions via Ollama
└─────────────────────┘
        │
        ▼
┌─────────────────────┐
│ artist-intelligence │  ← alias + identity resolution
└─────────────────────┘
        │
        ▼
┌──────────────────────────┐
│  metadata-enrich-online  │  ← Spotify / Deezer scoring
└──────────────────────────┘
        │
        ▼
┌─────────────────────┐
│ label-intelligence  │  ← label parsing + enrichment
└─────────────────────┘
        │
        ▼
  Rekordbox-ready output
```

> Each stage is standalone. Run one, or compose the full pipeline.

---

## 🚀 Features

| Area | Capability |
|---|---|
| 🧼 **Sanitize** | Removes junk tags, invalid ISRCs, promo text |
| 🤖 **AI Normalize** | Local LLM improves artist / title / version fields |
| 🧠 **Artist Intelligence** | Alias resolution + identity consistency |
| 🔎 **Enrichment** | Spotify + Deezer matching with confidence scoring |
| 🏷️ **Label Intelligence** | Beatport / Traxsource label extraction |
| 🎚️ **Quality Audit** | Classify tracks: `LOSSLESS` / `HIGH` / `MEDIUM` / `LOW` |
| 📁 **Export** | Rekordbox XML + M3U playlists |

---

## 🧪 Installation

### Requirements

- Python 3.10+
- Linux (Ubuntu recommended)
- [Ollama](https://ollama.com/) — for AI features

### Setup

```bash
git clone https://github.com/fgyamerah/CrateMindAI.git
cd CrateMindAI

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Install Ollama

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama serve
ollama pull qwen2.5-coder:3b
```

---

## 🔧 Usage

### 1. Clean metadata (safe, offline)

Preview first — nothing writes without `--apply`:

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox
```

Apply changes:

```bash
python3 pipeline.py metadata-sanitize --input ~/Music/inbox --apply
```

### 2. AI normalize

```bash
python3 pipeline.py ai-normalize --input ~/Music/inbox --apply
```

### 3. Full pipeline (recommended)

```bash
python3 pipeline.py ai-normalize \
  --input ~/Music/inbox \
  --pre-sanitize \
  --apply
```

### 4. Continue pipeline

```bash
python3 pipeline.py artist-intelligence --input ~/Music/inbox --apply
python3 pipeline.py metadata-enrich-online --input ~/Music/inbox --apply
python3 pipeline.py rekordbox-export
```

---

## 📸 Demo

> *Add GIFs here to show the pipeline in action.*

| Stage | Preview |
|---|---|
| Sanitize | `docs/gifs/sanitize.gif` |
| AI Normalize | `docs/gifs/ai-normalize.gif` |
| Web UI | `docs/gifs/ui.gif` |

---

## 🧪 Testing

```bash
# Generate test fixtures
python3 tests/create_sanitize_fixtures.py

# Run sanitize against fixtures
python3 pipeline.py metadata-sanitize \
  --input tests/fixtures/metadata_sanitize \
  --apply
```

---

## 📁 Project Structure

```
CrateMindAI/
├── pipeline.py
├── config.py
├── db.py
│
├── modules/
│   └── metadata_sanitize.py
│
├── ai/
│   └── normalizer.py
│
├── intelligence/
│   ├── artist/
│   ├── label/
│   └── enrichment/
│
└── tests/
```

---

## 🧭 Roadmap

- [ ] Cue points (Mixed In Key integration)
- [ ] Set / playlist builder
- [ ] SSD sync automation
- [ ] Label graph intelligence
- [ ] Improved scoring engine

---

## 🧠 Philosophy

- **Local-first** — your data stays on your machine
- **Deterministic + AI hybrid** — rules run first, AI fills the gaps
- **No silent data corruption** — every change is visible and reversible
- **Reproducible results** — idempotent by design

---

## 👤 Author

**fgyamerah** — [github.com/fgyamerah](https://github.com/fgyamerah)

---

## 📜 License

[MIT](LICENSE) — free to use, modify, and distribute.
