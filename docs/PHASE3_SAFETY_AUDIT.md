# Phase 3 Safety Audit

**Date:** 2026-05-05  
**Scope:** Documentation and light static verification only. No runtime behavior was changed during this audit.

## Verified Guarantees

### Dry-run and apply gates

The following write-capable CLI commands are now guarded by the central `assert_apply_mode(args)` helper in `pipeline.py`:

| Command | Default mode | Apply gate | Confirmation required | Status |
|---|---|---:|---:|---|
| `metadata-clean` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `tag-normalize` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `analyze-missing` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `convert-audio` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `cue-suggest` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `db-prune-stale` | dry-run | `--apply` | `--yes` or `--force` | guarded |
| `review-queue` | dry-run/list-only | `--apply` | `--yes` or `--force` | guarded |

The helper rejects ambiguous `--apply --dry-run` usage and prints a standard mode banner:

- `MODE: DRY-RUN`
- `MODE: APPLY`

### Central path update helper

`db.update_track_path_references(old_path, new_path, context)` is present and verified to:

- Resolve both paths under the active `config.MUSIC_ROOT`.
- Skip updates when the old path is not present in either `tracks` or non-stale `processed_state`.
- Skip updates when the new path already exists in `tracks`.
- Update `tracks.filepath` and `tracks.filename`.
- Update only non-stale `processed_state` rows.
- Leave `processed_state.status = 'stale'` rows untouched.
- Run inside a SQLite context-managed transaction.
- Return structured result data and log the path update or skip reason.

The Phase 3 path-moving modules now call this helper:

- `modules/artist_merge.py`
- `modules/artist_folder_clean.py`
- `modules/library_organize.py`

### Transactional DB writes

Verified transactional write areas:

- `db.update_track_path_references()` uses a single DB context for tracks and processed-state path updates.
- `path-reconcile --apply-auto-safe-only` explicitly uses `BEGIN`, `commit()`, and `rollback()`.
- `path-reconcile --mark-stale-pstate` explicitly uses `BEGIN`, `commit()`, and `rollback()`.
- `build-tracks` uses a DB transaction to upsert `tracks` from valid non-stale `processed_state`.

### Stale-row protection

Verified:

- Central path updates exclude `processed_state` rows where `lower(status) = 'stale'`.
- `path-audit` excludes stale processed-state rows from active current-state counts.
- `build-tracks` excludes stale processed-state rows.
- Stale processed-state marking changes only `status` and `reason`; it does not change `filepath`.

### Root isolation

Verified for root-scoped Phase 3 commands:

- `path-audit --root <root>` resolves an absolute selected root.
- `path-reconcile --root <root>` uses the same selected-root guard.
- `build-tracks --root <root>` reads `<root>/logs/processed.db`.
- Logs for these commands are written under `<root>/logs/`.
- DB paths outside the selected root are reported as mixed-root findings rather than treated as active current paths.
- Reconcile apply helpers reject old/new paths outside the selected root.

## Unverified Areas

The following areas were not fully source-audited in this pass:

- Backend write paths and `jobs.db` transaction behavior.
- Frontend-triggered workflows.
- Generated docs under `docs/generated/`; these may be stale relative to current source.
- Every optional write flag outside the Phase 3 hardening set.
- All tag-write internals in enrichment, artist repair, and sanitizer rollback flows.

## Remaining Risks

### Legacy organizer delete/reinsert pattern remains

`modules/organizer.py` still contains a legacy path mutation pattern:

- move file
- `db.upsert_track(new_path, ...)`
- `DELETE FROM tracks WHERE filepath = old_path`

This means the statement "no delete/reinsert DB patterns remain" is not globally true. The Phase 3-refactored modules use `update_track_path_references`, but the legacy main pipeline organizer still needs migration.

### Not every write-capable command requires confirmation

The seven commands hardened in the latest CLI pass require `--apply` plus confirmation. Other commands generally have preview/default apply gates, but not all require `--yes` or `--force`.

