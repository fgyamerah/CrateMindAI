"""
job_service — CRUD operations for the jobs table.

All functions are synchronous (raw sqlite3).  The async layer in
toolkit_runner calls these directly; FastAPI route handlers call them
from async context, which is fine for short DB operations.

Functions never raise on "not found" — they return None so callers
can decide what HTTP status to return.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from ..core.db import get_conn
from ..core.config import JOBS_LOG_DIR
from ..models.job import Job

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_job(row) -> Job:
    return Job.from_row(row)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

def create_job(command: str, args: List[str]) -> Job:
    """
    Insert a new job with status=pending and return the populated Job model.
    The log_path is assigned at creation time so it is available even before
    the subprocess starts.
    """
    job_id    = str(uuid.uuid4())
    log_path  = str(JOBS_LOG_DIR / f"{job_id}.log")
    now       = _now()
    args_json = json.dumps(args)

    with get_conn() as conn:
        conn.execute(
            """INSERT INTO jobs
               (id, command, args_json, status, created_at, log_path)
               VALUES (?, ?, ?, 'pending', ?, ?)""",
            (job_id, command, args_json, now, log_path),
        )

    log.info("job=%s  created  command=%s  args=%s", job_id, command, args)
    return get_job(job_id)


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_job(job_id: str) -> Optional[Job]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(limit: int = 100, offset: int = 0) -> List[Job]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_row_to_job(r) for r in rows]


# ---------------------------------------------------------------------------
# Status updates  (called by toolkit_runner)
# ---------------------------------------------------------------------------

def mark_running(job_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET status='running', started_at=? WHERE id=?",
            (_now(), job_id),
        )


def mark_finished(job_id: str, status: str, exit_code: int) -> None:
    """status must be 'succeeded', 'failed', or 'cancelled'."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs
               SET status=?, finished_at=?, exit_code=?
               WHERE id=?""",
            (status, _now(), exit_code, job_id),
        )


# ---------------------------------------------------------------------------
# Process / progress updates  (called by toolkit_runner / rsync_runner)
# ---------------------------------------------------------------------------

def mark_pid(job_id: str, pid: int) -> None:
    """Store the OS PID of the subprocess (called after proc is created)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET pid=? WHERE id=?",
            (pid, job_id),
        )


def mark_progress(
    job_id:  str,
    current: int,
    total:   int,
    percent: float,
    message: str,
) -> None:
    """
    Update job progress fields.  Called from the rsync background task
    each time a parseable progress line is received.
    """
    with get_conn() as conn:
        conn.execute(
            """UPDATE jobs
               SET progress_current=?, progress_total=?,
                   progress_percent=?, progress_message=?
               WHERE id=?""",
            (current, total, percent, message, job_id),
        )


def clear_pid(job_id: str) -> None:
    """Clear the PID once the process has exited."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE jobs SET pid=NULL WHERE id=?",
            (job_id,),
        )
