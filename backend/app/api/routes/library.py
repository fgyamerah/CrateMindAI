"""
Library tree + stats routes.

GET /api/library/tree   — real directory tree under MUSIC_ROOT
GET /api/library/stats  — global + folder-scoped track counts from the pipeline DB
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ...core.config import MUSIC_ROOT
from ...core.pipeline_db import get_pipeline_conn, pipeline_db_exists

router = APIRouter(tags=["library"])

# Directories under MUSIC_ROOT that contain no audio files and should not
# appear as job targets.  Keep this list small and explicit.
_SKIP_ROOT_NAMES: frozenset = frozenset({
    "logs",
    "data",
    "playlists",
    "_PLAYLISTS_M3U_EXPORT",
    "_REKORDBOX_XML_EXPORT",
    "processing",   # transient working dir — not a stable target
    "__pycache__",
    ".git",
})


class LibraryNode(BaseModel):
    label:      str
    path:       str
    executable: bool = True   # all returned nodes are real filesystem paths
    children:   List["LibraryNode"] = []

LibraryNode.model_rebuild()


class LibraryTreeResponse(BaseModel):
    root: LibraryNode


def _safe_iterdir(directory: Path) -> list[Path]:
    """List immediate subdirectories, sorted case-insensitively.  Never raises."""
    try:
        return sorted(
            [p for p in directory.iterdir() if p.is_dir()],
            key=lambda p: p.name.lower(),
        )
    except (PermissionError, OSError):
        return []


def _build_node(directory: Path, current_depth: int, max_depth: int) -> LibraryNode:
    node = LibraryNode(label=directory.name, path=str(directory))
    if current_depth >= max_depth:
        return node
    for child in _safe_iterdir(directory):
        node.children.append(_build_node(child, current_depth + 1, max_depth))
    return node


def _build_tree(max_depth: int) -> LibraryNode:
    """
    Build the library tree rooted at MUSIC_ROOT.

    Top-level non-audio directories are excluded.  All other directories,
    including library sub-categories and inbox sub-categories, are included.
    The 'sorted' directory is shown but its letter-based children are only
    included if max_depth allows.
    """
    root = LibraryNode(label="KKDJ", path=str(MUSIC_ROOT))

    for entry in _safe_iterdir(MUSIC_ROOT):
        if entry.name in _SKIP_ROOT_NAMES:
            continue
        child = _build_node(entry, 1, max_depth)
        root.children.append(child)

    return root


# ---------------------------------------------------------------------------
# GET /api/library/stats
# ---------------------------------------------------------------------------

class LibraryStatsResponse(BaseModel):
    global_count: int
    folder_count: int


def _filepath_prefixes(path_str: str) -> list[str]:
    """
    Return all filepath prefix variants to match against.

    The pipeline DB may store filepaths with MUSIC_ROOT as either:
    - the resolved canonical path  (/home/user/Music/music/...)
    - the /music symlink            (/music/...)
    We try both so the LIKE query works regardless of how the pipeline was run.
    """
    p = path_str.rstrip("/")
    prefixes: set[str] = {p + "/"}
    try:
        resolved = str(Path(path_str).resolve()).rstrip("/")
        prefixes.add(resolved + "/")
    except Exception:
        pass
    # Substitute between canonical root and /music symlink
    canon = str(MUSIC_ROOT).rstrip("/")
    symlink = "/music"
    for pf in list(prefixes):
        base = pf.rstrip("/")
        if base.startswith(canon):
            prefixes.add(symlink + base[len(canon):] + "/")
        elif base.startswith(symlink):
            prefixes.add(canon + base[len(symlink):] + "/")
    return list(prefixes)


def _is_safe_path(path_str: str) -> bool:
    """Accept only paths that resolve inside MUSIC_ROOT."""
    try:
        p = Path(path_str).resolve()
        root = MUSIC_ROOT
        return p == root or root in p.parents
    except Exception:
        return False


@router.get("/library/stats", response_model=LibraryStatsResponse)
async def get_library_stats(
    path: Optional[str] = Query(default=None, description="Directory path to scope the count"),
) -> LibraryStatsResponse:
    """
    Return global track count and, if a path is given, a folder-scoped count.

    Handles both canonical and /music-symlink filepath forms in the DB.
    """
    empty = LibraryStatsResponse(global_count=0, folder_count=0)
    if not pipeline_db_exists():
        return empty
    try:
        with get_pipeline_conn() as conn:
            global_count: int = conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
            folder_count = global_count

            if path and _is_safe_path(path):
                prefixes = _filepath_prefixes(path)
                placeholders = " OR ".join(["filepath LIKE ?" for _ in prefixes])
                row = conn.execute(
                    f"SELECT COUNT(*) FROM tracks WHERE {placeholders}",
                    [pf + "%" for pf in prefixes],
                ).fetchone()
                folder_count = row[0] if row else 0

        return LibraryStatsResponse(global_count=global_count, folder_count=folder_count)
    except Exception:
        return empty


@router.get("/library/tree", response_model=LibraryTreeResponse)
async def get_library_tree(
    depth: int = Query(
        default=2,
        ge=1,
        le=4,
        description=(
            "How many directory levels to expand below MUSIC_ROOT. "
            "depth=1 shows only top-level folders; depth=2 expands inbox/* and library/*."
        ),
    ),
) -> LibraryTreeResponse:
    """
    Return the audio library folder tree rooted at MUSIC_ROOT.

    Every returned node is a real filesystem directory.  Its `path` field can
    be passed directly as --input to metadata-sanitize and other pipeline jobs.
    Non-audio directories (logs, playlists, exports) are excluded.
    """
    return LibraryTreeResponse(root=_build_tree(max_depth=depth))
