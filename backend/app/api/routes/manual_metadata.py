"""Manual DB-only artist/title metadata edit routes."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ...services import read_only as read_only_service
from modules import manual_metadata

router = APIRouter(tags=["manual-metadata"])


class ManualMetadataRequest(BaseModel):
    track_id: int
    artist: str
    title: str


class ManualMetadataPreviewResponse(BaseModel):
    track_id: int
    filepath: str
    filename: str
    current: dict[str, Any]
    proposed: dict[str, Any]
    changed_fields: list[str]
    no_op: bool
    validation_warnings: list[str]
    diff: list[dict[str, Any]]


class ManualMetadataApplyResponse(ManualMetadataPreviewResponse):
    applied_fields: list[str]
    before: dict[str, Any]
    after: dict[str, Any]
    audit_path: Optional[str] = None


@router.post("/manual-metadata/preview", response_model=ManualMetadataPreviewResponse)
async def preview_manual_metadata(payload: ManualMetadataRequest) -> ManualMetadataPreviewResponse:
    try:
        result = manual_metadata.preview(read_only_service.get_library_root(), payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ManualMetadataPreviewResponse(**result)


@router.post("/manual-metadata/apply", response_model=ManualMetadataApplyResponse)
async def apply_manual_metadata(payload: ManualMetadataRequest) -> ManualMetadataApplyResponse:
    try:
        result = manual_metadata.apply(read_only_service.get_library_root(), payload.model_dump())
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ManualMetadataApplyResponse(**result)
