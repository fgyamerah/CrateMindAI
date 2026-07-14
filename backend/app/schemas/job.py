"""
Pydantic schemas for the jobs API.

JobCreate  — request body for POST /api/jobs
JobResponse — response shape for all job endpoints
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, field_validator

from ..services.toolkit_runner import ALLOWED_COMMANDS


class JobCreate(BaseModel):
    """Request body for submitting a new pipeline job."""

    command: str
    """
    The pipeline.py subcommand to run, e.g. "audit-quality" or "dedupe".
    Must be one of the values in ALLOWED_COMMANDS.
    """

    args: List[str] = []
    """
    Optional list of CLI flags for this subcommand.
    Only flags from the validated allowlist are accepted.
    Example: ["--dry-run", "--verbose"]
    """

    @field_validator("command")
    @classmethod
    def _command_in_allowlist(cls, v: str) -> str:
        if v not in ALLOWED_COMMANDS:
            raise ValueError(
                f"{v!r} is not an allowed command. "
                f"Allowed: {sorted(ALLOWED_COMMANDS)}"
            )
        return v


class JobResponse(BaseModel):
    """API representation of a job record."""

    id:               str
    command:          str
    args:             List[str]
    status:           str
    created_at:       str
    started_at:       Optional[str]   = None
    finished_at:      Optional[str]   = None
    exit_code:        Optional[int]   = None
    log_path:         Optional[str]   = None

    # Process PID (set once subprocess starts; null for pending / finished)
    pid:              Optional[int]   = None

    # Progress fields (only populated for rsync-sync jobs)
    progress_current: Optional[int]   = None
    progress_total:   Optional[int]   = None
    progress_percent: Optional[float] = None
    progress_message: Optional[str]   = None

    @classmethod
    def from_job(cls, job) -> "JobResponse":
        """Convert a Job model instance to a JobResponse."""
        return cls(
            id               = job.id,
            command          = job.command,
            args             = job.args,
            status           = job.status,
            created_at       = job.created_at,
            started_at       = job.started_at,
            finished_at      = job.finished_at,
            exit_code        = job.exit_code,
            log_path         = job.log_path,
            pid              = job.pid,
            progress_current = job.progress_current,
            progress_total   = job.progress_total,
            progress_percent = job.progress_percent,
            progress_message = job.progress_message,
        )
