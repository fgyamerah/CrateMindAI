"""
Metadata repair review and apply routes.

All repair logic is deterministic and local. Apply updates only tracks.artist
and tracks.title in the pipeline DB after explicit approval.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...services import read_only as read_only_service
from modules import metadata_repair

router = APIRouter(tags=["metadata-repair"])

ReviewStatus = Literal["approved", "rejected", "deferred"]
RepairField = Literal["artist", "title"]


class MetadataRepairQueueResponse(BaseModel):
    items: list[dict[str, Any]]
    counts: dict[str, dict[str, int]]
    total: int
    limit: int
    offset: int


class MetadataRepairSummaryResponse(BaseModel):
    queue_total: int
    pending_count: int
    approved_count: int
    partial_count: int
    rejected_count: int
    deferred_count: int
    applied_count: int
    partial_applied_count: int
    no_op_count: int
    high_count: int
    medium_count: int
    low_count: int
    counts: dict[str, dict[str, int]]
    queue_path: str
    state_path: str
    updated_at: Optional[str] = None


class MetadataRepairReviewResponse(BaseModel):
    track_id: int
    review_status: ReviewStatus
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataRepairFieldReviewResponse(BaseModel):
    track_id: int
    field: RepairField
    review_status: ReviewStatus
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataRepairProposalPatchRequest(BaseModel):
    proposed: str


class MetadataRepairProposalPatchResponse(BaseModel):
    track_id: int
    field: RepairField
    proposed: str
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataRepairApplyResponse(BaseModel):
    root: str
    db_path: str
    queue_path: str
    state_path: str
    dry_run: bool
    approved_seen: int
    proposed_count: int
    applied_count: int
    applied_field_count: int = 0
    skipped_count: int
    changes: list[dict[str, Any]]
    skipped: list[dict[str, Any]]


class MetadataRepairGenerateResponse(BaseModel):
    root: str
    track_id: int
    generated: bool
    replaced: bool
    no_op_reason: Optional[str] = None
    queue_path: str
    proposal: Optional[dict[str, Any]] = None


@router.get("/metadata-repair/queue", response_model=MetadataRepairQueueResponse)
async def get_metadata_repair_queue(
    repair_type: Optional[str] = Query(default=None),
    confidence: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    include_applied: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> MetadataRepairQueueResponse:
    payload = metadata_repair.queue_response(
        read_only_service.get_library_root(),
        repair_type=repair_type,
        confidence=confidence,
        status=status,
        include_applied=include_applied,
        limit=limit,
        offset=offset,
    )
    return MetadataRepairQueueResponse(**payload)


@router.get("/metadata-repair/summary", response_model=MetadataRepairSummaryResponse)
async def get_metadata_repair_summary() -> MetadataRepairSummaryResponse:
    payload = metadata_repair.summary(read_only_service.get_library_root())
    return MetadataRepairSummaryResponse(**payload)


def _review_action(track_id: int, review_status: ReviewStatus) -> MetadataRepairReviewResponse:
    try:
        state = metadata_repair.set_review_status(
            read_only_service.get_library_root(),
            track_id,
            review_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MetadataRepairReviewResponse(
        track_id=track_id,
        review_status=review_status,
        state=state,
    )


@router.post("/metadata-repair/{track_id}/approve", response_model=MetadataRepairReviewResponse)
async def approve_metadata_repair(track_id: int) -> MetadataRepairReviewResponse:
    return _review_action(track_id, "approved")


@router.post("/metadata-repair/{track_id}/reject", response_model=MetadataRepairReviewResponse)
async def reject_metadata_repair(track_id: int) -> MetadataRepairReviewResponse:
    return _review_action(track_id, "rejected")


@router.post("/metadata-repair/{track_id}/defer", response_model=MetadataRepairReviewResponse)
async def defer_metadata_repair(track_id: int) -> MetadataRepairReviewResponse:
    return _review_action(track_id, "deferred")


def _field_review_action(track_id: int, field: RepairField, review_status: ReviewStatus) -> MetadataRepairFieldReviewResponse:
    try:
        state = metadata_repair.set_field_review_status(
            read_only_service.get_library_root(),
            track_id,
            field,
            review_status,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, LookupError) else 400, detail=str(exc)) from exc
    return MetadataRepairFieldReviewResponse(
        track_id=track_id,
        field=field,
        review_status=review_status,
        state=state,
    )


@router.post("/metadata-repair/{track_id}/field/{field}/approve", response_model=MetadataRepairFieldReviewResponse)
async def approve_metadata_repair_field(track_id: int, field: RepairField) -> MetadataRepairFieldReviewResponse:
    return _field_review_action(track_id, field, "approved")


@router.post("/metadata-repair/{track_id}/field/{field}/reject", response_model=MetadataRepairFieldReviewResponse)
async def reject_metadata_repair_field(track_id: int, field: RepairField) -> MetadataRepairFieldReviewResponse:
    return _field_review_action(track_id, field, "rejected")


@router.post("/metadata-repair/{track_id}/field/{field}/defer", response_model=MetadataRepairFieldReviewResponse)
async def defer_metadata_repair_field(track_id: int, field: RepairField) -> MetadataRepairFieldReviewResponse:
    return _field_review_action(track_id, field, "deferred")


@router.patch("/metadata-repair/{track_id}/field/{field}/proposal", response_model=MetadataRepairProposalPatchResponse)
async def update_metadata_repair_field_proposal(
    track_id: int,
    field: RepairField,
    payload: MetadataRepairProposalPatchRequest,
) -> MetadataRepairProposalPatchResponse:
    try:
        state = metadata_repair.set_field_proposal(
            read_only_service.get_library_root(),
            track_id,
            field,
            payload.proposed,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, LookupError) else 400, detail=str(exc)) from exc
    return MetadataRepairProposalPatchResponse(
        track_id=track_id,
        field=field,
        proposed=payload.proposed,
        state=state,
    )


@router.post("/metadata-repair/generate/{track_id}", response_model=MetadataRepairGenerateResponse)
async def generate_metadata_repair(track_id: int) -> MetadataRepairGenerateResponse:
    try:
        result = metadata_repair.generate_track_proposal(read_only_service.get_library_root(), track_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MetadataRepairGenerateResponse(**result)


@router.post("/metadata-repair/apply-approved/dry-run", response_model=MetadataRepairApplyResponse)
async def dry_run_metadata_repair_apply() -> MetadataRepairApplyResponse:
    result = metadata_repair.apply_approved(read_only_service.get_library_root(), apply=False)
    return MetadataRepairApplyResponse(**result)


@router.post("/metadata-repair/apply-approved/apply", response_model=MetadataRepairApplyResponse)
async def apply_metadata_repair(confirm: bool = Query(default=False)) -> MetadataRepairApplyResponse:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required to apply approved metadata repairs")
    result = metadata_repair.apply_approved(read_only_service.get_library_root(), apply=True)
    return MetadataRepairApplyResponse(**result)
