# Session Completion Checklist

Use this checklist at the end of any task that changed documentation, code,
UI, backend routes, CLI behavior, schemas, or workflow logic.

## Required Documentation Updates

Update the docs that apply to the change:

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
* `docs/architecture/HUMAN_REVIEW_MODEL.md` when review flow changes
* `docs/architecture/UI_WORKFLOW_MODEL.md` when navigation/workflow changes

## Completion Steps

1. Update the relevant docs.
2. Run the relevant tests and/or build.
3. Run `git status --short`.
4. Report the files changed.
5. Report which docs were updated.
6. Report any risks or unverified areas.
7. Report the next task.

## Notes

* If the session was read-only, do not update the maintenance docs.
* If no markdown tooling exists in the repo, do not add new linting tools.
