# Human Review Model

CrateMindAI uses deterministic proposals plus human review for metadata
operations. The system is designed so reviewable metadata never mutates directly
from heuristic or provider output.

## Flow

1. Detect an issue or incomplete metadata.
2. Generate a deterministic proposal.
3. Place the proposal in the appropriate queue.
4. Let a human inspect the proposal.
5. Approve, reject, or defer at the field level.
6. Apply approved fields to the database only.
7. Record audit entries and keep the original proposal for traceability.

## Proposal Sources

* Metadata Repair: recover artist/title from filename and local context.
* Metadata Sanitation: remove junk/source/download contamination.
* Manual Metadata Editor: user-supplied correction when heuristics are not enough.
* Quality and Issues views: route tracks into the right workflow.

## Queue Model

Queues are review surfaces, not sources of truth. They can contain:

* current value
* proposed value
* original proposed value
* edited proposed value
* field status
* confidence or risk flags
* audit metadata

Queue entries may be updated or regenerated, but the original audit trail must
remain visible.

## Field-Level Approval

Artist and title are reviewed independently. Each field can be:

* approved
* rejected
* deferred
* marked applied
* marked no-op when the database already matches the proposed value

Apply should only touch approved fields and should never write empty values.

## Editable Proposals

Heuristic proposals may be edited before approval. Edits are stored separately
from the original generated proposal so that review history stays auditable.

## DB-Only Apply

Approved metadata changes update the `tracks` table only. These workflows do not:

* write audio tags
* rename files
* delete files
* touch BPM, key, or cue data
* modify `processed_state`

## Audit Logs

Every apply action should create an append-only audit record that captures:

* timestamp
* track id
* filepath
* before snapshot
* after snapshot
* changed fields

The audit log exists so changes can be reviewed later without re-running the
proposal logic.

## Why AI/Provider Results Never Mutate Directly

Provider output and heuristic output can be useful, but they are not trustworthy
enough to mutate library metadata on their own. They can be wrong, incomplete,
or context-blind. The review model keeps the system deterministic, auditable,
and safe for large libraries.
