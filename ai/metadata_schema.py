"""
ai/metadata_schema.py — Strict validated schema for AI-proposed normalized metadata.

Defines NormalizedMetadata, a dataclass that:
  - Accepts raw model output (dict) via from_dict()
  - Sanitizes all string fields (unicode normalize, strip whitespace, empty → None)
  - Sanitizes list fields (filters non-strings and empty values)
  - Clamps confidence to [0.0, 1.0]
  - Never raises on malformed model output — returns safe defaults instead

No external dependencies beyond stdlib.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_AI_CONFIDENCE = 0.80

# Structured rejection reason tokens — used in NormalizeResult and log messages
REJECTION_LOW_CONFIDENCE  = "low_confidence"
REJECTION_SCHEMA_INVALID  = "schema_invalid"
REJECTION_GUARDRAIL       = "guardrail_violation"
REJECTION_PARSED_CONFLICT = "parsed_conflict"
REJECTION_AI_ERROR        = "ai_error"


# ---------------------------------------------------------------------------
# Internal sanitization helpers (stdlib only, no project imports)
# ---------------------------------------------------------------------------

def _clean_str(value: object) -> Optional[str]:
    """
    Sanitize a single value from AI output to a clean string or None.
    Rejects non-strings, empty-after-strip values, and sentinel "null"/"none" strings.
    """
    if not isinstance(value, str):
        return None
    v = unicodedata.normalize("NFC", value).strip()
    if not v:
        return None
    # Model sometimes returns the literal word "null" or "none"
    if v.lower() in {"null", "none", "n/a", "unknown", "—", "-"}:
        return None
    return v


def _clean_list(value: object) -> List[str]:
    """
    Sanitize a list field from AI output.
    Filters out non-strings, empties, and sentinel values.
    Accepts a single string and wraps it in a list.
    """
    if isinstance(value, str):
        # Model occasionally returns a comma-separated string instead of a list
        value = [v.strip() for v in value.split(",")]
    if not isinstance(value, list):
        return []
    result: List[str] = []
    for item in value:
        s = _clean_str(item)
        if s:
            result.append(s)
    return result


def _clamp_confidence(value: object) -> float:
    """Parse confidence to float and clamp to [0.0, 1.0]."""
    try:
        f = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, f))


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

@dataclass
class NormalizedMetadata:
    """
    Validated, sanitized metadata proposal from the AI normalizer.

    Fields:
        artist           Primary artist name (without featured artists)
        title            Track title (without version/mix suffix)
        version          Mix/version string, e.g. "Original Mix", "Extended Mix"
        label            Record label name
        remixers         Remixer names (if the track is a remix)
        featured_artists Guest/featured artists split from the artist field
        confidence       Model's confidence in the proposal, 0.0–1.0
        notes            Free-text reasoning from the model (not written to tags)
    """
    artist:           Optional[str]  = None
    title:            Optional[str]  = None
    version:          Optional[str]  = None
    label:            Optional[str]  = None
    remixers:         List[str]      = field(default_factory=list)
    featured_artists: List[str]      = field(default_factory=list)
    confidence:       float          = 0.0
    notes:            Optional[str]  = None
    guardrail_fired:  bool           = False

    @classmethod
    def from_dict(cls, data: dict) -> "NormalizedMetadata":
        """
        Parse and validate a raw dict (AI model output) into this schema.
        Tolerates missing keys, wrong types, and sentinel null values.
        """
        return cls(
            artist=_clean_str(data.get("artist")),
            title=_clean_str(data.get("title")),
            version=_clean_str(data.get("version")),
            label=_clean_str(data.get("label")),
            remixers=_clean_list(data.get("remixers", [])),
            featured_artists=_clean_list(data.get("featured_artists", [])),
            confidence=_clamp_confidence(data.get("confidence", 0.0)),
            notes=_clean_str(data.get("notes")),
        )

    def to_dict(self) -> dict:
        """Serialize to a plain dict (useful for JSON output)."""
        return {
            "artist":           self.artist,
            "title":            self.title,
            "version":          self.version,
            "label":            self.label,
            "remixers":         self.remixers,
            "featured_artists": self.featured_artists,
            "confidence":       self.confidence,
            "notes":            self.notes,
            "guardrail_fired":  self.guardrail_fired,
        }


# ---------------------------------------------------------------------------
# Per-track normalization result (replaces raw 3-tuple)
# ---------------------------------------------------------------------------

@dataclass
class NormalizeResult:
    """
    Structured return type from _normalize_track().

    Replaces the raw tuple (current_tags, proposed, error).
    rejection_reason is always set when the result is a no-op — the error
    field carries human-readable detail for ai_error / schema_invalid cases.
    """
    current_tags:     Dict[str, str]
    proposed:         NormalizedMetadata
    rejection_reason: Optional[str] = None   # one of the REJECTION_* constants
    error:            Optional[str] = None   # machine-readable detail (ai / parse errors)

    @property
    def rejected(self) -> bool:
        """True when this result is a no-op — do not apply any changes."""
        return self.rejection_reason is not None
