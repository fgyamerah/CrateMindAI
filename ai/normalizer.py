"""
ai/normalizer.py — AI-assisted metadata normalization using a local Ollama model.

Architecture:
  file → read_full_tags() + parse_filename_stem()  (deterministic, existing utilities)
       → build_prompt()                             (construct strict JSON-only prompt)
       → OllamaClient.generate()                   (local inference, no cloud)
       → extract_json() + NormalizedMetadata.from_dict()  (strict schema validation)
       → compute_diff()                             (compare proposed vs current)
       → print_preview()                            (human-readable terminal output)
       → apply_normalized() if --apply              (write back via mutagen easy tags)

Safe by design:
  - Dry-run / preview is the default
  - apply requires explicit --apply flag AND confidence >= --min-confidence
  - Only writes artist, title (+ version), and label — never BPM, key, or cues
  - Reuses existing tag-write pattern (mutagen easy mode, same as sanitizer.py)

Public entry point: run_ai_normalize(args)  — called by pipeline.py dispatch.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from ai.metadata_schema import (
    NormalizedMetadata, NormalizeResult,
    MIN_AI_CONFIDENCE,
    REJECTION_LOW_CONFIDENCE, REJECTION_SCHEMA_INVALID,
    REJECTION_GUARDRAIL, REJECTION_PARSED_CONFLICT, REJECTION_AI_ERROR,
)
from ai.ollama_client import (
    OllamaClient, OllamaConnectionError, OllamaError, OllamaTimeoutError,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------

def _collect_files(input_path: Path, limit: Optional[int]) -> List[Path]:
    """
    Recursively collect all supported audio files under input_path.
    Mirrors the _collect_inbox() pattern in pipeline.py.
    """
    files: List[Path] = []
    seen: set = set()

    for ext in config.AUDIO_EXTENSIONS:
        for path in sorted(input_path.rglob(f"*{ext}")):
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)
        # Also catch uppercase extensions (e.g. .MP3 on case-insensitive mounts)
        for path in sorted(input_path.rglob(f"*{ext.upper()}")):
            key = str(path)
            if key not in seen:
                seen.add(key)
                files.append(path)

    files.sort()

    if limit is not None and limit > 0:
        files = files[:limit]

    return files


# ---------------------------------------------------------------------------
# Tag reading (extended — more fields than sanitizer._read_tags)
# ---------------------------------------------------------------------------

def _read_full_tags(path: Path) -> Dict[str, str]:
    """
    Read metadata from an audio file using mutagen easy tags.
    Returns a dict with string values (empty string if missing).

    Reads: title, artist, album, genre, comment, organization (label/TPUB)
    Uses easy=True for a format-agnostic interface across MP3/FLAC/M4A.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return {}
        get = lambda key: (audio.get(key) or [""])[0]
        return {
            "title":        get("title"),
            "artist":       get("artist"),
            "album":        get("album"),
            "genre":        get("genre"),
            "comment":      get("comment"),
            "organization": get("organization"),  # TPUB / label
        }
    except Exception as exc:
        log.debug("Could not read tags from %s: %s", path.name, exc)
        return {}


# ---------------------------------------------------------------------------
# Tag writing (apply step — mutagen easy mode, same pattern as sanitizer.py)
# ---------------------------------------------------------------------------

def _apply_tags(path: Path, fields: Dict[str, str], dry_run: bool) -> bool:
    """
    Write a subset of metadata fields back to a file using mutagen easy tags.
    Only writes non-empty proposed values. Returns True on success.

    In dry_run mode, validates the file is readable but writes nothing.
    """
    if dry_run:
        return True
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            log.warning("Could not open %s for tag writing", path.name)
            return False
        for key, value in fields.items():
            if not value:
                continue
            try:
                audio[key] = [value]
            except Exception as exc:
                # Some formats don't support all easy tag keys — skip gracefully
                log.debug("Could not set tag %r on %s: %s", key, path.name, exc)
        audio.save()
        return True
    except Exception as exc:
        log.error("Tag write failed for %s: %s", path.name, exc)
        return False


# ---------------------------------------------------------------------------
# Deterministic pre-cleanup  (runs BEFORE AI sees the tags)
# ---------------------------------------------------------------------------

