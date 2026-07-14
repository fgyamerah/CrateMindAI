"""
playlist_service — read-only access to set_playlists from the pipeline DB.

The backend never writes to the pipeline database.  All playlist records
are created by the pipeline's set_builder module (via a subprocess job).
The service here only reads them back for display in the UI.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from ..core.pipeline_db import get_pipeline_conn, pipeline_db_exists
from ..schemas.playlist import PlaylistDetail, PlaylistSummary, SetTrackResponse

log = logging.getLogger(__name__)


def list_playlists(limit: int = 50, offset: int = 0) -> List[PlaylistSummary]:
    """Return recent set playlists from the pipeline DB, newest first."""
    if not pipeline_db_exists():
        return []
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                """SELECT id, name, created_at, duration_sec, track_count, config_json
                   FROM set_playlists
                   ORDER BY created_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            ).fetchall()
        return [
            PlaylistSummary(
                id=r["id"],
                name=r["name"],
                created_at=r["created_at"],
                duration_sec=float(r["duration_sec"] or 0),
                track_count=int(r["track_count"] or 0),
                config_json=r["config_json"],
            )
            for r in rows
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.warning("list_playlists failed: %s", exc)
        return []


def get_playlist(playlist_id: int) -> Optional[PlaylistSummary]:
    """Return a single playlist header or None if not found."""
    if not pipeline_db_exists():
        return None
    try:
        with get_pipeline_conn() as conn:
            row = conn.execute(
                "SELECT id, name, created_at, duration_sec, track_count, config_json "
                "FROM set_playlists WHERE id=?",
                (playlist_id,),
            ).fetchone()
        if row is None:
            return None
        return PlaylistSummary(
            id=row["id"],
            name=row["name"],
            created_at=row["created_at"],
            duration_sec=float(row["duration_sec"] or 0),
            track_count=int(row["track_count"] or 0),
            config_json=row["config_json"],
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        log.warning("get_playlist(%s) failed: %s", playlist_id, exc)
        return None


def get_playlist_tracks(playlist_id: int) -> List[SetTrackResponse]:
    """Return ordered tracks for a playlist, joined with track metadata."""
    if not pipeline_db_exists():
        return []
    try:
        with get_pipeline_conn() as conn:
            rows = conn.execute(
                """SELECT spt.position, spt.phase, spt.transition_note,
                          spt.filepath,
                          t.artist, t.title, t.bpm, t.key_camelot,
                          t.genre, t.duration_sec
                   FROM set_playlist_tracks spt
                   LEFT JOIN tracks t ON t.filepath = spt.filepath
                   WHERE spt.set_id = ?
                   ORDER BY spt.position""",
                (playlist_id,),
            ).fetchall()
        return [
            SetTrackResponse(
                position=r["position"],
                phase=r["phase"] or "",
                artist=r["artist"],
                title=r["title"],
                bpm=float(r["bpm"]) if r["bpm"] is not None else None,
                key_camelot=r["key_camelot"],
                genre=r["genre"],
                duration_sec=float(r["duration_sec"]) if r["duration_sec"] is not None else None,
                transition_note=r["transition_note"],
                filepath=r["filepath"],
            )
            for r in rows
        ]
    except FileNotFoundError:
        return []
    except Exception as exc:
        log.warning("get_playlist_tracks(%s) failed: %s", playlist_id, exc)
        return []


def get_playlist_detail(playlist_id: int) -> Optional[PlaylistDetail]:
    """Return playlist header + tracks, or None if not found."""
    playlist = get_playlist(playlist_id)
    if playlist is None:
        return None
    tracks = get_playlist_tracks(playlist_id)
    return PlaylistDetail(playlist=playlist, tracks=tracks)
