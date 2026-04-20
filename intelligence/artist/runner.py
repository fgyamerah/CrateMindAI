"""
artist_intelligence/runner.py — CLI entry point for the artist-intelligence
subcommand.

Architecture:
  file → _read_full_tags()       (reused from ai.normalizer)
       → parse_artist_string()   (deterministic split + feat extraction)
       → alias_store.lookup_any() (canonical name resolution)
       → _propose_artist()       (build proposed string, detect no-ops)
       → _compute_diff()         (compare vs current tag)
       → preview / apply / review-queue

Safe by design:
  - Preview is the default — no writes without --apply
  - Never rewrites the title field
  - Never moves "(feat ...)" from the title into the artist field
  - Only writes artist when confidence >= --min-confidence
  - Below-threshold candidates are written to the review queue, never applied
  - Dry-run and --apply are mutually exclusive

Public entry point: run_artist_intelligence(args)  called by pipeline.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from ai.normalizer import _collect_files, _read_full_tags, _apply_tags
from intelligence.artist.artist_schema import ArtistParseResult
from intelligence.artist.artist_parser import parse_artist_string, _try_personal_name_split
from intelligence.artist.artist_normalizer import normalize_artist_string
from intelligence.artist.artist_alias_store import ArtistAliasStore, ArtistReviewQueue
from intelligence.artist.artist_schema import ArtistParseResult, ArtistEntity

log = logging.getLogger(__name__)

_SEP_THICK = "=" * 72
_SEP_THIN  = "-" * 72

# ---------------------------------------------------------------------------
# Change reason constants
# ---------------------------------------------------------------------------

REASON_FEAT_NORMALIZED      = "feat_normalized"
REASON_FEAT_DEDUPED         = "feat_deduped"
REASON_SEPARATOR_NORMALIZED = "separator_normalized"
REASON_ALIAS_RESOLVED       = "alias_resolved"
REASON_ALIAS_NORMALIZED     = "alias_normalized"
REASON_SPACING_FIXED        = "spacing_fixed"
REASON_POLLUTION_REMOVED    = "pollution_removed"
REASON_MULTI_ARTIST_SPLIT   = "multi_artist_split"
REASON_SPLIT_AMBIGUOUS      = "split_ambiguous_skipped"
REASON_SKIPPED_AMBIGUOUS    = "skipped_ambiguous_alias"
REASON_NESTED_SPLIT             = "nested_split"
REASON_CASING_NORMALIZED        = "casing_normalized"
REASON_CASING_SKIPPED_ACRONYM   = "casing_skipped_acronym"
REASON_CASING_NORMALIZED_WORD   = "casing_normalized_word"
REASON_CONFIDENCE_BOOSTED       = "confidence_boosted"
REASON_ALIAS_CANDIDATE          = "alias_candidate_detected"

# Matches non-canonical feat tokens (not "feat." exactly)
_FEAT_NON_CANONICAL_RE = re.compile(r"\b(featuring|ft)\.?\b", re.IGNORECASE)
# Matches any feat-family token regardless of form
_FEAT_ANY_RE            = re.compile(r"\b(feat(?:uring)?|ft)\.?\b", re.IGNORECASE)
# BPM or version-bracket pollution patterns (mirrors artist_normalizer.py)
_BPM_RE                 = re.compile(r"\b\d+\s*bpm\b", re.IGNORECASE)
_VERSION_BRACKET_RE     = re.compile(r"[\[\(](?:Original Mix|Extended Mix|Radio Edit"
                                     r"|Dub Mix|Instrumental|Remix|Rework|VIP Mix"
                                     r"|Club Mix|Short Mix|Edit|Re-Edit)[^\]\)]*[\]\)]",
                                     re.IGNORECASE)


# ---------------------------------------------------------------------------
# Per-track processing
# ---------------------------------------------------------------------------

def _process_track(
    path: Path,
    alias_store: ArtistAliasStore,
) -> Tuple[Dict[str, str], ArtistParseResult]:
    """
    Run the full artist intelligence flow for one track.

    1. Read current tags.
    2. Parse the artist string deterministically.
    3. Resolve canonical names via the alias store — conservative:
       CI-only matches reduce entity confidence to 0.70.

    Returns (current_tags, parse_result).
    """
    current_tags   = _read_full_tags(path)
    current_artist = (current_tags.get("artist") or "").strip()
    current_title  = (current_tags.get("title")  or "").strip()

    if not current_artist:
        return current_tags, ArtistParseResult(
            confidence=0.0,
            notes="no artist tag",
        )

    result = parse_artist_string(current_artist, current_title)

    # Multi-artist heuristic: if the parser returned a single entity with no
    # separators detected AND the alias store doesn't know this as a single
    # artist, try to split on personal-name boundaries.
    if len(result.main_artists) == 1 and not result.featured_artists:
        candidate = result.main_artists[0].normalized
        # Only attempt if the whole string is not a known single-artist
        if not alias_store.lookup_any(candidate):
            split = _try_personal_name_split(candidate)
            if split:
                parts, split_conf = split
                new_entities: List[ArtistEntity] = []
                for part in parts:
                    part_norm = normalize_artist_string(part)
                    part_can  = alias_store.lookup_any(part_norm)
                    new_entities.append(ArtistEntity(
                        raw=part,
                        normalized=part_norm,
                        canonical=part_can or None,
                        confidence=split_conf,
                        source="alias_store:normalized" if part_can else "heuristic_split",
                    ))
                result.main_artists = new_entities
                result.confidence   = split_conf
                note = f"personal-name split: {parts}"
                result.notes = "; ".join(filter(None, [result.notes, note]))
                log.debug("Multi-artist split: %r → %s", candidate, parts)
            elif len(candidate.split()) >= 4:
                # Suspicious length, no confident split found — flag it
                note = REASON_SPLIT_AMBIGUOUS
                result.notes = "; ".join(filter(None, [result.notes, note]))
                log.debug("Split ambiguous — left unchanged: %r", candidate)

    # Alias store resolution — track which lookup method matched
    for entity in result.main_artists:
        if entity.source == "heuristic_split":
            # Already got canonical in the split block; skip second lookup
            continue
        match = alias_store.lookup_with_method(entity.normalized)
        if match:
            canonical, method = match
            entity.canonical = canonical
            entity.source    = f"alias_store:{method}"
            if method == "ci":
                # CI-only match: weaker evidence — lower confidence
                entity.confidence = min(entity.confidence, 0.70)
                result.confidence = min(result.confidence, 0.70)
                note = f"ci-only alias match for {entity.normalized!r} — flagged as ambiguous"
                result.notes = "; ".join(filter(None, [result.notes, note]))

    return current_tags, result


def _propose_artist(
    current_artist: str,
    result: ArtistParseResult,
) -> Optional[str]:
    """
    Build a proposed artist string from the parse result.

    Returns None when no main artists were parsed, or when the proposed
    string is identical to the current tag (plain string equality).

    Uses direct string comparison — NOT names_are_equivalent() — so that
    feat-token normalization (ft. → feat.) and canonical-form aliases are
    always surfaced as proposed changes, not silently suppressed.

    Featured artists extracted from the artist field are appended with
    "feat." convention.  Title-based feat tokens are never touched here.
    """
    if not result.main_artists:
        return None

    parts = [e.best for e in result.main_artists]

    if result.featured_artists:
        fa_str = ", ".join(result.featured_artists)
        proposed = f"{', '.join(parts)} feat. {fa_str}"
    else:
        proposed = ", ".join(parts)

    # Direct equality — not semantic equivalence — so ft./featuring variants
    # and canonical capitalization differences ARE surfaced as changes.
    if proposed.strip() == current_artist.strip():
        return None

    return proposed


# ---------------------------------------------------------------------------
# Change reason computation
# ---------------------------------------------------------------------------

def _compute_change_reasons(
    current_artist: str,
    proposed_artist: str,
    result: ArtistParseResult,
) -> List[str]:
    """
    Return a list of structured reason tokens explaining why the artist tag
    would change.  Called only when proposed_artist != current_artist.

    Reason tokens (may be combined):
      multi_artist_split   — no-separator concatenated string was split
      pollution_removed    — BPM token or version bracket stripped
      feat_normalized      — non-canonical feat variant (ft./featuring) normalized
      feat_deduped         — duplicate feat token collapsed
      alias_resolved       — alias store matched a structurally different canonical
      alias_normalized     — alias store matched a case-only variant
      separator_normalized — separator symbol or style changed (& → comma, etc.)
      spacing_fixed        — only whitespace changed

    CI-only alias matches never appear in this list — they cause a skip
    (REASON_SKIPPED_AMBIGUOUS) handled separately in the main loop.
    """
    reasons: List[str] = []

    # 1. Multi-artist heuristic split was performed
    if any(e.source == "heuristic_split" for e in result.main_artists):
        reasons.append(REASON_MULTI_ARTIST_SPLIT)

    # 1b. Nested separator split (comma + nested &)
    if result.notes and "split on 'nested'" in result.notes:
        reasons.append(REASON_NESTED_SPLIT)

    # 2. Pollution removed: BPM or version-bracket tokens present in original
    if _BPM_RE.search(current_artist) or _VERSION_BRACKET_RE.search(current_artist):
        reasons.append(REASON_POLLUTION_REMOVED)

    # 3. feat deduplication: original had 2+ feat tokens, proposed has fewer
    feat_before = len(re.findall(r"\bfeat", current_artist, re.IGNORECASE))
    feat_after  = len(re.findall(r"\bfeat", proposed_artist, re.IGNORECASE))
    if feat_before > 1 and feat_after < feat_before:
        reasons.append(REASON_FEAT_DEDUPED)

    # 4. feat variant normalized: current contained non-canonical feat token
    if _FEAT_NON_CANONICAL_RE.search(current_artist):
        reasons.append(REASON_FEAT_NORMALIZED)

    # 4b. Casing normalization: current was all-caps, proposed is title-case
    #     Detected when current has no lowercase but proposed does
    cur_letters  = re.sub(r"[^a-zA-Z]", "", current_artist)
    prop_letters = re.sub(r"[^a-zA-Z]", "", proposed_artist)
    if (cur_letters and cur_letters.isupper()
            and prop_letters and not prop_letters.isupper()
            and cur_letters.upper() == prop_letters.upper()):
        reasons.append(REASON_CASING_NORMALIZED)

    # 5. Alias resolved or alias_normalized (case-only)
    for entity in result.main_artists:
        if (
            entity.canonical
            and entity.source.startswith("alias_store")
            and not entity.source.endswith(":ci")
        ):
            if entity.canonical == entity.normalized:
                pass   # self-mapping, no visible change
            elif entity.canonical.lower() == entity.normalized.lower():
                # Case is the only difference
                reasons.append(REASON_ALIAS_NORMALIZED)
            else:
                # Structural alias (hyphen/space, different spelling, etc.)
                reasons.append(REASON_ALIAS_RESOLVED)
            break

    # 6. Spacing / separator — fallback for changes not yet explained
    if not reasons:
        cur_no_ws  = re.sub(r"\s+", "", current_artist)
        prop_no_ws = re.sub(r"\s+", "", proposed_artist)
        if cur_no_ws == prop_no_ws:
            reasons.append(REASON_SPACING_FIXED)
        else:
            reasons.append(REASON_SEPARATOR_NORMALIZED)

    return reasons


def _compute_diff(
    current_tags: Dict[str, str],
    proposed_artist: Optional[str],
    reasons: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """
    Return a change list in the same format as ai.normalizer.compute_diff.
    Only includes artist — title is never written by this command.
    Carries reasons in each change dict for display and JSON output.
    """
    if not proposed_artist:
        return []
    current = (current_tags.get("artist") or "").strip()
    if proposed_artist.strip() == current:
        return []
    return [{
        "field":   "artist",
        "old":     current,
        "new":     proposed_artist.strip(),
        "reasons": reasons or [],
    }]


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _print_track_result(
    path: Path,
    current_tags: Dict[str, str],
    result: ArtistParseResult,
    changes: List[Dict[str, str]],
    applied: bool,
    skipped_reason: Optional[str],
    review_queued: bool,
) -> None:
    print(_SEP_THICK)
    print(f"File: {path.name}")
    print(_SEP_THIN)

    def _show(label: str, val: str) -> None:
        display = val.strip() if val and val.strip() else "(empty)"
        print(f"  {label:<16}: {display}")

    print("Current tags:")
    _show("artist",   current_tags.get("artist", ""))
    _show("title",    current_tags.get("title",  ""))

    print()
    print(f"Parse result  (confidence: {result.confidence:.2f}):")
    if result.main_artists:
        for e in result.main_artists:
            method = e.source.split(":")[-1] if ":" in e.source else e.source
            src = f"alias:{method}" if e.canonical else "normalized"
            print(f"  main           : {e.best!r}  [{src}]")
    if result.featured_artists:
        print(f"  feat (artist)  : {', '.join(result.featured_artists)}")
    if result.notes:
        print(f"  notes          : {result.notes}")

    if not changes:
        print()
        print("  No changes proposed.")
    else:
        print()
        print("Changes:")
        for ch in changes:
            old_d   = f'"{ch["old"]}"' if ch["old"] else "(empty)"
            reasons = ch.get("reasons") or []
            reason_str = f"  [{', '.join(reasons)}]" if reasons else ""
            print(f"  {ch['field']:<16}: {old_d} → \"{ch['new']}\"{reason_str}")

    print()
    if skipped_reason:
        print(f"Status: SKIPPED        ({skipped_reason})")
    elif review_queued:
        print(f"Status: REVIEW QUEUED  ({REASON_SKIPPED_AMBIGUOUS} — written to review queue)")
    elif applied:
        print("Status: APPLIED")
    else:
        print("Status: PREVIEW        (pass --apply to write changes)")
    print()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_artist_intelligence(args) -> int:
    """
    Entry point called by pipeline.py 'artist-intelligence' dispatch.
    """
    import logging as _logging
    level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    _logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")

    input_path     = Path(args.input).expanduser().resolve()
    limit          = args.limit
    dry_run        = args.dry_run
    do_apply       = getattr(args, "apply", False)
    min_confidence = args.min_confidence
    output_json    = getattr(args, "output_json", None)

    if do_apply and dry_run:
        print("ERROR: --apply and --dry-run are mutually exclusive.", file=sys.stderr)
        return 1

    if not input_path.exists() or not input_path.is_dir():
        print(f"ERROR: --input must be an existing directory: {input_path}", file=sys.stderr)
        return 1

    # Load alias store and review queue from config paths
    alias_store  = ArtistAliasStore(config.ARTIST_ALIAS_STORE)
    review_queue = ArtistReviewQueue(config.ARTIST_REVIEW_QUEUE)

    print(f"Alias store  : {config.ARTIST_ALIAS_STORE}  ({len(alias_store)} canonical artists)")
    print(f"Review queue : {config.ARTIST_REVIEW_QUEUE}")

    # Collect files
    print(f"\nScanning {input_path} ...")
    files = _collect_files(input_path, limit)
    if not files:
        print("No supported audio files found.")
        return 0
    print(f"Found {len(files)} file(s).")

    mode_label = "DRY-RUN" if dry_run else ("APPLY" if do_apply else "PREVIEW")
    print(f"Mode: {mode_label}  |  Min confidence: {min_confidence}\n")

    results          = []
    n_applied        = 0
    n_skipped        = 0
    n_review         = 0
    n_no_change      = 0
    reason_counts: Dict[str, int] = {}

    for path in files:
        current_tags, result = _process_track(path, alias_store)
        current_artist = (current_tags.get("artist") or "").strip()

        proposed_artist = _propose_artist(current_artist, result)

        # Compute structured change reasons before diff so they travel with changes
        change_reasons: List[str] = []
        if proposed_artist:
            change_reasons = _compute_change_reasons(current_artist, proposed_artist, result)

        changes = _compute_diff(current_tags, proposed_artist, change_reasons)

        applied:        bool          = False
        skipped_reason: Optional[str] = None
        review_queued:  bool          = False

        if not changes:
            n_no_change += 1

        elif result.confidence < min_confidence:
            # Determine review reason: ci-only alias match vs general low-confidence
            has_ci_alias = any(
                e.source.endswith(":ci")
                for e in result.main_artists
                if e.source.startswith("alias_store")
            )
            review_note = (
                f"{REASON_SKIPPED_AMBIGUOUS}; confidence={result.confidence:.2f}"
                if has_ci_alias
                else f"confidence {result.confidence:.2f} < {min_confidence}"
            )
            review_queue.add(
                file=str(path),
                raw_artist=current_artist,
                normalized_candidate=proposed_artist or "",
                existing_title=current_tags.get("title", ""),
                confidence=result.confidence,
                notes=review_note,
            )
            review_queued = True
            n_review += 1
            log.info(
                "REVIEW [%s] %s  confidence=%.2f",
                REASON_SKIPPED_AMBIGUOUS if has_ci_alias else "low_confidence",
                path.name, result.confidence,
            )

        elif do_apply and not dry_run:
            ok = _apply_tags(path, {"artist": changes[0]["new"]}, dry_run=False)
            if ok:
                applied = True
                n_applied += 1
                log.info(
                    "APPLIED [%s] %s  %r → %r",
                    ", ".join(change_reasons), path.name,
                    changes[0]["old"], changes[0]["new"],
                )
                for r in change_reasons:
                    reason_counts[r] = reason_counts.get(r, 0) + 1
            else:
                skipped_reason = "tag write failed"
                n_skipped += 1

        _print_track_result(
            path, current_tags, result, changes,
            applied, skipped_reason, review_queued,
        )

        results.append({
            "file":             str(path),
            "current_artist":   current_artist,
            "proposed_artist":  proposed_artist,
            "confidence":       result.confidence,
            "changes":          changes,
            "change_reasons":   change_reasons,
            "applied":          applied,
            "skipped_reason":   skipped_reason,
            "review_queued":    review_queued,
            "notes":            result.notes,
        })

    # Persist review queue if anything was added this run
    if n_review > 0:
        review_queue.save()
        log.debug("Saved review queue (%d entries) to %s", len(review_queue), config.ARTIST_REVIEW_QUEUE)

    # Summary
    print(_SEP_THICK)
    print(f"Summary: {len(files)} file(s) processed")
    print(f"  Changes found  : {sum(1 for r in results if r['changes'])}")
    print(f"  Applied        : {n_applied}")
    if reason_counts:
        breakdown = ", ".join(f"{k}: {v}" for k, v in sorted(reason_counts.items()))
        print(f"    Reasons      : {breakdown}")
    print(f"  Skipped        : {n_skipped}")
    print(f"  Review queued  : {n_review}  (confidence < {min_confidence})")
    print(f"  No change      : {n_no_change}")

    preview_pending = sum(
        1 for r in results
        if r["changes"] and not r["review_queued"] and not r["applied"]
    )
    if not do_apply and preview_pending:
        print(f"\n  {preview_pending} change(s) ready — re-run with --apply to write them.")
    print()

    # Optional structured JSON output
    if output_json:
        out_path = Path(output_json).expanduser().resolve()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, ensure_ascii=False)
            print(f"Preview saved to: {out_path}")
        except Exception as exc:
            print(f"WARNING: Could not write JSON output: {exc}", file=sys.stderr)

    return 0