def _pre_clean_title(title: str) -> str:
    """
    Apply deterministic cleanup to a title tag before AI normalization.
    No model involved — fixes known data-quality patterns in existing tags.

    Steps:
      1. Pipe-duplicate removal  — "Track | 02 Track (Original Mix)" → "Track (Original Mix)"
         Strips numeric/Camelot prefixes from each segment, deduplicates
         case-insensitively, and keeps the segment with the most content
         (e.g. the one that carries version info).
      2. Trailing conflict-number removal — "Track (2)" → "Track"
         Strips (2)–(9) at end; never strips (1) or multi-digit numbers.
      3. Spacing normalization — collapse double spaces; add missing space
         before opening parenthesis.
    """
    if not title:
        return title

    # 1. Pipe-duplicated content
    if "|" in title:
        # Strip numeric AND Camelot-style prefixes: "3A - ", "02 | ", "003. "
        _pfx = re.compile(r"^\s*[\*\d]+[A-Za-z]?\s*[\.\|\-]*\s*")
        parts = [_pfx.sub("", p).strip() for p in title.split("|") if p.strip()]

        # Deduplicate case-insensitively
        seen: set = set()
        unique: list = []
        for p in parts:
            k = p.lower()
            if k not in seen:
                seen.add(k)
                unique.append(p)

        if len(unique) == 1:
            title = unique[0]
        elif unique:
            # If one part extends another (e.g. adds version info), keep the
            # longer one.  Otherwise keep the first (most-significant) part.
            best = unique[0]
            for p in unique[1:]:
                bl, pl = best.lower(), p.lower()
                if pl.startswith(bl) or bl.startswith(pl):
                    best = p if len(p) > len(best) else best
            title = best

    # 2. Trailing conflict-number artifact: (2) … (9)
    #    Added by iTunes / file managers on naming conflicts.
    #    Strip only when something meaningful remains.
    base = re.sub(r"\s*\([2-9]\)\s*$", "", title).strip()
    if base:
        title = base

    # 3. Spacing
    title = re.sub(r"  +", " ", title)              # double spaces → single
    title = re.sub(r"([^\s\(])\(", r"\1 (", title)  # space before ( when missing
    return title.strip()


def _pre_clean_tags(tags: Dict[str, str]) -> Dict[str, str]:
    """
    Return a copy of tags with deterministic title cleanup applied.
    Called at the start of _normalize_track, before the AI prompt is built.
    """
    cleaned = dict(tags)
    if cleaned.get("title"):
        orig = cleaned["title"]
        cleaned["title"] = _pre_clean_title(orig)
        if cleaned["title"] != orig:
            log.debug("Pre-clean title: %r → %r", orig, cleaned["title"])
    return cleaned


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

# EDM/DJ version terms — listed in the prompt so the model knows what to extract
_EDM_VERSION_TERMS = (
    "Original Mix, Extended Mix, Radio Edit, Dub Mix, Instrumental, Remix, "
    "Rework, Bootleg, VIP Mix, Club Mix, Short Mix, Intro Mix, Outro Mix, "
    "Reprise, Edit, Re-Edit, Vocal Mix, Acapella, Intro, Outro"
)


