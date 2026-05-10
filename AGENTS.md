# AGENTS.md

Guidance for Codex/OpenAI agents in this repository.

## Project Summary

CrateMindAI is a local-first, human-guided DJ library metadata operations workstation.
It combines a CLI pipeline, FastAPI backend, and React/Vite frontend around a
canonical SQLite `tracks` table, review queues, and DB-only metadata workflows.

## Current Architecture

* `pipeline.py` is the CLI router.
* `logs/processed.db` stores the canonical `tracks` table and historical `processed_state`.
* FastAPI serves the backend API.
* React/Vite serves the operational UI.
* Metadata Repair recovers missing/broken artist/title from filename/context.
* Metadata Sanitation removes junk/source/download contamination.
* Manual Metadata Editor handles safe human correction when heuristics cannot decide.
* Issue routing sends tracks to the right review surface.
* Reconciliation and validation record state without broad write automation.

## Non-Negotiable Safety Rules

* Never overwrite BPM, key, or cue data.
* Never write audio tags unless explicitly requested.
* Never rename or delete audio files unless explicitly requested.
* Treat `tracks` as the working metadata surface.
* Do not modify `processed_state` from repair, sanitation, or manual metadata workflows.
* Prefer DB-only operations first.
* Preserve human review before apply.

## Root Path Rule

* Default library root: `/mnt/music_ssd/KKDJ`
* Do not use `/home/koolkatdj/Music/music` unless the user explicitly requests it.

## Human Review Workflow

1. Detect an issue.
2. Generate a deterministic proposal.
3. Show the proposal in a queue or inspector.
4. Human approves, rejects, or defers at field level.
5. Apply writes to the DB only.
6. Record audit/log entries.

## Metadata Ownership

* Metadata Repair: recover missing or broken artist/title from filename/context.
* Metadata Sanitation: remove junk/source/download contamination.
* Manual Metadata Editor: human correction when heuristics cannot decide.
* Provider results and heuristics must never mutate metadata directly without review.

## Documentation Requirement

After any code, UI, backend, CLI, schema, or workflow change, update the required docs:

* `NEXT_TASKS.txt`
* `PROJECT_CONTEXT.md`
* `PROJECT_CONTEXT.txt`
* `README.md`
* `commands.md`
* `COMMANDS.txt`
* `COMMANDS.md`
* `COMMANDS.html`
* `CHANGELOG.txt`
* `CLAUDE.md` when agent guidance changes
* `AGENTS.md` when agent guidance changes

## Completion Checklist

* Update the relevant docs.
* Run the relevant tests/build.
* Run `git status --short`.
* Report files changed.
* Report docs updated.
* Report any risks or unverified areas.
* Report the next task.

## Standard Commands

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

## Current CLI Commands

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

## Scope Discipline

Obey explicit task scope. Do not expand scope without asking.
