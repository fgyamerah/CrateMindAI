"""
intelligence/enrichment/metadata_matcher.py

Strict multi-factor matching engine for EnrichmentCandidates.

Matching pipeline (each candidate must pass all gates)
───────────────────────────────────────────────────────
  Gate A — Title similarity
    Strip version/mix tokens AND noise tokens (label fragments, BPM suffixes)
    from both sides before comparing.
    Candidate is discarded when title_sim < TITLE_SIM_GATE (0.92).
    ISRC exact matches bypass this gate.
    Reason code: "title_cleaned_for_match" when pre-clean changed either side.

  Gate B — Artist consistency
    Compare canonical artist strings (alias-resolved when available).
    Falls back to list-based subset matching for multi-artist tracks:
      "subset_artist_match"  — cand artists ⊆ file artists → score 0.88 (passes gate)
      "partial_artist_match" — some overlap, not a strict subset → score 0.82 (fails gate)
    Candidate is rejected (decision_code "skipped_artist_mismatch") when
    artist_sim < ARTIST_SIM_GATE (0.85) and no ISRC anchor.

  Gate C — Version alignment
    Extract version label (e.g. "Original Mix") from both sides.
    Candidate is rejected (decision_code "skipped_version_conflict") when
    both sides carry explicit but conflicting version tokens.
    Tolerance: if exactly one side lacks a version token AND title_sim >= 0.95
    AND artist match is strong, version_sim is set to 0.70 rather than 0.0.
    Reason code: "version_missing_tolerated".

Scoring formula (applied after all gates pass)
───────────────────────────────────────────────
  confidence = title_sim  * 0.50
             + artist_sim * 0.30
             + version_sim * 0.20

  Optional boosters (additive, result capped at 1.0):
    label_match_boost — +0.05 when label similarity >= 0.70
    label_alias_boost — +0.05 when labels are known synonyms (e.g. "SME"↔"Sony")

Album safety filter (_build_changes)
──────────────────────────────────────
  Candidate albums containing "Mix", "Compilation", or "Playlist" are rejected
  unless the match is ISRC-anchored OR label_sim >= 0.70.
  Reason code: "album_rejected_compilation" (logged at DEBUG).

Decision thresholds
────────────────────
  >= THRESHOLD_APPLY (0.90)  → "ready"  (auto-apply when --apply is set)
  >= THRESHOLD_REVIEW (0.75) → "review" (needs human confirmation)
  < THRESHOLD_REVIEW         → "skipped_low_score"

Ambiguity rule
──────────────
  When >= 2 candidates both pass all gates AND the confidence gap between
  them is < AMBIGUITY_GAP (0.05), the result is marked "review_ambiguous".
  No changes are proposed — do not guess.

ISRC exact match
─────────────────
  Bypasses all gates; confidence is set to 0.98.

Change policy (safety rules)
─────────────────────────────
  artist       : NEVER proposed — owned by intelligence/artist/ layer.
  title        : only if current is empty, or ISRC matched AND base differs.
  album        : if current empty, or ISRC matched.
  label        : if current empty; or ISRC matched AND confidence >= 0.95.
  isrc         : only if not already set.

  Existing version/mix info in the current title is always preserved.

Exported constants (used by runner.py)
────────────────────────────────────────
  THRESHOLD_APPLY   — minimum confidence to auto-apply
  THRESHOLD_REVIEW  — minimum confidence to queue for review
  TITLE_SIM_GATE    — per-candidate pre-filter on base-title similarity
  ARTIST_SIM_GATE   — per-candidate artist rejection threshold
  AMBIGUITY_GAP     — max confidence gap between top-2 before flagging ambiguous
"""
from __future__ import annotations

import difflib
import logging
import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

from intelligence.enrichment.enrichment_schema import EnrichmentCandidate, EnrichmentMatch

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Exported threshold constants
# ---------------------------------------------------------------------------

THRESHOLD_APPLY  = 0.90   # confidence >= this → ready to apply
THRESHOLD_REVIEW = 0.75   # confidence in [this, THRESHOLD_APPLY) → needs review

# Per-candidate gates (checked before weighted scoring)
TITLE_SIM_GATE  = 0.92   # base-title similarity floor
ARTIST_SIM_GATE = 0.85   # artist similarity floor (no ISRC anchor)