def _build_prompt(
    filename: str,
    current_tags: Dict[str, str],
    parsed: Dict[str, Any],
) -> str:
    """
    Build the prompt sent to Ollama.

    Uses a strict JSON-only instruction to minimize model hallucination.
    Passes both the current file tags and the deterministic filename parse
    so the model can cross-reference both sources.
    """
    # Format tag values for display — show "(empty)" for missing fields
    def _fmt(val: str) -> str:
        return val.strip() if val and val.strip() else "(empty)"

    prompt = f"""You are a music metadata normalization assistant for a DJ track library.

CRITICAL RULE: Return ONLY a valid JSON object. No explanation, no markdown code fences, no extra text before or after the JSON. Any non-JSON output breaks the pipeline.

EDM/DJ VERSION TERMS — the only terms allowed in the "version" field:
{_EDM_VERSION_TERMS}

TASK: Normalize the metadata for this DJ library track. Only propose a change if it is a clear, deterministic improvement from the provided data. When in doubt, return the original value unchanged.

STRICT NORMALIZATION RULES:

1. ARTIST — DO NOT modify the artist field if it already exists.
   - Never move "(feat ...)" from the title into the artist field.
   - Never append featured artist text to the artist string.
   - Never add or infer featured artists that are not already present in the existing tags.
   - Artist is authoritative from existing tags. Return it exactly as-is unless it is clearly corrupt garbage.

2. FEATURED ARTISTS — DO NOT infer or invent featured artists.
   - If "(feat. X)" or "(ft. X)" already appears in the title, leave it there. Do NOT copy it into featured_artists.
   - Only populate featured_artists if a featured artist token is in the ARTIST tag itself (not in the title).
   - Return featured_artists as [] in all other cases.

3. LABEL — DO NOT modify the label field if it is empty or unknown.
   - If the current label tag is empty, absent, or unrecognisable, return label as null.
   - Never output placeholder strings like "(empty)" — always use null.
   - Only return a label value if a real, non-empty label tag already exists in the current tags.

4. VERSION — DO NOT invent version information.
   - Only populate the version field if a version term (from the list above) is clearly and explicitly present in the filename or title.
   - NEVER add "Original Mix" or any other version unless it is literally written in the filename or title.
   - If the title already contains a version qualifier such as "(Original Mix)" or "(Extended Mix)", return version as null — do NOT duplicate it.
   - Never duplicate version strings.

5. DO NOT guess or hallucinate.
   - If uncertain about any field, return the original value unchanged.
   - Lower confidence instead of guessing.
   - Return null for any field you cannot determine with high confidence from the provided data.

6. TITLE normalization:
   - Preserve existing "(feat ...)" in the title exactly as-is.
   - Append a version only if it is missing from the title AND is clearly present in the filename.
   - Never produce a title that contains duplicate parentheses blocks (e.g. "(Original Mix) (Original Mix)").

7. CONFIDENCE rules:
   - Set confidence >= 0.85 ONLY when the proposed change is obvious and deterministic from the provided data.
   - Set confidence 0.5–0.7 for uncertain or partially-inferred cases.
   - Set confidence < 0.5 when guessing. Prefer returning the original value instead.

8. OUTPUT FORMAT:
   - Return JSON only. No markdown, no explanation.
   - Use null for unknown or empty fields. NEVER use strings like "(empty)" or "unknown".
   - Never return a field value that is identical to the current tag value (no no-op edits).

FILENAME: {filename}

CURRENT FILE TAGS:
  artist       : {_fmt(current_tags.get('artist', ''))}
  title        : {_fmt(current_tags.get('title', ''))}
  album        : {_fmt(current_tags.get('album', ''))}
  label        : {_fmt(current_tags.get('organization', ''))}
  genre        : {_fmt(current_tags.get('genre', ''))}
  comment      : {_fmt(current_tags.get('comment', ''))}

DETERMINISTIC FILENAME PARSE (for reference only — do not override existing non-empty tags):
  artist       : {_fmt(str(parsed.get('artist', '')))}
  title        : {_fmt(str(parsed.get('title', '')))}
  version      : {_fmt(str(parsed.get('version', '')))}

Return this exact JSON structure (replace values, keep all keys):
{{
  "artist": "string or null",
  "title": "string or null",
  "version": "string or null",
  "label": "string or null",
  "remixers": [],
  "featured_artists": [],
  "confidence": 0.0,
  "notes": "string or null"
}}"""

    return prompt


# ---------------------------------------------------------------------------
# JSON extraction from model response
# ---------------------------------------------------------------------------

def _extract_json(text: str) -> dict:
    """
    Safely extract a JSON object from a model response that may contain
    extra text or markdown code fences.

    Tries in order:
      1. Strip markdown ```json ... ``` fence and parse the inner block
      2. Find and parse the first {...} block in the response
      3. Parse the entire response as JSON

    Raises ValueError if no valid JSON object is found.
    """
    # 1. Markdown code fence: ```json ... ``` or ``` ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 2. First {...} block (greedy, handles nested braces poorly but sufficient here)
    brace = re.search(r"\{[\s\S]*\}", text)
    if brace:
        candidate = brace.group(0)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            pass

    # 3. Full response
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"No valid JSON found in model response (first 200 chars): "
            f"{text[:200]!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Per-track normalization
# ---------------------------------------------------------------------------

