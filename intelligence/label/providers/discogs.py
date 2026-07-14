"""
Discogs label-matching provider — Phase 2 placeholder.

This module is intentionally a no-op stub.
Pass --use-discogs to opt in once implemented.

Phase 2 plan:
  - Use the Discogs REST API (https://www.discogs.com/developers/)
  - Match a normalized label name to a Discogs label entity
  - Return canonical name, country, profile URL, genre hints
  - Requires a Discogs personal access token or OAuth2 app token
"""
from __future__ import annotations

from typing import Optional


class DiscogsProvider:
    """
    Single-label lookup against the Discogs API.

    Not yet implemented — calling match() raises NotImplementedError.
    """

    def __init__(self, token: Optional[str] = None) -> None:
        self.token = token

    def match(self, label_name: str) -> Optional[dict]:
        raise NotImplementedError(
            "Discogs provider is not yet implemented (Phase 2). "
            "Run with --use-discogs to see this message; remove the flag to continue."
        )
