"""
intelligence/enrichment/runner.py — Entry point for the metadata-enrich-online
subcommand.

Architecture:
  file → _read_full_tags() + _read_isrc()    (current state from disk)
       → _search_with_fallback()              (Spotify → Deezer → Traxsource)
       → best_match()                         (score candidates, build changes)
       → _print_result()                      (terminal preview)
       → _apply_enrichment()   if --apply    (write tags + ISRC to disk)
       → _log_result()                        (append to JSONL dataset files)

Source priority order:
  1. Spotify ISRC lookup  (most precise — skipped if no ISRC on file)
  2. Spotify artist+title search
  3. Deezer artist+title  (fallback when Spotify conf < _FALLBACK_THRESHOLD)
  4. Traxsource scrape    (dance-music specialist; triggered by genre or low conf)

  Traxsource is the specialist fallback — not the first source.  It is triggered
  when Spotify+Deezer confidence is below the apply threshold OR when the file's
  genre tag signals house/Afro/deep/soulful territory.

  The winning candidate is always the highest-confidence result across all sources
  tried; source_used and sources_tried in the returned EnrichmentMatch reflect this.

Safe by design:
  - Preview is the default — no writes without --apply.
  - Artist is NEVER written.
  - Min confidence default is 0.80 (higher than ai-normalize's 0.75) because
    online APIs return authoritative data but fuzzy-string matching is noisy.
  - ISRC exact matches override the confidence formula (0.98).
  - Existing version/remix info in the title is preserved even when overwriting.
  - Labels are only overwritten by online data when confidence >= 0.95.
  - All results (applied/rejected/skipped) are logged to JSONL.

Dataset files written to data/intelligence/:
  enrichment_queue.jsonl    — one entry per processed file (all outcomes)
  enrichment_accepted.jsonl — changes that were applied
  enrichment_rejected.jsonl — changes that were skipped or below threshold

Public entry point: run_metadata_enrich_online(args) — called by pipeline.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config
from ai.normalizer import _collect_files, _apply_tags

from intelligence.enrichment.enrichment_schema import EnrichmentCandidate, EnrichmentMatch
from intelligence.enrichment.metadata_matcher import best_match
from intelligence.enrichment.spotify_lookup import SpotifyClient, SpotifyError
from intelligence.enrichment.deezer_lookup import DeezerClient
from intelligence.enrichment.traxsource_lookup import TraxsourceClient

log = logging.getLogger(__name__)

_SEP_THICK = "=" * 72
_SEP_THIN  = "-" * 72

# ---------------------------------------------------------------------------
# Junk metadata detection
# ---------------------------------------------------------------------------

_JUNK_ALBUM_RE = re.compile(
    r"https?://"                # explicit URLs
    r"|www\."                   # www. prefix
    r"|\.com\b|\.net\b|\.org\b" # TLD fragments
    r"|traxcrate"               # known piracy watermarks
    r"|0day"
    r"|zippy"
    r"|tukillas"
    r"|maismusicapro"
    r"|\bdownload\b"            # whole-word to avoid "Uptown Downtown"
    r"|blogspot"
    r"|soundcloud\s*rip"
    r"|\bmp3\b",                # whole-word — avoids "Compound3" etc.
    re.IGNORECASE,
)


def is_junk_album(album: str) -> bool:
    """
    Return True when an album string is clearly garbage rather than a real
    release name.

    Rules (any one match is sufficient):
      1. Contains a URL pattern (http/https/www/TLD) or known piracy watermark
         (traxcrate, 0day, zippy, tukillas, maismusicapro, download, mp3,
         blogspot, soundcloud rip).
      2. Contains more than two dots — typical of domain names or file paths
         (e.g. "www.site.com", "Various.Artists.2024").
      3. Contains a forward or backward slash — path fragments.
      4. Mostly uppercase alpha characters (>80 %) AND contains punctuation
         that is typical of automated filename-to-tag conversion
         (e.g. "DJ.POOL.UPLOAD", "ZIPPY-MP3-2024").

    Empty or whitespace-only strings return False (already absent — not junk).
    """
    if not album or not album.strip():
        return False

    s = album.strip()

    # Rule 1: URL / known watermark keywords
    if _JUNK_ALBUM_RE.search(s):
        return True

    # Rule 2: Path/domain dot count
    if s.count(".") > 2:
        return True

    # Rule 3: Slash (forward or backward)
    if "/" in s or "\\" in s:
        return True

    # Rule 4: Mostly uppercase + automated-looking punctuation
    alpha = [c for c in s if c.isalpha()]
    if len(alpha) >= 4:
        upper_ratio = sum(1 for c in alpha if c.isupper()) / len(alpha)
        has_auto_punct = bool(re.search(r"[._\-]{2,}|[._]{1}[A-Z0-9]", s))
        if upper_ratio > 0.80 and has_auto_punct:
            return True

    return False


def _clear_easy_tag(path: Path, key: str) -> bool:
    """
    Delete a single easy-tag key from an audio file and save.

    Uses mutagen easy tags (same interface as _apply_tags).  Returns True on
    success, False if the file could not be opened or saved.  Does not raise.
    """
    try:
        from mutagen import File as MFile
        audio = MFile(str(path), easy=True)
        if audio is None:
            return False
        if key in audio:
            del audio[key]
            audio.save()
        return True
    except Exception as exc:
        log.warning("Could not clear tag %r from %s: %s", key, path.name, exc)
        return False


def _clean_junk_fields(
    path: Path,
    current_tags: Dict[str, str],
    do_write: bool,
    dry_run: bool,
) -> List[Dict[str, str]]:
    """
    Detect and optionally clear junk metadata fields.

    Currently checks: album.
    Modifies *current_tags in place* so the downstream API search uses the
    cleaned values rather than the garbage string.

    Returns a list of cleaned entries in proposed_changes format:
        [{"field": "album", "old": "<junk>", "new": ""}]

    Writes to disk only when do_write=True and dry_run=False.
    """
    cleaned: List[Dict[str, str]] = []

    album = (current_tags.get("album") or "").strip()
    if album and is_junk_album(album):
        log.info("album cleaned (junk detected): %r  file=%s", album, path.name)
        cleaned.append({"field": "album", "old": album, "new": ""})
        current_tags["album"] = ""          # update in-memory view immediately
        if do_write and not dry_run:
            _clear_easy_tag(path, "album")

    return cleaned


# ---------------------------------------------------------------------------
# Tag reading (extends ai.normalizer._read_full_tags with ISRC)
# ---------------------------------------------------------------------------

def _read_full_tags(path: Path) -> Dict[str, str]:
    """Read easy tags from an audio file (mirrors ai/normalizer.py's helper)."""
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
            "organization": get("organization"),
        }
    except Exception as exc:
        log.debug("Could not read tags from %s: %s", path.name, exc)
        return {}


