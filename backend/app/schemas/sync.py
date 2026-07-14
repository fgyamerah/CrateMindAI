"""
Pydantic schemas for the SSD sync API.
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Preview (dry-run)
# ---------------------------------------------------------------------------

class SyncPreviewRequest(BaseModel):
    source: Literal["library", "inbox"] = Field(
        "library",
        description=(
            "'library' → /home/koolkatdj/Music/music/library  "
            "'inbox'   → /home/koolkatdj/Music/music/inbox"
        ),
    )


class SyncFileChange(BaseModel):
    """A single file or directory that rsync would transfer."""
    path:      str
    is_dir:    bool = False


class SyncPreviewResponse(BaseModel):
    source_path:  str
    dest_path:    str
    file_count:   int                    # total transferable files (not dirs)
    files:        List[SyncFileChange]   # up to MAX_PREVIEW_FILES entries
    truncated:    bool = False
    summary:      Optional[str] = None  # "sent X bytes  …  total size is Y"
    warnings:     List[str] = []
    ssd_mounted:  bool = True


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

class SyncRunRequest(BaseModel):
    source: Literal["library", "inbox"] = "library"
    allow_delete: bool = Field(
        False,
        description=(
            "Pass --delete to rsync — removes files from the destination that "
            "are no longer in the source.  DESTRUCTIVE: enable only intentionally."
        ),
    )


class SyncRunResponse(BaseModel):
    job_id:  str
    message: str


# ---------------------------------------------------------------------------
# Status convenience (mirrors JobResponse subset for the sync history table)
# ---------------------------------------------------------------------------

class SyncConfigResponse(BaseModel):
    """Read-only config shown on the Sync page."""
    sources:  dict[str, str]   # name → resolved path
    dest:     str
    rsync_bin: str
    ssd_mounted: bool
