# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

# 🔴 PRIMARY EXECUTION RULE (CRITICAL)

Claude MUST operate in **controlled, scoped mode**.

### NEVER:

* Explore the full codebase unless explicitly instructed
* Traverse directories automatically
* Read files not explicitly listed by the user
* Load unnecessary context “for understanding”
* Re-read the same file multiple times

### ALWAYS:

* Work only on explicitly specified files
* Ask before expanding scope
* Minimize token usage
* Prefer precision over coverage

If a task requires broader context:
→ STOP and ask for permission

---

# 🧠 WORKING MODES

Claude operates in ONE mode per task:

## 1. READ MODE (default)

* Read ONLY files explicitly listed
* Do NOT discover additional files
* Do NOT infer architecture beyond given files

## 2. MODIFY MODE

* Only modify specified files
* Preserve structure and conventions
* Do NOT refactor unrelated logic

## 3. EXPLORE MODE (RARE — must be explicitly requested)

Allowed ONLY if user explicitly says: **“explore codebase”**

When enabled:

* Limit to specified directories
* Stop after minimal understanding
* Summarize before proceeding

If mode is unclear → default to READ MODE

---

# ⚠️ TOKEN DISCIPLINE RULES

* Target <10k tokens per operation
* Avoid multi-file reads unless required
* Never load entire modules unless necessary
* Do not expand context speculatively

If token usage may exceed ~20k:
→ STOP and ask for confirmation

---

# 🧩 FILE SCOPE RULE

Claude MUST follow strict scope:

* Only read files explicitly provided
* Only analyze logic relevant to the task
* If additional files are needed:
  → ASK instead of searching

---

# 🔁 SESSION HOUSEKEEPING (REQUIRED)

At the end of every session where you changed code, fixed a bug, added a feature, or completed a task, you MUST update the required documentation set before finishing:

### CHANGELOG.txt

Add an entry at the top:
[YYYY-MM-DD] — Short title describing what changed

* What changed and why
* Files affected
* Migration notes (if any)

### NEXT_TASKS.txt

* Mark completed tasks [x]
* Add new follow-ups
* Update [~] for in-progress tasks

### PROJECT_CONTEXT.md

Update any sections where:

* architecture changed
* CLI behavior changed
* DB schema changed
* config keys changed
* known issues changed

### PROJECT_CONTEXT.txt

Keep the plain-text mirror aligned with `PROJECT_CONTEXT.md`.

### README.md

Update the high-level direction when architecture or workflow changed.

### commands.md / COMMANDS.txt / COMMANDS.md / COMMANDS.html

Update command references when startup, CLI, backend, or workflow commands change.

### docs/architecture/HUMAN_REVIEW_MODEL.md

Update when review queues, editable proposals, or DB-only apply behavior changes.

### docs/architecture/UI_WORKFLOW_MODEL.md

Update when navigation, routing, or review surfaces change.

### docs/operations/SESSION_COMPLETION_CHECKLIST.md

Keep the completion checklist aligned with the current documentation workflow.

### AGENTS.md

Update Codex/OpenAI guidance when operating rules or architecture change.

Do NOT update these files if session was read-only.

---

# 🧱 PROJECT OVERVIEW

CrateMindAI is a local-first, human-guided DJ library metadata operations workstation.

It turns a messy DJ library into a reviewed, auditable, current-state metadata
system with deterministic repair, sanitation, and manual correction workflows.

Current architecture:

* `pipeline.py` CLI router
* SQLite `logs/processed.db`
* canonical `tracks` current-state table
* `processed_state` history/audit table
* FastAPI backend
* React/Vite frontend
* metadata repair workflow
* metadata sanitation workflow
* manual metadata editor
* issue routing and review queues
* reconciliation ledger / validation

The user controls architecture and scope. If a task requires broader context,
STOP and ask for permission.

Runs on Linux → outputs to Windows-compatible DJ drive.

---

# 🎧 MIXED IN KEY — HARD RULE (NON-NEGOTIABLE)

Mixed In Key (MIK) is the authoritative source for:

* BPM
* Key
* Cue points

### NEVER:

* Overwrite existing BPM
* Overwrite existing key
* Overwrite cue points
* Re-analyze if data already exists

### ALWAYS:

1. Check DB and file tags first
2. Preserve existing values
3. Only fill missing data

Use:

* `_read_existing_analysis()` helper