_ISRC_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{3}[0-9]{7}$")


def _validate_isrc(raw: Optional[str]) -> Optional[str]:
    """
    Return the ISRC uppercased if it matches the standard format, else None.

    Valid format: 12 characters — [A-Z]{2}[A-Z0-9]{3}[0-9]{7}
      CC NNN YYNNNNN
      ^^           country code (2 letters)
         ^^^       registrant code (3 alphanumeric)
            ^^^^^^^year + designation number (2+5 digits)

    Examples:
        "GBUM71029604" → "GBUM71029604"   valid
        "TraxCrate.com" → None            invalid (URL junk in tag)
        "gb-UM7-10-29604" → None          invalid (hyphens, wrong length)
        "" / None → None

    The check is intentionally strict: an ISRC that doesn't pass is treated
    as missing rather than used for a lookup that would return wrong results.
    """
    if not raw:
        return None
    normalised = raw.strip().upper()
    if _ISRC_RE.match(normalised):
        return normalised
    log.debug("Ignoring malformed ISRC %r — does not match [A-Z]{2}[A-Z0-9]{3}[0-9]{7}", raw)
    return None


def _read_isrc(path: Path) -> Optional[str]:
    """
    Read and validate the ISRC from an audio file.

    MP3  : ID3 TSRC frame (not exposed by mutagen easy tags)
    FLAC : 'isrc' Vorbis comment (also not in easy tags)
    Other formats: not supported — returns None.

    Returns None if the ISRC is absent, unreadable, invalid, or the format
    is unsupported.  Invalid values (e.g. URL junk written into the TSRC frame
    by some download tools) are discarded via _validate_isrc().
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3
            tags = ID3(str(path))
            tsrc = tags.get("TSRC")
            return _validate_isrc(str(tsrc)) if tsrc else None

        if suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            vals = audio.get("isrc") or audio.get("ISRC")
            return _validate_isrc(vals[0].strip()) if vals else None

    except Exception as exc:
        log.debug("Could not read ISRC from %s: %s", path.name, exc)

    return None


# ---------------------------------------------------------------------------
# Tag writing (ISRC — not available via mutagen easy tags)
# ---------------------------------------------------------------------------

def _write_isrc(path: Path, isrc: str) -> bool:
    """
    Write an ISRC to an audio file.

    MP3  : ID3 TSRC frame (ID3v2.3, consistent with config.ID3_VERSION)
    FLAC : 'isrc' Vorbis comment

    Returns True on success, False on failure. Does not raise.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".mp3":
            from mutagen.id3 import ID3, TSRC
            tags = ID3(str(path))
            tags["TSRC"] = TSRC(encoding=3, text=[isrc])
            tags.save(v2_version=config.ID3_VERSION)
            return True

        if suffix == ".flac":
            from mutagen.flac import FLAC
            audio = FLAC(str(path))
            audio["isrc"] = [isrc]
            audio.save()
            return True

    except Exception as exc:
        log.warning("ISRC write failed for %s: %s", path.name, exc)

    return False


