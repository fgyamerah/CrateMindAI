"""
Pydantic schemas for the playlists API.
"""
from __future__ import annotations

import re
from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class SetBuilderRequest(BaseModel):
    duration: int = Field(60, ge=10, le=360, description="Target set duration in minutes")
    vibe: str = Field("peak", description="Phase-weight preset: warm / peak / deep / driving")
    strategy: str = Field(
        "safest",
        description="Harmonic transition ranking strategy",
    )
    structure: str = Field(
        "full",
        description="Phase structure: full / simple / peak_only",
    )
    genre: Optional[str] = Field(None, description="Genre filter (substring match, optional)")
    max_bpm_jump: float = Field(3.0, ge=0.0, le=20.0, description="Max absolute BPM jump between tracks")
    strict_harmonic: bool = Field(True, description="Enforce strict harmonic key transitions")
    artist_repeat_window: int = Field(3, ge=0, le=10, description="Reject same artist within this many tracks")
    name: Optional[str] = Field(None, description="Optional set name (alphanumeric, hyphens, underscores)")
    dry_run: bool = Field(False, description="Preview only — no files or DB records written")

    @field_validator("vibe")
    @classmethod
    def _vibe_choices(cls, v: str) -> str:
        allowed = {"warm", "peak", "deep", "driving"}
        if v not in allowed:
            raise ValueError(f"vibe must be one of {sorted(allowed)}")
        return v

    @field_validator("strategy")
    @classmethod
    def _strategy_choices(cls, v: str) -> str:
        allowed = {"safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"}
        if v not in allowed:
            raise ValueError(f"strategy must be one of {sorted(allowed)}")
        return v

    @field_validator("structure")
    @classmethod
    def _structure_choices(cls, v: str) -> str:
        allowed = {"full", "simple", "peak_only"}
        if v not in allowed:
            raise ValueError(f"structure must be one of {sorted(allowed)}")
        return v

    @field_validator("genre")
    @classmethod
    def _genre_safe(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^[\w\s\-/&']{1,64}$", v):
            raise ValueError("genre contains disallowed characters or is too long (max 64)")
        return v

    @field_validator("name")
    @classmethod
    def _name_safe(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if not re.match(r"^[\w\-]{1,64}$", v):
            raise ValueError("name must be alphanumeric/hyphen/underscore, max 64 chars")
        return v


class SetBuilderJobResponse(BaseModel):
    job_id: str
    message: str


class PlaylistSummary(BaseModel):
    id: int
    name: str
    created_at: str
    duration_sec: float
    track_count: int
    config_json: Optional[str] = None


class SetTrackResponse(BaseModel):
    position: int
    phase: str
    artist: Optional[str] = None
    title: Optional[str] = None
    bpm: Optional[float] = None
    key_camelot: Optional[str] = None
    genre: Optional[str] = None
    duration_sec: Optional[float] = None
    transition_note: Optional[str] = None
    filepath: str


class PlaylistDetail(BaseModel):
    playlist: PlaylistSummary
    tracks: List[SetTrackResponse]
