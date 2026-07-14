"""
Beatport label-clean provider — Phase 2 placeholder.

This module is intentionally a no-op stub for the label-clean flow.

Note: bulk Beatport label scraping is already available via
  python pipeline.py label-intel --label-sources beatport

This stub is for single-label lookup during the label-clean flow
(matching a detected label name against Beatport's label catalogue)
— a separate use case not yet implemented.

Phase 2 plan:
  - Resolve a normalized label name to a Beatport label page
  - Confirm canonical display name and Beatport label ID
  - Reuse BeatportSource from label_intel.sources.beatport where possible
"""
from __future__ import annotations

from typing import Optional


class BeatportCleanProvider:
    """
    Single-label lookup against Beatport.

    Not yet implemented — calling match() raises NotImplementedError.
    """

    def match(self, label_name: str) -> Optional[dict]:
        raise NotImplementedError(
            "Beatport clean provider is not yet implemented (Phase 2). "
            "For bulk label scraping use: python pipeline.py label-intel"
        )