Examples needing future standardization:

- `metadata-sanitize`
- `filename-normalize`
- `library-organize`
- `artist-merge`
- `artist-folder-clean`
- `dedupe`
- `orphan-scan`
- `artist-intelligence`
- `ai-normalize`
- `metadata-enrich-online`
- `artist-repair`
- `artist-repair-review`
- `audit-quality` optional write flags

### Root isolation is partial

Root isolation is stable for `path-audit`, `path-reconcile`, and `build-tracks`, but several older commands still depend on global `config.MUSIC_ROOT`, `config.DB_PATH`, `config.LOGS_DIR`, or command-specific paths. See `docs/audits/ROOT_ISOLATION_AUDIT.md`.

### Reconciliation apply is partial

`path-reconcile --apply` is intentionally not implemented. Current apply-like modes are limited to:

- `--apply-auto-safe-only`: updates `processed_state.filepath` for classified auto-safe candidates.
- `--mark-stale-pstate`: marks superseded processed-state rows stale.

Neither mode moves files, updates queues, deletes rows, or writes tags.

## Write-Capable Command Safety Status

| Command | Safety status | Notes |
|---|---|---|
| `metadata-clean` | guarded | Dry-run default; `--apply` requires confirmation. |
| `tag-normalize` | guarded | Dry-run default; `--apply` requires confirmation. |
| `analyze-missing` | guarded | Dry-run default; `--apply` requires confirmation. |
| `convert-audio` | guarded | Dry-run default; `--apply` requires confirmation; no deletes, archives originals. |
| `cue-suggest` | guarded | Dry-run default; `--apply` requires confirmation. |
| `db-prune-stale` | guarded | Dry-run default; `--apply` requires confirmation. |
| `review-queue` | guarded | Dry-run maps to list-only; interactive mutation requires confirmation. |
| `path-audit` | read-only | Writes reports only. |
| `path-reconcile` | partial | Plan mode safe; limited apply helpers only; full `--apply` not implemented. |
| `build-tracks` | DB-write command | Populates `tracks`; excludes stale/missing files; does not alter `processed_state`. |
| `artist-merge` | partially guarded | Preview/apply split; uses central path update helper after moves. |
| `artist-folder-clean` | partially guarded | Preview/apply split; uses central path update helper after moves. |
| `library-organize` | partially guarded | Preview/apply split; uses central path update helper after moves. |
| `filename-normalize` | partially guarded | Preview/apply split; path updates are not globally reconciled beyond processed-state tracking. |
| `dedupe` | partially guarded | Preview/apply split; quarantine workflow lacks universal restore manifest. |
| `orphan-scan` | partially guarded | Preview/apply split; marks stale rows only. |
| `metadata-sanitize` | partially guarded | Preview default; sanitizer-specific rollback exists for logged changes. |
| `artist-intelligence` | partially guarded | Preview/apply split; writes artist tags above confidence gate. |
| `ai-normalize` | partially guarded | Preview/apply split; local AI output constrained by ownership rules. |
| `metadata-enrich-online` | partially guarded | Preview/apply split; review queue and confidence gates present. |
| `artist-repair` | partially guarded | Preview/apply split; review/quarantine paths remain high-risk. |
| `artist-repair-review` | manual guarded | Writes only with `--apply-approved`; queue state must be reviewed. |
| `audit-quality` | partially guarded | Read-only by default, but optional move/tag flags need confirmation standardization. |

## Safest Next Steps

1. Migrate `modules/organizer.py` to `update_track_path_references`.
2. Standardize `--apply --yes` confirmation across all remaining write-capable commands.
3. Extend selected-root resolution to older commands that still use global config paths.
4. Add a lightweight static test that fails on future `DELETE FROM tracks` path-mutation patterns outside approved maintenance commands.
5. Keep `path-reconcile --apply` unimplemented until queue/cue/set/reference updates have full planning and rollback coverage.
