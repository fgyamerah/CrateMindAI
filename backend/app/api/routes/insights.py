"""
Read-only insight routes for queue, review state, and audit artifacts.
"""
from __future__ import annotations

import json
from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...services import read_only as read_only_service
from ...services.track_service import get_track_by_id
from modules import enrichment_apply

router = APIRouter(tags=["insights"])


ReviewStatus = Literal["approved", "rejected", "deferred"]


class ReviewStateItem(BaseModel):
    track_id: int
    review_status: ReviewStatus
    updated_at: Optional[str] = None


class ReviewStateResponse(BaseModel):
    items: dict[str, ReviewStateItem]
    approved: list[int]
    rejected: list[int]
    deferred: list[int]
    counts: dict[str, int]
    approved_high_count: int = 0
    approved_medium_count: int = 0
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)
    queue_total: int = 0
    updated_at: Optional[str] = None


class ReviewSummaryResponse(BaseModel):
    pending_count: int
    approved_count: int
    rejected_count: int
    deferred_count: int
    approved_high_count: int
    approved_medium_count: int
    rejected_by_reason: dict[str, int] = Field(default_factory=dict)
    last_updated: Optional[str] = None


class EnrichmentQueueResponse(BaseModel):
    items: list[dict[str, Any]]
    counts: dict[str, dict[str, int]]
    limit: int
    offset: int
    total: int


class ReviewActionResponse(BaseModel):
    track_id: int
    review_status: ReviewStatus
    state: ReviewStateResponse


class EnrichmentApplyResponse(BaseModel):
    root: str
    db_path: str
    state_path: str
    log_path: str
    dry_run: bool
    approved_seen: int
    proposed_count: int
    applied_count: int
    skipped_count: int
    changes: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


@router.get("/enrichment/queue", response_model=EnrichmentQueueResponse)
async def get_enrichment_queue(
    action: Optional[str] = Query(
        default=None,
        description="Filter by action suggestion (auto_candidate, review, ignore).",
    ),
    confidence: Optional[str] = Query(
        default=None,
        description="Filter by confidence tier (HIGH, MEDIUM, LOW).",
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> EnrichmentQueueResponse:
    payload = read_only_service.load_enrichment_queue(
        action=action,
        confidence=confidence,
        limit=limit,
        offset=offset,
    )
    return EnrichmentQueueResponse(**payload)


@router.get("/enrichment/review/state", response_model=ReviewStateResponse)
async def get_enrichment_review_state() -> ReviewStateResponse:
    return ReviewStateResponse(**read_only_service.load_review_state())


@router.get("/enrichment/review/export")
async def export_enrichment_review_state() -> Response:
    state = read_only_service.load_review_state()
    payload = {
        "approved": state.get("approved", []),
        "rejected": state.get("rejected", []),
        "deferred": state.get("deferred", []),
        "counts": state.get("counts", {}),
        "updated_at": state.get("updated_at"),
    }
    body = json.dumps(payload, indent=2, sort_keys=True)
    headers = {
        "Content-Disposition": 'attachment; filename="enrichment_review_state.json"',
    }
    return Response(content=body, media_type="application/json", headers=headers)


@router.get("/enrichment/review/summary", response_model=ReviewSummaryResponse)
async def get_enrichment_review_summary() -> ReviewSummaryResponse:
    return ReviewSummaryResponse(**read_only_service.build_review_summary())


def _review_action(track_id: int, review_status: ReviewStatus) -> ReviewActionResponse:
    track = get_track_by_id(track_id)
    if track is None:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found")
    queue_item = read_only_service.lookup_enrichment_queue_item(track.filepath)
    if queue_item is None:
        raise HTTPException(
            status_code=404,
            detail=f"Track {track_id} is not present in the enrichment queue",
        )
    try:
        state = read_only_service.set_review_state(track_id, review_status, queue_item=queue_item)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return ReviewActionResponse(
        track_id=track_id,
        review_status=review_status,
        state=ReviewStateResponse(**state),
    )


@router.post("/enrichment/review/{track_id}/approve", response_model=ReviewActionResponse)
async def approve_enrichment_review(track_id: int) -> ReviewActionResponse:
    return _review_action(track_id, "approved")


@router.post("/enrichment/review/{track_id}/reject", response_model=ReviewActionResponse)
async def reject_enrichment_review(track_id: int) -> ReviewActionResponse:
    return _review_action(track_id, "rejected")


@router.post("/enrichment/review/{track_id}/defer", response_model=ReviewActionResponse)
async def defer_enrichment_review(track_id: int) -> ReviewActionResponse:
    return _review_action(track_id, "deferred")


@router.post("/enrichment/apply-approved/dry-run", response_model=EnrichmentApplyResponse)
async def dry_run_enrichment_apply_approved() -> EnrichmentApplyResponse:
    result = enrichment_apply.build_approved_enrichment_plan(read_only_service.get_library_root())
    return EnrichmentApplyResponse(**result)


@router.post("/enrichment/apply-approved/apply", response_model=EnrichmentApplyResponse)
async def apply_enrichment_apply_approved(confirm: bool = Query(default=False)) -> EnrichmentApplyResponse:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required to apply approved enrichment updates")
    result = enrichment_apply.apply_approved_enrichment(read_only_service.get_library_root(), apply=True)
    return EnrichmentApplyResponse(**result)


@router.get("/audit/latest")
async def get_latest_audit() -> dict[str, Any]:
    report = read_only_service.load_latest_audit_json()
    if report is None:
        return {"available": False}
    return report