def _normalize_track(
    path: Path,
    client: OllamaClient,
    min_confidence: float = MIN_AI_CONFIDENCE,
) -> NormalizeResult:
    """
    Run the full AI normalization flow for a single track.

    Returns a NormalizeResult whose .rejected property is True when the result
    is a no-op (rejection_reason will be one of the REJECTION_* constants).

    Rejection order:
      1. ai_error        — Ollama unreachable or model error
      2. schema_invalid  — model returned unparseable JSON
      3. low_confidence  — confidence below min_confidence threshold
      4. parsed_conflict — AI version contradicts deterministic filename parse
      5. guardrail_violation — AI artist contradicts current tag (model confused)
    """
    current_tags = _read_full_tags(path)
    current_tags = _pre_clean_tags(current_tags)

    from modules.parser import parse_filename_stem
    parsed = parse_filename_stem(path.stem)

    prompt = _build_prompt(path.name, current_tags, parsed)

    try:
        response_text = client.generate(prompt)
    except (OllamaConnectionError, OllamaTimeoutError, OllamaError) as exc:
        err = str(exc)
        log.info("REJECT [%s] %s — %s", REJECTION_AI_ERROR, path.name, err)
        return NormalizeResult(
            current_tags=current_tags,
            proposed=NormalizedMetadata(),
            rejection_reason=REJECTION_AI_ERROR,
            error=err,
        )

    try:
        raw_dict = _extract_json(response_text)
    except ValueError as exc:
        err = f"JSON parse error: {exc}"
        log.info("REJECT [%s] %s — %s", REJECTION_SCHEMA_INVALID, path.name, exc)
        return NormalizeResult(
            current_tags=current_tags,
            proposed=NormalizedMetadata(),
            rejection_reason=REJECTION_SCHEMA_INVALID,
            error=err,
        )

    proposed = NormalizedMetadata.from_dict(raw_dict)

    # Strip residual junk from AI output fields
    try:
        from modules.sanitizer import sanitize_text
        if proposed.artist:
            proposed.artist = sanitize_text(proposed.artist).strip() or None
        if proposed.title:
            proposed.title = sanitize_text(proposed.title).strip() or None
        if proposed.label:
            proposed.label = sanitize_text(proposed.label).strip() or None
    except Exception as exc:
        log.debug("Sanitize pass failed for %s: %s", path.name, exc)

    # Confidence gate — reject before any further processing
    if proposed.confidence < min_confidence:
        log.info(
            "REJECT [%s] %s  confidence=%.2f < %.2f",
            REJECTION_LOW_CONFIDENCE, path.name, proposed.confidence, min_confidence,
        )
        return NormalizeResult(
            current_tags=current_tags,
            proposed=proposed,
            rejection_reason=REJECTION_LOW_CONFIDENCE,
        )

    # Parsed alignment check
    align_reason = _check_parsed_alignment(proposed, parsed, current_tags)
    if align_reason:
        log.info("REJECT [%s] %s", align_reason, path.name)
        return NormalizeResult(
            current_tags=current_tags,
            proposed=proposed,
            rejection_reason=align_reason,
        )

    # Hard guards — enforced in code regardless of model output
    proposed, guard_reason = _apply_hard_guards(proposed, current_tags, path.name)
    if guard_reason:
        log.info("REJECT [%s] %s", guard_reason, path.name)

    return NormalizeResult(
        current_tags=current_tags,
        proposed=proposed,
        rejection_reason=guard_reason,  # None in normal flow
    )


# ---------------------------------------------------------------------------
# Hard post-processing guards  (model output is an untrusted suggestion)
# ---------------------------------------------------------------------------

# Numeric / symbol prefixes to strip from the start of a title.
# Matches patterns like: "1 | ", "01 | ", "003. ", "01 ", "***2 | "
_NUMERIC_PREFIX_RE = re.compile(r"^\s*[\*\d]+\s*[\.\|\-]*\s*")


def _strip_numeric_prefix(title: str) -> str:
    """
    Remove a leading numeric / symbol prefix from a title string.

    Examples:
      "1 | Track Name"  → "Track Name"
      "01 Track Name"   → "Track Name"
      "003. Track Name" → "Track Name"
      "***2 | Track"    → "Track"
    """
    stripped = _NUMERIC_PREFIX_RE.sub("", title).strip()
    # Only accept the result if actual text remains (guards against stripping
    # a title that is *entirely* numeric, e.g. "01" — keep original in that case)
    return stripped if stripped else title


# Recognised version/mix tokens — used in multiple guards below.
_VERSION_BLOCK_RE = re.compile(
    r"[\(\[]\s*("
    r"Original Mix|Extended Mix|Radio Edit|Dub Mix|Instrumental"
    r"|Remix|Rework|Bootleg|VIP Mix|Club Mix|Short Mix|Intro Mix"
    r"|Outro Mix|Reprise|Edit|Re-Edit|Vocal Mix|Acapella|Intro|Outro|Dub"
    r")\s*[\)\]]",
    re.IGNORECASE,
)