# Ambiguity: if top-2 candidates are within this margin, flag as ambiguous
AMBIGUITY_GAP = 0.05

# Artist subset/partial matching scores (used when direct string sim < ARTIST_SIM_GATE)
_SUBSET_ARTIST_SCORE  = 0.88   # cand artists are a strict subset of file artists
_PARTIAL_ARTIST_SCORE = 0.82   # some overlap but not a strict subset (below gate — rejected)

# Album compilation filter — reject these terms in candidate album unless ISRC/label anchored
_COMPILATION_ALBUM_RE = re.compile(r'\b(?:mix|compilation|playlist)\b', re.IGNORECASE)

# Title pre-clean regexes (noise removal before similarity comparison)
_BPM_SUFFIX_RE = re.compile(r'\s*\b\d{2,3}\s*bpm\b\s*$', re.IGNORECASE)
# Matches label-keyword brackets ANYWHERE in title (not trailing-only)
_LABEL_BRACKET_RE = re.compile(
    r'\s*[\(\[]\s*[^)\]]*\b(?:records?|music(?:al)?|entertainment|digital|audio|label)\b'
    r'[^)\]]*\s*[\)\]]',
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Version-term normalisation
# ---------------------------------------------------------------------------

# Regex to split multi-artist strings into individual names
_ARTIST_SPLIT_RE = re.compile(
    r"\s*(?:,|&|\bfeat\.?\b|\bft\.?\b|\bvs\.?\b|\bx\b)\s*",
    re.IGNORECASE,
)

_VERSION_TERMS_RE = re.compile(
    r"\s*[\(\[]\s*("
    r"Original Mix|Extended Mix|Radio Edit|Dub Mix|Instrumental"
    r"|Remix|Rework|Bootleg|VIP Mix|Club Mix|Short Mix|Intro Mix"
    r"|Outro Mix|Reprise|Edit|Re-Edit|Vocal Mix|Acapella|Intro|Outro|Dub"
    r")[^)\]]*[\)\]]",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

_W_TITLE   = 0.50
_W_ARTIST  = 0.30
_W_VERSION = 0.20

# Optional label boosters (additive, capped at 1.0)
_LABEL_SIM_BOOST   = 0.05   # awarded when label_sim >= _LABEL_SIM_THRESHOLD
_LABEL_SIM_THRESHOLD = 0.70
_LABEL_ALIAS_BOOST = 0.05   # awarded when labels are in the same alias group

# ---------------------------------------------------------------------------
# Label alias groups
# ---------------------------------------------------------------------------

_LABEL_ALIAS_GROUPS: List[frozenset] = [
    frozenset(["sony music", "sony music entertainment", "sme", "sony"]),
    frozenset(["warner music", "warner records", "wea", "warner music group", "wmg"]),
    frozenset(["universal music", "universal music group", "umg", "umi", "universal"]),
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
    """Lower-case, NFC, strip punctuation, collapse spaces (comparison only)."""
    if not s:
        return ""
    s = _nfc(s).lower()
    s = re.sub(r"[''`]s\b", "s", s)
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\b(the|a|an)\b\s*", "", s)
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
    """Return the bare version term, e.g. 'Original Mix', or ''."""
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
    Score version/mix agreement between two title strings.

    Both absent  → 1.0  (agree: neither has a version marker)
    Both present, same   → 1.0
    Both present, differ → 0.0  (different recordings — hard mismatch)
    One present, one absent → 0.0  (asymmetric, uncertain)
    """
    v_a = _extract_version_label(title_a)
    v_b = _extract_version_label(title_b)

    if not v_a and not v_b:
        return 1.0
    if v_a and v_b:
        return 1.0 if v_a.lower() == v_b.lower() else 0.0
    return 0.0


def _label_alias_boost(label_a: str, label_b: str) -> float:
    """Return _LABEL_ALIAS_BOOST when both labels resolve to the same alias group."""
    if not label_a or not label_b:
        return 0.0
    gi_a = _LABEL_ALIAS_INDEX.get(_normalize_for_compare(label_a))
    gi_b = _LABEL_ALIAS_INDEX.get(_normalize_for_compare(label_b))
    if gi_a is not None and gi_a == gi_b:
        return _LABEL_ALIAS_BOOST
    return 0.0


def _clean_title_for_match(title: str) -> Tuple[str, bool]:
    """
    Strip noise tokens from a title before similarity comparison.

    Strips (in order):
      1. Label-keyword brackets anywhere — e.g. "(Kontor Records)", "(Defected Music)"
      2. Known-label-alias brackets anywhere — e.g. "(Defected)", "(Toolroom)"
      3. Trailing BPM with keyword ("128bpm") then bare number in range 60–200
      4. Duplicate adjacent bracket segments — e.g. "(Remix) (Remix)" → "(Remix)"
      5. Collapse extra whitespace

    Does NOT touch version tokens — handled separately by _strip_version().
    Returns (cleaned_title, was_cleaned).
    """
    t = title.strip()

    # 1. Label-keyword brackets (anywhere)
    t = _LABEL_BRACKET_RE.sub("", t)

    # 2. Known-alias label brackets (anywhere) — scan all brackets, remove matches
    spans = [
        (m.start(), m.end())
        for m in re.finditer(r'[\(\[][^)\]]*[\)\]]', t)
        if _normalize_for_compare(m.group(0)[1:-1]) in _LABEL_ALIAS_INDEX
    ]
    for start, end in reversed(spans):
        t = t[:start] + t[end:]

    # 3. Trailing BPM — with keyword then bare number (60–200)
    t = _BPM_SUFFIX_RE.sub("", t)
    t = re.sub(r'\s+\b(?:[6-9]\d|1[0-9]{2}|200)\b\s*$', '', t)

    # 4. Duplicate adjacent bracket segments
    t = re.sub(r'([\(\[][^)\]]*[\)\]])\s*\1', r'\1', t, flags=re.IGNORECASE)

    t = re.sub(r'\s+', ' ', t).strip()
    return t, t != title.strip()


def _parse_artist_list(s: str) -> List[str]:
    """Split an artist string into a list of normalized individual artist names."""
    if not s:
        return []
    parts = _ARTIST_SPLIT_RE.split(s)
    return [_normalize_for_compare(p) for p in parts if p.strip()]


def _artist_match_score(file_artist: str, cand_artist: str) -> Tuple[float, str]:
    """
    Return (effective_sim, match_note) for artist comparison.

    Falls back from string similarity to list-based subset matching when direct
    sim is below ARTIST_SIM_GATE — handles multi-artist tracks where a candidate
    represents only one of the credited artists.

    match_note:
      ""                    — standard string match (no special case)
      "subset_artist_match" — cand artists ⊆ file artists (score _SUBSET_ARTIST_SCORE)
      "partial_artist_match"— some overlap, not a strict subset (score _PARTIAL_ARTIST_SCORE,
                               which is below ARTIST_SIM_GATE and will fail Gate B)
    """
    sim = _similarity(file_artist, cand_artist)
    if sim >= ARTIST_SIM_GATE:
        return sim, ""

    file_set = set(_parse_artist_list(file_artist))
    cand_set = set(_parse_artist_list(cand_artist))
    if not file_set or not cand_set:
        return sim, ""

    if cand_set <= file_set:
        return _SUBSET_ARTIST_SCORE, "subset_artist_match"

    if file_set & cand_set:
        return _PARTIAL_ARTIST_SCORE, "partial_artist_match"

    return sim, ""


# ---------------------------------------------------------------------------
# Score a single candidate
# ---------------------------------------------------------------------------

def score_candidate(
    candidate: EnrichmentCandidate,
    current_tags: Dict[str, str],
    current_isrc: Optional[str] = None,
    canonical_artist: Optional[str] = None,
) -> Tuple[float, bool, str, Dict[str, float]]:
    """
    Compute a confidence score for one candidate against the track's current tags.

    Returns:
        (confidence, isrc_matched, reason_string, component_sims)

    component_sims keys: title_sim, artist_sim, version_sim, label_sim,
                         label_boost, alias_boost
    """
    # --- ISRC exact match (highest confidence; bypasses all gates) ---
    if (
        current_isrc
        and candidate.isrc
        and current_isrc.strip().upper() == candidate.isrc.strip().upper()
    ):
        sims: Dict[str, float] = {
            "title_sim": 1.0, "artist_sim": 1.0, "version_sim": 1.0,
            "label_sim": 0.0, "label_boost": 0.0, "alias_boost": 0.0,
        }
        return 0.98, True, "exact ISRC match", sims

    # Artist to compare: prefer canonical (alias-resolved) over raw tag
    current_artist = (canonical_artist or current_tags.get("artist", "")).strip()
    current_title  = (current_tags.get("title", "")).strip()
    current_label  = (current_tags.get("organization", "")).strip()
    cand_title_str = candidate.title or ""

    # Title pre-clean: strip label fragments + BPM before base comparison.
    # Version tokens (Original Mix etc.) are stripped separately by _strip_version().
    current_title_cmp, _cur_cleaned  = _clean_title_for_match(current_title)
    cand_title_cmp,    _cand_cleaned = _clean_title_for_match(cand_title_str)
    title_cleaned_for_match = _cur_cleaned or _cand_cleaned

    # Base titles (version tokens stripped from both sides)
    current_base = _strip_version(current_title_cmp)
    cand_base    = _strip_version(cand_title_cmp)

    title_sim                     = _similarity(current_base, cand_base)
    artist_sim, artist_match_note = _artist_match_score(current_artist, candidate.artist or "")
    version_sim                   = _version_sim(current_title, cand_title_str)
    label_sim   = _similarity(current_label, candidate.label or "") if current_label else 0.0

    # Version tolerance: if exactly one side lacks a version token and the
    # title+artist signal is very strong, treat the asymmetry as a soft miss
    # rather than a hard zero in the version component.
    version_missing_tolerated = False
    _v_orig = _extract_version_label(current_title)
    _v_cand = _extract_version_label(cand_title_str)
    if (
        version_sim == 0.0
        and bool(_v_orig) != bool(_v_cand)
        and title_sim >= 0.95
        and artist_sim >= ARTIST_SIM_GATE
    ):
        version_sim = 0.70
        version_missing_tolerated = True

    # Weighted composite (title-primary)
    confidence = (
        title_sim   * _W_TITLE
        + artist_sim  * _W_ARTIST
        + version_sim * _W_VERSION
    )

    # Optional label boosters
    label_boost = _LABEL_SIM_BOOST if label_sim >= _LABEL_SIM_THRESHOLD else 0.0
    alias_boost = _label_alias_boost(current_label, candidate.label or "")
    confidence = min(1.0, confidence + label_boost + alias_boost)

    notes = []
    if artist_match_note:
        notes.append(artist_match_note)
    if version_missing_tolerated:
        notes.append("version_missing_tolerated")
    if label_boost:
        notes.append("label_match_boost")
    if alias_boost:
        notes.append("label_alias_boost")
    if title_cleaned_for_match:
        notes.append("title_cleaned_for_match")

    reason = (
        f"title={title_sim:.2f} artist={artist_sim:.2f} "
        f"version={version_sim:.2f} label={label_sim:.2f}"
    )
    if notes:
        reason += " [" + ", ".join(notes) + "]"

    sims = {
        "title_sim": title_sim,
        "artist_sim": artist_sim,
        "version_sim": version_sim,
        "label_sim": label_sim,
        "label_boost": label_boost,
        "alias_boost": alias_boost,
    }
    return confidence, False, reason, sims


# ---------------------------------------------------------------------------
# Public: select best candidate and build proposed changes
# ---------------------------------------------------------------------------

def best_match(
    candidates: List[EnrichmentCandidate],
    current_tags: Dict[str, str],
    current_isrc: Optional[str] = None,
    sources_tried: Optional[List[str]] = None,
    canonical_artist: Optional[str] = None,
) -> EnrichmentMatch:
    """
    Run the full matching pipeline against all candidates and return the best
    EnrichmentMatch, including a machine-readable decision_code.

    Pipeline:
      1. Score each candidate (ISRC exact match bypasses all gates)
      2. Title gate  — discard candidates with title_sim < TITLE_SIM_GATE
      3. Artist gate — reject if artist_sim < ARTIST_SIM_GATE (no ISRC anchor)
      4. Version gate — reject on conflicting version tokens
      5. Ambiguity  — flag "review_ambiguous" when top-2 are too close
      6. Threshold  — "ready" / "review" / "skipped_low_score"
    """
    _tried = sources_tried or []

    if not candidates:
        return EnrichmentMatch(
            source_used="none",
            reason="no candidates returned",
            decision_code="skipped_low_score",
            sources_tried=_tried,
        )

    # --- Score all candidates ---
    scored: List[Tuple[float, bool, str, Dict[str, float], EnrichmentCandidate]] = []
    for cand in candidates:
        conf, isrc_hit, reason, sims = score_candidate(
            cand, current_tags, current_isrc, canonical_artist,
        )
        scored.append((conf, isrc_hit, reason, sims, cand))

    # Sort: ISRC matches first, then confidence descending
    scored.sort(key=lambda t: (t[1], t[0]), reverse=True)

    # --- ISRC exact match: bypass all gates ---
    if scored[0][1]:  # isrc_hit
        top_conf, _, top_reason, _, top_cand = scored[0]
        changes = _build_changes(top_cand, current_tags, current_isrc, top_conf, True,
                                 label_sim=0.0)
        return EnrichmentMatch(
            candidate=top_cand,
            confidence=top_conf,
            proposed_changes=changes,
            source_used=top_cand.source,
            reason=top_reason,
            isrc_matched=True,
            decision_code="ready",
            sources_tried=_tried,
        )

    current_title = (current_tags.get("title") or "").strip()

    # --- Gate A: Title similarity (>= TITLE_SIM_GATE required) ---
    gate_a_passing = [
        (conf, isrc_hit, reason, sims, cand)
        for (conf, isrc_hit, reason, sims, cand) in scored
        if sims["title_sim"] >= TITLE_SIM_GATE
    ]

    if not gate_a_passing:
        best_title_sim = max(s[3]["title_sim"] for s in scored)
        return EnrichmentMatch(
            candidate=scored[0][4],
            confidence=scored[0][0],
            proposed_changes=[],
            source_used=scored[0][4].source,
            reason=(
                f"title gate failed: best title_sim={best_title_sim:.2f} < {TITLE_SIM_GATE}"
            ),
            decision_code="skipped_low_score",
            sources_tried=_tried,
        )

    top_conf, _, top_reason, top_sims, top_cand = gate_a_passing[0]

    # --- Gate B: Artist consistency ---
    artist_sim = top_sims["artist_sim"]
    if artist_sim < ARTIST_SIM_GATE:
        log.info(
            "Artist mismatch: sim=%.2f < %.2f  file=%r  candidate=%r",
            artist_sim, ARTIST_SIM_GATE,
            current_tags.get("artist", "?"), top_cand.artist,
        )
        return EnrichmentMatch(
            candidate=top_cand,
            confidence=min(top_conf, 0.74),
            proposed_changes=[],
            source_used=top_cand.source,
            reason=(
                f"artist_mismatch: sim={artist_sim:.2f} < {ARTIST_SIM_GATE} "
                f"(file={current_tags.get('artist','?')!r} "
                f"candidate={top_cand.artist!r})  [{top_reason}]"
            ),
            decision_code="skipped_artist_mismatch",
            sources_tried=_tried,
        )

    # --- Gate C: Version alignment ---
    orig_v = _extract_version_label(current_title)
    cand_v = _extract_version_label(top_cand.title or "")
    if orig_v and cand_v and orig_v.lower() != cand_v.lower():
        log.info(
            "Version conflict: %r vs %r  file=%r",
            orig_v, cand_v, current_title,
        )
        return EnrichmentMatch(
            candidate=top_cand,
            confidence=min(top_conf, 0.74),
            proposed_changes=[],
            source_used=top_cand.source,
            reason=(
                f"version_conflict: file={orig_v!r} candidate={cand_v!r}  [{top_reason}]"
            ),
            decision_code="skipped_version_conflict",
            sources_tried=_tried,
        )

    # --- Ambiguity check ---
    # If 2+ candidates passed gate A and their confidence gap is too small, don't guess.
    if len(gate_a_passing) >= 2:
        second_conf = gate_a_passing[1][0]
        gap = top_conf - second_conf
        if gap < AMBIGUITY_GAP and top_conf >= THRESHOLD_REVIEW:
            log.info(
                "Ambiguous match: top=%.2f second=%.2f gap=%.2f — marking review_ambiguous",
                top_conf, second_conf, gap,
            )
            return EnrichmentMatch(
                candidate=top_cand,
                confidence=top_conf,
                proposed_changes=[],    # do not propose changes when ambiguous
                source_used=top_cand.source,
                reason=(
                    f"ambiguous: top={top_conf:.2f} second={second_conf:.2f} "
                    f"gap={gap:.2f} < {AMBIGUITY_GAP}  [{top_reason}]"
                ),
                decision_code="review_ambiguous",
                sources_tried=_tried,
            )

    # --- Score threshold ---
    if top_conf < THRESHOLD_REVIEW:
        return EnrichmentMatch(
            candidate=top_cand,
            confidence=top_conf,
            proposed_changes=[],
            source_used=top_cand.source,
            reason=f"low_score: {top_conf:.2f} < {THRESHOLD_REVIEW}  [{top_reason}]",
            decision_code="skipped_low_score",
            sources_tried=_tried,
        )

    changes = _build_changes(top_cand, current_tags, current_isrc, top_conf, False,
                             label_sim=top_sims["label_sim"])

    if top_conf >= THRESHOLD_APPLY:
        decision_code = "ready"
    else:
        decision_code = "review"   # 0.75–0.89: human confirmation needed

    return EnrichmentMatch(
        candidate=top_cand,
        confidence=top_conf,
        proposed_changes=changes,
        source_used=top_cand.source,
        reason=top_reason,
        isrc_matched=False,
        decision_code=decision_code,
        sources_tried=_tried,
    )


# ---------------------------------------------------------------------------
# Internal: safe change policy
# ---------------------------------------------------------------------------

def _build_changes(
    cand: EnrichmentCandidate,
    current_tags: Dict[str, str],
    current_isrc: Optional[str],
    confidence: float,
    isrc_matched: bool,
    label_sim: float = 0.0,
) -> List[Dict[str, str]]:
    """
    Build safe proposed tag changes. See module docstring for full policy.

    artist is never proposed — owned by the artist-intelligence layer.
    """
    changes: List[Dict[str, str]] = []

    def _add(field: str, current_val: str, new_val: str) -> None:
        cur = (current_val or "").strip()
        new = (new_val or "").strip()
        if new and new != cur:
            changes.append({"field": field, "old": cur, "new": new})

    current_title = (current_tags.get("title") or "").strip()
    current_album = (current_tags.get("album") or "").strip()
    current_label = (current_tags.get("organization") or "").strip()

    # title
    if cand.title:
        if not current_title:
            _add("title", current_title, cand.title)
        elif isrc_matched:
            curr_base = _strip_version(current_title)
            cand_base = _strip_version(cand.title)
            if _similarity(curr_base, cand_base) < 0.80:
                existing_version = _extract_version(current_title)
                proposed = cand.title
                if existing_version and existing_version.lower() not in proposed.lower():
                    proposed = f"{_strip_version(proposed)} {existing_version}".strip()
                _add("title", current_title, proposed)

    # album — reject compilation/mix/playlist albums unless identity is anchored
    if cand.album:
        is_compilation = _COMPILATION_ALBUM_RE.search(cand.album) is not None
        allow_album = (
            not is_compilation
            or isrc_matched
            or label_sim >= _LABEL_SIM_THRESHOLD
        )
        if not allow_album:
            log.debug(
                "album_rejected_compilation: %r  title=%r",
                cand.album, current_tags.get("title", "?"),
            )
        elif not current_album:
            _add("album", current_album, cand.album)
        elif isrc_matched and _similarity(current_album, cand.album) < 0.70:
            _add("album", current_album, cand.album)

    # label (organization/TPUB)
    if cand.label:
        if not current_label:
            _add("label", current_label, cand.label)
        elif isrc_matched and confidence >= 0.95 and _similarity(current_label, cand.label) < 0.70:
            _add("label", current_label, cand.label)

    # isrc
    if cand.isrc and not current_isrc:
        _add("isrc", "", cand.isrc)

    return changes