# ---------------------------------------------------------------------------
# Apply changes to disk
# ---------------------------------------------------------------------------

def _apply_enrichment(
    path: Path,
    match: EnrichmentMatch,
    dry_run: bool,
) -> Tuple[bool, Dict[str, str]]:
    """
    Write proposed changes from an EnrichmentMatch to the audio file.

    Returns (success, written_fields).
    ISRC is written via _write_isrc() since it cannot be set through easy tags.
    All other fields use _apply_tags() (mutagen easy mode, same as ai-normalize).
    """
    if not match.proposed_changes:
        return True, {}

    easy_fields: Dict[str, str] = {}
    isrc_to_write: Optional[str] = None

    field_to_easy = {
        "title":  "title",
        "album":  "album",
        "label":  "organization",
    }

    for ch in match.proposed_changes:
        field = ch["field"]
        value = ch["new"]
        if field == "isrc":
            isrc_to_write = value
        elif field in field_to_easy:
            easy_fields[field_to_easy[field]] = value

    written: Dict[str, str] = {}
    success = True

    if easy_fields:
        ok = _apply_tags(path, easy_fields, dry_run)
        if ok:
            written.update(easy_fields)
        else:
            success = False

    if isrc_to_write and not dry_run:
        ok = _write_isrc(path, isrc_to_write)
        if ok:
            written["TSRC"] = isrc_to_write
        else:
            success = False

    return success, written


# ---------------------------------------------------------------------------
# Genre detection — determines whether Traxsource is a useful specialist source
# ---------------------------------------------------------------------------

# Genre terms that suggest dance/house music where Traxsource excels.
# Any substring match (case-insensitive) against the file's genre tag triggers
# Traxsource as an additional lookup source.
_DANCE_GENRE_TERMS: frozenset = frozenset([
    "house", "afro", "deep", "soulful", "tech house", "organic",
    "progressive", "melodic", "tribal", "amapiano", "kwaito",
    "electronic", "edm", "dance", "garage", "minimal", "disco",
    "funky", "jackin", "dub techno", "techno",
])


def _is_dance_genre(genre: str) -> bool:
    """Return True when the genre string suggests house/electronic territory."""
    if not genre:
        return False
    g = genre.lower()
    return any(term in g for term in _DANCE_GENRE_TERMS)


# ---------------------------------------------------------------------------
# Multi-source search with fallback and source-merge
# ---------------------------------------------------------------------------

# Deezer fallback: triggered when Spotify confidence is below this
_FALLBACK_THRESHOLD    = 0.70
# Traxsource fallback: triggered when best-so-far is below the apply threshold
# OR the genre signals dance music
_TRAXSOURCE_THRESHOLD  = 0.80  # mirrors config.ENRICH_ONLINE_MIN_CONFIDENCE default


