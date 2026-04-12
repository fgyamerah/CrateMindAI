"""
Health and version routes.

  GET /api/health   — liveness check; always returns 200 while the server is up
  GET /api/version  — backend and toolkit version strings
"""
from fastapi import APIRouter
from pydantic import BaseModel

from ...core.config import BACKEND_VERSION, PIPELINE_PY, TOOLKIT_ROOT

# Import toolkit version without running the full pipeline module
import importlib.util, sys

router = APIRouter(tags=["health"])


def _toolkit_version() -> str:
    """Read PIPELINE_VERSION from config.py without importing pipeline.py."""
    try:
        spec = importlib.util.spec_from_file_location(
            "_tk_config", str(TOOLKIT_ROOT / "config.py")
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "PIPELINE_VERSION", "unknown")
    except Exception:
        return "unknown"


class HealthResponse(BaseModel):
    status: str
    pipeline_py_found: bool


class VersionResponse(BaseModel):
    backend_version: str
    toolkit_version: str
    pipeline_py: str


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        pipeline_py_found=PIPELINE_PY.is_file(),
    )


@router.get("/version", response_model=VersionResponse)
async def version() -> VersionResponse:
    return VersionResponse(
        backend_version=BACKEND_VERSION,
        toolkit_version=_toolkit_version(),
        pipeline_py=str(PIPELINE_PY),
    )
