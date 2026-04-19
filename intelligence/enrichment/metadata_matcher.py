"""
intelligence/enrichment/metadata_matcher.py

Deterministic matcher that scores EnrichmentCandidates against a track's
current tags and builds a list of proposed safe changes.

Scoring
───────
Composite confidence is a weighted combination of:

  artist_sim  — normalised string similarity of artist fields       weight 0.35
  title_sim   — normalised string similarity of base titles         weight 0.30
                (version/mix terms stripped from both sides before
                 comparison so "Track (Original Mix)" ↔ "Track" still scores high)
  version_sim — similarity of extracted version tokens               weight 0.25
                (weighted higher because version differences indicate different
                 masters; dance music relies on mix version identity)
  label_sim   — similarity of label fields (advisory only)           weight 0.10

ISRC exact match overrides the formula: confidence is set to 0.98 (near certain).

Label alias boost
─────────────────
When the current file's label and the candidate's label are known aliases of each
other (e.g. "SME" ↔ "Sony Music Entertainment"), confidence is boosted by +0.05
(capped at 1.0).  The alias table lives in _LABEL_ALIAS_GROUPS below.

Hard safeguards (applied in best_match after scoring)
───────────────────────────────────────────────────────
  1. Version mismatch — if the current title carries an explicit version token
     (e.g. "Original Mix") AND the candidate carries a DIFFERENT token (e.g.
     "Radio Edit"), confidence is capped at 0.74 and proposed changes are cleared.
     The wrong version is the wrong recording; no auto-apply is safe.
     Missing version on either side is NOT a mismatch.

  2. Low artist similarity — if artist similarity < 0.90 (and no ISRC anchor),
     confidence is capped at 0.74. Proposed changes are kept for preview but the
     match will not auto-apply below the 0.80 threshold.

Change policy (safety rules)
─────────────────────────────
  artist       : NEVER proposed — artist normalisation is owned by
                 intelligence/artist/; online enrichment must not touch it.
  title        : only proposed when current title is empty or the base
                 title differs AND an ISRC match anchors it.
  album        : proposed when current is empty, or when ISRC matches exactly.
  organization : (label/TPUB) proposed when current is empty; requires
                 confidence >= 0.95 to overwrite an existing non-empty label.
  isrc         : proposed when not already set in the file.

Existing version/mix info in the current title is always preserved.
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Dict, List, Optional, Tuple

from intelligence.enrichment.enrichment_schema import EnrichmentCandidate, EnrichmentMatch

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version-term normalisation (mirrors ai/normalizer.py's _VERSION_BLOCK_RE)
# ---------------------------------------------------------------------------

_VERSION_TERMS_RE = re.compile(
    r"\s*[\(\[]\s*("
    r"Original Mix|Extended Mix|Radio Edit|Dub Mix|Instrumental"
    r"|Remix|Rework|Bootleg|VIP Mix|Club Mix|Short Mix|Intro Mix"
    r"|Outro Mix|Reprise|Edit|Re-Edit|Vocal Mix|Acapella|Intro|Outro|Dub"
    r")[^)\]]*[\)\]]",
    re.IGNORECASE,
)

# Weights for composite score
_W_ARTIST  = 0.35
_W_TITLE   = 0.30
_W_VERSION = 0.25
_W_LABEL   = 0.10

# Label alias boost — added to confidence when current + candidate labels are
# known aliases of each other, regardless of string similarity score.
_LABEL_ALIAS_BOOST = 0.05

# Each frozenset is a group of normalized label name variants that are
# considered the same label.  Normalization: lowercase, no punctuation/articles
# (consistent with _normalize_for_compare).  Add more groups as needed.
_LABEL_ALIAS_GROUPS: List[frozenset] = [
    # Major distributors (appear in many abbreviated forms on digital stores)
    frozenset(["sony music", "sony music entertainment", "sme", "sony"]),
    frozenset(["warner music", "warner records", "wea", "warner music group", "wmg"]),
    frozenset(["universal music", "universal music group", "umg", "umi", "universal"]),
    # Common Afro House / Deep House labels
    frozenset(["afro brotherz music", "afro brotherz", "abm"]),
    frozenset(["black motion music", "bmm", "black motion"]),
    frozenset(["enoo napa music", "enm", "enoo napa"]),
    frozenset(["silo sound", "silo"]),
    frozenset(["mzansi beat", "mzansibeat", "mzansi"]),
    frozenset(["africa music", "africamusic"]),
    frozenset(["traxsource", "ts"]),
    frozenset(["defected records", "defected"]),
    frozenset(["ministry of sound", "mos"]),
    frozenset(["toolroom records", "toolroom"]),
]

# Pre-build a fast lookup: normalized_name → group_index
_LABEL_ALIAS_INDEX: Dict[str, int] = {}
for _gi, _group in enumerate(_LABEL_ALIAS_GROUPS):
    for _name in _group:
        _LABEL_ALIAS_INDEX[_name] = _gi


# ---------------------------------------------------------------------------
# String helpers
# ---------------------------------------------------------------------------

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def _normalize_for_compare(s: str) -> str:
    """
    Lower-case, NFC, strip punctuation, collapse spaces.
    Used only for similarity comparison — not for display or writing.
    """
    if not s:
        return ""
    s = _nfc(s).lower()
    # Remove possessives and punctuation
    s = re.sub(r"[''`]s\b", "s", s)          # artist's → artists
    s = re.sub(r"[^\w\s]", " ", s)            # punctuation → space
    s = re.sub(r"\b(the|a|an)\b\s*", "", s)   # strip leading articles
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _strip_version(s: str) -> str:
    """Remove version/mix tokens from a title string for base-title comparison."""
    return _VERSION_TERMS_RE.sub("", s).strip()


def _extract_version(s: str) -> str:
    """Return the full version bracket from a title, e.g. '(Original Mix)', or ''."""
    m = _VERSION_TERMS_RE.search(s)
    return m.group(0).strip() if m else ""


def _extract_version_label(s: str) -> str:
    """
    Return only the version term text from a title, e.g. 'Original Mix', or ''.
    Used for human-readable mismatch messages and case-insensitive comparison.
    """
    m = _VERSION_TERMS_RE.search(s)
    return m.group(1).strip() if m else ""


def _similarity(a: str, b: str) -> float:
    """SequenceMatcher ratio on normalised strings. Returns 0.0–1.0."""
    na = _normalize_for_compare(a)
    nb = _normalize_for_compare(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _version_sim(title_a: str, title_b: str) -> float:
    """
    Score version/remix agreement between two title strings.

    Extracts the version label (e.g. "Original Mix") from each title and
    applies the following rules:

      Both absent  → 1.0  (neither has a version; they agree on that)
      Both present, same  → 1.0
      Both present, differ → 0.0  (different recordings, hard mismatch)
      One present, one absent → 0.0  (asymmetric; uncertain which is correct)

    This replaces the old _similarity(_extract_version(a), _extract_version(b))
    call, which incorrectly returned 0.0 when both titles had no version token
    because _similarity short-circuits on empty strings.
    """
    v_a = _extract_version_label(title_a)
    v_b = _extract_version_label(title_b)

    both_absent = not v_a and not v_b
    if both_absent:
        return 1.0                          # "Setter" ↔ "Setter" — full agreement

    if v_a and v_b:
        return 1.0 if v_a.lower() == v_b.lower() else 0.0   # match or hard mismatch

    return 0.0                              # one has version, other does not


def _label_alias_boost(label_a: str, label_b: str) -> float:
    """
    Return _LABEL_ALIAS_BOOST if both labels resolve to the same alias group,
    0.0 otherwise.  Empty labels never trigger the boost.
    """
    if not label_a or not label_b:
        return 0.0
    na = _normalize_for_compare(label_a)
    nb = _normalize_for_compare(label_b)
    gi_a = _LABEL_ALIAS_INDEX.get(na)
    gi_b = _LABEL_ALIAS_INDEX.get(nb)
    if gi_a is not None and gi_a == gi_b:
        return _LABEL_ALIAS_BOOST
    return 0.0


# ---------------------------------------------------------------------------
# Public: score a single candidate against current tags
# ---------------------------------------------------------------------------

def score_candidate(
    candidate: EnrichmentCandidate,
    current_tags: Dict[str, str],
    current_isrc: Optional[str] = None,
) -> Tuple[float, bool, str]:
    """
    Compute a confidence score for one candidate against the track's current tags.

    Returns:
        (confidence, isrc_matched, reason_string)
    """
    # --- ISRC exact match (highest confidence) ---
    if (
        current_isrc
        and candidate.isrc
        and current_isrc.strip().upper() == candidate.isrc.strip().upper()
    ):
        return 0.98, True, "exact ISRC match"

    if (
        not current_isrc
        and candidate.isrc
        and current_tags.get("title")
        and candidate.title
    ):
        # Candidate brings an ISRC we don't have — score normally but note it
        pass

    current_artist  = current_tags.get("artist", "")
    current_title   = current_tags.get("title", "")
    current_label   = current_tags.get("organization", "")

    # Base titles (version stripped from both sides)
    current_base = _strip_version(current_title)
    cand_base    = _strip_version(candidate.title or "")

    # Individual similarities
    artist_sim  = _similarity(current_artist, candidate.artist or "")
    title_sim   = _similarity(current_base,   cand_base)
    version_sim = _version_sim(current_title, candidate.title or "")
    label_sim   = _similarity(current_label, candidate.label or "") if current_label else 0.0

    # Weighted composite
    confidence = (
        artist_sim  * _W_ARTIST
        + title_sim   * _W_TITLE
        + version_sim * _W_VERSION
        + label_sim   * _W_LABEL
    )

    # Label alias boost: +0.05 when both labels are known aliases of each other
    alias_boost = _label_alias_boost(current_label, candidate.label or "")
    if alias_boost:
        confidence = min(1.0, confidence + alias_boost)

    reason = (
        f"artist={artist_sim:.2f} title={title_sim:.2f} "
        f"version={version_sim:.2f} label={label_sim:.2f}"
        + (f" [label-alias+{alias_boost:.2f}]" if alias_boost else "")
    )
    return confidence, False, reason


# ---------------------------------------------------------------------------
# Public: pick best candidate and build proposed changes
# ---------------------------------------------------------------------------

def best_match(
    candidates: List[EnrichmentCandidate],
    current_tags: Dict[str, str],
    current_isrc: Optional[str] = None,
    sources_tried: Optional[List[str]] = None,
) -> EnrichmentMatch:
    """
    Score all candidates and return the best EnrichmentMatch.

    Returns an EnrichmentMatch with confidence=0.0 if candidates is empty.

    Hard safeguards applied after scoring (in priority order):
      1. Version mismatch  — caps at 0.74, clears proposed changes.
      2. Low artist sim    — caps at 0.74, keeps proposed changes for preview.
    Neither fires when an ISRC exact match anchors the result.
    """
    if not candidates:
        return EnrichmentMatch(
            source_used="none",
            reason="no candidates returned",
            sources_tried=sources_tried or [],
        )

    scored: List[Tuple[float, bool, str, EnrichmentCandidate]] = []
    for cand in candidates:
        conf, isrc_hit, reason = score_candidate(cand, current_tags, current_isrc)
        scored.append((conf, isrc_hit, reason, cand))

    # Sort: ISRC matches first, then by confidence descending
    scored.sort(key=lambda t: (t[1], t[0]), reverse=True)

    top_conf, top_isrc, top_reason, top_cand = scored[0]

    changes = _build_changes(top_cand, current_tags, current_isrc, top_conf, top_isrc)

    current_title = (current_tags.get("title") or "").strip()

    # --- Safeguard 1: Version mismatch ---
    # When the current title carries an explicit version token (e.g. "Original Mix")
    # AND the best candidate carries a DIFFERENT version token (e.g. "Radio Edit"),
    # the match is almost certainly the wrong recording.  Block the apply by capping
    # confidence at 0.74 (below the 0.80 apply threshold) and clearing proposed
    # changes — no tag writes are allowed for a version-mismatched match.
    #
    # Missing version on either side is NOT a mismatch — APIs routinely omit
    # "(Original Mix)" from their track titles, so "Track" ↔ "Track (Original Mix)"
    # is allowed through.  ISRC exact matches are still blocked (Radio Edit ≠
    # Original Mix even when the ISRC superficially matches a catalogue entry).
    orig_v = _extract_version_label(current_title)
    cand_v = _extract_version_label(top_cand.title or "")

    if orig_v and cand_v and orig_v.lower() != cand_v.lower():
        log.info(
            "Blocked due to version mismatch: %s vs %s  (file=%s)",
            orig_v, cand_v,
            current_tags.get("title", "?"),
        )
        top_conf = min(top_conf, 0.74)
        top_reason = (
            f"VERSION MISMATCH BLOCKED — original: {orig_v!r}  candidate: {cand_v!r}  "
            f"({top_reason})"
        )
        changes = []   # no tag writes allowed for a version-mismatched match

    # --- Safeguard 2: Low artist similarity ---
    # When artist similarity is below 0.90 (and no ISRC anchor confirms the match),
    # cap confidence at 0.74 to prevent auto-apply.  Keep proposed changes so the
    # user can still review them in --preview mode.
    elif not top_isrc:
        current_artist = current_tags.get("artist", "")
        artist_sim = _similarity(current_artist, top_cand.artist or "")
        if artist_sim < 0.90:
            log.debug(
                "Artist similarity %.2f < 0.90 — capping confidence for %s",
                artist_sim, current_tags.get("title", "?"),
            )
            top_conf = min(top_conf, 0.74)
            top_reason = (
                f"LOW ARTIST SIM ({artist_sim:.2f}) — auto-apply blocked  "
                f"({top_reason})"
            )

    return EnrichmentMatch(
        candidate=top_cand,
        confidence=top_conf,
        proposed_changes=changes,
        source_used=top_cand.source,
        reason=top_reason,
        isrc_matched=top_isrc,
        sources_tried=sources_tried or [],
    )


# ---------------------------------------------------------------------------
# Internal: change policy
# ---------------------------------------------------------------------------

def _build_changes(
    cand: EnrichmentCandidate,
    current_tags: Dict[str, str],
    current_isrc: Optional[str],
    confidence: float,
    isrc_matched: bool,
) -> List[Dict[str, str]]:
    """
    Build a list of safe proposed tag changes from a matched candidate.

    Safety rules (see module docstring for full spec):
      - artist: never proposed
      - title: only if current is empty, or if ISRC matched AND base differs
      - album: if current is empty, or if ISRC matched
      - label: if current is empty; or if isrc_matched AND confidence >= 0.95
      - isrc: only if not already set
    """
    changes: List[Dict[str, str]] = []

    def _add(field: str, current_val: str, new_val: str) -> None:
        cur  = (current_val or "").strip()
        new  = (new_val or "").strip()
        if new and new != cur:
            changes.append({"field": field, "old": cur, "new": new})

    current_title = (current_tags.get("title") or "").strip()
    current_album = (current_tags.get("album") or "").strip()
    current_label = (current_tags.get("organization") or "").strip()

    # --- title ---
    # Only propose if current is blank, or ISRC match + base title meaningfully differs
    if cand.title:
        if not current_title:
            _add("title", current_title, cand.title)
        elif isrc_matched:
            curr_base = _strip_version(current_title)
            cand_base = _strip_version(cand.title)
            if _similarity(curr_base, cand_base) < 0.8:
                # Preserve existing version info when combining
                existing_version = _extract_version(current_title)
                proposed = cand.title
                if existing_version and existing_version.lower() not in proposed.lower():
                    proposed = f"{_strip_version(proposed)} {existing_version}".strip()
                _add("title", current_title, proposed)

    # --- album ---
    if cand.album:
        if not current_album:
            _add("album", current_album, cand.album)
        elif isrc_matched and _similarity(current_album, cand.album) < 0.7:
            _add("album", current_album, cand.album)

    # --- label (organization/TPUB) ---
    if cand.label:
        if not current_label:
            _add("label", current_label, cand.label)
        elif isrc_matched and confidence >= 0.95 and _similarity(current_label, cand.label) < 0.7:
            _add("label", current_label, cand.label)

    # --- isrc ---
    if cand.isrc and not current_isrc:
        _add("isrc", "", cand.isrc)

    return changes