def _search_with_fallback(
    artist: str,
    title: str,
    current_isrc: Optional[str],
    current_tags: Dict[str, str],
    spotify_client: Optional[SpotifyClient],
    deezer_client: DeezerClient,
    traxsource_client: Optional[TraxsourceClient] = None,
) -> EnrichmentMatch:
    """
    Query sources in priority order and return the highest-confidence match.

    Source priority:
      1. Spotify ISRC  (if current file has an ISRC)
      2. Spotify artist+title
      3. Deezer        (when Spotify confidence < _FALLBACK_THRESHOLD)
      4. Traxsource    (when best-so-far < _TRAXSOURCE_THRESHOLD OR dance genre)

    Source-merge rule: the winning candidate is always the one with the highest
    confidence across all sources attempted.  EnrichmentMatch.source_used and
    EnrichmentMatch.sources_tried document how the result was reached.
    """
    sources_tried: List[str] = []
    genre = (current_tags.get("genre") or "").strip()
    is_dance = _is_dance_genre(genre)

    # ------------------------------------------------------------------
    # 1 + 2. Spotify
    # ------------------------------------------------------------------
    spotify_match: Optional[EnrichmentMatch] = None
    if spotify_client is not None:
        sources_tried.append("spotify")
        candidates: List[EnrichmentCandidate] = []

        if current_isrc:
            log.debug("Spotify ISRC search: %s", current_isrc)
            candidates = spotify_client.search_by_isrc(current_isrc)

        if not candidates:
            candidates = spotify_client.search_by_artist_title(artist, title)

        if candidates:
            spotify_match = best_match(
                candidates, current_tags, current_isrc,
                sources_tried=list(sources_tried),
            )
            log.debug(
                "Spotify best: confidence=%.2f isrc_match=%s reason=%s",
                spotify_match.confidence, spotify_match.isrc_matched, spotify_match.reason,
            )

    # Current best across all sources
    best: Optional[EnrichmentMatch] = spotify_match

    if best and best.confidence >= _FALLBACK_THRESHOLD:
        # Spotify is strong — skip Deezer but still check Traxsource for dance music
        pass
    else:
        # ------------------------------------------------------------------
        # 3. Deezer fallback
        # ------------------------------------------------------------------
        sources_tried.append("deezer")
        deezer_candidates = deezer_client.search_by_artist_title(artist, title)
        if deezer_candidates:
            deezer_match = best_match(
                deezer_candidates, current_tags, current_isrc,
                sources_tried=list(sources_tried),
            )
            log.debug(
                "Deezer best: confidence=%.2f reason=%s",
                deezer_match.confidence, deezer_match.reason,
            )
            if best is None or deezer_match.confidence > best.confidence:
                best = deezer_match

    # ------------------------------------------------------------------
    # 4. Traxsource specialist fallback
    # ------------------------------------------------------------------
    # Triggered when:
    #   a) Best-so-far confidence is below the apply threshold, OR
    #   b) Genre signals house / Afro / deep / soulful territory
    # Traxsource is skipped when an ISRC exact match (0.98) already anchors the result.
    best_conf = best.confidence if best else 0.0
    isrc_locked = best is not None and best.isrc_matched and best.confidence >= 0.95

    should_try_traxsource = (
        traxsource_client is not None
        and not isrc_locked
        and (best_conf < _TRAXSOURCE_THRESHOLD or is_dance)
    )

    if should_try_traxsource:
        sources_tried.append("traxsource")
        log.debug(
            "Trying Traxsource (best_conf=%.2f, is_dance=%s, genre=%r)",
            best_conf, is_dance, genre,
        )
        ts_candidates = traxsource_client.search_by_artist_title(artist, title)
        if ts_candidates:
            ts_match = best_match(
                ts_candidates, current_tags, current_isrc,
                sources_tried=list(sources_tried),
            )
            log.debug(
                "Traxsource best: confidence=%.2f reason=%s",
                ts_match.confidence, ts_match.reason,
            )
            if best is None or ts_match.confidence > best.confidence:
                best = ts_match

    if best is not None:
        # Stamp the full sources_tried list onto the winning match
        best.sources_tried = sources_tried
        return best

    return EnrichmentMatch(
        source_used="none",
        reason="no results found",
        sources_tried=sources_tried,
    )


# ---------------------------------------------------------------------------
# Terminal output
# ---------------------------------------------------------------------------

