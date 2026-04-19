"""
Conservative filename-based label extraction.

Only called when tag-based detection fails.  Returns a candidate label
plus a confidence score and the pattern name that matched.

Deliberately conservative:
  - returns None if no pattern fires with reasonable confidence
  - does not guess aggressively
  - only applies patterns that are structurally unambiguous
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class FilenameParseResult:
    label_candidate: str    # extracted label text
    confidence: float       # 0.0 – 1.0
    pattern: str            # which pattern matched (for reporting)
    raw_match: str          # verbatim text that was matched (truncated)


# ---------------------------------------------------------------------------
# Patterns  (tried in order — first match wins)
# ---------------------------------------------------------------------------
# Each entry: (compiled_regex, pattern_name, base_confidence)
#
# Group 1 of every pattern must capture the label candidate.
#
# Confidence guidance:
#   0.70 — structurally unambiguous (bracket prefix with separator)
#   0.65 — strong indicator (bracket at start, or explicit label suffix)
#   0.60 — moderate (double separator prefix)
#   0.55 — weaker (bracket at end with label word)

_PATTERNS: list[tuple[re.Pattern, str, float]] = [

    # [Label] Artist - Title
    # Square bracket at very start, whitespace, then meaningful content.
    (
        re.compile(r"^\[([^\[\]]{2,50})\]\s+.{5,}"),
        "bracket_prefix_square",
        0.70,
    ),

    # (Label) Artist - Title
    # Round paren at very start, whitespace, then meaningful content.
    (
        re.compile(r"^\(([^()]{2,50})\)\s+.{5,}"),
        "bracket_prefix_round",
        0.70,
    ),

    # Artist - Title (Defected Records)   — paren at end with label-word suffix
    (
        re.compile(
            r".{5,}\s+\(([^()]{2,50}"
            r"(?:records?|recordings?|music|audio|label|trax|sounds?))\)\s*$",
            re.IGNORECASE,
        ),
        "paren_suffix_label_word",
        0.65,
    ),

    # Label__Artist__Title  — double-underscore prefix separator
    (
        re.compile(r"^([A-Za-z][A-Za-z0-9 &'.\-]{1,49})__\S"),
        "double_underscore_prefix",
        0.60,
    ),

    # Artist - Title [Defected Records]   — bracket at end with label-word suffix
    (
        re.compile(
            r".{5,}\s+\[([^\[\]]{2,50}"
            r"(?:records?|recordings?|music|audio|label|trax|sounds?))\]\s*$",
            re.IGNORECASE,
        ),
        "bracket_suffix_label_word",
        0.60,
    ),
]

# Minimum candidate length after stripping
_MIN_LEN = 2
# Maximum candidate length (guards against accidental title captures)
_MAX_LEN = 60


def parse_label_from_filename(stem: str) -> Optional[FilenameParseResult]:
    """
    Try to extract a label name from a filename stem.

    Returns a FilenameParseResult on a confident match, otherwise None.
    """
    stem = stem.strip()
    for pattern, name, base_conf in _PATTERNS:
        m = pattern.match(stem)
        if not m:
            continue
        candidate = m.group(1).strip()
        if len(candidate) < _MIN_LEN or len(candidate) > _MAX_LEN:
            continue
        # Sanity: candidate must contain at least one letter
        if not re.search(r"[A-Za-z]", candidate):
            continue
        return FilenameParseResult(
            label_candidate=candidate,
            confidence=base_conf,
            pattern=name,
            raw_match=m.group(0)[:80],
        )
    return None
