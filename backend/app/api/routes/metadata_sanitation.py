"""
Metadata sanitation review and apply routes.

All sanitation logic is deterministic and local. Apply updates only
tracks.artist and tracks.title in the pipeline DB after explicit approval.
"""
from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ...services import read_only as read_only_service
from modules import metadata_sanitation

router = APIRouter(tags=["metadata-sanitation"])

ReviewStatus = Literal["approved", "rejected", "deferred"]
SanitationField = Literal["artist", "title"]


class MetadataSanitationQueueResponse(BaseModel):
    items: list[dict[str, Any]]
    counts: dict[str, dict[str, int]]
    total: int
    limit: int
    offset: int


class MetadataSanitationSummaryResponse(BaseModel):
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


class MetadataSanitationReviewResponse(BaseModel):
    track_id: int
    review_status: ReviewStatus
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataSanitationFieldReviewResponse(BaseModel):
    track_id: int
    field: SanitationField
    review_status: ReviewStatus
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataSanitationProposalPatchRequest(BaseModel):
    proposed: str


class MetadataSanitationProposalPatchResponse(BaseModel):
    track_id: int
    field: SanitationField
    proposed: str
    state: dict[str, Any] = Field(default_factory=dict)


class MetadataSanitationApplyResponse(BaseModel):
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


class MetadataSanitationGenerateResponse(BaseModel):
    root: str
    track_id: int
    generated: bool
    replaced: bool
    no_op_reason: Optional[str] = None
    recommended_route: Optional[str] = None
    queue_path: str
    proposal: Optional[dict[str, Any]] = None
    track: Optional[dict[str, Any]] = None


@router.get("/metadata-sanitation/queue", response_model=MetadataSanitationQueueResponse)
async def get_metadata_sanitation_queue(
    repair_type: Optional[str] = Query(default=None),
    confidence: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    include_applied: bool = Query(default=False),
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> MetadataSanitationQueueResponse:
    payload = metadata_sanitation.queue_response(
        read_only_service.get_library_root(),
        repair_type=repair_type,
        confidence=confidence,
        status=status,
        include_applied=include_applied,
        limit=limit,
        offset=offset,
    )
    return MetadataSanitationQueueResponse(**payload)


@router.get("/metadata-sanitation/summary", response_model=MetadataSanitationSummaryResponse)
async def get_metadata_sanitation_summary() -> MetadataSanitationSummaryResponse:
    payload = metadata_sanitation.summary(read_only_service.get_library_root())
    return MetadataSanitationSummaryResponse(**payload)


def _review_action(track_id: int, review_status: ReviewStatus) -> MetadataSanitationReviewResponse:
    try:
        state = metadata_sanitation.set_review_status(
            read_only_service.get_library_root(),
            track_id,
            review_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MetadataSanitationReviewResponse(
        track_id=track_id,
        review_status=review_status,
        state=state,
    )


@router.post("/metadata-sanitation/{track_id}/approve", response_model=MetadataSanitationReviewResponse)
async def approve_metadata_sanitation(track_id: int) -> MetadataSanitationReviewResponse:
    return _review_action(track_id, "approved")


@router.post("/metadata-sanitation/{track_id}/reject", response_model=MetadataSanitationReviewResponse)
async def reject_metadata_sanitation(track_id: int) -> MetadataSanitationReviewResponse:
    return _review_action(track_id, "rejected")


@router.post("/metadata-sanitation/{track_id}/defer", response_model=MetadataSanitationReviewResponse)
async def defer_metadata_sanitation(track_id: int) -> MetadataSanitationReviewResponse:
    return _review_action(track_id, "deferred")


def _field_review_action(
    track_id: int,
    field: SanitationField,
    review_status: ReviewStatus,
) -> MetadataSanitationFieldReviewResponse:
    try:
        state = metadata_sanitation.set_field_review_status(
            read_only_service.get_library_root(),
            track_id,
            field,
            review_status,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, LookupError) else 400, detail=str(exc)) from exc
    return MetadataSanitationFieldReviewResponse(
        track_id=track_id,
        field=field,
        review_status=review_status,
        state=state,
    )


@router.post("/metadata-sanitation/{track_id}/field/{field}/approve", response_model=MetadataSanitationFieldReviewResponse)
async def approve_metadata_sanitation_field(
    track_id: int,
    field: SanitationField,
) -> MetadataSanitationFieldReviewResponse:
    return _field_review_action(track_id, field, "approved")


@router.post("/metadata-sanitation/{track_id}/field/{field}/reject", response_model=MetadataSanitationFieldReviewResponse)
async def reject_metadata_sanitation_field(
    track_id: int,
    field: SanitationField,
) -> MetadataSanitationFieldReviewResponse:
    return _field_review_action(track_id, field, "rejected")


@router.post("/metadata-sanitation/{track_id}/field/{field}/defer", response_model=MetadataSanitationFieldReviewResponse)
async def defer_metadata_sanitation_field(
    track_id: int,
    field: SanitationField,
) -> MetadataSanitationFieldReviewResponse:
    return _field_review_action(track_id, field, "deferred")


@router.patch("/metadata-sanitation/{track_id}/field/{field}/proposal", response_model=MetadataSanitationProposalPatchResponse)
async def update_metadata_sanitation_field_proposal(
    track_id: int,
    field: SanitationField,
    payload: MetadataSanitationProposalPatchRequest,
) -> MetadataSanitationProposalPatchResponse:
    try:
        state = metadata_sanitation.set_field_proposal(
            read_only_service.get_library_root(),
            track_id,
            field,
            payload.proposed,
        )
    except (LookupError, ValueError) as exc:
        raise HTTPException(status_code=404 if isinstance(exc, LookupError) else 400, detail=str(exc)) from exc
    return MetadataSanitationProposalPatchResponse(
        track_id=track_id,
        field=field,
        proposed=payload.proposed,
        state=state,
    )


@router.post("/metadata-sanitation/generate/{track_id}", response_model=MetadataSanitationGenerateResponse)
async def generate_metadata_sanitation(track_id: int) -> MetadataSanitationGenerateResponse:
    try:
        result = metadata_sanitation.generate_track_proposal(read_only_service.get_library_root(), track_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return MetadataSanitationGenerateResponse(**result)


@router.post("/metadata-sanitation/apply-approved/dry-run", response_model=MetadataSanitationApplyResponse)
async def dry_run_metadata_sanitation_apply() -> MetadataSanitationApplyResponse:
    result = metadata_sanitation.apply_approved(read_only_service.get_library_root(), apply=False)
    return MetadataSanitationApplyResponse(**result)


@router.post("/metadata-sanitation/apply-approved/apply", response_model=MetadataSanitationApplyResponse)
async def apply_metadata_sanitation(confirm: bool = Query(default=False)) -> MetadataSanitationApplyResponse:
    if not confirm:
        raise HTTPException(status_code=400, detail="confirm=true is required to apply approved metadata sanitation")
    result = metadata_sanitation.apply_approved(read_only_service.get_library_root(), apply=True)
    return MetadataSanitationApplyResponse(**result)