def _reconstruct_title(current_title: str, proposed_version: Optional[str], filename: str) -> str:
    """
    Build the final title deterministically from the current tag — the AI
    title output is ignored entirely.

    Steps:
      1. Strip leading numeric / symbol prefix  ("3 | Track" → "Track")
      2. Collapse any duplicate version blocks or malformed parens
      3. If proposed.version is explicitly in the filename AND is not already
         present in the cleaned title, append it once

    Guarantees:
      - Lossless: no words or brackets removed from the original tag
      - Prefix-clean: numeric / symbol prefixes fully removed
      - Version-safe: all version tokens preserved; one new one may be added
      - No hallucination: AI cannot introduce words or numbers
    """
    if not current_title:
        return current_title

    # 1. Strip numeric / symbol prefix
    title = _strip_numeric_prefix(current_title)

    # 2. Clean up malformed parens and deduplicate version blocks
    title = _collapse_duplicate_versions(title)

    # 3. Append proposed.version only when it is explicitly in the filename
    #    and genuinely absent from the title (AI-detected gap, filename-verified)
    if proposed_version:
        in_filename      = proposed_version.lower() in filename.lower()
        already_in_title = re.search(re.escape(proposed_version), title, re.IGNORECASE)
        if in_filename and not already_in_title:
            title = f"{title} ({proposed_version})"
            log.debug("Reconstruction appended version %r from filename", proposed_version)

    return title


def _apply_hard_guards(
    proposed: NormalizedMetadata,
    current_tags: Dict[str, str],
    filename: str,
) -> Tuple[NormalizedMetadata, Optional[str]]:
    """
    Enforce DJ-library-safe constraints on the model's proposed metadata.

    The model output is treated as an untrusted hint — never as source of truth.
    This function is the final authority on every field.

    Guards (in order):
      1. Artist lock       — always use current artist tag; AI artist ignored.
      2. Feat cleanup      — strip feat suffix AI injected into artist field.
      3. Title reconstruct — build title deterministically from current tag;
                             AI title is discarded. Only proposed.version is
                             consulted (and only if present in filename).
      4. Label sanitation  — null out empty / placeholder label values.

    Returns:
        (proposed, guard_reason)
        guard_reason is REJECTION_GUARDRAIL when the model proposed an artist
        significantly different from the current tag (signal of model confusion).
        None in all normal cases — artist lock / title reconstruct are expected.
    """
    current_artist = (current_tags.get("artist") or "").strip()
    current_title  = (current_tags.get("title")  or "").strip()
    guard_reason: Optional[str] = None

    # ------------------------------------------------------------------
    # 1. HARD LOCK: artist field
    # AI artist output is ignored entirely — always overwrite with current tag.
    # Artist normalization is owned by the artist-intelligence layer.
    # If the AI proposed a materially different artist, flag it — the model
    # may be confused about track identity, which undermines its other proposals.
    # ------------------------------------------------------------------
    ai_artist = (proposed.artist or "").strip()
    proposed.artist = current_artist or None

    if ai_artist and current_artist and ai_artist.lower() != current_artist.lower():
        proposed.guardrail_fired = True
        guard_reason = REJECTION_GUARDRAIL
        log.debug(
            "Guard artist-lock: AI proposed %r but current tag is %r — flagging confusion",
            ai_artist, current_artist,
        )

    # ------------------------------------------------------------------
    # 2. Strip any feat suffix AI injected into the artist field.
    # Feat lives in the title — never in the artist tag.
    # ------------------------------------------------------------------
    if proposed.artist and re.search(r"\(feat", current_title, re.IGNORECASE):
        proposed.featured_artists = []
        proposed.artist = re.sub(
            r"\s+feat\.?\s+.*$", "", proposed.artist, flags=re.IGNORECASE
        ).strip() or proposed.artist

    # ------------------------------------------------------------------
    # 3. DETERMINISTIC TITLE RECONSTRUCTION
    # Ignore AI title completely. Build from current_title only.
    # proposed.version is the only AI output consulted (version-gap hint).
    # After reconstruction proposed.version is nulled — it has been consumed.
    # ------------------------------------------------------------------
    proposed.title   = _reconstruct_title(current_title, proposed.version, filename)
    proposed.version = None   # consumed; prevents _effective_title double-append

    # ------------------------------------------------------------------
    # 4. LABEL SANITATION
    # Null out empty / placeholder label values.
    # ------------------------------------------------------------------
    _LABEL_PLACEHOLDERS = {"(empty)", "unknown", "n/a", "none", ""}
    if not proposed.label or proposed.label.strip().lower() in _LABEL_PLACEHOLDERS:
        proposed.label = None

    return proposed, guard_reason


# ---------------------------------------------------------------------------
# Parsed-alignment check
# ---------------------------------------------------------------------------