def _print_result(
    path: Path,
    current_tags: Dict[str, str],
    current_isrc: Optional[str],
    match: EnrichmentMatch,
    applied: bool,
    skipped_reason: Optional[str],
    error: Optional[str],
) -> None:
    """Print a human-readable diff block for one file to stdout."""
    print(_SEP_THICK)
    print(f"File: {path}")
    print(_SEP_THIN)

    def _show(label: str, val: str) -> None:
        print(f"  {label:<14}: {val if val and val.strip() else '(empty)'}")

    print("Current tags:")
    _show("artist",  current_tags.get("artist", ""))
    _show("title",   current_tags.get("title", ""))
    _show("album",   current_tags.get("album", ""))
    _show("label",   current_tags.get("organization", ""))
    _show("isrc",    current_isrc or "")

    print()
    if match.candidate:
        cand = match.candidate
        sources_str = " → ".join(match.sources_tried) if match.sources_tried else match.source_used
        print(
            f"Best match [{match.source_used.upper()}]"
            f"  confidence={match.confidence:.2f}"
            + (" [ISRC EXACT]" if match.isrc_matched else "")
        )
        print(f"  Sources tried : {sources_str}")
        _show("artist",  cand.artist  or "")
        _show("title",   cand.title   or "")
        _show("album",   cand.album   or "")
        _show("label",   cand.label   or "")
        _show("genre",   cand.genre   or "")
        _show("isrc",    cand.isrc    or "")
        _show("released",cand.release_date or "")
        print(f"  Match reason  : {match.reason}")
    else:
        print("  No match found.")

    if error:
        print(f"\n  ERROR: {error}")
    elif not match.proposed_changes:
        print("\n  No changes proposed.")
    else:
        print("\nChanges:")
        for ch in match.proposed_changes:
            old_d = f'"{ch["old"]}"' if ch["old"] else "(empty)"
            print(f"  {ch['field']:<14}: {old_d} → \"{ch['new']}\"")

    print()
    if error:
        print("Status: ERROR")
    elif skipped_reason:
        print(f"Status: SKIPPED  ({skipped_reason})")
    elif applied:
        print("Status: APPLIED")
    elif not match.proposed_changes:
        print("Status: NO CHANGE")
    else:
        print("Status: PREVIEW  (pass --apply to write changes)")
    print()


# ---------------------------------------------------------------------------
# JSONL dataset logging
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        log.warning("enrichment dataset write failed (%s): %s", path.name, exc)


