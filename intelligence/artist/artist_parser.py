"""
artist_intelligence/artist_parser.py — Deterministic parser for artist tag strings.

Splits compound artist strings ("A, B & C feat. D") into structured
ArtistParseResult objects.  Purely deterministic — no AI involved.

Splitting rules (applied in priority order):
  1. feat / ft / featuring → featured_artists  (artist-field only)
  2. comma         ", "
  3. ampersand     " & "
  4. x-separator   " x "   (word-boundary, common in Afro/electronic)
  5. vs-separator  " vs " / " vs. "
  6. "and"         (lowest priority — many artist names contain the word "and")

"and" splitting policy:
  - Only applied when the string also contains another separator (comma, &, x,
    vs) on a different segment, OR when both sides individually pass
    is_valid_artist() from modules/parser.py.
  - This prevents "Above and Beyond" or "Salt-N-Pepa and Friends" from being
    incorrectly split.

House-style rules honoured here:
  - If "(feat" already appears in the TITLE, feat tokens in the artist string
    are left alone and NOT extracted (the title is authoritative for feat).
  - Featured artists extracted from the artist field are stored separately and
    never merged back into the main artist string automatically.
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

from intelligence.artist.artist_schema import ArtistEntity, ArtistParseResult
from intelligence.artist.artist_normalizer import normalize_artist_string


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Featured artist extraction from ARTIST field (not from title)
_FEAT_RE = re.compile(
    r"\s+(?:feat(?:uring)?|ft)\.?\s+(.+)$",
    re.IGNORECASE,
)

# Ordered separator specs: (pattern, label, confidence_penalty)
# Lower penalty = higher reliability.
_SEPARATORS: List[Tuple[re.Pattern, str, float]] = [
    (re.compile(r"\s*,\s*"),                    "comma",     0.00),
    (re.compile(r"\s+&\s+"),                    "ampersand", 0.00),
    (re.compile(r"\s+x\s+", re.IGNORECASE),     "x",         0.05),
    (re.compile(r"\s+vs\.?\s+", re.IGNORECASE), "vs",        0.00),
]

_AND_RE = re.compile(r"\s+and\s+", re.IGNORECASE)

# Lazy import guard — resolved once on first use
_is_valid_artist = None


def _get_is_valid_artist():
    global _is_valid_artist
    if _is_valid_artist is None:
        try:
            from modules.parser import is_valid_artist
            _is_valid_artist = is_valid_artist
        except ImportError:
            # Fallback: accept any non-empty string with at least one letter
            _is_valid_artist = lambda s: bool(s and re.search(r"[a-zA-Z]", s))
    return _is_valid_artist


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_FEAT_LEADING_RE = re.compile(r"^(?:feat(?:uring)?|ft)\.?\s+", re.IGNORECASE)


def _strip_feat(artist_string: str) -> Tuple[str, Optional[str]]:
    """
    Extract a trailing feat token from an artist string.
    Returns (artist_without_feat, feat_string_or_None).

    If the extracted feat_part itself starts with another feat token
    (e.g. "feat. Another" from a "feat. feat. Another" original), that
    leading duplicate is stripped so the feat_part is clean.

    Only call this when the title does NOT already contain a feat token —
    the caller is responsible for that guard.
    """
    m = _FEAT_RE.search(artist_string)
    if m:
        main_part = artist_string[: m.start()].strip()
        feat_part = m.group(1).strip()
        # Strip any duplicate leading feat token from the captured group
        feat_part = _FEAT_LEADING_RE.sub("", feat_part).strip()
        return main_part, feat_part if feat_part else None
    return artist_string, None


def _split_on_separators(name: str) -> Tuple[List[str], str, float]:
    """
    Split name on recognised multi-artist separators.

    Hierarchical rule: comma is split first; within each comma-segment,
    ampersand is applied as a second pass.  This handles cases like
    "AC Slater, Chris Lorenzo & Fly With Us" → 3 artists instead of 2.

    Returns (parts, separator_label, confidence_penalty).
    Falls back to ([name], "none", 0.0) when no separator is found.
    """
    comma_pat, _, _ = _SEPARATORS[0]   # ","
    amp_pat,   _, _ = _SEPARATORS[1]   # "&"

    comma_parts = comma_pat.split(name)
    if len(comma_parts) > 1:
        comma_parts = [p.strip() for p in comma_parts if p.strip()]
        final: List[str] = []
        nested = False
        for seg in comma_parts:
            amp_parts = amp_pat.split(seg)
            if len(amp_parts) > 1:
                final.extend(p.strip() for p in amp_parts if p.strip())
                nested = True
            else:
                final.append(seg)
        label = "nested" if nested else "comma"
        return final, label, 0.00

    # No comma — try remaining separators in order
    for pattern, label, penalty in _SEPARATORS[1:]:
        parts = pattern.split(name)
        if len(parts) > 1:
            parts = [p.strip() for p in parts if p.strip()]
            return parts, label, penalty

    return [name], "none", 0.0


def _try_and_split(name: str) -> Optional[List[str]]:
    """
    Attempt "and" splitting with conservative guard.
    Returns split parts only when both sides look like valid artist names.
    Returns None when splitting would be unsafe.
    """
    parts = _AND_RE.split(name)
    if len(parts) < 2:
        return None
    parts = [p.strip() for p in parts if p.strip()]
    is_valid = _get_is_valid_artist()
    if all(is_valid(p) for p in parts):
        return parts
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_artist_string(
    artist_string: str,
    current_title: str = "",
) -> ArtistParseResult:
    """
    Parse a raw artist tag string into a structured ArtistParseResult.

    Args:
        artist_string  — raw value from the audio file's artist tag
        current_title  — current title tag value (used to detect title-based feat)

    Returns:
        ArtistParseResult with main_artists, featured_artists, confidence, notes
    """
    if not artist_string or not artist_string.strip():
        return ArtistParseResult(
            confidence=0.0,
            notes="empty artist string",
        )

    raw = artist_string.strip()
    confidence = 1.0
    notes_parts: List[str] = []

    # ------------------------------------------------------------------
    # Step 1: Featured artist extraction from artist field
    # House rule: if "(feat" already appears in the title, the feat info
    # lives there and we do NOT extract / duplicate it from the artist field.
    # ------------------------------------------------------------------
    title_has_feat = bool(re.search(r"\(feat", current_title, re.IGNORECASE))
    artist_feat_str: Optional[str] = None

    if not title_has_feat:
        raw_no_feat, artist_feat_str = _strip_feat(raw)
        if artist_feat_str:
            raw = raw_no_feat
            notes_parts.append(f"feat extracted from artist field: '{artist_feat_str}'")
    else:
        notes_parts.append("feat token in title — not extracted from artist")

    # ------------------------------------------------------------------
    # Step 2: Split on primary separators (comma / & / x / vs)
    # ------------------------------------------------------------------
    parts, sep_label, penalty = _split_on_separators(raw)
    confidence -= penalty

    if len(parts) > 1:
        notes_parts.append(f"split on '{sep_label}': {parts}")
    elif len(parts) == 1 and sep_label == "none":
        # No primary separator found — try conservative "and" split
        and_parts = _try_and_split(raw)
        if and_parts and len(and_parts) > 1:
            parts = and_parts
            sep_label = "and"
            confidence -= 0.10  # lower confidence for "and" splits
            notes_parts.append(f"split on 'and': {parts}")

    # ------------------------------------------------------------------
    # Step 3: Normalize each part → ArtistEntity
    # ------------------------------------------------------------------
    entities: List[ArtistEntity] = []
    for part in parts:
        normalized = normalize_artist_string(part)
        entities.append(ArtistEntity(
            raw=part,
            normalized=normalized,
            canonical=None,   # filled in by alias store lookup in runner.py
            confidence=confidence,
            source="tag",
        ))

    # ------------------------------------------------------------------
    # Step 4: Confidence calibration
    # ------------------------------------------------------------------
    if len(entities) == 1 and not artist_feat_str:
        # Single artist, no splitting needed — maximum confidence
        confidence = 1.0
    elif len(entities) > 1:
        # Multiple artists after splitting — moderate confidence
        # (splitting rules can be wrong for compound artist names)
        confidence = max(0.70, confidence)

    # Clamp
    confidence = max(0.0, min(1.0, confidence))

    return ArtistParseResult(
        main_artists=entities,
        featured_artists=[artist_feat_str] if artist_feat_str else [],
        confidence=confidence,
        notes="; ".join(notes_parts) if notes_parts else None,
    )


# ---------------------------------------------------------------------------
# Multi-artist heuristic split (no-separator concatenated names)
# ---------------------------------------------------------------------------

# Words that are never valid stand-alone artist name tokens
_NOT_NAME_WORDS = frozenset({
    "presents", "featuring", "feat", "ft", "the", "a", "an",
    "and", "or", "vs", "with", "from",
    "prod", "production", "productions",
    "remix", "remixed", "original", "radio", "extended", "edit", "mix",
})

# Strict title-case word: Capital + 1-11 lowercase letters, purely alphabetic
_NAME_WORD_RE   = re.compile(r"^[A-Z][a-z]{1,11}$")
# Single initial: one capital letter, optional trailing dot
_INITIAL_RE     = re.compile(r"^[A-Z]\.?$")


def _is_personal_name_part(s: str) -> bool:
    """
    Return True if s plausibly matches a personal or single-word DJ artist name.

    Accepts:
      "Mark Francis"   — 2-word personal name
      "Aaron K Gray"   — personal name with middle initial
      "Keinemusik"     — single-word DJ name (≥ 5 chars)
      "Hosh"           — short single-word DJ name

    Rejects:
      "The"            — article
      "Presents"       — non-name word
      "128"            — number
      "SBCR"           — all-caps abbreviation (≤ 4 chars)
      "DJ Lag"         — abbreviation prefix (DJ doesn't match title-case pattern)
    """
    if not s:
        return False
    words = s.split()
    if not words:
        return False

    # Blocklisted tokens immediately disqualify
    if any(w.lower() in _NOT_NAME_WORDS for w in words):
        return False

    # Every word must be purely alphabetic (optional trailing dot for initials)
    if not all(re.match(r"^[A-Za-z]+\.?$", w) for w in words):
        return False

    if len(words) == 1:
        w = words[0]
        if not w[0].isupper():
            return False
        # Reject short all-caps abbreviations (SBCR, MAW, DJ…)
        if w.isupper() and len(w) <= 4:
            return False
        return len(w) >= 2

    if len(words) == 2:
        # "Mark Francis", "Adri Block"
        return bool(_NAME_WORD_RE.match(words[0])) and bool(_NAME_WORD_RE.match(words[1]))

    if len(words) == 3:
        # "Aaron K Gray" — First + Initial + Surname only
        return (
            bool(_NAME_WORD_RE.match(words[0]))
            and bool(_INITIAL_RE.match(words[1]))
            and bool(_NAME_WORD_RE.match(words[2]))
        )

    return False   # 4+ words: too ambiguous to classify as a single name


def _try_personal_name_split(name: str) -> Optional[Tuple[List[str], float]]:
    """
    Try to split a no-separator string into two personal-name-pattern artists.

    Only splits when BOTH halves independently match _is_personal_name_part.
    Requires at least 4 words in the input.

    Returns:
        ([left, right], confidence)  when a confident split is found
        None                         when no safe split exists
    """
    words = name.split()
    if len(words) < 4:
        return None

    for i in range(1, len(words)):
        left  = " ".join(words[:i])
        right = " ".join(words[i:])
        if _is_personal_name_part(left) and _is_personal_name_part(right):
            return ([left, right], 0.90)

    return None
