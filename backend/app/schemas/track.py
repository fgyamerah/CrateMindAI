"""
Pydantic schemas for the tracks API.

TrackSummary  — lightweight row shape used in list responses
TrackDetail   — full field set for single-track responses
TrackStats    — aggregate counts for the stats endpoint
TrackIssueItem — single item in the issues list response
"""
from __future__ import annotations

from typing import Dict, List, Optional

from pydantic import BaseModel

from ..models.track import Track


class TrackSummary(BaseModel):
    """Lightweight representation used in list/table responses."""

    id:           int
    filepath:     str
    filename:     str
    artist:       Optional[str] = None
    title:        Optional[str] = None
    genre:        Optional[str] = None
    bpm:          Optional[float] = None
    key_camelot:  Optional[str] = None
    key_musical:  Optional[str] = None
    duration_sec: Optional[float] = None
    bitrate_kbps: Optional[int] = None
    status:       str
    quality_tier: Optional[str] = None
    issues:       List[str] = []

    @classmethod
    def from_track(cls, t: Track) -> "TrackSummary":
        return cls(
            id=t.id,
            filepath=t.filepath,
            filename=t.filename,
            artist=t.artist,
            title=t.title,
            genre=t.genre,
            bpm=t.bpm,
            key_camelot=t.key_camelot,
            key_musical=t.key_musical,
            duration_sec=t.duration_sec,
            bitrate_kbps=t.bitrate_kbps,
            status=t.status,
            quality_tier=t.quality_tier,
            issues=t.issues,
        )


class TrackDetail(BaseModel):
    """Full field set returned for a single track."""

    id:             int
    filepath:       str
    filename:       str
    artist:         Optional[str] = None
    title:          Optional[str] = None
    genre:          Optional[str] = None
    bpm:            Optional[float] = None
    key_camelot:    Optional[str] = None
    key_musical:    Optional[str] = None
    duration_sec:   Optional[float] = None
    bitrate_kbps:   Optional[int] = None
    filesize_bytes: Optional[int] = None
    status:         str
    error_msg:      Optional[str] = None
    processed_at:   Optional[str] = None
    pipeline_ver:   Optional[str] = None
    quality_tier:   Optional[str] = None
    issues:         List[str] = []

    @classmethod
    def from_track(cls, t: Track) -> "TrackDetail":
        return cls(
            id=t.id,
            filepath=t.filepath,
            filename=t.filename,
            artist=t.artist,
            title=t.title,
            genre=t.genre,
            bpm=t.bpm,
            key_camelot=t.key_camelot,
            key_musical=t.key_musical,
            duration_sec=t.duration_sec,
            bitrate_kbps=t.bitrate_kbps,
            filesize_bytes=t.filesize_bytes,
            status=t.status,
            error_msg=t.error_msg,
            processed_at=t.processed_at,
            pipeline_ver=t.pipeline_ver,
            quality_tier=t.quality_tier,
            issues=t.issues,
        )


class TrackStats(BaseModel):
    """Aggregate counts returned by GET /api/tracks/stats."""

    total:          int
    by_status:      Dict[str, int]
    by_quality:     Dict[str, int]
    missing_bpm:    int
    missing_key:    int
    missing_artist: int
    missing_title:  int


class TrackIssueItem(BaseModel):
    """One entry in the issues list."""

    id:      int
    filepath: str
    filename: str
    artist:  Optional[str] = None
    title:   Optional[str] = None
    status:  str
    issues:  List[str]
