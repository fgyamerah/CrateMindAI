"""
Pydantic schemas for the BPM analysis / review API.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator


class BpmAnomalyResponse(BaseModel):
    """One anomalous track record stored in the backend DB."""

    id:                 int
    track_id:           int
    filepath:           str
    artist:             Optional[str] = None
    title:              Optional[str] = None
    genre:              Optional[str] = None
    current_bpm:        Optional[float] = None
    suggested_bpm:      Optional[float] = None
    reason:             str
    reason_label:       str
    review_status:      str
    detected_at:        str
    reviewed_at:        Optional[str] = None
    review_note:        Optional[str] = None
    reanalysis_job_id:  Optional[str] = None


class BpmCheckResult(BaseModel):
    """Response from POST /api/analysis/bpm-check."""

    tracks_scanned:  int
    new_anomalies:   int
    resolved:        int
    total_active:    int
    items:           List[BpmAnomalyResponse]


class BpmSummary(BaseModel):
    """Response from GET /api/analysis/bpm-anomalies/summary."""

    by_status: Dict[str, int]
    by_reason: Dict[str, int]


class UpdateAnomalyRequest(BaseModel):
    """
    Request body for PATCH /api/analysis/bpm-anomalies/{id}.

    review_status must be one of: reviewed | ignored | requeued | pending
    """

    review_status: str
    review_note:   Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def _valid_status(cls, v: str) -> str:
        allowed = {"reviewed", "ignored", "requeued", "pending"}
        if v not in allowed:
            raise ValueError(f"review_status must be one of {sorted(allowed)}")
        return v


class ReanalyzeRequest(BaseModel):
    """
    Request body for POST /api/analysis/reanalyze.

    Submits an analyze-missing job through the existing job system.
    force=True adds --reanalyze to force re-detection even for tracks
    that already have a BPM stored.
    dry_run=True adds --dry-run (no writes).
    """

    force:   bool = True
    dry_run: bool = False
