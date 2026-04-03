"""
utils/prompt_logger.py — Timestamped prompt/response logger for LLM requests.

Every call to save() writes a new file to ./last-prompts/ at the project root.
Files are never overwritten — each request produces a unique timestamped file.

File format:
    YYYYMMDD_HHMMSS__prompt_log.txt

Used by utils/llm_client.py so all LLM calls are logged automatically.
"""
from __future__ import annotations

import traceback
from datetime import datetime
from pathlib import Path
from typing import Optional

# ./last-prompts/ relative to project root (two levels up from utils/)
_LOG_DIR  = Path(__file__).parent.parent / "last-prompts"
_MAX_LINES = 1000


def _truncate(text: str, label: str = "") -> str:
    """Keep last _MAX_LINES lines; prepend a notice if lines were dropped."""
    lines = text.splitlines()
    if len(lines) <= _MAX_LINES:
        return text
    dropped = len(lines) - _MAX_LINES
    header  = f"[... {dropped} lines truncated — showing last {_MAX_LINES} ...]"
    return header + "\n" + "\n".join(lines[-_MAX_LINES:])


def save(
    prompt:    str,
    response:  Optional[str] = None,
    error:     Optional[str] = None,
    model:     str = "",
    extra:     Optional[dict] = None,
) -> Path:
    """
    Write a prompt/response log file to ./last-prompts/.

    Args:
        prompt:   Full prompt text sent to the LLM.
        response: Response text (None if the call failed or was interrupted).
        error:    Error message or traceback string, if any.
        model:    Model identifier (e.g. "claude-haiku-4-5-20251001").
        extra:    Optional dict of additional key/value metadata lines.

    Returns:
        Path of the written log file.
    """
    _LOG_DIR.mkdir(parents=True, exist_ok=True)

    ts       = datetime.now()
    filename = ts.strftime("%Y%m%d_%H%M%S") + "__prompt_log.txt"
    log_path = _LOG_DIR / filename

    # If a file with this exact second already exists, append microseconds
    if log_path.exists():
        filename = ts.strftime("%Y%m%d_%H%M%S") + f"_{ts.microsecond:06d}__prompt_log.txt"
        log_path = _LOG_DIR / filename

    sep = "=" * 60

    sections: list[str] = [
        f"timestamp : {ts.isoformat()}",
        f"model     : {model or 'unknown'}",
        f"status    : {'ok' if error is None else 'error/interrupted'}",
    ]
    if extra:
        for k, v in extra.items():
            sections.append(f"{k:<10}: {v}")

    sections += [
        "",
        sep,
        "PROMPT",
        sep,
        _truncate(prompt),
    ]

    if response is not None:
        sections += [
            "",
            sep,
            "RESPONSE",
            sep,
            _truncate(response),
        ]
    else:
        sections += [
            "",
            sep,
            "RESPONSE  (none — call failed or was interrupted)",
            sep,
        ]

    if error:
        sections += [
            "",
            sep,
            "ERROR / INTERRUPTED",
            sep,
            error,
        ]

    log_path.write_text("\n".join(sections) + "\n", encoding="utf-8")
    print(f"[prompt_logger] saved → {log_path}")
    return log_path
