"""
Runtime readiness routes.

  GET /api/runtime/preflight — read-only environment and safety validation
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from ...services.runtime_preflight import run_preflight

router = APIRouter(tags=["runtime"])


class PreflightCheck(BaseModel):
    id: str
    label: str
    status: str  # pass | warn | fail
    detail: str
    remediation: str = ""
    optional: bool = False


class PreflightResponse(BaseModel):
    status: str  # ready | degraded | unsafe
    library_root: str
    generated_at: str
    checks: List[PreflightCheck]


@router.get("/runtime/preflight", response_model=PreflightResponse)
async def runtime_preflight() -> PreflightResponse:
    return PreflightResponse(**run_preflight())
