"""
track_service — read-only queries against the pipeline's tracks table.

All functions open a fresh read-only connection from pipeline_db and return
Python model objects.  Functions never raise on "DB not found" — they return
empty results so callers can decide what to surface.
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..models.track import Track
from ..schemas.track import TrackStats, TrackIssueItem

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Allowed sort columns — never interpolate user input directly into SQL
# ---------------------------------------------------------------------------
_SORT_COLUMNS = {
    "artist":       "LOWER(COALESCE(artist, ''))",
    "title":        "LOWER(COALESCE(title, ''))",
    "bpm":          "bpm",
    "processed_at": "processed_at",
    "filename":     "LOWER(filename)",
}
_DEFAULT_SORT = "artist"


# ---------------------------------------------------------------------------
# list_tracks
# ---------------------------------------------------------------------------

def list_tracks(
    *,
    q: Optional[str] = None,
    status: Optional[str] = None,
    artist: Optional[str] = None,
    genre: Optional[str] = None,
    key: Optional[str] = None,
    quality_tier: Optional[str] = None,
    bpm_min: Optional[float] = None,
    bpm_max: Optional[float] = None,
    sort: str = _DEFAULT_SORT,
    order: str = "asc",
    limit: int = 100,
    offset: int = 0,
) -> Tuple[List[Track], int]:
    """
    Return (rows, total_count) for the given filters.

    total_count is the count with filters applied but without limit/offset,
    so callers can build pagination controls.
    """
    if not pipeline_db_exists():
        return [], 0

    where_clauses: List[str] = []
    params: List[object] = []

    if q:
        term = f"%{q}%"
        where_clauses.append(
            "(artist LIKE ? OR title LIKE ? OR filename LIKE ?)"
        )
        params.extend([term, term, term])

    if status:
        where_clauses.append("status = ?")
        params.append(status)

    if artist:
        where_clauses.append("LOWER(COALESCE(artist,'')) = LOWER(?)")
        params.append(artist)

    if genre:
        where_clauses.append("LOWER(COALESCE(genre,'')) = LOWER(?)")
        params.append(genre)

    if key:
        where_clauses.append(
            "(LOWER(COALESCE(key_camelot,'')) = LOWER(?) OR LOWER(COALESCE(key_musical,'')) = LOWER(?))"
        )
        params.extend([key, key])

    if quality_tier:
        where_clauses.append("quality_tier = ?")
        params.append(quality_tier.upper())

    if bpm_min is not None:
        where_clauses.append("bpm >= ?")
        params.append(bpm_min)

    if bpm_max is not None:
        where_clauses.append("bpm <= ?")
        params.append(bpm_max)

    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sort_col = _SORT_COLUMNS.get(sort, _SORT_COLUMNS[_DEFAULT_SORT])
    order_dir = "ASC" if order.lower() != "desc" else "DESC"

    try:
        with get_pipeline_conn() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM tracks {where_sql}", params
            ).fetchone()[0]

            rows = conn.execute(
                f"""SELECT * FROM tracks {where_sql}
                    ORDER BY {sort_col} {order_dir}
                    LIMIT ? OFFSET ?""",
                params + [limit, offset],
            ).fetchall()

        return [Track.from_row(r) for r in rows], total

    except FileNotFoundError:
        return [], 0
    except Exception as exc:
        log.exception("list_tracks query failed: %s", exc)
        return [], 0


# ---------------------------------------------------------------------------
# get_track
# ---------------------------------------------------------------------------

def get_track(track_id: int) -> Optional[Track]:
    if not pipeline_db_exists():
        return None
    try:
        with get_pipeline_conn() as conn:
            row = conn.execute(
                "SELECT * FROM tracks WHERE id = ?", (track_id,)
            ).fetchone()
        return Track.from_row(row) if row else None
    except Exception as exc:
        log.exception("get_track(%s) failed: %s", track_id, exc)
        return None


# ---------------------------------------------------------------------------
# get_stats
# ---------------------------------------------------------------------------

def get_stats() -> TrackStats:
    empty = TrackStats(
        total=0,
        by_status={},
        by_quality={},
        missing_bpm=0,
        missing_key=0,
        missing_artist=0,
        missing_title=0,
    )
    if not pipeline_db_exists():
        return empty
    try:
        with get_pipeline_conn() as conn:
            agg = conn.execute(
                """SELECT
                       COUNT(*)                                                     AS total,
                       SUM(CASE WHEN bpm IS NULL THEN 1 ELSE 0 END)                AS missing_bpm,
                       SUM(CASE WHEN key_camelot IS NULL
                                 AND key_musical IS NULL THEN 1 ELSE 0 END)        AS missing_key,
                       SUM(CASE WHEN TRIM(COALESCE(artist,'')) = ''
                                THEN 1 ELSE 0 END)                                 AS missing_artist,
                       SUM(CASE WHEN TRIM(COALESCE(title,''))  = ''
                                THEN 1 ELSE 0 END)                                 AS missing_title
                   FROM tracks"""
            ).fetchone()

            by_status: Dict[str, int] = {
                row["status"]: row["cnt"]
                for row in conn.execute(
                    "SELECT status, COUNT(*) AS cnt FROM tracks GROUP BY status"
                ).fetchall()
            }

            by_quality: Dict[str, int] = {
                (row["quality_tier"] or "UNKNOWN"): row["cnt"]
                for row in conn.execute(
                    """SELECT COALESCE(quality_tier,'UNKNOWN') AS quality_tier,
                              COUNT(*) AS cnt
                       FROM tracks GROUP BY quality_tier"""
                ).fetchall()
            }

        return TrackStats(
            total=agg["total"] or 0,
            by_status=by_status,
            by_quality=by_quality,
            missing_bpm=agg["missing_bpm"] or 0,
            missing_key=agg["missing_key"] or 0,
            missing_artist=agg["missing_artist"] or 0,
            missing_title=agg["missing_title"] or 0,
        )
    except Exception as exc:
        log.exception("get_stats failed: %s", exc)
        return empty


# ---------------------------------------------------------------------------
# get_issues
# ---------------------------------------------------------------------------

def get_issues(limit: int = 200) -> List[TrackIssueItem]:
    """Return tracks that have at least one issue flag."""
    if not pipeline_db_exists():
        return []
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM tracks
                   WHERE bpm IS NULL
                      OR (key_camelot IS NULL AND key_musical IS NULL)
                      OR TRIM(COALESCE(artist,'')) = ''
                      OR TRIM(COALESCE(title,''))  = ''
                      OR quality_tier = 'LOW'
                      OR status IN ('error', 'needs_review')
                   ORDER BY
                       CASE status
                           WHEN 'error'        THEN 0
                           WHEN 'needs_review' THEN 1
                           ELSE                     2
                       END,
                       LOWER(COALESCE(artist,''))
                   LIMIT ?""",
                (limit,),
            ).fetchall()

        items: List[TrackIssueItem] = []
        for row in rows:
            t = Track.from_row(row)
            items.append(
                TrackIssueItem(
                    id=t.id,
                    filepath=t.filepath,
                    filename=t.filename,
                    artist=t.artist,
                    title=t.title,
                    status=t.status,
                    issues=t.issues,
                )
            )
        return items
    except Exception as exc:
        log.exception("get_issues failed: %s", exc)
        return []
