"""
Label normalization layer.

Produces three representations of a label name:
  raw        — original value, exactly as found in tags or filename
  normalized — lowercase, punctuation-collapsed, noise-suffix-stripped key
               used for deduplication and alias merging
  canonical  — best display name (original stylization preserved)

The normalized form intentionally loses suffix noise ("Records", "Recordings",
"Music", etc.) so that "Defected", "Defected Records" and "Defected Recordings"
all hash to the same key and can be merged to one canonical identity.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional


# ---------------------------------------------------------------------------
# Noise suffixes stripped for matching (but preserved in display names)
# ---------------------------------------------------------------------------
_NOISE_SUFFIXES: frozenset[str] = frozenset({
    "records", "recordings", "record", "recording",
    "music", "audio", "sounds", "sound",
    "label", "labels",
    "group", "trax", "digital", "worldwide",
    "international", "collective", "entertainment",
})

_RE_PUNCT      = re.compile(r"[''´`'\-_.,!?&]")
_RE_MULTI_SPACE = re.compile(r"\s{2,}")


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def normalize_label(raw: str) -> str:
    """
    Produce a normalized form for matching / deduplication.

    >>> normalize_label("Defected Records")
    'defected'
    >>> normalize_label("Defected Recordings")
    'defected'
    >>> normalize_label("  Sub  Underground  ")
    'sub underground'
    """
    if not raw:
        return ""
    s = raw.strip().lower()
    s = _RE_PUNCT.sub(" ", s)
    s = _RE_MULTI_SPACE.sub(" ", s).strip()
    tokens = s.split()
    # strip trailing noise tokens only (keep leading ones — "Music Factory" ≠ "Factory")
    while tokens and tokens[-1] in _NOISE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def make_canonical(raw: str) -> str:
    """
    Produce a display-quality canonical name.

    Preserves original stylization; only trims edges and collapses
    internal whitespace.  Does NOT alter casing.

    >>> make_canonical("  Defected  Records  ")
    'Defected  Records'
    """
    if not raw:
        return ""
    s = _RE_MULTI_SPACE.sub(" ", raw.strip())
    return s


@dataclass
class LabelNames:
    raw: str            # as found in the tag / filename
    normalized: str     # for deduplication / matching
    canonical: str      # display name (stylization preserved)


def build_label_names(raw: str) -> LabelNames:
    return LabelNames(
        raw=raw,
        normalized=normalize_label(raw),
        canonical=make_canonical(raw),
    )


# ---------------------------------------------------------------------------
# Alias / display-name selection
# ---------------------------------------------------------------------------

def best_display_name(candidates: Iterable[str]) -> str:
    """
    Given several observed spellings of the same label (same normalized key),
    return the best display name.

    Preference order:
      1. Not all-uppercase
      2. Longer (more descriptive)
      3. First seen (tie-break)
    """
    valid = [c.strip() for c in candidates if c and c.strip()]
    if not valid:
        return ""
    if len(valid) == 1:
        return valid[0]
    non_allcaps = [v for v in valid if not v.isupper()]
    pool = non_allcaps if non_allcaps else valid
    return max(pool, key=len)


# ---------------------------------------------------------------------------
# Alias registry  (lightweight, in-memory, keyed by normalized form)
# ---------------------------------------------------------------------------

class AliasRegistry:
    """
    Collects all observed spellings of a label across the library.

    Key   = normalize_label(raw)
    Value = list of all observed raw spellings for that key
    """

    def __init__(self) -> None:
        self._aliases: dict[str, list[str]] = {}

    def register(self, raw: str) -> Optional[str]:
        """
        Register a raw label spelling.

        Returns the normalized key, or None if raw is empty.
        """
        if not raw:
            return None
        key = normalize_label(raw)
        if not key:
            return None
        self._aliases.setdefault(key, [])
        if raw not in self._aliases[key]:
            self._aliases[key].append(raw)
        return key

    def canonical_for(self, raw_or_key: str) -> str:
        """
        Return the best display name for a given raw value or normalized key.
        Falls back to the input itself if nothing is registered.
        """
        key = normalize_label(raw_or_key)
        spellings = self._aliases.get(key, [raw_or_key])
        return best_display_name(spellings) or raw_or_key

    def alias_count(self) -> int:
        """Number of keys where multiple spellings were seen."""
        return sum(1 for v in self._aliases.values() if len(v) > 1)

    def all_normalized_keys(self) -> list[str]:
        return sorted(self._aliases.keys())
