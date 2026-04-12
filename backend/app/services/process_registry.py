"""
process_registry — shared table of running asyncio subprocesses.

Both toolkit_runner and rsync_runner register their subprocess objects here
so the cancel route can send SIGTERM regardless of which runner spawned
the job.

Concurrency note:
  All writes happen from asyncio tasks on the same event loop (create_task).
  CPython dict/set operations are GIL-protected, so no additional locking is
  needed for a single-process, single-event-loop server.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import Dict, Optional

log = logging.getLogger(__name__)

# job_id → running subprocess
_registry: Dict[str, "asyncio.subprocess.Process"] = {}

# job IDs for which cancel has been explicitly requested.
# Background tasks check this flag to choose 'cancelled' over 'failed'.
_cancelling: set[str] = set()


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

def register(job_id: str, proc: "asyncio.subprocess.Process") -> None:
    """Register an active subprocess for a job."""
    _registry[job_id] = proc


def unregister(job_id: str) -> None:
    """Remove a job from the registry (call this when the process exits)."""
    _registry.pop(job_id, None)
    _cancelling.discard(job_id)


def get_proc(job_id: str) -> Optional["asyncio.subprocess.Process"]:
    return _registry.get(job_id)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

def is_cancelling(job_id: str) -> bool:
    """True if cancel was explicitly requested for this job."""
    return job_id in _cancelling


def request_cancel(job_id: str) -> bool:
    """
    Send SIGTERM to the process registered for job_id.

    Returns True if a signal was delivered, False if no process was found
    (job is not running / already finished).
    """
    proc = _registry.get(job_id)
    if proc is None:
        log.warning("cancel: no running process for job %s", job_id)
        return False

    _cancelling.add(job_id)
    try:
        proc.send_signal(signal.SIGTERM)
        log.info("cancel: sent SIGTERM to pid=%s (job=%s)", proc.pid, job_id)
        return True
    except ProcessLookupError:
        # Process already exited — tidy up
        log.info("cancel: process already dead (job=%s)", job_id)
        _cancelling.discard(job_id)
        unregister(job_id)
        return False
    except Exception as exc:
        log.exception("cancel: unexpected error (job=%s): %s", job_id, exc)
        _cancelling.discard(job_id)
        return False
