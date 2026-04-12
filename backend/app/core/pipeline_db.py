"""
Read-only connection to the toolkit's pipeline database (processed.db).

The backend NEVER writes to this database — it belongs to the pipeline.
All queries here are SELECT-only.  If the DB does not exist yet (no
pipeline run has happened), callers receive an empty result set rather
than an error.
"""
from __future__ import annotations

import contextlib
import logging
import sqlite3
from typing import Iterator

from .config import PIPELINE_DB_PATH

log = logging.getLogger(__name__)


@contextlib.contextmanager
def get_pipeline_conn() -> Iterator[sqlite3.Connection]:
    """
    Yield a read-only WAL connection to processed.db.

    Raises FileNotFoundError if the database does not exist — callers
    should catch this and return an appropriate empty response.
    """
    if not PIPELINE_DB_PATH.exists():
        raise FileNotFoundError(
            f"Pipeline database not found at {PIPELINE_DB_PATH}. "
            "Run the pipeline at least once to create it."
        )
    # uri=True + ?mode=ro prevents any accidental write
    conn = sqlite3.connect(
        f"file:{PIPELINE_DB_PATH}?mode=ro",
        uri=True,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def pipeline_db_exists() -> bool:
    return PIPELINE_DB_PATH.exists()