### XML:

* Disabled by default
* Only use `--force-xml` if MIK is not used

### M3U:

* Always safe

---

# ⚙️ COMMANDS

## Current App

Backend startup:

```bash
export CRATEMINDAI_LIBRARY_ROOT=/mnt/music_ssd/KKDJ
uvicorn backend.app.main:app --reload --port 8000
```

Frontend startup:

```bash
cd frontend
npm run dev -- --host 127.0.0.1
```

Frontend build:

```bash
cd frontend
npm run build
```

Backend tests:

```bash
./.venv/bin/python -m pytest tests/test_backend_api.py -q
```

Important CLI commands:

* `path-audit`
* `path-reconcile`
* `build-tracks`
* `extract-track-metadata`
* `metadata-score-online`
* `metadata-repair-scan`
* `metadata-repair-apply`
* `metadata-sanitation-scan`
* `metadata-sanitation-apply`
* `enrichment-apply-approved`

## Pipeline

python3 pipeline.py
python3 pipeline.py --dry-run
python3 pipeline.py --skip-beets
python3 pipeline.py --skip-analysis
python3 pipeline.py --force-cue-suggest
python3 pipeline.py --path /mnt/music_ssd/KKDJ
python3 pipeline.py --reanalyze

## Tests

python3 -m pytest tests/ -v
python3 -m pytest tests/test_sanitizer.py -v
python3 -m pytest tests/test_sanitizer.py::TestSanitizeText -v
python3 -m unittest tests.test_sanitizer -v

## Install

pip install -r requirements.txt
pip install -r requirements.txt pytest

---

# 🔧 SUBCOMMANDS

playlists
dedupe
cue-suggest
set-builder
harmonic-suggest
artist-folder-clean
artist-merge
metadata-clean
label-intel
rekordbox-export
rekordbox-export --force-xml
analyze-missing
convert-audio
audit-quality

---

# 🧬 ARCHITECTURE

## Entry Point

pipeline.py is the single entry point.

Pipeline steps (fixed order):
QC → dedupe → organize → sanitize → analyze → tag → cue → playlists → report

---

## Configuration

* config.py defines all paths
* NEVER hardcode paths
* Use config_local.py for overrides
* Support env variables

---

## Database

SQLite via db.py

Tables:

* tracks
* track_history
* pipeline_runs

All writes via:

* db.upsert_track()
* db.mark_status()

Idempotency:

* db.is_processed()
* TXXX:PROCESSED=1

---

## Modules

Each module:
run(files, run_id, dry_run) → files

Stateless and isolated.

Core modules:
qc.py
dedupe.py
organizer.py
sanitizer.py
analyzer.py
tagger.py
playlists.py
parser.py
cue_suggest.py
set_builder.py
harmonic.py
library_dedupe.py
rekordbox_export.py
analyze_missing.py
convert_audio.py
audit_quality.py
metadata_clean.py
artist_merge.py
artist_folder_clean.py

---

# 🚫 FORBIDDEN BEHAVIOR

Claude must NOT:

* Auto-explore repository
* Load unrelated files
* Rewrite large sections without instruction
* Introduce new dependencies without approval
* Break CLI compatibility
* Override MIK data
* Change architecture without approval

---

# ✅ SAFE BEHAVIOR

Claude SHOULD:

* Make minimal, surgical edits
* Preserve existing patterns
* Maintain backward compatibility
* Use deterministic logic
* Follow module boundaries

---

# 🧪 TESTING RULES

* Provide minimal test instructions
* Prefer targeted tests
* Avoid full test suite unless required

---

# 📦 OUTPUT FORMAT

When making changes, ALWAYS return:

1. Files changed
2. Code changes (diff or full function)
3. Short explanation
4. How to test safely

---

# 🧠 STRATEGIC GUIDELINE

User controls:

* architecture
* scope
* design

Claude provides:

* implementation
* debugging
* refinement

Claude is NOT autonomous.

---

# 🔧 OPTIONAL LLM USAGE

* Use utils/llm_client.py only if needed
* Do not introduce new APIs without approval
* Prefer local-first

---

# 🪵 PROMPT LOGGING

* Log prompts via utils/prompt_logger.py
* Maintain traceability

---

# 🧩 FINAL RULE

If unsure:

→ Ask instead of exploring
→ Ask instead of assuming
→ Ask instead of expanding scope

---
