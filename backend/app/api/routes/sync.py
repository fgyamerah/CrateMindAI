"""
SSD Sync routes.

  GET  /api/sync/config        — return configured paths and SSD mount status
  POST /api/sync/preview       — dry-run rsync, return list of would-be changes
  POST /api/sync/run           — dispatch a live rsync job
  GET  /api/sync               — list past sync jobs
  GET  /api/sync/{job_id}      — get a single sync job
"""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from ...schemas.job import JobResponse
from ...schemas.sync import (
    SyncConfigResponse,
    SyncPreviewRequest,
    SyncPreviewResponse,
    SyncRunRequest,
    SyncRunResponse,
)
from ...services import job_service
from ...services import rsync_runner

log = logging.getLogger(__name__)
router = APIRouter(tags=["sync"])


# ---------------------------------------------------------------------------
# GET /api/sync/config
# ---------------------------------------------------------------------------

@router.get("/sync/config", response_model=SyncConfigResponse)
async def get_sync_config() -> SyncConfigResponse:
    """
    Return the configured sync paths and whether the SSD is currently mounted.

    Call this on page load so the frontend can display the paths and warn the
    user if the SSD is not available before they attempt a preview or sync.
    """
    return rsync_runner.get_sync_config()


# ---------------------------------------------------------------------------
# POST /api/sync/preview
# ---------------------------------------------------------------------------

@router.post("/sync/preview", response_model=SyncPreviewResponse)
async def preview_sync(body: SyncPreviewRequest) -> SyncPreviewResponse:
    """
    Run rsync --dry-run and return a structured list of files that would be
    transferred.  No files are written.  Times out after 60 seconds.

    Use this to review pending changes before committing a full sync run.
    """
    return await rsync_runner.preview_sync(body)


# ---------------------------------------------------------------------------
# POST /api/sync/run
# ---------------------------------------------------------------------------

@router.post("/sync/run", response_model=SyncRunResponse, status_code=202)
async def run_sync(body: SyncRunRequest) -> SyncRunResponse:
    """
    Dispatch a live rsync job.

    Returns a job_id immediately (HTTP 202).  Poll GET /api/sync/{job_id} or
    GET /api/jobs/{job_id} for status and progress.  Stream logs via
    GET /api/jobs/{job_id}/logs.

    allow_delete is False by default.  Setting it True adds --delete to
    rsync, which removes files from the SSD that no longer exist in the source.
    Only enable this intentionally — it is destructive on the destination.
    """
    try:
        job = rsync_runner.start_sync_job(
            source_key   = body.source,
            allow_delete = body.allow_delete,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    flags = [body.source]
    if body.allow_delete:
        flags.append("--delete")

    return SyncRunResponse(
        job_id  = job.id,
        message = f"Sync job queued ({', '.join(flags)})",
    )


# ---------------------------------------------------------------------------
# GET /api/sync
# ---------------------------------------------------------------------------

@router.get("/sync", response_model=List[JobResponse])
async def list_sync_jobs(
    limit:  int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0,  ge=0),
) -> List[JobResponse]:
    """
    Return past ssd-sync jobs, newest first.

    These are a filtered view of the global jobs table — the same job_id
    can be used with GET /api/jobs/{id}/logs to stream the rsync log.
    """
    all_jobs  = job_service.list_jobs(limit=500, offset=0)
    sync_jobs = [j for j in all_jobs if j.command == "ssd-sync"]
    page      = sync_jobs[offset: offset + limit]
    return [JobResponse.from_job(j) for j in page]


# ---------------------------------------------------------------------------
# GET /api/sync/{job_id}
# ---------------------------------------------------------------------------

@router.get("/sync/{job_id}", response_model=JobResponse)
async def get_sync_job(job_id: str) -> JobResponse:
    """
    Return a single sync job by its ID.

    Raises 404 if the job is not found or is not a sync job.
    Use GET /api/jobs/{job_id}/logs to read the rsync log output.
    """
    job = job_service.get_job(job_id)
    if job is None or job.command != "ssd-sync":
        raise HTTPException(
            status_code=404,
            detail=f"Sync job {job_id!r} not found.",
        )
    return JobResponse.from_job(job)
