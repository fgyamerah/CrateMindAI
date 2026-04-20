"""
Track routes — read-only views into the pipeline's processed.db.

  GET /api/tracks          — list tracks with filtering, search, sort, pagination
  GET /api/tracks/stats    — aggregate counts (status, quality, missing fields)
  GET /api/tracks/issues   — tracks with at least one issue flag
  GET /api/tracks/{id}     — single track detail

IMPORTANT: /stats and /issues must be registered before /{id} so FastAPI
does not interpret the literal strings as integer IDs.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query

from ...schemas.track import TrackDetail, TrackIssueItem, TrackStats, TrackSummary
from ...services import track_service

log = logging.getLogger(__name__)
router = APIRouter(tags=["tracks"])


# ---------------------------------------------------------------------------
# GET /api/tracks/stats
# ---------------------------------------------------------------------------

@router.get("/tracks/stats", response_model=TrackStats)
async def get_track_stats() -> TrackStats:
    """
    Return aggregate counts for the whole library:
    - total tracks and breakdown by status
    - breakdown by quality tier
    - counts of tracks missing BPM, key, artist, or title
    """
    return track_service.get_stats()


# ---------------------------------------------------------------------------
# GET /api/tracks/issues
# ---------------------------------------------------------------------------

@router.get("/tracks/issues", response_model=List[TrackIssueItem])
async def get_track_issues(
    limit: int = Query(default=200, ge=1, le=1000),
) -> List[TrackIssueItem]:
    """
    Return tracks with at least one issue:
    missing BPM, missing key, missing artist/title, low quality, error, needs_review.

    Results are ordered: errors first, then needs_review, then by artist.
    """
    return track_service.get_issues(limit=limit)


# ---------------------------------------------------------------------------
# GET /api/tracks
# ---------------------------------------------------------------------------

@router.get("/tracks", response_model=List[TrackSummary])
async def list_tracks(
    path:         Optional[str]   = Query(default=None, description="Filter by filesystem directory prefix (e.g. /music/inbox)"),
    q:            Optional[str]   = Query(default=None, description="Search artist, title, filename"),
    status:       Optional[str]   = Query(default=None, description="Filter by status (ok, error, …)"),
    artist:       Optional[str]   = Query(default=None, description="Exact artist match (case-insensitive)"),
    genre:        Optional[str]   = Query(default=None, description="Exact genre match (case-insensitive)"),
    key:          Optional[str]   = Query(default=None, description="Camelot or musical key (e.g. 8A, Am)"),
    quality_tier: Optional[str]   = Query(default=None, description="LOSSLESS | HIGH | MEDIUM | LOW | UNKNOWN"),
    bpm_min:      Optional[float] = Query(default=None, ge=0,   le=300),
    bpm_max:      Optional[float] = Query(default=None, ge=0,   le=300),
    sort:         str             = Query(default="artist",      description="artist | title | bpm | processed_at | filename"),
    order:        str             = Query(default="asc",         description="asc | desc"),
    limit:        int             = Query(default=100, ge=1, le=500),
    offset:       int             = Query(default=0,  ge=0),
) -> List[TrackSummary]:
    """
    List library tracks with optional filtering, full-text search, sorting,
    and pagination.

    Pass ?path=/music/inbox to scope results to a specific directory.
    The response does not include total_count in the body; use
    GET /api/tracks/stats for aggregate numbers.
    """
    tracks, _total = track_service.list_tracks(
        path=path,
        q=q,
        status=status,
        artist=artist,
        genre=genre,
        key=key,
        quality_tier=quality_tier,
        bpm_min=bpm_min,
        bpm_max=bpm_max,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    )
    return [TrackSummary.from_track(t) for t in tracks]


# ---------------------------------------------------------------------------
# GET /api/tracks/{track_id}
# ---------------------------------------------------------------------------

@router.get("/tracks/{track_id}", response_model=TrackDetail)
async def get_track(track_id: int) -> TrackDetail:
    """Return the full detail record for a single track."""
    track = track_service.get_track(track_id)
    if not track:
        raise HTTPException(status_code=404, detail=f"Track {track_id} not found.")
    return TrackDetail.from_track(track)
