"""
ai/ollama_client.py — Minimal HTTP client for a local Ollama instance.

Uses only stdlib (urllib.request + json) — no extra dependencies.

Ollama API used:
  GET  /api/tags      → healthcheck (checks the server is up and models are loaded)
  POST /api/generate  → text generation with stream=false
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Optional


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class OllamaError(Exception):
    """Base class for all Ollama client errors."""


class OllamaConnectionError(OllamaError):
    """Could not reach the Ollama server (not running, wrong URL, firewall)."""


class OllamaTimeoutError(OllamaError):
    """Ollama request exceeded the configured timeout."""


class OllamaModelError(OllamaError):
    """The requested model is not available on this Ollama instance."""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class OllamaClient:
    """
    Thin wrapper around the Ollama local HTTP API.

    Args:
        base_url: Base URL of the Ollama server (default: http://127.0.0.1:11434)
        model:    Default model name to use for generate() calls
        timeout:  Request timeout in seconds (applies to generate; healthcheck uses 5s)
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "qwen2.5-coder:3b",
        timeout: int = 120,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model    = model
        self.timeout  = timeout

    # ------------------------------------------------------------------
    # Healthcheck
    # ------------------------------------------------------------------

    def healthcheck(self) -> bool:
        """
        Return True if Ollama is running and has at least one model loaded.
        Uses a short 5-second timeout so it fails fast.
        """
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status != 200:
                    return False
                body = json.loads(resp.read().decode("utf-8"))
                # /api/tags returns {"models": [...]}
                return isinstance(body.get("models"), list)
        except Exception:
            return False

    def list_models(self) -> list[str]:
        """Return names of all models available on this Ollama instance."""
        try:
            req = urllib.request.Request(
                f"{self.base_url}/api/tags",
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return [m.get("name", "") for m in body.get("models", [])]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(self, prompt: str, model: Optional[str] = None) -> str:
        """
        Send a prompt to Ollama and return the response text.

        Args:
            prompt: The full prompt string to send.
            model:  Model name override; uses self.model if omitted.

        Returns:
            Generated text string.

        Raises:
            OllamaConnectionError: Cannot reach the server.
            OllamaTimeoutError:    Request timed out.
            OllamaModelError:      Model not found on the server.
            OllamaError:           Any other Ollama-side error.
        """
        target_model = model or self.model
        payload = json.dumps({
            "model":  target_model,
            "prompt": prompt,
            "stream": False,
        }).encode("utf-8")

        req = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                return body.get("response", "")

        except urllib.error.HTTPError as exc:
            # 404 usually means the model is not pulled yet
            if exc.code == 404:
                raise OllamaModelError(
                    f"Model '{target_model}' not found on {self.base_url}. "
                    f"Run: ollama pull {target_model}"
                ) from exc
            raise OllamaError(
                f"Ollama HTTP {exc.code} from {self.base_url}: {exc.reason}"
            ) from exc

        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if "timed out" in reason.lower() or "timeout" in reason.lower():
                raise OllamaTimeoutError(
                    f"Ollama generate timed out after {self.timeout}s "
                    f"(model={target_model})"
                ) from exc
            raise OllamaConnectionError(
                f"Could not reach Ollama at {self.base_url}: {exc.reason}"
            ) from exc

        except TimeoutError as exc:
            raise OllamaTimeoutError(
                f"Ollama generate timed out after {self.timeout}s "
                f"(model={target_model})"
            ) from exc
