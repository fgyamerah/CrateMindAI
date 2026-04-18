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
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import config
from ai.normalizer import _collect_files, _read_full_tags, _apply_tags
from artist_intelligence.artist_schema import ArtistParseResult
from artist_intelligence.artist_parser import parse_artist_string
from artist_intelligence.artist_normalizer import normalize_artist_string, names_are_equivalent
from artist_intelligence.artist_alias_store import ArtistAliasStore, ArtistReviewQueue

log = logging.getLogger(__name__)

_SEP_THICK = "=" * 72
_SEP_THIN  = "-" * 72


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
    3. Resolve canonical names via the alias store.

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

    # Alias store resolution: fill in entity.canonical where available
    for entity in result.main_artists:
        canonical = alias_store.lookup_any(entity.normalized)
        if canonical:
            entity.canonical = canonical
            entity.source = "alias_store"

    return current_tags, result


def _propose_artist(
    current_artist: str,
    result: ArtistParseResult,
) -> Optional[str]:
    """
    Build a proposed artist string from the parse result.

    Returns None when:
      - No main artists were parsed.
      - The proposed value is equivalent to the current after normalization
        (no-op suppression).

    Featured artists extracted from the artist field are appended with "feat."
    convention.  Title-based feat tokens are never duplicated here.
    """
    if not result.main_artists:
        return None

    parts = [e.best for e in result.main_artists]

    if result.featured_artists:
        fa_str = ", ".join(result.featured_artists)
        proposed = f"{', '.join(parts)} feat. {fa_str}"
    else:
        proposed = ", ".join(parts)

    if names_are_equivalent(proposed, current_artist):
        return None

    return proposed


def _compute_diff(
    current_tags: Dict[str, str],
    proposed_artist: Optional[str],
) -> List[Dict[str, str]]:
    """
    Return a change list in the same format as ai.normalizer.compute_diff.
    Only includes artist — title is never written by this command.
    """
    if not proposed_artist:
        return []
    current = (current_tags.get("artist") or "").strip()
    if proposed_artist.strip() == current:
        return []
    return [{"field": "artist", "old": current, "new": proposed_artist.strip()}]


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
            src = "alias" if e.canonical else "normalized"
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
            old_d = f'"{ch["old"]}"' if ch["old"] else "(empty)"
            print(f"  {ch['field']:<16}: {old_d} → \"{ch['new']}\"")

    print()
    if skipped_reason:
        print(f"Status: SKIPPED        ({skipped_reason})")
    elif review_queued:
        print("Status: REVIEW QUEUED  (written to artist_review_queue.json)")
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

    results         = []
    n_applied       = 0
    n_skipped       = 0
    n_review        = 0
    n_no_change     = 0

    for path in files:
        current_tags, result = _process_track(path, alias_store)
        current_artist = (current_tags.get("artist") or "").strip()

        proposed_artist = _propose_artist(current_artist, result)
        changes         = _compute_diff(current_tags, proposed_artist)

        applied:        bool          = False
        skipped_reason: Optional[str] = None
        review_queued:  bool          = False

        if not changes:
            n_no_change += 1

        elif result.confidence < min_confidence:
            # Below threshold — queue for human review, never auto-apply
            review_queue.add(
                file=str(path),
                raw_artist=current_artist,
                normalized_candidate=proposed_artist or "",
                existing_title=current_tags.get("title", ""),
                confidence=result.confidence,
                notes=result.notes or "",
            )
            review_queued = True
            n_review += 1

        elif do_apply and not dry_run:
            ok = _apply_tags(path, {"artist": changes[0]["new"]}, dry_run=False)
            if ok:
                applied = True
                n_applied += 1
                log.info("APPLIED: %s — artist: %r", path.name, changes[0]["new"])
            else:
                skipped_reason = "tag write failed"
                n_skipped += 1

        # Preview / dry-run falls through with no action (just printed below)

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
