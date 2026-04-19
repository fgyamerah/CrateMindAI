"""
intelligence/enrichment/enrichment_schema.py

Shared data types for the online metadata enrichment layer.

EnrichmentCandidate  — one metadata result from a remote API
EnrichmentMatch      — best candidate scored against current tags + proposed changes

Design rule: all fields are Optional so callers can partially-fill
             from APIs that do not expose every field.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class EnrichmentCandidate:
    """
    A single metadata result from an online music API.

    source       : "spotify" | "deezer" | "traxsource"
                   (future extension: "musicbrainz", …)
    artist       : primary artist string as returned by the API
    title        : track title as returned by the API (may include version)
    album        : album / release name
    label        : record label (Traxsource provides this directly; Spotify/Deezer
                   require an extra album-detail call — see their lookup modules)
    isrc         : International Standard Recording Code, e.g. "GBUM71029604"
    release_date : ISO date string — "2024-03-01" or partial "2024"
    genre        : genre/subgenre string (Traxsource; absent for Spotify/Deezer
                   search results)
    raw          : original API response dict (kept for debugging / future use)
    """
    source:       str
    artist:       Optional[str]       = None
    title:        Optional[str]       = None
    album:        Optional[str]       = None
    label:        Optional[str]       = None
    isrc:         Optional[str]       = None
    release_date: Optional[str]       = None
    genre:        Optional[str]       = None
    raw:          Dict[str, Any]      = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source":       self.source,
            "artist":       self.artist,
            "title":        self.title,
            "album":        self.album,
            "label":        self.label,
            "isrc":         self.isrc,
            "release_date": self.release_date,
            "genre":        self.genre,
        }


@dataclass
class EnrichmentMatch:
    """
    A scored candidate paired with proposed tag changes for one track.

    candidate        : best EnrichmentCandidate found (None = no result)
    confidence       : composite similarity score 0.0–1.0
    proposed_changes : [{field, old, new}] dicts — same format as ai/normalizer.py
    source_used      : which API produced the winning result
                       ("spotify" | "deezer" | "traxsource" | "none")
    reason           : human-readable explanation of match quality and
                       any safeguards applied (version mismatch, artist guard, etc.)
    isrc_matched     : True when the match was anchored by an exact ISRC hit
    sources_tried    : ordered list of sources queried during this search
    """
    candidate:        Optional[EnrichmentCandidate] = None
    confidence:       float                         = 0.0
    proposed_changes: List[Dict[str, str]]          = field(default_factory=list)
    source_used:      str                           = "none"
    reason:           str                           = ""
    isrc_matched:     bool                          = False
    sources_tried:    List[str]                     = field(default_factory=list)

    @property
    def match_reason(self) -> str:
        """Alias for reason — preferred name in structured output."""
        return self.reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate":        self.candidate.to_dict() if self.candidate else None,
            "confidence":       self.confidence,
            "proposed_changes": self.proposed_changes,
            "source_used":      self.source_used,
            "match_reason":     self.reason,
            "isrc_matched":     self.isrc_matched,
            "sources_tried":    self.sources_tried,
        }
