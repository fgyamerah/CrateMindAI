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

# Adjacent feat tokens — "feat. feat." artifact from tag concatenation
_FEAT_DUPLICATE = re.compile(r"\bfeat\.\s+feat\.", re.IGNORECASE)

# BPM pollution — "128 BPM", "128bpm", "@ 130 BPM"
_BPM_TOKEN = re.compile(r"\s*@?\s*\b\d+\s*bpm\b\s*", re.IGNORECASE)

# Dotted initials pattern: M.E., D.J., etc.
_DOTTED_INITIALS_RE = re.compile(r"^[A-Z](\.[A-Z])+\.?$")

# All-caps abbreviations to preserve during casing normalization
_KNOWN_ABBREVS: frozenset = frozenset({"DJ", "MC", "NY", "UK", "US", "EP", "LP", "B2B"})

# Trailing country/region code in parentheses: (IT), (UK), (SA), (DE), (FR), etc.
_COUNTRY_CODE_RE = re.compile(r"\s*\([A-Z]{2,4}\)\s*$")

# Label strings for casing decisions (used in debug logging)
CASING_LABEL_SKIPPED_ACRONYM   = "casing_skipped_acronym"
CASING_LABEL_NORMALIZED_WORD   = "casing_normalized_word"

# Single-word all-caps names at or below this length are treated as DJ brand
# names / acronyms and left unchanged. Above this threshold we apply title-case.
_ACRONYM_MAX_LEN = 6

# Version/mix tokens leaked into artist field via bracket wrapping
_VERSION_BRACKET = re.compile(
    r"\s*[\[\(]"
    r"(?:Original Mix|Extended Mix|Radio Edit|Dub Mix|Instrumental|Remix"
    r"|Rework|VIP Mix|Club Mix|Short Mix|Intro Mix|Outro Mix|Edit|Re-Edit)"
    r"[^\]\)]*[\]\)]",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_casing(name: str) -> str:
    """
    Convert ALL-CAPS artist names to title case, preserving DJ brand identities.

    Rules (applied in order):
      1. Any lowercase letter present → return unchanged (casing is intentional).
      2. Separate a trailing country/region code: (IT), (UK), (SA) — always kept uppercase.
      3. Single-word body ≤ _ACRONYM_MAX_LEN chars → keep entirely (ACRAZE, ANOTR, ATFC, DJ…).
      4. Single-word body > _ACRONYM_MAX_LEN chars → title-case it (MAPHORISA → Maphorisa).
      5. Multi-word body → word-by-word: known abbrevs (DJ/MC) and dotted initials (M.E.)
         are preserved; every other all-caps word is title-cased.

    Safe: if nothing changes, the original string is returned unchanged.
    """
    if not name:
        return name
    # Any lowercase letter → mixed-case is intentional; do not modify
    if re.search(r"[a-z]", name):
        return name
    # Need at least 2 uppercase letters to be worth examining
    if len(re.findall(r"[A-Z]", name)) < 2:
        return name

    # Separate trailing country/region code parenthetical
    suffix = ""
    body = name
    m = _COUNTRY_CODE_RE.search(name)
    if m:
        suffix = m.group(0).rstrip()   # keep surrounding whitespace tidy
        body = name[: m.start()].strip()

    if not body:
        return name  # nothing outside the country code — leave as-is

    words = body.split()

    # Single-word entity: apply length-based acronym guard
    if len(words) == 1:
        word = words[0]
        alpha_len = len(re.sub(r"[^A-Za-z]", "", word))
        if alpha_len <= _ACRONYM_MAX_LEN:
            # Short single-word all-caps → DJ brand / acronym — keep entirely
            import logging as _log
            _log.getLogger(__name__).debug(
                "%s: %r kept (single-word ≤%d chars)",
                CASING_LABEL_SKIPPED_ACRONYM, name, _ACRONYM_MAX_LEN,
            )
            return name
        # Long single word → title case
        converted = word[0].upper() + word[1:].lower()
        return (converted + (" " + suffix.strip() if suffix.strip() else "")).strip()

    # Multi-word entity: convert word-by-word
    out: list = []
    for w in words:
        if not re.search(r"[A-Za-z]", w):
            out.append(w)                                   # & or numeric token
        elif w in _KNOWN_ABBREVS:
            out.append(w)                                   # DJ, MC, etc.
        elif _DOTTED_INITIALS_RE.match(w):
            out.append(w)                                   # M.E., D.J.
        elif w.isupper():
            import logging as _log
            _log.getLogger(__name__).debug(
                "%s: %r → %r", CASING_LABEL_NORMALIZED_WORD, w,
                w[0].upper() + w[1:].lower(),
            )
            out.append(w[0].upper() + w[1:].lower())        # LISA → Lisa
        else:
            out.append(w)

    result = " ".join(out)
    if suffix.strip():
        result = result + " " + suffix.strip()
    return result


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
    # 5b. Collapse duplicate adjacent feat tokens: "feat. feat." → "feat."
    result = _FEAT_DUPLICATE.sub("feat.", result)

    # 6. Strip BPM pollution that bled into the artist field
    result = _BPM_TOKEN.sub(" ", result).strip()

    # 7. Strip version/mix tokens wrapped in brackets
    result = _VERSION_BRACKET.sub("", result).strip()

    # 8. Casing normalization: ALL-CAPS names → title case
    result = _normalize_casing(result)

    # Final collapse
    result = re.sub(r"  +", " ", result).strip()

    return result


def names_are_equivalent(a: str, b: str) -> bool:
    """
    Return True if two artist name strings are semantically equivalent after
    normalization.  Used to suppress no-op change proposals.
    """
    return normalize_artist_string(a).lower() == normalize_artist_string(b).lower()
