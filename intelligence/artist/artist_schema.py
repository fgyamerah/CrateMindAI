"""
artist_intelligence/artist_schema.py — Typed schema for artist intelligence.

Mirrors the NormalizedMetadata pattern in ai/metadata_schema.py so both
AI-based and deterministic intelligence layers share a recognisable shape.

Designed for reuse: a future label_intelligence package can copy this
schema pattern with LabelEntity / LabelParseResult instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ArtistEntity:
    """
    A single resolved artist with raw, normalized, and canonical forms.

    Fields:
        raw         — value exactly as read from the audio tag or filename
        normalized  — after whitespace / punctuation / unicode cleanup
        canonical   — alias-store resolved canonical name, or None if unknown
        confidence  — how confident we are this entity is correctly identified
        source      — where the value came from: "tag", "filename", "alias_store"
    """
    raw:        str
    normalized: str
    canonical:  Optional[str] = None
    confidence: float         = 1.0
    source:     str           = "tag"

    @property
    def best(self) -> str:
        """Return the best available form: canonical > normalized > raw."""
        return self.canonical or self.normalized or self.raw


@dataclass
class ArtistParseResult:
    """
    Full parse result for an artist tag string.

    Fields:
        main_artists      — one ArtistEntity per detected main artist
        featured_artists  — raw strings kept as-is (feat tokens from the
                            ARTIST field only — title feat is never touched)
        remixers          — raw remixer strings when found in the artist field
        confidence        — overall parse confidence (0.0–1.0)
        notes             — human-readable explanation, set for uncertain cases
    """
    main_artists:     List[ArtistEntity] = field(default_factory=list)
    featured_artists: List[str]          = field(default_factory=list)
    remixers:         List[str]          = field(default_factory=list)
    confidence:       float              = 1.0
    notes:            Optional[str]      = None
