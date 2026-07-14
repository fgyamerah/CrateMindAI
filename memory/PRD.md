# CrateMindAI — PRD & Progress Memory

## Original problem statement
Audit, redesign, refactor, and improve the existing CrateMindAI repository
(https://github.com/fgyamerah/CrateMindAI): a local-first DJ music-library
operations platform (Python pipeline + FastAPI backend + React/Vite/TS frontend
+ SQLite). Preserve the working backend, tested domain logic, dry-run-first
safety guarantees, and local-first philosophy. Transform fragmented specialist
pages into one coherent product journey (Home → Library → Fix & Review → Sets →
Publish → Operations). Full spec includes phases A–G (foundation, home, library
workspace, unified review center, sets/publish, operations, hardening).

## User choices
- Work approved in milestones; ONLY Phases A+B approved and delivered so far.
- Fixture library only (never touch/mutate a real music library).
- LLM/AI is optional — user can configure a local LLM (Ollama) in settings;
  never a requirement. Do NOT wire Emergent LLM key.
- No auth by design (trusted-local-only) — not a bug.
- Repo cloned from GitHub main (fc8e863) into /app; platform git manages
  commits; push via "Save to Github".

## Architecture (current)
- Pipeline: /app/pipeline.py + modules/, ai/, intelligence/, utils/ (untouched)
- Backend: /app/backend/app (FastAPI). Served in preview by
  /app/backend/server.py shim (loads backend/.env) on port 8001.
- Frontend: /app/frontend (React 18 + Vite + TS + TanStack Query), port 3000,
  /api proxied to 8001.
- Pipeline DB: <library_root>/logs/processed.db ; jobs DB: backend/data/jobs.db
- Env (backend/.env): CRATEMINDAI_LIBRARY_ROOT=/app/fixture_library,
  DJ_MUSIC_ROOT=/app/fixture_library, CRATEMINDAI_SYNC_SOURCE_LIBRARY,
  CRATEMINDAI_SYNC_SOURCE_INBOX, CRATEMINDAI_SYNC_DEST=/app/fixture_ssd/KKDJ
- Fixture seeder: scripts/seed_fixture_library.py (deterministic, 310 tracks,
  real tiny MP3s + ID3, issue coverage, real repair/sanitation/path-audit scans)

## What's been implemented (dates)
- 2026-07-14 Phase A: repo ported to /app; supervisor runtime adaptation;
  GET /api/runtime/preflight (read-only readiness; ready|degraded|unsafe);
  env-overridable sync paths; fixture seeder; 5 new backend tests (862 total);
  design tokens (frontend/src/styles/tokens.css, Archivo + JetBrains Mono,
  graphite + electric-cyan); unified status model (src/lib/status.ts).
- 2026-07-14 Phase B: responsive AppShell (collapsible sidebar, mobile drawer,
  skip link, reduced motion); consolidated IA nav (Overview/Library/Fix &
  Review/Sets/Publish/Operations); command-center Home at '/' (next-action
  banner via pure computeNextAction engine, metrics that render "Unavailable"
  on fetch failure — never fake zeros, readiness panel, coverage bars,
  attention action-cards, recent jobs); library workspace moved '/'→'/library'
  with all legacy redirects; 11 Vitest unit tests; router future flags.
- Testing: iteration_1.json — 100% pass backend + frontend e2e, no bugs.
  Testing agent's backend e2e script: /app/backend/tests/backend_test.py.

## Test/build status (2026-07-14)
- python3 -m pytest -q → 862 passed
- frontend: tsc --noEmit OK; vitest 11 passed; production build OK

## Safety guarantees preserved
- Dry-run-first, confirm=true apply gates, allowlisted jobs, read-only pipeline
  DB access in backend, path containment, planning-only reconciliation,
  MIK ownership of BPM/key/cues. No new write behavior added.
- Preflight never exposes secret values (presence booleans only).

## Prioritized backlog
- P1 Phase C: Library workspace refactor (split CrateMind.tsx 1373 lines,
  virtualized table, URL filter state, saved views, keyboard nav, inspector)
- P1 Phase D: Unified Fix & Review center (repair/sanitation/enrichment/BPM in
  one queue UI, lifecycle labels via src/lib/status.ts, diff viewer, safe bulk
  approve with confirmation summary, dry-run/apply distinction)
- P2 Phase E: Sets improvements + guided Publish workflow (export readiness →
  dry-run → export → sync preview → typed confirm → verify)
- P2 Phase F: duplicates review API/UI (quarantine-first; duplicate_groups
  table exists, no API yet), orphan-scan exposure, quarantine browser, job
  improvements, notification center, Settings page (library root + optional
  local LLM/Ollama config)
- P2 Phase G: WCAG 2.2 AA pass, responsive tables, large-library perf,
  e2e fixture tests, docs regeneration
- Known repo debt: 'DJ Toolkit' naming remains in some docs/files; old
  Dashboard/Collection/Tracks/Settings pages remain in tree (redirected);
  API list endpoints lack common envelope (adapters planned Phase D+).

## Notes for next session
- Never run apply/sync/export with confirm against a real library.
- Re-seed fixture: python3 scripts/seed_fixture_library.py --force
- BPM anomalies summary is empty until POST /api/analysis/bpm-check is run.
- Update CHANGELOG.txt / PROJECT_CONTEXT.md / NEXT_TASKS.txt after each phase
  (repo rule from AGENTS.md).
