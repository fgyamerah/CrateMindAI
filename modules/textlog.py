"""
Human-readable, append-only processing log.

All pipeline actions are written here in plain text so you can audit what
happened to every file without opening the SQLite DB or parsing JSON.

Thread-safe: a module-level lock serialises writes from any number of workers
calling log_action() concurrently.

Log format (matches user examples exactly):
    [2026-03-22 14:22:01] PROCESS: Black Motion - Rainbow.mp3
    [2026-03-22 14:22:02] CLEAN: title "Rainbow [fordjonly.com]" → "Rainbow"
    [2026-03-22 14:22:03] TAGGED: BPM=122, KEY=8A
    [2026-03-22 14:22:04] ORGANIZED: /music/library/sorted/B/Black Motion/...

Config:
    TEXT_LOG_PATH  — path to the log file (default: LOGS_DIR/processing_log.txt)
"""
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

import config

_lock = threading.Lock()
log   = logging.getLogger(__name__)


def log_action(message: str) -> None:
    """
    Append a timestamped entry to the human-readable processing log.

    Args:
        message: Full action string, e.g. "TAGGED: BPM=122, KEY=8A"
                 Caller is responsible for formatting. The timestamp is
                 added automatically.

    Never raises — any I/O error is swallowed and sent to the Python logger
    so pipeline execution is never blocked by a log write failure.
    """
    ts    = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry = f"[{ts}] {message}\n"

    try:
        log_path: Path = config.TEXT_LOG_PATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with _lock:
            with open(log_path, "a", encoding="utf-8") as fh:
                fh.write(entry)
    except Exception as exc:
        log.warning("textlog: could not write processing log: %s", exc)


def log_run_separator(label: str = "PIPELINE RUN") -> None:
    """Write a visible separator line between runs — makes the log easy to scan."""
    ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    sep = "=" * 60
    log_action(f"{'':->0}{sep}")
    log_action(f"START {label} at {ts}")
    log_action(f"{'':->0}{sep}")