def _check_parsed_alignment(
    proposed: NormalizedMetadata,
    parsed: Dict[str, Any],
    current_tags: Dict[str, str],  # noqa: ARG001  (reserved for future checks)
) -> Optional[str]:
    """
    Return a rejection reason if the AI proposal strongly contradicts the
    deterministic filename parse. Returns None if the outputs are consistent.

    Current checks:
      - Version conflict: parsed detected a version token AND the AI proposed
        a completely different version (neither string is a substring of the other).

    Artist is not checked here — it is hard-locked in _apply_hard_guards
    regardless of alignment.
    """
    parsed_version = str(parsed.get("version", "")).strip().lower()
    if parsed_version and proposed.version:
        ai_version = proposed.version.strip().lower()
        if parsed_version not in ai_version and ai_version not in parsed_version:
            log.debug(
                "Parsed alignment mismatch — filename version %r vs AI version %r",
                parsed_version, ai_version,
            )
            return REJECTION_PARSED_CONFLICT
    return None


# ---------------------------------------------------------------------------
# Build effective apply-time tag values from a NormalizedMetadata
# ---------------------------------------------------------------------------

def _effective_artist(proposed: NormalizedMetadata) -> Optional[str]:
    """
    Build the final artist string, incorporating featured_artists.
    Convention: "Primary Artist feat. FA1, FA2"
    """
    if not proposed.artist:
        return None
    artist = proposed.artist
    if proposed.featured_artists:
        fa_str = ", ".join(proposed.featured_artists)
        artist = f"{artist} feat. {fa_str}"
    return artist


def _clean_title_parens(title: str) -> str:
    """
    Fix malformed parentheses in a title string.

    Handles:
      ((Original Mix))       → (Original Mix)
      ( (Original Mix) )     → (Original Mix)
    Does not touch correctly-formed single parentheses blocks.
    """
    # Collapse double parens: ((text)) → (text)
    cleaned = re.sub(r"\(\s*\(([^)]+)\)\s*\)", r"(\1)", title)
    # Trim internal whitespace around content: ( text ) → (text)
    cleaned = re.sub(r"\(\s+([^)]+?)\s+\)", r"(\1)", cleaned)
    return re.sub(r"  +", " ", cleaned).strip()


def _collapse_duplicate_versions(title: str) -> str:
    """
    Collapse duplicate version substrings in a title.

    Handles:
      "Track (Original Mix) (Original Mix)" → "Track (Original Mix)"
      "Track ((Original Mix))"              → "Track (Original Mix)"
    """
    # First fix any double-paren wrapping
    title = _clean_title_parens(title)
    # Then deduplicate repeated parenthesised blocks
    seen: set = set()
    def _replace(m: re.Match) -> str:
        block = m.group(0)
        key = block.strip().lower()
        if key in seen:
            return ""
        seen.add(key)
        return block
    deduped = re.sub(r"\([^)]+\)", _replace, title)
    return re.sub(r"  +", " ", deduped).strip()


def _effective_title(proposed: NormalizedMetadata) -> Optional[str]:
    """
    Build the final title string, incorporating version/mix info.
    Convention: "Track Name (Original Mix)"

    Applies a duplicate-version safeguard before returning.
    """
    if not proposed.title:
        return None
    if proposed.version:
        combined = f"{proposed.title} ({proposed.version})"
    else:
        combined = proposed.title
    return _collapse_duplicate_versions(combined)


# ---------------------------------------------------------------------------
# Diff computation
# ---------------------------------------------------------------------------

def compute_diff(
    current_tags: Dict[str, str],
    proposed: NormalizedMetadata,
) -> List[Dict[str, str]]:
    """
    Compare current file tags to proposed metadata.

    Returns a list of change dicts: [{"field": ..., "old": ..., "new": ...}]

    Only includes:
      - Fields where the proposal is non-empty
      - Fields where the new value actually differs from the current value

    Fields checked:
      title        → mutagen easy 'title'  (combined with version if present)
      organization → mutagen easy 'organization'  (label / TPUB)

    Artist is intentionally excluded: artist normalization is owned by the
    artist-intelligence layer.  AI may never propose artist changes.
    """
    changes: List[Dict[str, str]] = []

    effective_title = _effective_title(proposed)
    effective_label = proposed.label

    candidates = [
        ("title", "title",        current_tags.get("title", ""),        effective_title),
        ("label", "organization", current_tags.get("organization", ""), effective_label),
    ]

    for display_name, _tag_key, current_val, proposed_val in candidates:
        if not proposed_val:
            continue
        cur  = (current_val or "").strip()
        prop = proposed_val.strip()
        if prop and prop != cur:
            changes.append({
                "field": display_name,
                "old":   cur,
                "new":   prop,
            })

    return changes


