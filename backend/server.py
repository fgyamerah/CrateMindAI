"""
Supervisor entry shim for the CrateMindAI FastAPI backend.

The preview environment runs `uvicorn server:app` from /app/backend on port 8001.
This shim loads backend/.env (library root + sync path configuration) before the
application modules resolve their paths, then re-exports the real app.
"""
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BACKEND_DIR.parent

load_dotenv(_BACKEND_DIR / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from backend.app.main import app  # noqa: E402,F401
