# UI Workflow Model

CrateMindAI's UI is an operational workspace, not a general-purpose consumer app.
The navigation and inspector patterns are designed for fast review, safe routing,
and DB-only metadata operations.

## Core Screens

* Library
* Issues
* Quality
* Metadata Repair
* Metadata Sanitation
* Enrichment Queue
* Reconciliation
* Manual Metadata Editor modal

## Screen Responsibilities

### Library

Primary browse surface for canonical tracks, filters, and selection.

### Issues

Operational routing hub. Issues can route a track into Metadata Repair,
Metadata Sanitation, or manual edit workflows.

### Quality

Read-only dashboard showing cleanup progress, coverage, queue state, and next
recommended actions.

### Metadata Repair

Review missing or broken artist/title proposals recovered from filename/context.

### Metadata Sanitation

Review proposals that remove junk, source, or download contamination from
artist/title fields.

### Enrichment Queue

Review provider-backed enrichment proposals before any apply action.

### Reconciliation

Inspect validation and current-state consistency without mutating data.

### Manual Metadata Editor

Fallback modal for safe human corrections when heuristics cannot determine the
right metadata.

## Routing Patterns

* Sidebar navigation opens the major workspace pages.
* Row actions can route directly to a track-specific page.
* Query params such as `?track=<id>` preserve selection and inspector context.
* If a proposal is missing, the inspector should still show the selected track's
  context and explain why the workflow is empty.
* After apply or generation actions, refresh the current queue or page state
  without losing the user's place.

## Interaction Expectations

* Keep the top controls stable.
* Keep queue and inspector areas independently scrollable.
* Preserve filters and search state after refreshes.
* Avoid modal spam for routine review actions.
* Use compact, dense, dark operational styling.