def _log_result(
    path: Path,
    current_tags: Dict[str, str],
    current_isrc: Optional[str],
    match: EnrichmentMatch,
    applied: bool,
    written_fields: Dict[str, str],
    skipped_reason: Optional[str],
    error: Optional[str],
) -> None:
    """Append one record to each relevant dataset JSONL file."""
    now = _now_iso()

    if applied:
        status = "applied"
    elif error:
        status = "error"
    elif skipped_reason:
        status = "skipped"
    elif not match.proposed_changes:
        status = "no_change"
    else:
        status = "preview"

    queue_record = {
        "id":               str(uuid.uuid4()),
        "file_path":        str(path),
        "filename":         path.name,
        "task":             "metadata-enrich-online",
        "current_tags":     current_tags,
        "current_isrc":     current_isrc,
        "source_used":      match.source_used,
        "sources_tried":    match.sources_tried,
        "candidate":        match.candidate.to_dict() if match.candidate else None,
        "confidence":       match.confidence,
        "isrc_matched":     match.isrc_matched,
        "proposed_changes": match.proposed_changes,
        "match_reason":     match.reason,
        "status":           status,
        "created_at":       now,
    }
    _append_jsonl(config.AI_ENRICH_QUEUE, queue_record)

    if applied:
        _append_jsonl(config.AI_ENRICH_ACCEPTED, {
            "input":          {"filename": path.name, "current_tags": current_tags,
                               "current_isrc": current_isrc},
            "candidate":      match.candidate.to_dict() if match.candidate else None,
            "source_used":    match.source_used,
            "confidence":     match.confidence,
            "isrc_matched":   match.isrc_matched,
            "written_fields": written_fields,
            "reason_codes":   ["auto_applied", "isrc_match" if match.isrc_matched
                               else "similarity_match"],
            "logged_at":      now,
        })

    elif status in ("skipped", "error"):
        reason_codes = []
        if error:
            reason_codes.append("error")
        elif skipped_reason and "confidence" in skipped_reason:
            reason_codes.append("low_confidence")
        elif skipped_reason:
            reason_codes.append("skipped")

        _append_jsonl(config.AI_ENRICH_REJECTED, {
            "input":          {"filename": path.name, "current_tags": current_tags,
                               "current_isrc": current_isrc},
            "candidate":      match.candidate.to_dict() if match.candidate else None,
            "source_used":    match.source_used,
            "confidence":     match.confidence,
            "decision":       status,
            "reason_codes":   reason_codes,
            "skipped_reason": skipped_reason,
            "error":          error,
            "logged_at":      now,
        })


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_metadata_enrich_online(args) -> int:
    """
    Entry point called by pipeline.py 'metadata-enrich-online' dispatch.

    Flow:
      1. Setup logging, validate inputs
      2. Build Spotify + Deezer clients
      3. Collect audio files from --input
      4. For each file: read tags → search → score → preview
      5. If --apply: write high-confidence changes
      6. Log all outcomes to JSONL
      7. Print summary
    """
    import logging as _logging
    level = _logging.DEBUG if getattr(args, "verbose", False) else _logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    _logging.basicConfig(level=level, format=fmt, datefmt="%H:%M:%S")

    input_path      = Path(args.input).expanduser().resolve()
    limit           = args.limit
    dry_run         = args.dry_run
    do_apply        = getattr(args, "apply", False)
    min_confidence  = args.min_confidence
    output_json     = getattr(args, "output_json", None)
    clean_junk_only = getattr(args, "clean_junk_only", False)

    # Resolve credentials: CLI flags take priority over config (which reads env vars)
    spotify_id     = getattr(args, "spotify_client_id",     None) or config.SPOTIFY_CLIENT_ID
    spotify_secret = getattr(args, "spotify_client_secret", None) or config.SPOTIFY_CLIENT_SECRET

    if do_apply and dry_run:
        print("ERROR: --apply and --dry-run are mutually exclusive.", file=sys.stderr)
        return 1

    preview_only = not do_apply and not dry_run

    if not input_path.exists() or not input_path.is_dir():
        print(f"ERROR: --input must be an existing directory: {input_path}", file=sys.stderr)
        return 1

    # --- Build clients ---
    spotify_client: Optional[SpotifyClient] = None
    if spotify_id and spotify_secret:
        try:
            spotify_client = SpotifyClient(spotify_id, spotify_secret)
            print(f"Spotify: credentials loaded (client_id={spotify_id[:8]}…)")
        except Exception as exc:
            print(f"WARNING: Spotify client init failed: {exc}", file=sys.stderr)
            print("  Falling back to Deezer only.")
    else:
        print(
            "WARNING: Spotify credentials not set.\n"
            "  Set SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET env vars,\n"
            "  or pass --spotify-client-id / --spotify-client-secret.\n"
            "  Falling back to Deezer only."
        )

    deezer_client = DeezerClient()
    print("Deezer: ready (no credentials required)")

    traxsource_client: Optional[TraxsourceClient] = None
    if getattr(args, "enable_traxsource", False):
        traxsource_client = TraxsourceClient()
        print("Traxsource: enabled (dance-music specialist fallback, scraper-backed)")
    else:
        print("Traxsource: disabled (pass --enable-traxsource to activate)")

    # --- Collect files ---
    print(f"\nScanning {input_path} ...")
    files = _collect_files(input_path, limit)
    if not files:
        print("No supported audio files found.")
        return 0
    print(f"Found {len(files)} file(s) to process.")

    if clean_junk_only:
        mode_label = "JUNK-CLEAN-ONLY (DRY-RUN)" if not do_apply else "JUNK-CLEAN-ONLY (APPLY)"
    else:
        mode_label = "DRY-RUN" if dry_run else ("APPLY" if do_apply else "PREVIEW")
    print(f"Mode: {mode_label}  |  Min confidence: {min_confidence}\n")

    # --- Per-file loop ---
    results         = []
    n_applied       = 0
    n_skipped       = 0
    n_errors        = 0
    n_no_change     = 0
    n_no_result     = 0
    n_junk_cleaned  = 0   # fields cleared by the junk cleaner (across all files)

    for path in files:
        current_tags = _read_full_tags(path)
        current_isrc = _read_isrc(path)

        # ------------------------------------------------------------------
        # Step 1: Junk metadata cleanup
        # Runs unconditionally — before any API call, even when no match is
        # found or the file would otherwise be NO CHANGE.
        # Modifies current_tags in place so the API search below sees clean values.
        # ------------------------------------------------------------------
        junk_changes = _clean_junk_fields(
            path, current_tags,
            do_write=(do_apply or clean_junk_only),
            dry_run=dry_run,
        )
        n_junk_cleaned += len(junk_changes)

        # ------------------------------------------------------------------
        # Step 2: --clean-junk-only mode — skip API entirely
        # ------------------------------------------------------------------
        if clean_junk_only:
            print(_SEP_THICK)
            print(f"File: {path}")
            if junk_changes:
                print("Junk cleaned:")
                for ch in junk_changes:
                    written = "WRITTEN" if (do_apply and not dry_run) else "PREVIEW"
                    print(f"  {ch['field']:<14}: {ch['old']!r} → \"\"  [{written}]")
            else:
                print("  No junk detected.")
            print()
            continue

        # ------------------------------------------------------------------
        # Step 3: API enrichment search
        # ------------------------------------------------------------------
        artist = (current_tags.get("artist") or "").strip()
        title  = (current_tags.get("title")  or "").strip()

        if not artist and not title and not current_isrc:
            log.debug("Skipping %s — no artist, title, or ISRC to search with", path.name)
            match = EnrichmentMatch(source_used="none", reason="no searchable tags")
            n_no_result += 1
        else:
            match = _search_with_fallback(
                artist, title, current_isrc, current_tags,
                spotify_client, deezer_client, traxsource_client,
            )

        applied        = False
        written_fields: Dict[str, str] = {}
        skipped_reason: Optional[str]  = None
        error:          Optional[str]  = None

        if match.source_used == "none":
            n_no_result += 1 if artist or title else 0

        elif not match.proposed_changes:
            n_no_change += 1

        elif do_apply:
            if match.confidence < min_confidence:
                skipped_reason = (
                    f"confidence {match.confidence:.2f} < {min_confidence} threshold"
                )
                n_skipped += 1
            else:
                ok, written_fields = _apply_enrichment(path, match, dry_run=False)
                if ok:
                    applied = True
                    n_applied += 1
                    log.info("APPLIED: %s  source=%s  conf=%.2f",
                             path.name, match.source_used, match.confidence)
                else:
                    error = "tag write failed"
                    n_errors += 1

        else:
            # Preview / dry-run
            if match.confidence < min_confidence and match.proposed_changes:
                skipped_reason = (
                    f"confidence {match.confidence:.2f} < {min_confidence} threshold"
                )
                n_skipped += 1

        _print_result(
            path, current_tags, current_isrc, match,
            applied, skipped_reason, error,
        )

        # Dataset logging (only in apply / dry-run modes, not bare preview)
        if not preview_only:
            _log_result(
                path, current_tags, current_isrc, match,
                applied, written_fields, skipped_reason, error,
            )

        results.append({
            "file":           str(path),
            "current_tags":   current_tags,
            "current_isrc":   current_isrc,
            "match":          match.to_dict(),
            "applied":        applied,
            "written_fields": written_fields,
            "skipped_reason": skipped_reason,
            "error":          error,
            "junk_changes":   junk_changes,
        })

    # --- Summary ---
    print(_SEP_THICK)
    print(f"Summary: {len(files)} file(s) processed")
    print(f"  Junk fields cleared : {n_junk_cleaned}")
    if not clean_junk_only:
        print(f"  Changes found   : {sum(1 for r in results if r['match']['proposed_changes'])}")
        print(f"  Applied         : {n_applied}")
        print(f"  Skipped         : {n_skipped}  (confidence below threshold)")
        print(f"  No change       : {n_no_change}")
        print(f"  No API result   : {n_no_result}")
        print(f"  Errors          : {n_errors}")
    if preview_only and any(r["match"]["proposed_changes"] for r in results):
        changeable = sum(
            1 for r in results
            if r["match"]["proposed_changes"] and not r["error"]
        )
        print(f"\n  {changeable} change(s) ready — re-run with --apply to write them.")
    print()

    # --- Optional JSON output ---
    if output_json:
        out_path = Path(output_json).expanduser().resolve()
        try:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=2, ensure_ascii=False)
            print(f"Preview saved to: {out_path}")
        except Exception as exc:
            print(f"WARNING: Could not write JSON output: {exc}", file=sys.stderr)

    return 0 if n_errors == 0 else 1
