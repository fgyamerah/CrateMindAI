"""
artist_intelligence/artist_normalizer.py — Deterministic string normalization
for artist name strings.

Normalization steps (applied in order):
  1. Unicode NFC
  2. Strip leading / trailing whitespace; collapse runs of spaces
  3. Normalize Unicode dash variants → ASCII hyphen  (mirrors modules/parser.py)
  4. Normalize smart / curly quotes → straight quotes
  5. Normalize feat token variants → canonical "feat."

What is NOT done here (intentional):
  - Casing is NOT changed — DJ names have specific capitalization conventions
    ("Above & Beyond", "DJ Maphorisa", "Black Coffee") and blind title-casing
    would corrupt them.
  - Hyphens in compound names (Heavy-K) are NOT stripped here; that mapping
    lives in the alias store as an explicit canonical → variant relationship.
  - Splitting on separators is handled by artist_parser.py, not here.
"""
from __future__ import annotations

import re
import unicodedata


# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# Unicode dash variants (mirrors _DASH_CHARS in modules/parser.py)
_UNICODE_DASHES = re.compile(r"[\u2013\u2014\u2015\u2012\u2212]")

# Smart / curly quote pairs: (unicode char, ASCII replacement)
_SMART_QUOTES: list = [
    ("\u2018", "'"),   # LEFT SINGLE QUOTATION MARK
    ("\u2019", "'"),   # RIGHT SINGLE QUOTATION MARK
    ("\u201c", '"'),   # LEFT DOUBLE QUOTATION MARK
    ("\u201d", '"'),   # RIGHT DOUBLE QUOTATION MARK
    ("\u2039", "'"),   # SINGLE LEFT-POINTING ANGLE QUOTATION MARK
    ("\u203a", "'"),   # SINGLE RIGHT-POINTING ANGLE QUOTATION MARK
]

# feat token variants — match with optional dot and surrounding whitespace
# Captures: "featuring", "feat.", "feat", "ft.", "ft"
_FEAT_VARIANTS = re.compile(r"\b(feat(?:uring)?|ft)\.?\b", re.IGNORECASE)

# Double dots left behind after feat normalization
_DOUBLE_DOT = re.compile(r"feat\.{2,}")

# Missing space after feat.  e.g. "feat.Toshi" → "feat. Toshi"
_FEAT_NO_SPACE = re.compile(r"feat\.(?!\s)")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_artist_string(name: str) -> str:
    """
    Apply all normalization steps to a single artist name string.
    Returns the normalized form; input is never mutated.

    Safe to call on an already-normalized string — idempotent.
    """
    if not name:
        return name

    # 1. Unicode NFC
    result = unicodedata.normalize("NFC", name)

    # 2. Strip and collapse spaces
    result = result.strip()
    result = re.sub(r"  +", " ", result)

    # 3. Unicode dashes → ASCII hyphen
    result = _UNICODE_DASHES.sub("-", result)

    # 4. Smart quotes → straight
    for smart, straight in _SMART_QUOTES:
        result = result.replace(smart, straight)

    # 5. Normalize feat token variants → "feat."
    result = _FEAT_VARIANTS.sub("feat.", result)
    result = _DOUBLE_DOT.sub("feat.", result)
    result = _FEAT_NO_SPACE.sub("feat. ", result)
    # Final collapse in case step 5 introduced new double spaces
    result = re.sub(r"  +", " ", result).strip()

    return result


def names_are_equivalent(a: str, b: str) -> bool:
    """
    Return True if two artist name strings are semantically equivalent after
    normalization.  Used to suppress no-op change proposals.
    """
    return normalize_artist_string(a).lower() == normalize_artist_string(b).lower()