# ---------------------------------------------------------------------------
# Apply step
# ---------------------------------------------------------------------------

def apply_normalized(
    path: Path,
    proposed: NormalizedMetadata,
    changes: List[Dict[str, str]],
    dry_run: bool,
) -> bool:
    """
    Write the proposed changes back to the audio file.

    Only writes fields that appear in the changes list (i.e. fields that
    actually differ from the current value). Returns True on success.
    """
    if not changes:
        return True  # nothing to write

    # Build the fields dict using effective values, keyed by mutagen easy tag name
    field_map: Dict[str, str] = {}

    changed_display_names = {c["field"] for c in changes}

    # Artist is never written here — owned by artist-intelligence layer.

    if "title" in changed_display_names:
        val = _effective_title(proposed)
        if val:
            field_map["title"] = val

    if "label" in changed_display_names and proposed.label:
        field_map["organization"] = proposed.label

    if not field_map:
        return True

    return _apply_tags(path, field_map, dry_run)


# ---------------------------------------------------------------------------
# Terminal preview output
# ---------------------------------------------------------------------------

_SEP_THICK = "=" * 72
_SEP_THIN  = "-" * 72


def _print_file_result(
    path: Path,
    current_tags: Dict[str, str],
    proposed: NormalizedMetadata,
    changes: List[Dict[str, str]],
    applied: bool,
    skipped_reason: Optional[str],
    error: Optional[str],
) -> None:
    """Print a human-readable diff block for one file to stdout."""
    print(_SEP_THICK)
    print(f"File: {path}")
    print(_SEP_THIN)

    # Current tags summary
    def _show(label: str, val: str) -> None:
        display = val.strip() if val and val.strip() else "(empty)"
        print(f"  {label:<12}: {display}")

    print("Current tags:")
    _show("artist",  current_tags.get("artist", ""))
    _show("title",   current_tags.get("title", ""))
    _show("label",   current_tags.get("organization", ""))

    print()
    conf_str = f"{proposed.confidence:.2f}"
    print(f"AI proposal  (confidence: {conf_str}):")
    if proposed.artist:
        eff_artist = _effective_artist(proposed)
        print(f"  {'artist':<12}: {eff_artist}")
    if proposed.title or proposed.version:
        eff_title = _effective_title(proposed)
        print(f"  {'title':<12}: {eff_title}")
    if proposed.label:
        print(f"  {'label':<12}: {proposed.label}")
    if proposed.remixers:
        print(f"  {'remixers':<12}: {', '.join(proposed.remixers)}")
    if proposed.notes:
        print(f"  {'notes':<12}: {proposed.notes}")

    if error:
        print()
        print(f"  ERROR: {error}")
    elif not changes:
        print()
        print("  No changes proposed.")
    else:
        print()
        print("Changes:")
        for ch in changes:
            old_disp = f'"{ch["old"]}"' if ch["old"] else "(empty)"
            new_disp = f'"{ch["new"]}"'
            print(f"  {ch['field']:<12}: {old_disp} → {new_disp}")

    print()
    if error:
        print("Status: ERROR")
    elif skipped_reason:
        print(f"Status: SKIPPED  ({skipped_reason})")
    elif applied:
        print("Status: APPLIED")
    else:
        print("Status: PREVIEW  (pass --apply to write changes)")

    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_ai_normalize(args) -> int:
    """
    Entry point called by the pipeline.py 'ai-normalize' subcommand dispatch.

    Flow:
      1. Setup logging + healthcheck Ollama
      2. Collect audio files from --input
      3. For each file: normalize → diff → preview
      4. If --apply: write high-confidence diffs
      5. If --output-json: write structured results to JSON
      6. Print summary and return exit code
    """
    import logging as _logging
    level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    _logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")

    # Parse arguments
    input_path    = Path(args.input).expanduser().resolve()
    model         = args.model
    ollama_url    = args.ollama_url
    timeout       = args.timeout
    limit         = args.limit
    dry_run       = args.dry_run
    do_apply      = getattr(args, "apply", False)
    min_confidence = args.min_confidence
    output_json   = getattr(args, "output_json", None)

    # --apply and --dry-run are mutually exclusive
    if do_apply and dry_run:
        print("ERROR: --apply and --dry-run are mutually exclusive.", file=sys.stderr)
        return 1

    # Default mode is preview (neither --apply nor --dry-run passed)
    preview_only = not do_apply and not dry_run

    if not input_path.exists():
        print(f"ERROR: --input path does not exist: {input_path}", file=sys.stderr)
        return 1
    if not input_path.is_dir():
        print(f"ERROR: --input must be a directory: {input_path}", file=sys.stderr)
        return 1

    # Build Ollama client
    client = OllamaClient(base_url=ollama_url, model=model, timeout=timeout)

    # Healthcheck
    print(f"Checking Ollama at {ollama_url} ...")
    if not client.healthcheck():
        print(
            f"\nERROR: Cannot reach Ollama at {ollama_url}.\n"
            "  Is Ollama running?  Run: ollama serve\n"
            "  Is the URL correct? Use --ollama-url to override.\n",
            file=sys.stderr,
        )
        return 1

    available = client.list_models()
    if available:
        print(f"Available models: {', '.join(available)}")
    if model not in (available or []):
        # Non-fatal warning — the model name may still work (e.g. with tag variants)
        print(f"WARNING: Model '{model}' not in listed models. Proceeding anyway.")

    # Collect files
    print(f"\nScanning {input_path} ...")
    files = _collect_files(input_path, limit)
    if not files:
        print("No supported audio files found.")
        return 0
    print(f"Found {len(files)} file(s) to process.")

    mode_label = "DRY-RUN" if dry_run else ("APPLY" if do_apply else "PREVIEW")
    print(f"Mode: {mode_label}  |  Model: {model}  |  Min confidence: {min_confidence}\n")

    # Per-file processing
    results = []
    n_applied = n_skipped = n_errors = n_no_change = 0
    # Rejection reason breakdown counters
    rejection_counts: Dict[str, int] = {}

    for path in files:
        result      = _normalize_track(path, client, min_confidence)
        current_tags = result.current_tags
        proposed     = result.proposed
        changes: List[Dict[str, str]] = []
        applied      = False
        write_error: Optional[str] = None

        if result.rejection_reason == REJECTION_AI_ERROR or (
            result.rejection_reason == REJECTION_SCHEMA_INVALID
        ):
            n_errors += 1
            rejection_counts[result.rejection_reason] = (
                rejection_counts.get(result.rejection_reason, 0) + 1
            )
        elif result.rejected:
            n_skipped += 1
            rejection_counts[result.rejection_reason] = (  # type: ignore[index]
                rejection_counts.get(result.rejection_reason, 0) + 1  # type: ignore[arg-type]
            )
        else:
            changes = compute_diff(current_tags, proposed)
            if not changes:
                n_no_change += 1
            elif do_apply:
                ok = apply_normalized(path, proposed, changes, dry_run=False)
                if ok:
                    applied = True
                    n_applied += 1
                    log.info("APPLIED: %s", path.name)
                else:
                    write_error = "tag write failed"
                    n_errors += 1

        _print_file_result(
            path, current_tags, proposed, changes,
            applied, result.rejection_reason, write_error or result.error,
        )

        # Build structured result for JSON output
        results.append({
            "file":             str(path),
            "current_tags":     current_tags,
            "proposed":         proposed.to_dict(),
            "changes":          changes,
            "confidence":       proposed.confidence,
            "applied":          applied,
            "rejection_reason": result.rejection_reason,
            "error":            write_error or result.error,
        })

    # Summary
    print(_SEP_THICK)
    print(f"Summary: {len(files)} file(s) processed")
    print(f"  Changes found  : {sum(1 for r in results if r['changes'])}")
    print(f"  Applied        : {n_applied}")
    if n_skipped:
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(rejection_counts.items())
                              if k not in (REJECTION_AI_ERROR, REJECTION_SCHEMA_INVALID))
        print(f"  Rejected       : {n_skipped}  ({breakdown})")
    else:
        print(f"  Rejected       : {n_skipped}")
    print(f"  No change      : {n_no_change}")
    print(f"  Errors         : {n_errors}")
    if preview_only and any(r["changes"] for r in results):
        changeable = sum(1 for r in results if r["changes"] and not r["rejection_reason"])
        print(f"\n  {changeable} change(s) ready — re-run with --apply to write them.")
    print()

    # Optional JSON output
    if output_json:
        out_path = Path(output_json).expanduser().resolve()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, ensure_ascii=False)
            print(f"Preview saved to: {out_path}")
        except Exception as exc:
            print(f"WARNING: Could not write JSON output to {out_path}: {exc}", file=sys.stderr)

    return 0 if n_errors == 0 else 1
