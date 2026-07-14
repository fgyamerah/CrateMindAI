"""
utils/llm_client.py — Shared Claude API wrapper with automatic prompt logging.

All LLM calls in this project should go through call() so that every
request is automatically logged by utils.prompt_logger.

Usage:
    from utils.llm_client import call

    text = call("Suggest a genre for: ATFC - About This Body")
    # → response string
    # → logs to ./last-prompts/YYYYMMDD_HHMMSS__prompt_log.txt

Install the SDK if not already present:
    pip install anthropic
"""
from __future__ import annotations

import traceback
from typing import Optional

from utils.prompt_logger import save as _log

# ---------------------------------------------------------------------------
# SDK import — optional; hard error only at call time, not import time
# ---------------------------------------------------------------------------
try:
    import anthropic as _anthropic
    _client    = _anthropic.Anthropic()   # reads ANTHROPIC_API_KEY from env
    _SDK_OK    = True
except ImportError:
    _client = None
    _SDK_OK = False

_DEFAULT_MODEL     = "claude-haiku-4-5-20251001"
_DEFAULT_MAX_TOKENS = 1024


def call(
    prompt:     str,
    model:      str = _DEFAULT_MODEL,
    max_tokens: int = _DEFAULT_MAX_TOKENS,
    system:     str = "",
) -> str:
    """
    Send a prompt to Claude and return the response text.

    Automatically saves a log file via utils.prompt_logger for every call,
    including failed or interrupted calls.

    Args:
        prompt:     User message to send.
        model:      Claude model ID. Defaults to Haiku (fast, cheap).
        max_tokens: Maximum tokens in the response.
        system:     Optional system prompt.

    Returns:
        Response text string.

    Raises:
        RuntimeError: if the anthropic SDK is not installed, or if the API
                      call fails with no partial response.
    """
    if not _SDK_OK:
        err = (
            "anthropic SDK not installed.\n"
            "Run:  pip install anthropic\n"
            "Then: export ANTHROPIC_API_KEY=sk-ant-..."
        )
        _log(prompt=prompt, error=err, model=model)
        raise RuntimeError(err)

    response_text: Optional[str] = None
    error_msg:     Optional[str] = None

    try:
        messages = [{"role": "user", "content": prompt}]
        kwargs: dict = dict(model=model, max_tokens=max_tokens, messages=messages)
        if system:
            kwargs["system"] = system

        result        = _client.messages.create(**kwargs)
        response_text = result.content[0].text

    except KeyboardInterrupt:
        error_msg = "INTERRUPTED — KeyboardInterrupt raised during API call"

    except Exception:
        error_msg = traceback.format_exc()

    _log(
        prompt=prompt,
        response=response_text,
        error=error_msg,
        model=model,
    )

    if error_msg is not None and response_text is None:
        raise RuntimeError(f"LLM call failed:\n{error_msg}")

    return response_text  # type: ignore[return-value]
