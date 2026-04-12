"""
DJ Toolkit — FastAPI backend entry point.

Start the server:
  uvicorn backend.app.main:app --reload --port 8000

From the project root (djtoolkit/):
  uvicorn backend.app.main:app --reload --port 8000 --app-dir .
"""
from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import analysis as analysis_router
from .api.routes import exports as exports_router
from .api.routes import health as health_router
from .api.routes import jobs as jobs_router
from .api.routes import playlists as playlists_router
from .api.routes import sync as sync_router
from .api.routes import tracks as tracks_router
from .core.config import BACKEND_VERSION, PIPELINE_DB_PATH, PIPELINE_PY, TOOLKIT_ROOT
from .core.db import init_db

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (startup / shutdown)
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- startup ---
    log.info("DJ Toolkit backend v%s starting up", BACKEND_VERSION)
    log.info("Toolkit root : %s", TOOLKIT_ROOT)
    log.info("pipeline.py  : %s  (exists=%s)", PIPELINE_PY, PIPELINE_PY.is_file())
    log.info("Pipeline DB  : %s  (exists=%s)", PIPELINE_DB_PATH, PIPELINE_DB_PATH.exists())

    init_db()

    yield

    # --- shutdown ---
    log.info("DJ Toolkit backend shutting down")


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(
    title="DJ Toolkit API",
    description=(
        "Local-first REST API wrapper around the DJ Toolkit pipeline. "
        "Submit pipeline jobs, track their progress, and stream their logs."
    ),
    version=BACKEND_VERSION,
    lifespan=lifespan,
)

# Allow the local frontend dev server (Phase 2) to call the API.
# In production restrict origins explicitly.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

API_PREFIX = "/api"

app.include_router(health_router.router,     prefix=API_PREFIX)
app.include_router(jobs_router.router,       prefix=API_PREFIX)
app.include_router(tracks_router.router,     prefix=API_PREFIX)
app.include_router(analysis_router.router,   prefix=API_PREFIX)
app.include_router(playlists_router.router,  prefix=API_PREFIX)
app.include_router(exports_router.router,    prefix=API_PREFIX)
app.include_router(sync_router.router,       prefix=API_PREFIX)
