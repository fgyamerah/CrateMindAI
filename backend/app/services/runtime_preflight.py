"""
Runtime preflight checks.

Read-only environment validation: verifies the selected library root, the
pipeline database, backend storage, required tools, and optional provider
configuration. Never exposes secret values — only presence booleans.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from datetime import datetime, timezone
from typing import Any

from ..core.config import (
    BACKEND_DATA_DIR,
    PIPELINE_PY,
    RSYNC_BIN,
    SYNC_DEST_SSD,
    SYNC_SOURCE_LIBRARY,
)
from ..core.library_root import library_db_path, selected_library_root


def _check(
    check_id: str,
    label: str,
    status: str,
    detail: str,
    remediation: str = "",
    optional: bool = False,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "label": label,
        "status": status,
        "detail": detail,
        "remediation": remediation,
        "optional": optional,
    }


def run_preflight() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    root_display = ""

    # --- library root -------------------------------------------------------
    try:
        root = selected_library_root()
        root_display = str(root)
        if not root.exists():
            checks.append(_check(
                "library_root", "Library root", "fail",
                f"Selected root does not exist: {root}",
                "Set CRATEMINDAI_LIBRARY_ROOT to an existing library directory.",
            ))
            root = None
        elif not root.is_dir():
            checks.append(_check(
                "library_root", "Library root", "fail",
                f"Selected root is not a directory: {root}",
                "Point CRATEMINDAI_LIBRARY_ROOT at the library folder.",
            ))
            root = None
        elif not os.access(root, os.R_OK):
            checks.append(_check(
                "library_root", "Library root", "fail",
                f"Selected root is not readable: {root}",
                "Fix filesystem permissions on the library root.",
            ))
            root = None
        else:
            checks.append(_check(
                "library_root", "Library root", "pass", f"{root}",
            ))
    except RuntimeError as exc:
        checks.append(_check(
            "library_root", "Library root", "fail", str(exc),
            "Set CRATEMINDAI_LIBRARY_ROOT to an absolute library path.",
        ))
        root = None

    # --- library root writable (needed for apply operations) ----------------
    if root is not None:
        if os.access(root, os.W_OK):
            checks.append(_check(
                "library_root_writable", "Library root writable", "pass",
                "Write-capable operations can create logs and queue files.",
            ))
        else:
            checks.append(_check(
                "library_root_writable", "Library root writable", "warn",
                f"Root is read-only: {root}. Scans work; apply operations are blocked.",
                "Fix filesystem permissions before running apply operations.",
            ))

    # --- pipeline database ---------------------------------------------------
    if root is not None:
        db_path = library_db_path(root)
        if not db_path.exists():
            checks.append(_check(
                "pipeline_db", "Pipeline database", "warn",
                f"No database at {db_path}. The library has not been scanned yet.",
                "Run build-tracks (or the pipeline) to create the library database.",
            ))
        else:
            try:
                conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
                conn.execute("SELECT 1")
                conn.close()
                checks.append(_check(
                    "pipeline_db", "Pipeline database", "pass", str(db_path),
                ))
            except sqlite3.Error as exc:
                checks.append(_check(
                    "pipeline_db", "Pipeline database", "fail",
                    f"Database at {db_path} could not be opened read-only: {exc}",
                    "Restore the database from backup or re-run build-tracks.",
                ))

    # --- pipeline entry point -------------------------------------------------
    if PIPELINE_PY.is_file():
        checks.append(_check(
            "pipeline_py", "Pipeline entry point", "pass", str(PIPELINE_PY),
        ))
    else:
        checks.append(_check(
            "pipeline_py", "Pipeline entry point", "fail",
            f"pipeline.py not found at {PIPELINE_PY}",
            "Verify the CrateMindAI installation directory.",
        ))

    # --- backend job storage ---------------------------------------------------
    try:
        BACKEND_DATA_DIR.mkdir(parents=True, exist_ok=True)
        writable = os.access(BACKEND_DATA_DIR, os.W_OK)
    except OSError:
        writable = False
    if writable:
        checks.append(_check(
            "jobs_storage", "Job storage", "pass", str(BACKEND_DATA_DIR),
        ))
    else:
        checks.append(_check(
            "jobs_storage", "Job storage", "fail",
            f"Backend data directory is not writable: {BACKEND_DATA_DIR}",
            "Fix permissions on backend/data so jobs can be recorded.",
        ))

    # --- rsync (needed only for SSD sync) ---------------------------------------
    if shutil.which("rsync") or os.path.isfile(RSYNC_BIN):
        checks.append(_check(
            "rsync", "rsync binary", "pass", RSYNC_BIN, optional=True,
        ))
    else:
        checks.append(_check(
            "rsync", "rsync binary", "warn",
            "rsync not found. SSD sync is unavailable.",
            "Install rsync to enable SSD synchronization.",
            optional=True,
        ))

    # --- sync source / destination ------------------------------------------------
    if SYNC_SOURCE_LIBRARY.is_dir():
        checks.append(_check(
            "sync_source", "Sync source (library)", "pass",
            str(SYNC_SOURCE_LIBRARY), optional=True,
        ))
    else:
        checks.append(_check(
            "sync_source", "Sync source (library)", "warn",
            f"Configured sync source does not exist: {SYNC_SOURCE_LIBRARY}",
            "Set CRATEMINDAI_SYNC_SOURCE_LIBRARY to the working library folder.",
            optional=True,
        ))

    if SYNC_DEST_SSD.is_dir():
        checks.append(_check(
            "sync_dest", "Sync destination (SSD)", "pass",
            str(SYNC_DEST_SSD), optional=True,
        ))
    else:
        checks.append(_check(
            "sync_dest", "Sync destination (SSD)", "warn",
            f"SSD destination not mounted: {SYNC_DEST_SSD}",
            "Mount the external SSD (or set CRATEMINDAI_SYNC_DEST) before syncing.",
            optional=True,
        ))

    # --- optional providers (presence only, never values) --------------------------
    spotify_configured = bool(
        os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET")
    )
    checks.append(_check(
        "provider_spotify", "Spotify enrichment provider",
        "pass" if spotify_configured else "warn",
        "Credentials configured." if spotify_configured
        else "Not configured. Online enrichment via Spotify is unavailable.",
        "" if spotify_configured
        else "Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET to enable (optional).",
        optional=True,
    ))

    ollama_url = os.environ.get("OLLAMA_BASE_URL", "")
    checks.append(_check(
        "provider_ollama", "Local AI (Ollama)",
        "pass" if ollama_url else "warn",
        f"Configured at {ollama_url}" if ollama_url
        else "Not configured. AI normalization review is unavailable.",
        "" if ollama_url
        else "Set OLLAMA_BASE_URL to a local Ollama instance to enable (optional).",
        optional=True,
    ))

    # --- overall status ---------------------------------------------------------
    has_fail = any(c["status"] == "fail" for c in checks)
    has_required_warn = any(
        c["status"] == "warn" and not c["optional"] for c in checks
    )
    if has_fail:
        status = "unsafe"
    elif has_required_warn:
        status = "degraded"
    else:
        status = "ready"

    return {
        "status": status,
        "library_root": root_display,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
    }
