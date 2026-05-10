# Documentation Maintenance Policy

## Goal

Prevent safety documentation drift. Any change that affects command behavior, metadata writes, file movement, DB schema, queues, or rollback must update docs in the same work session.

## Mandatory Update Triggers

| Trigger | Required docs |
|---|---|
| New CLI command/subcommand | `COMMAND_RISK_MATRIX.md`, `STABILITY_MATRIX.md`, generated command docs |
| New destructive operation | `SAFETY_GAP_AUDIT.md`, `COMMAND_RISK_MATRIX.md`, `ROLLBACK_AND_RECOVERY.md` |
| New tag write or metadata field mutation | `METADATA_OWNERSHIP_MATRIX.md`, `SAFETY_MODEL.md`, command docs |
| New file rename/move/delete behavior | `FILESYSTEM_DB_CONSISTENCY_AUDIT.md`, `COMMAND_RISK_MATRIX.md`, `ROLLBACK_AND_RECOVERY.md` |
| Schema/table/index change | `DB_SCHEMA_AUDIT.md`, generated SQL indexes/dumps, consistency audit |
| Queue schema/lifecycle change | `SAFETY_GAP_AUDIT.md`, consistency audit, operator playbook |
| Confidence threshold or hard guard change | `SAFETY_MODEL.md`, `SAFETY_GAP_AUDIT.md`, context docs |
| Rollback or history change | `ROLLBACK_AND_RECOVERY.md`, `SAFETY_GAP_AUDIT.md` |
| Backend job/sync behavior change | command risk matrix, stability matrix, operator playbook |
| AI provider/model policy change | `SAFETY_MODEL.md`, `METADATA_OWNERSHIP_MATRIX.md`, `PROJECT_CONTEXT.md` |

## Required Review Before Merge

- Regenerate static analysis docs if command/schema/safety logic changed.
- Verify all destructive commands are classified.
- Verify all default-writing commands are called out.
- Verify rollback limitations are explicit.
- Mark unknown behavior as `UNVERIFIED`.
- Mark conflicting behavior as `CONFLICTING_IMPLEMENTATIONS`.

## Drift Controls

- Keep `PROJECT_CONTEXT.md` concise and link to detailed docs instead of duplicating them.
- Prefer generated indexes as future AI input.
- Do not edit safety claims from memory when source/docs conflict.
- If exact implementation is unclear, document uncertainty instead of guessing.

