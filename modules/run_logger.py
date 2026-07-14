"""
modules/run_logger.py

Per-stage processed-state tracker for incremental library runs.

Prevents reprocessing unchanged files across large libraries (10 k+ files).
A file is considered "done" for a stage when its path, size, and mtime match
a prior record whose status is in SKIP_STATUSES.

Statuses
--------
success    — changes were applied / file was renamed
no_change  — file was analysed; nothing needed changing
skipped    — skipped for a deterministic reason (missing tags, hard reject, etc.)
ignored    — file moved to the IGNORED quarantine
review     — queued for human review (NOT in SKIP_STATUSES — re-evaluated each run)
error      — processing failed (NOT in SKIP_STATUSES — retried each run)

Usage inside a runner
---------------------
    import modules.run_logger as proc

    STAGE = "my-stage"

    # At the top of run_*() — before the file loop:
    if getattr(args, "reset_stage", False):
        proc.clear_stage(STAGE)
    _force = getattr(args, "force", False)
    n_skip_unchanged = 0

    # First line inside the file loop:
    if not _force and proc.should_skip(STAGE, path):
        n_skip_unchanged += 1
        continue

    # After processing, record the outcome:
    proc.record(STAGE, path, "success" | "no_change" | "error" | ...)

    # In the summary:
    if n_skip_unchanged:
        print(f"  Skipped unchanged : {n_skip_unchanged}")

    # filename-normalize only — after a successful rename:
    proc.rename_path(old_path, new_path)
"""
from __future__ import annotations

from pathlib import Path

import db

# Statuses that cause a file to be skipped on the next run (when unchanged).
# "error" and "review" are deliberately excluded — those are retried every run.
SKIP_STATUSES: frozenset = frozenset({"success", "no_change", "skipped", "ignored"})


def should_skip(stage: str, path: Path, reason_prefix: str = "") -> bool:
    """
    Return True if this file should be skipped for this stage.

    Requirements for a skip:
      1. A prior record exists for (stage, resolved_path).
      2. The prior status is in SKIP_STATUSES.
      3. The file's current size and mtime match the recorded values exactly.
      4. If reason_prefix is given, the stored reason must start with that prefix.
         This invalidates stale "no_change" records when rules change between runs.

    Any failure (stat error, DB error, missing table) returns False so the
    runner handles it gracefully rather than crashing.
    """
    try:
        stat = path.stat()
    except OSError:
        return False

    try:
        row = db.get_processed_state(stage, _norm(path))
    except Exception:
        return False

    if row is None:
        return False
    if row["status"] not in SKIP_STATUSES:
        return False
    if row["file_size"] != stat.st_size:
        return False
    if abs(row["file_mtime"] - stat.st_mtime) > 0.001:
        return False
    if reason_prefix and not row.get("reason", "").startswith(reason_prefix):
        return False
    return True


def record(stage: str, path: Path, status: str, reason: str = "") -> None:
    """
    Upsert a processed-state record for (stage, path).

    Failures are silently swallowed — tracking must never abort processing.
    """
    try:
        stat = path.stat()
        db.set_processed_state(
            stage,
            _norm(path),
            file_size=stat.st_size,
            file_mtime=stat.st_mtime,
            status=status,
            reason=reason,
        )
    except Exception:
        pass


def clear_stage(stage: str) -> None:
    """Delete all processed-state records for a stage (implements --reset-stage)."""
    try:
        db.clear_stage_processed(stage)
    except Exception:
        pass


def rename_path(old_path: Path, new_path: Path) -> None:
    """
    Update all processed-state records when a file is renamed.
    Call this after every successful rename in filename-normalize so that
    prior-stage records (sanitize, ai-normalize, etc.) remain valid under
    the new path and are not re-processed unnecessarily.
    """
    try:
        db.rename_processed_path(_norm(old_path), _norm(new_path))
    except Exception:
        pass


def _norm(path: Path) -> str:
    """Return a consistent, absolute path string for use as a DB key."""
    return str(path.resolve())
