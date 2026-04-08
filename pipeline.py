#!/usr/bin/env python3
"""
DJ Toolkit — main pipeline entry point.

Usage:
    python3 pipeline.py [--dry-run] [--skip-beets] [--skip-analysis]
    python3 pipeline.py label-intel [--label-seeds PATH] [--label-output DIR]

Steps (in order):
    1. Init dirs + DB
    2. Collect inbox files
    3. QC check (ffprobe)
    4. Duplicate detection (rmlint)
    5. Organize (beets → fallback Python)
    6. BPM + key analysis (aubio + keyfinder-cli)
    7. Tag writing (mutagen)
    8. Mark tracks OK in DB
    9. Playlist generation (M3U + Rekordbox XML)
   10. Report

All steps are idempotent. Already-processed tracks (TXXX:PROCESSED=1 in tags
and status='ok' in DB) are skipped on subsequent runs.

Label Intelligence subcommand:
    python3 pipeline.py label-intel
        Scrape Beatport/Traxsource for every label in the seeds file and
        export results to JSON, CSV, TXT, and SQLite under the output dir.
        Seeds default: $DJ_MUSIC_ROOT/data/labels/seeds.txt
        Output default: $DJ_MUSIC_ROOT/data/labels/output/
        Cache default:  $DJ_MUSIC_ROOT/.cache/label_intel/
"""
import argparse
import logging
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: make sure the djtoolkit directory is on the path
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent))

import config
import db
from modules import (
    qc, dedupe, organizer, sanitizer, analyzer, tagger, playlists, reporter,
    artist_merge, artist_folder_clean, metadata_clean, tag_normalize,
)
from modules.textlog import log_action, log_run_separator


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    fmt   = "%(asctime)s  %(levelname)-8s  %(name)s — %(message)s"
    datefmt = "%H:%M:%S"
    logging.basicConfig(level=level, format=fmt, datefmt=datefmt)
    # Also write to file
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(config.LOGS_DIR / "pipeline.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    logging.getLogger().addHandler(fh)


log = logging.getLogger("pipeline")


# ---------------------------------------------------------------------------
# Custom library path support
# ---------------------------------------------------------------------------

def _resolve_path(path_arg: str | None) -> Path | None:
    """
    Validate and return the user-supplied --path directory.
    Returns None when no --path was given (fall back to config defaults).
    Exits with an error message when the path does not exist.
    """
    if not path_arg:
        return None
    root = Path(path_arg).expanduser().resolve()
    if not root.exists():
        print(f"ERROR: --path directory does not exist: {root}", file=sys.stderr)
        sys.exit(2)
    if not root.is_dir():
        print(f"ERROR: --path is not a directory: {root}", file=sys.stderr)
        sys.exit(2)
    return root


def _collect_audio_from_dir(root: Path) -> list:
    """Return all audio files under root (recursive, deduplicated)."""
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(root.rglob(f"*{ext}"))
        files.extend(root.rglob(f"*{ext.upper()}"))
    seen: set = set()
    result = []
    for f in sorted(files):
        key = str(f)
        if key not in seen:
            seen.add(key)
            result.append(f)
    return result


def _override_music_root(root: Path) -> None:
    """
    Override every config path that is derived from MUSIC_ROOT.
    Called when --path is passed to the main pipeline run so that all
    modules (organizer, analyzer, tagger, playlists …) use the custom root.
    """
    config.MUSIC_ROOT        = root
    config.INBOX             = root / "inbox"
    config.PROCESSING        = root / "processing"
    config.LIBRARY           = root / "library"
    config.SORTED            = config.LIBRARY / "sorted"
    config.UNSORTED          = config.SORTED  / "_unsorted"
    config.COMPILATIONS      = config.SORTED  / "_compilations"
    config.DUPLICATES        = root / "duplicates"
    config.REJECTED          = root / "rejected"
    config.PLAYLISTS         = root / "playlists"
    config.M3U_DIR           = config.PLAYLISTS / "m3u"
    config.GENRE_M3U_DIR     = config.M3U_DIR   / "Genre"
    config.ENERGY_M3U_DIR    = config.M3U_DIR   / "Energy"
    config.COMBINED_M3U_DIR  = config.M3U_DIR   / "Combined"
    config.KEY_M3U_DIR       = config.M3U_DIR   / "Key"
    config.ROUTE_M3U_DIR     = config.M3U_DIR   / "Route"
    config.XML_DIR           = config.PLAYLISTS / "xml"
    config.LOGS_DIR          = root / "logs"
    config.DB_PATH           = config.LOGS_DIR  / "processed.db"
    config.REPORTS_DIR       = config.LOGS_DIR  / "reports"
    config.BEETS_LOG         = config.LOGS_DIR  / "beets_import.log"
    config.TEXT_LOG_PATH     = config.LOGS_DIR  / "processing_log.txt"
    config.README_PATH       = config.LOGS_DIR  / "README.md"
    config.LABEL_INTEL_SEEDS             = root / "data" / "labels" / "seeds.txt"
    config.LABEL_INTEL_OUTPUT            = root / "data" / "labels" / "output"
    config.LABEL_INTEL_CACHE             = root / ".cache" / "label_intel"
    config.LABEL_CLEAN_OUTPUT            = root / "data" / "labels" / "clean"
    config.METADATA_CLEAN_REPORT_DIR      = config.LOGS_DIR / "metadata_clean"
    config.ARTIST_MERGE_REPORT_DIR        = config.LOGS_DIR / "artist_merge"
    config.ARTIST_FOLDER_CLEAN_REPORT_DIR = config.LOGS_DIR / "artist_folder_clean"
    config.DEDUPE_QUARANTINE_DIR          = config.SORTED / "_duplicates"


def _log_active_path(label: str, path: Path) -> None:
    """Emit a consistent INFO line and textlog entry for the active library path."""
    log.info("Using library path: %s", path)
    log_action(f"{label} — library path: {path}")


# ---------------------------------------------------------------------------
# Directory initialization
# ---------------------------------------------------------------------------
def _init_dirs() -> None:
    for d in [
        config.INBOX,
        config.PROCESSING,
        config.SORTED,
        config.UNSORTED,
        config.COMPILATIONS,
        config.DUPLICATES,
        config.REJECTED,
        config.M3U_DIR,
        config.GENRE_M3U_DIR,
        config.ENERGY_M3U_DIR,
        config.COMBINED_M3U_DIR,
        config.KEY_M3U_DIR,
        config.ROUTE_M3U_DIR,
        config.XML_DIR,
        config.LOGS_DIR,
        config.REPORTS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# File collection
# ---------------------------------------------------------------------------
def _collect_library_for_reanalysis() -> list:
    """Return all audio files in SORTED that are missing BPM or key."""
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(config.SORTED.rglob(f"*{ext}"))
        files.extend(config.SORTED.rglob(f"*{ext.upper()}"))
    seen = set()
    result = []
    for f in sorted(files):
        if str(f) in seen:
            continue
        seen.add(str(f))
        row = db.get_track(str(f))
        # Include if missing BPM, key, or not yet processed
        if row is None or row["bpm"] is None or row["key_camelot"] is None:
            if row is None:
                db.upsert_track(str(f), status="pending")
            result.append(f)
    return result


def _collect_inbox() -> list:
    """Return all audio files in INBOX (recursive). Skip already-processed."""
    files = []
    for ext in config.AUDIO_EXTENSIONS:
        files.extend(config.INBOX.rglob(f"*{ext}"))
        files.extend(config.INBOX.rglob(f"*{ext.upper()}"))
    # Deduplicate (rglob can match same file twice on case-insensitive FS)
    seen = set()
    unique = []
    for f in sorted(files):
        if str(f) not in seen:
            seen.add(str(f))
            unique.append(f)
    return unique


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(dry_run: bool, skip_beets: bool, skip_analysis: bool, verbose: bool,
                 reanalyze: bool = False, custom_path: Path | None = None,
                 skip_cue_suggest: bool = True) -> int:
    """
    Execute the full pipeline.
    Returns exit code: 0 = success, 1 = some files failed, 2 = fatal error.
    """
    t_start = time.monotonic()

    if custom_path is not None:
        _override_music_root(custom_path)

    _setup_logging(verbose)
    _log_active_path("PIPELINE", config.MUSIC_ROOT)
    _init_dirs()
    db.init_db()

    run_id = db.start_run(dry_run)
    log.info("Pipeline start (run_id=%d, dry_run=%s)", run_id, dry_run)
    log_run_separator(f"run_id={run_id}" + (" DRY-RUN" if dry_run else ""))

    # --- Step 1: Collect files ---
    if reanalyze:
        # Re-analyze all tracks in sorted library that are missing BPM or key
        inbox_files = _collect_library_for_reanalysis()
        if not inbox_files:
            log.info("No tracks need re-analysis")
            db.finish_run(run_id, inbox_count=0, processed=0, duration_sec=0.0)
            return 0
        log.info("Re-analysis mode: %d tracks to process", len(inbox_files))
    else:
        inbox_files = _collect_inbox()
        if not inbox_files:
            log.info("Inbox is empty — nothing to process")
            db.finish_run(run_id, inbox_count=0, processed=0, duration_sec=0.0)
            return 0

    log.info("Inbox: %d files found", len(inbox_files))
    db.finish_run(run_id, inbox_count=len(inbox_files))

    # Register all inbox files in DB as 'pending' and log each one
    for f in inbox_files:
        if not db.is_processed(str(f)):
            db.upsert_track(str(f), status="pending")
        log_action(f"PROCESS: {f.name}")

    # --- Step 2: QC ---
    log.info("[1/7] Quality control ...")
    files = qc.run(inbox_files, run_id, dry_run)
    rejected_count = len(inbox_files) - len(files)

    # --- Step 3: Deduplicate ---
    log.info("[2/7] Duplicate detection ...")
    files = dedupe.run(files, run_id, dry_run)
    dupe_count = (len(inbox_files) - rejected_count) - len(files)

    # --- Step 4: Organize ---
    log.info("[3/7] Organizing library ...")
    files = organizer.run(files, run_id, dry_run, use_beets=not skip_beets)

    # --- Label enrichment (optional post-pipeline step) ---
    # Run separately:  python pipeline.py --label-enrich-from-library

    # --- Step 5: Sanitize tags ---
    log.info("[4/7] Sanitizing tags ...")
    files = sanitizer.run(files, run_id, dry_run)

    # --- Step 6: BPM + key analysis ---
    if not skip_analysis:
        log.info("[5/8] BPM + key analysis ...")
        files = analyzer.run(files, run_id, dry_run)
    else:
        log.info("[5/8] Skipping analysis (--skip-analysis)")

    # --- Step 7: Write tags ---
    log.info("[6/8] Writing tags ...")
    files = tagger.run(files, run_id, dry_run)

    # --- Step 8: Mark as OK in DB ---
    processed_count = 0
    error_count     = 0
    for f in files:
        row = db.get_track(str(f))
        if row and row["status"] not in ("rejected", "duplicate", "needs_review"):
            db.mark_status(str(f), "ok")
            processed_count += 1
        elif row and row["status"] == "error":
            error_count += 1

    # --- Step 8b: Cue point suggestion (disabled by default; use --force-cue-suggest) ---
    # MIK-first policy: cue data is owned by Mixed In Key / Rekordbox.
    # The toolkit will not generate or overwrite cues unless explicitly forced.
    if not skip_cue_suggest and not skip_analysis and files:
        log.info("[7/8] Cue point suggestion ...")
        try:
            from modules import cue_suggest as _cue_suggest
            min_conf = getattr(config, "CUE_SUGGEST_MIN_CONFIDENCE", 0.4)
            _analysed, _stored = _cue_suggest.run(
                [Path(f) if not isinstance(f, Path) else f for f in files],
                dry_run  = dry_run,
                min_conf = min_conf,
            )
            log.info("Cue suggest: %d analysed, %d stored", _analysed, _stored)
        except Exception as exc:
            log.warning("Cue suggest step failed (non-fatal): %s", exc)
    else:
        log.info(
            "[7/8] Cue point suggestion skipped "
            "(disabled by default — use --force-cue-suggest to enable)"
        )

    # --- Step 8c: Playlist generation ---
    log.info("[8/8] Generating playlists ...")
    playlists.run(files, run_id, dry_run)

    # --- Step 9: Report ---
    t_end       = time.monotonic()
    duration    = t_end - t_start
    unsorted    = db.get_tracks_by_status("needs_review")

    db.finish_run(
        run_id,
        inbox_count=len(inbox_files),
        processed=processed_count,
        rejected=rejected_count,
        duplicates=dupe_count,
        unsorted=len(unsorted),
        errors=error_count,
        duration_sec=duration,
    )

    log.info("[9/9] Writing report ...")
    report_path = reporter.generate(run_id, duration, dry_run)
    reporter.generate_readme(run_id, duration, dry_run)
    reporter.print_summary(run_id, duration)
    log.info("Report: %s", report_path)
    log_action(f"RUN COMPLETE: run_id={run_id}, processed={processed_count}, errors={error_count}, duration={duration:.1f}s")

    return 0 if error_count == 0 else 1


# ---------------------------------------------------------------------------
# Label Intelligence
# ---------------------------------------------------------------------------
def run_label_intel(args) -> int:
    """Scrape label metadata and export to all formats."""
    _setup_logging(getattr(args, "verbose", False))

    seeds_path  = Path(args.label_seeds)
    output_dir  = Path(args.label_output)
    cache_dir   = Path(args.label_cache)
    sources     = args.label_sources
    delay       = float(args.label_delay)
    skip_enrich = args.label_skip_enrich

    if not seeds_path.exists():
        log.error("Seeds file not found: %s", seeds_path)
        log.error(
            "Create it with one label name per line, for example:\n"
            "  MoBlack Records\n"
            "  Defected Records\n"
            "  Drumcode"
        )
        return 2

    try:
        from label_intel.scraper import scrape_labels
        from label_intel import exporters
    except ImportError as exc:
        log.error("label_intel package not found (%s). "
                  "Ensure label_intel/ is at the project root.", exc)
        return 2

    output_dir.mkdir(parents=True, exist_ok=True)
    log_action("LABEL-INTEL START")
    log.info("Seeds:   %s", seeds_path)
    log.info("Output:  %s", output_dir)
    log.info("Cache:   %s", cache_dir)
    log.info("Sources: %s  |  delay: %.1fs  |  skip_enrich: %s",
             sources, delay, skip_enrich)

    store = scrape_labels(
        seed_path=seeds_path,
        cache_dir=cache_dir,
        source_names=sources,
        delay=delay,
        skip_enrich=skip_enrich,
    )

    records = store.values()
    log.info("Scraped %d label record(s)", len(records))

    exporters.export_json(records,   output_dir / "labels.json")
    exporters.export_csv(records,    output_dir / "labels.csv")
    exporters.export_txt(records,    output_dir / "labels.txt")
    exporters.export_sqlite(records, output_dir / "labels.db")

    log.info("Exported to %s:", output_dir)
    log.info("  labels.json  — full metadata")
    log.info("  labels.csv   — spreadsheet-friendly")
    log.info("  labels.txt   — one name per line  "
             "(copy to known_labels.txt to update parser blocklist)")
    log.info("  labels.db    — SQLite for ad-hoc queries")
    log_action(f"LABEL-INTEL DONE: {len(records)} records → {output_dir}")
    return 0


# ---------------------------------------------------------------------------
# Label Enrichment from Library
# ---------------------------------------------------------------------------
def _collect_library_tracks_for_enrichment() -> list:
    """
    Return [{label, bpm, genre}] for every OK track in the library.

    Reads genre + bpm from the pipeline DB (already stored there after the
    analyze/tag steps) and recovers the record-label name from the audio
    file's 'organization' easy-tag (mutagen → TPUB for ID3, ORGANIZATION
    for Vorbis).  No BPM/key re-analysis is performed.
    """
    from mutagen import File as MFile

    rows   = db.get_all_ok_tracks()
    tracks = []
    for row in rows:
        fpath = row["filepath"]
        try:
            audio = MFile(fpath, easy=True)
            if audio is None:
                continue
            label = (audio.get("organization") or [""])[0].strip()
            if not label:
                continue
        except Exception:
            continue

        tracks.append({
            "label": label,
            "bpm":   row["bpm"],
            "genre": row["genre"] or "",
        })
    return tracks


def run_label_enrichment_from_library(verbose: bool = False) -> int:
    """
    Enrich the label database with real BPM/genre data from the local library.

    Loads labels.json (if it exists), merges in library metadata via
    enrich_store_from_tracks(), then overwrites labels.json / labels.csv /
    labels.db.  Only improves bpm_min/max, genres, subgenres, energy_profile
    and creates new label records for labels not seen before.
    """
    _setup_logging(verbose)

    try:
        from label_intel.enrich_from_library import enrich_store_from_tracks
        from label_intel.store import LabelStore
        from label_intel.models import LabelRecord
        from label_intel import exporters
        from label_intel.utils import normalize_label_name
    except ImportError as exc:
        log.error("label_intel package not found (%s). "
                  "Ensure label_intel/ is at the project root.", exc)
        return 2

    import json as _json
    import dataclasses

    db.init_db()
    output_dir = config.LABEL_INTEL_OUTPUT
    json_path  = output_dir / "labels.json"

    # --- Load existing store ---
    store = LabelStore()
    if json_path.exists():
        raw          = _json.loads(json_path.read_text(encoding="utf-8"))
        valid_fields = {f.name for f in dataclasses.fields(LabelRecord)}
        loaded       = 0
        for item in raw:
            try:
                rec = LabelRecord(**{k: v for k, v in item.items() if k in valid_fields})
                store.records[rec.normalized_name] = rec
                loaded += 1
            except Exception as exc:
                log.debug("Skipped malformed label record: %s", exc)
        log.info("Loaded %d existing label record(s) from %s", loaded, json_path)
    else:
        log.info("No labels.json found — starting with an empty store")

    # --- Collect tracks ---
    tracks = _collect_library_tracks_for_enrichment()
    log.info("Collected %d track(s) with label metadata from library", len(tracks))

    if not tracks:
        log.warning(
            "No labelled tracks found in the library database.\n"
            "Tip: run the full pipeline first so tracks are organised and "
            "their tags are stored (status='ok')."
        )
        return 0

    # --- Snapshot keys for summary counts ---
    before_keys  = set(store.records.keys())
    matched_keys = {
        normalize_label_name(t["label"]) for t in tracks if t.get("label")
    }
    n_will_enrich = len(before_keys & matched_keys)

    log_action("LABEL-ENRICH-LIBRARY START")
    enrich_store_from_tracks(store, tracks)

    after_keys = set(store.records.keys())
    n_new      = len(after_keys - before_keys)
    total      = len(store.records)

    # --- Re-export (TXT intentionally omitted here; use label-intel for a fresh scrape) ---
    output_dir.mkdir(parents=True, exist_ok=True)
    records = store.values()
    exporters.export_json(records,   output_dir / "labels.json")
    exporters.export_csv(records,    output_dir / "labels.csv")
    exporters.export_sqlite(records, output_dir / "labels.db")

    log.info("Label enrichment from library complete:")
    log.info("  %d new label(s) discovered from library", n_new)
    log.info("  %d existing label(s) enriched (bpm / genres / energy)", n_will_enrich)
    log.info("  %d total label(s) in database", total)
    log.info("  Exported to: %s", output_dir)
    log_action(
        f"LABEL-ENRICH-LIBRARY DONE: {n_new} new, {n_will_enrich} enriched → {output_dir}"
    )
    return 0


# ---------------------------------------------------------------------------
# Artist Merge
# ---------------------------------------------------------------------------
def run_artist_merge(args) -> int:
    """
    Scan the sorted library for artist folder variants and merge them.

    Modes:
      --dry-run   scan + report JSON, no file moves (default when neither flag given)
      --apply     apply safe merges; uncertain cases go to report only
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    sorted_root = custom_path if custom_path is not None else config.SORTED
    report_dir  = config.ARTIST_MERGE_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    _log_active_path("ARTIST-MERGE", sorted_root)

    do_apply = getattr(args, "apply", False)

    if do_apply:
        log_action("ARTIST-MERGE APPLY START")
        artist_merge.run_apply(sorted_root, report_dir)
        log_action("ARTIST-MERGE APPLY DONE")
    else:
        log_action("ARTIST-MERGE DRY-RUN START")
        artist_merge.run_dry_run(sorted_root, report_dir)
        log_action("ARTIST-MERGE DRY-RUN DONE")

    return 0


# ---------------------------------------------------------------------------
# Artist Folder Clean
# ---------------------------------------------------------------------------
def run_artist_folder_clean(args) -> int:
    """
    Scan the sorted library for artist folders with bad names (Camelot key
    prefixes, bracket watermarks, URL/domain names, symbol garbage) and fix
    them retroactively.

    Modes:
      --dry-run   scan + report JSON, no file moves (default when neither flag given)
      --apply     apply all recoverable renames/merges; review cases go to report only
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    sorted_root = custom_path if custom_path is not None else config.SORTED
    report_dir  = config.ARTIST_FOLDER_CLEAN_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    _log_active_path("FOLDER-CLEAN", sorted_root)

    do_apply = getattr(args, "apply", False)

    if do_apply:
        log_action("FOLDER-CLEAN APPLY START")
        rc = artist_folder_clean.run_apply(sorted_root, report_dir)
        log_action("FOLDER-CLEAN APPLY DONE")
    else:
        log_action("FOLDER-CLEAN DRY-RUN START")
        rc = artist_folder_clean.run_dry_run(sorted_root, report_dir)
        log_action("FOLDER-CLEAN DRY-RUN DONE")

    return rc


# ---------------------------------------------------------------------------
# Label Clean
# ---------------------------------------------------------------------------
def run_label_clean(args) -> int:
    """
    Detect, normalize, and (optionally) write back label metadata.

    Modes:
      default / --dry-run   scan + report, no file writes
      --write-tags          scan + report + write high-confidence labels
      --review-only         scan + export only unresolved / low-confidence cases
    """
    _setup_logging(getattr(args, "verbose", False))

    try:
        from label_intel.cleaner import (
            scan_tracks, write_label_tag, WRITE_THRESHOLD,
        )
        from label_intel.normalizer import AliasRegistry
        from label_intel import reports as _reports
    except ImportError as exc:
        log.error("label_intel package not found (%s). "
                  "Ensure label_intel/ is at the project root.", exc)
        return 2

    # Provider placeholder warnings
    if getattr(args, "use_discogs", False):
        log.warning("--use-discogs: Discogs provider is not yet implemented (Phase 2) — skipped.")
    if getattr(args, "use_beatport", False):
        log.warning("--use-beatport: Beatport clean provider is not yet implemented (Phase 2) — skipped.")

    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))

    if custom_path is not None:
        _log_active_path("LABEL-CLEAN", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("LABEL-CLEAN", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    threshold   = getattr(args, "confidence_threshold", config.LABEL_CLEAN_THRESHOLD)
    do_write    = getattr(args, "write_tags", False) and not getattr(args, "dry_run", False)
    review_only = getattr(args, "review_only", False)
    output_dir  = config.LABEL_CLEAN_OUTPUT

    log.info("Scanning %d track(s) for label metadata ...", len(paths))
    log.info("Confidence threshold : %.2f   write-back: %s   review-only: %s",
             threshold, do_write, review_only)
    log_action("LABEL-CLEAN START")

    alias_registry = AliasRegistry()
    results = scan_tracks(paths, write_threshold=threshold, alias_registry=alias_registry)

    # --- Write-back ---
    written = 0
    if do_write:
        for r in results:
            if r.writable and r.cleaned_label:
                if write_label_tag(Path(r.filepath), r.cleaned_label):
                    r.action_taken = "written"
                    written += 1
                    log.info("WROTE label %r → %s", r.cleaned_label, Path(r.filepath).name)

    # --- Reports ---
    report_paths = _reports.generate_all(
        results, output_dir, written=written, review_only=review_only,
    )
    _reports.print_summary(results, written)

    log.info("Reports written to: %s", output_dir)
    for label, rpath in report_paths.items():
        log.info("  %-15s %s", label, rpath.name)

    alias_merges = alias_registry.alias_count()
    if alias_merges:
        log.info("Alias merges detected: %d label(s) have multiple spellings", alias_merges)

    log_action(
        f"LABEL-CLEAN DONE: {len(results)} scanned, {written} written, "
        f"{alias_merges} alias merges → {output_dir}"
    )
    return 0


# ---------------------------------------------------------------------------
# Dedupe library
# ---------------------------------------------------------------------------
def run_dedupe(args) -> int:
    """
    Scan the sorted library (or a custom path) for duplicate audio files and
    optionally quarantine them.

    Modes:
      --dry-run   scan + preview groups, no files moved
      (no flag)   scan + quarantine duplicates

    Detection:
      Case A — exact hash match       → safe to quarantine automatically
      Case B — same title, lower quality → quarantine lower-quality copy
      Case C — different versions     → reported only, never removed
    """
    from modules import library_dedupe

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path   = _resolve_path(getattr(args, "path", None))
    quarantine_raw = getattr(args, "quarantine_dir", None)
    quarantine_dir = Path(quarantine_raw) if quarantine_raw else config.DEDUPE_QUARANTINE_DIR

    if custom_path is not None:
        _log_active_path("DEDUPE", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("DEDUPE", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    dry_run = getattr(args, "dry_run", False)

    log.info(
        "Dedupe: %d track(s) to scan  dry_run=%s  quarantine=%s",
        len(paths), dry_run, quarantine_dir,
    )

    scanned, groups, quarantined, bytes_freed = library_dedupe.run(
        paths         = paths,
        dry_run       = dry_run,
        quarantine_dir = quarantine_dir,
    )

    return 0


# ---------------------------------------------------------------------------
# Standalone playlist generation
# ---------------------------------------------------------------------------
def run_playlists(args) -> int:
    """
    Generate all M3U playlists and Rekordbox XML from the current library DB.

    Runs outside the full pipeline — useful after manual library edits, after
    dedupe cleanup, or any time you want to refresh exports without re-processing
    the inbox.

    Modes:
      --dry-run     print what would be written, no files created
      (no flag)     write all playlist files and rekordbox_library.xml

    Subsets (default: all):
      --no-genre    skip Genre/ playlists
      --no-energy   skip Energy/ playlists
      --no-combined skip Combined/ playlists
      --no-key      skip Key/ playlists
      --no-route    skip Route/ playlists
      --no-xml      skip Rekordbox XML generation
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    if custom_path is not None:
        _override_music_root(custom_path)
        _log_active_path("PLAYLISTS", custom_path)

    dry_run = getattr(args, "dry_run", False)

    # Per-category toggles (command-line can disable individual categories)
    if getattr(args, "no_genre", False):
        config.GENERATE_GENRE_PLAYLISTS = False
    if getattr(args, "no_energy", False):
        config.GENERATE_ENERGY_PLAYLISTS = False
    if getattr(args, "no_combined", False):
        config.GENERATE_COMBINED_PLAYLISTS = False
    if getattr(args, "no_key", False):
        config.GENERATE_KEY_PLAYLISTS = False
    if getattr(args, "no_route", False):
        config.GENERATE_ROUTE_PLAYLISTS = False

    # Ensure output directories exist
    for d in [
        config.M3U_DIR, config.GENRE_M3U_DIR, config.ENERGY_M3U_DIR,
        config.COMBINED_M3U_DIR, config.KEY_M3U_DIR, config.ROUTE_M3U_DIR,
        config.XML_DIR,
    ]:
        if not dry_run:
            d.mkdir(parents=True, exist_ok=True)

    log_action(f"PLAYLISTS {'DRY-RUN' if dry_run else 'GENERATE'} START")

    playlists.generate_m3u(dry_run)
    playlists.generate_genre_m3u(dry_run)
    playlists.generate_energy_m3u(dry_run)
    playlists.generate_combined_m3u(dry_run)
    playlists.generate_key_m3u(dry_run)
    playlists.generate_route_m3u(dry_run)

    if not getattr(args, "no_xml", False):
        xml_path = playlists.generate_rekordbox_xml(dry_run)
        if not dry_run:
            log.info("Rekordbox XML: %s", xml_path)

    log_action(f"PLAYLISTS {'DRY-RUN' if dry_run else 'GENERATE'} DONE")
    return 0


# ---------------------------------------------------------------------------
# Cue Suggest
# ---------------------------------------------------------------------------
def run_cue_suggest(args) -> int:
    """
    Analyse tracks for cue points (intro / drop / outro) and store results
    in the database.  Optionally writes .cues.json sidecars per track.

    Modes:
      --dry-run   analyse and print cues, no DB writes or sidecars
      (no flag)   analyse + store in DB
    """
    from modules import cue_suggest

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))
    dry_run     = getattr(args, "dry_run", False)
    min_conf    = getattr(args, "min_confidence", config.CUE_SUGGEST_MIN_CONFIDENCE)

    if custom_path is not None:
        _log_active_path("CUE-SUGGEST", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("CUE-SUGGEST", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    track_filter = getattr(args, "track",         None)
    limit        = getattr(args, "limit",         None)
    fmt_raw      = getattr(args, "export_format", None)
    export_fmts  = None
    if fmt_raw:
        export_fmts = [f.strip().lower() for f in fmt_raw.split(",") if f.strip()]

    # Apply track filter and limit to the candidate list up front so that
    # the logged count and the actual iteration both reflect the restriction.
    if track_filter:
        paths = [
            p for p in paths
            if track_filter.lower() in f"{p.stem} {p.parent.name}".lower()
        ]
    if limit is not None:
        paths = paths[:limit]

    log.info(
        "cue-suggest: %d candidate(s)  dry_run=%s  min_conf=%.2f",
        len(paths), dry_run, min_conf,
    )
    log_action(f"CUE-SUGGEST {'DRY-RUN' if dry_run else 'START'}: {len(paths)} candidates")

    analysed, stored = cue_suggest.run(
        paths,
        dry_run        = dry_run,
        min_conf       = min_conf,
        export_formats = export_fmts,
    )

    log.info("cue-suggest complete: %d analysed, %d cues stored", analysed, stored)
    log_action(f"CUE-SUGGEST DONE: {analysed} analysed, {stored} cues stored")
    return 0


# ---------------------------------------------------------------------------
# Set Builder
# ---------------------------------------------------------------------------
def run_set_builder(args) -> int:
    """
    Build an energy-curve DJ set from the library database and export it as
    an M3U playlist + CSV summary.

    Phases: warmup → build → peak → release → outro
    """
    from modules import set_builder

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    dry_run              = getattr(args, "dry_run",               False)
    vibe                 = getattr(args, "vibe",                  "peak")
    duration             = getattr(args, "duration",              60)
    genre                = getattr(args, "genre",                 None)
    strategy             = getattr(args, "strategy",              "safest")
    structure            = getattr(args, "structure",              "full")
    max_bpm_jump         = getattr(args, "max_bpm_jump",          3.0)
    strict_harmonic      = getattr(args, "strict_harmonic",       True)
    artist_repeat_window = getattr(args, "artist_repeat_window",  3)
    name                 = getattr(args, "name",                  None)
    start_e              = getattr(args, "start_energy",          None)
    end_e                = getattr(args, "end_energy",             None)

    log.info(
        "set-builder: vibe=%s  structure=%s  duration=%dmin  genre=%s  strategy=%s  "
        "max_bpm_jump=%s  strict_harmonic=%s  artist_repeat_window=%d  dry_run=%s",
        vibe, structure, duration, genre or "any", strategy,
        max_bpm_jump, strict_harmonic, artist_repeat_window, dry_run,
    )

    count, m3u_path = set_builder.run(
        target_duration_min  = duration,
        genre_filter         = genre,
        vibe                 = vibe,
        start_energy         = start_e,
        end_energy           = end_e,
        strategy             = strategy,
        structure            = structure,
        max_bpm_jump         = max_bpm_jump,
        strict_harmonic      = strict_harmonic,
        artist_repeat_window = artist_repeat_window,
        name                 = name,
        dry_run              = dry_run,
    )

    if count == 0:
        log.warning("set-builder produced no tracks — is your DB populated?")
        return 1

    if not dry_run and m3u_path:
        log.info("Set playlist: %s", m3u_path)

    return 0


# ---------------------------------------------------------------------------
# Harmonic Suggest
# ---------------------------------------------------------------------------
def run_harmonic_suggest(args) -> int:
    """
    Suggest the best next tracks based on harmonic / BPM / energy compatibility.

    Two modes:
      --track PATH   suggest from a specific file already in the library
      --key K --bpm B  suggest from a virtual track (key + BPM only)
    """
    from modules import harmonic

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    track_path = getattr(args, "track",    None)
    key        = getattr(args, "key",      None)
    bpm        = getattr(args, "bpm",      None)
    strategy   = getattr(args, "strategy", "safest")
    top_n      = getattr(args, "top_n",    10)
    energy     = getattr(args, "energy",   None)
    genre      = getattr(args, "genre",    None)
    json_out   = getattr(args, "json",     False)
    dry_run    = getattr(args, "dry_run",  False)

    if not track_path and not (key and bpm):
        log.error(
            "harmonic-suggest: provide either --track PATH or both --key and --bpm."
        )
        return 2

    if track_path:
        # Track-based mode: look up the source track's metadata from the DB
        # so we can pass from_title / from_key / from_bpm to the table formatter.
        # (track lookup not yet implemented — placeholder values used until then)
        log.info("harmonic-suggest: from track %s  strategy=%s  top_n=%d",
                 Path(track_path).name, strategy, top_n)
        results = harmonic.suggest_next(
            from_filepath    = track_path,
            strategy         = strategy,
            top_n            = top_n,
        )
        from_title = Path(track_path).stem
        from_key   = key   or ""
        from_bpm   = float(bpm) if bpm else 0.0
    else:
        log.info("harmonic-suggest: key=%s  bpm=%s  strategy=%s  top_n=%d",
                 key, bpm, strategy, top_n)
        results = harmonic.suggest_by_key_bpm(
            key      = key,
            bpm      = float(bpm),
            energy   = energy,
            genre    = genre,
            strategy = strategy,
            top_n    = top_n,
        )
        from_title = "Manual Input"
        from_key   = key
        from_bpm   = float(bpm)

    if not results:
        log.warning("harmonic-suggest: no results — is your DB populated?")
        return 0

    print(harmonic.format_suggestions_table(
        results, strategy=strategy,
        from_title=from_title, from_key=from_key, from_bpm=from_bpm,
    ))

    if json_out and not dry_run:
        out_path = harmonic.write_suggestions_json(
            results,
            strategy    = strategy,
            from_path   = track_path or f"key={key} bpm={bpm}",
        )
        log.info("JSON output: %s", out_path)

    log_action(
        f"HARMONIC-SUGGEST DONE: {len(results)} suggestions  strategy={strategy}"
    )
    return 0


# ---------------------------------------------------------------------------
# Analyze Missing
# ---------------------------------------------------------------------------
def run_analyze_missing(args) -> int:
    """
    Scan the library for tracks missing BPM or Camelot key, run analysis on
    those tracks only, and write results back to the DB and audio file tags.
    """
    from modules import analyze_missing

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    raw_path = getattr(args, "path", None)
    path = _resolve_path(raw_path) if raw_path else None

    if path:
        _log_active_path("analyze-missing scope", path)

    raw_corrupt = getattr(args, "corrupt_dir", None)
    corrupt_base_dir = _resolve_path(raw_corrupt) if raw_corrupt else None

    return analyze_missing.run(
        path             = path,
        dry_run          = getattr(args, "dry_run",           False),
        limit            = getattr(args, "limit",             None),
        timeout_sec      = getattr(args, "timeout_sec",       None),
        min_confidence   = getattr(args, "min_confidence",    0.0),
        verbose          = getattr(args, "verbose",           False),
        per_file_timeout = getattr(args, "file_timeout_sec",  10.0),
        isolate_corrupt  = getattr(args, "isolate_corrupt",   True),
        corrupt_base_dir = corrupt_base_dir,
    )


# ---------------------------------------------------------------------------
# Rekordbox Export
# ---------------------------------------------------------------------------
def run_rekordbox_export(args) -> int:
    """
    Export the full library as a Rekordbox-ready package for Windows (M: drive).

    Outputs:
      _REKORDBOX_XML_EXPORT/rekordbox_library.xml  — Rekordbox-importable XML
      _REKORDBOX_XML_EXPORT/export_report.txt       — tag validation warnings
      _PLAYLISTS_M3U_EXPORT/Genre/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Energy/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Combined/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Key/*.m3u8
      _PLAYLISTS_M3U_EXPORT/Route/*.m3u8

    Path mapping:
      Linux  (RB_LINUX_ROOT)    /mnt/music_ssd/
      Windows (RB_WINDOWS_DRIVE) M:\\
    """
    from modules import rekordbox_export

    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    dry_run              = getattr(args, "dry_run",                 False)
    skip_m3u             = getattr(args, "no_m3u",                  False)
    recover_missing      = getattr(args, "recover_missing_analysis", False)

    # MIK-first: Rekordbox XML is owned by Rekordbox + Mixed In Key.
    # XML export is DISABLED by default to prevent accidental data loss.
    # Use --force-xml to override.
    force_xml = getattr(args, "force_xml", False)
    skip_xml  = not force_xml
    if not skip_xml:
        log.warning(
            "WARNING: Rekordbox XML is managed by Rekordbox + Mixed In Key. "
            "Toolkit export is disabled by default to prevent data loss. "
            "Proceeding because --force-xml was explicitly requested."
        )
    recover_limit        = getattr(args, "recover_limit",            None)
    recover_timeout_sec  = getattr(args, "recover_timeout_sec",      None)

    # Allow per-run overrides of drive letter, Linux root, and export root
    win_drive   = getattr(args, "win_drive",   None)
    linux_root  = getattr(args, "linux_root",  None)
    export_root = getattr(args, "export_root", None)
    if win_drive:
        config.RB_WINDOWS_DRIVE = win_drive.rstrip(":\\")
    if linux_root:
        from pathlib import Path as _Path
        config.RB_LINUX_ROOT = _Path(linux_root)
    if export_root:
        from pathlib import Path as _Path
        _root = _Path(export_root)
        config.REKORDBOX_XML_EXPORT_DIR = _root / "_REKORDBOX_XML_EXPORT"
        config.REKORDBOX_M3U_EXPORT_DIR = _root / "_PLAYLISTS_M3U_EXPORT"

    return rekordbox_export.run(
        dry_run             = dry_run,
        skip_xml            = skip_xml,
        skip_m3u            = skip_m3u,
        recover_missing     = recover_missing,
        recover_limit       = recover_limit,
        recover_timeout_sec = recover_timeout_sec,
    )


# ---------------------------------------------------------------------------
# Metadata Clean
# ---------------------------------------------------------------------------
def run_metadata_clean(args) -> int:
    """
    Scan the sorted library for URL/promo junk in all metadata fields and
    optionally write cleaned values back.

    Modes:
      --dry-run   scan + preview, no file writes (default when neither flag given)
      (no flag)   scan + apply all changes
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    custom_path = _resolve_path(getattr(args, "path", None))

    if custom_path is not None:
        _log_active_path("METADATA-CLEAN", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("METADATA-CLEAN", config.SORTED)
        rows  = db.get_all_ok_tracks()
        paths = [Path(row["filepath"]) for row in rows if Path(row["filepath"]).exists()]

    if not paths:
        if custom_path is not None:
            log.warning("No audio files found in: %s", custom_path)
        else:
            log.warning(
                "No processed tracks found in the library database.\n"
                "Run the full pipeline first so tracks are organised (status='ok')."
            )
        return 0

    dry_run = getattr(args, "dry_run", False)

    log.info(
        "metadata-clean: scanning %d track(s)  dry_run=%s",
        len(paths), dry_run,
    )
    log_action(f"METADATA-CLEAN {'DRY-RUN' if dry_run else 'APPLY'}: {len(paths)} track(s)")

    report_dir = config.METADATA_CLEAN_REPORT_DIR
    report_dir.mkdir(parents=True, exist_ok=True)

    scanned, changed, fields = metadata_clean.run(paths, dry_run=dry_run)

    if not dry_run:
        log.info(
            "metadata-clean summary: %d scanned / %d files modified / %d fields cleaned",
            scanned, changed, fields,
        )
        _print_metadata_clean_summary(scanned, changed, fields)

    return 0


def _print_metadata_clean_summary(scanned: int, changed: int, fields: int) -> None:
    """Print a brief terminal summary after applying metadata-clean."""
    print(f"\n=== metadata-clean complete ===")
    print(f"  Tracks scanned  : {scanned}")
    print(f"  Files modified  : {changed}")
    print(f"  Fields cleaned  : {fields}")
    if changed:
        print(
            f"\n  Rekordbox note  : re-import your library after cleaning so "
            f"Rekordbox picks up the updated tags."
        )
    print()


# ---------------------------------------------------------------------------
# Tag Normalize
# ---------------------------------------------------------------------------
def run_tag_normalize(args) -> int:
    """
    Scan the sorted library (or a custom path) for MP3 files with ID3v2.4 tags
    or a trailing ID3v1 block, and normalise them to ID3v2.3 / no ID3v1.
    """
    _setup_logging(getattr(args, "verbose", False))

    custom_path = _resolve_path(getattr(args, "path", None))
    if custom_path is not None:
        _log_active_path("TAG-NORMALIZE", custom_path)
        paths = _collect_audio_from_dir(custom_path)
    else:
        _log_active_path("TAG-NORMALIZE", config.SORTED)
        paths = _collect_audio_from_dir(config.SORTED)

    mp3_paths = [p for p in paths if p.suffix.lower() == ".mp3"]

    if not mp3_paths:
        log.warning("tag-normalize: no MP3 files found in scan path")
        return 0

    dry_run = getattr(args, "dry_run", False)
    scanned, normalized, v24, v1 = tag_normalize.run(
        paths=mp3_paths,
        dry_run=dry_run,
        verbose=getattr(args, "verbose", False),
    )

    if not dry_run:
        print(f"\n=== tag-normalize complete ===")
        print(f"  MP3s scanned    : {scanned}")
        print(f"  Normalized      : {normalized}")
        print(f"  v2.4 downgraded : {v24}")
        print(f"  v1 removed      : {v1}")
        if normalized:
            print(
                f"\n  Rekordbox note  : re-import your library after normalizing so "
                f"Rekordbox picks up the updated tag format."
            )
        print()

    return 0


# ---------------------------------------------------------------------------
# DB Prune Stale
# ---------------------------------------------------------------------------
def run_db_prune_stale(args) -> int:
    """
    Mark DB rows as 'stale' when the file no longer exists on the current
    SSD library and cannot be located by filename anywhere under --path.
    Rows are marked, never deleted, so you can always review what was pruned.
    """
    _setup_logging(getattr(args, "verbose", False))
    db.init_db()

    raw_path = getattr(args, "path", None)
    lib_root = _resolve_path(raw_path) if raw_path else Path(config.RB_LINUX_ROOT)

    if lib_root is None or not lib_root.exists():
        log.error("db-prune-stale: path not found: %s", lib_root or raw_path)
        return 1

    dry_run = getattr(args, "dry_run", False)
    mode    = "DRY-RUN" if dry_run else "APPLY"
    log.info("db-prune-stale %s: scanning DB against %s", mode, lib_root)

    checked, pruned = db.prune_stale_tracks(lib_root, dry_run=dry_run)

    print(f"\n=== db-prune-stale {'(DRY-RUN) ' if dry_run else ''}===")
    print(f"  Library root    : {lib_root}")
    print(f"  DB rows checked : {checked}")
    print(f"  Stale rows      : {pruned}"
          + (" (would mark stale)" if dry_run else " (marked status='stale')"))
    if pruned and dry_run:
        print( "  Run without --dry-run to apply.")
    if pruned and not dry_run:
        print( "  These rows are now excluded from rekordbox-export.")
        print( "  They are NOT deleted — query the DB to review them:")
        print( "    SELECT filepath FROM tracks WHERE status='stale';")
    print()

    log_action(
        f"DB-PRUNE-STALE {mode}: {checked} checked, {pruned} marked stale, "
        f"lib_root={lib_root}"
    )
    return 0


# ---------------------------------------------------------------------------
# Convert Audio
# ---------------------------------------------------------------------------
def run_convert_audio(args) -> int:
    """
    Convert .m4a files to .aiff, preserve metadata, archive originals.

    Requires:
      --src   root directory containing .m4a files (scanned recursively)
      --dst   root directory for .aiff output files (relative structure preserved)
      --archive  root directory where original .m4a files are moved after conversion

    On success: original .m4a is moved to --archive (never deleted outright).
    On failure: original is left in place; failed output file is removed.
    """
    from modules import convert_audio

    _setup_logging(getattr(args, "verbose", False))

    src_raw     = getattr(args, "src",     None)
    dst_raw     = getattr(args, "dst",     None)
    archive_raw = getattr(args, "archive", None)

    if not src_raw:
        print("ERROR: --src is required", file=sys.stderr)
        return 2
    if not dst_raw:
        print("ERROR: --dst is required", file=sys.stderr)
        return 2
    if not archive_raw:
        print("ERROR: --archive is required", file=sys.stderr)
        return 2

    src_path     = Path(src_raw).expanduser().resolve()
    dst_path     = Path(dst_raw).expanduser().resolve()
    archive_path = Path(archive_raw).expanduser().resolve()

    if not src_path.is_dir():
        print(f"ERROR: --src does not exist or is not a directory: {src_path}", file=sys.stderr)
        return 2

    # Safety guard: make sure archive is not inside src or dst
    if str(archive_path).startswith(str(src_path) + "/"):
        print("ERROR: --archive must not be inside --src", file=sys.stderr)
        return 2
    if str(archive_path).startswith(str(dst_path) + "/"):
        print("ERROR: --archive must not be inside --dst", file=sys.stderr)
        return 2

    workers   = getattr(args, "workers",              4)
    overwrite = getattr(args, "overwrite",             False)
    tolerance = getattr(args, "verify_tolerance_sec",  1.0)
    dry_run   = getattr(args, "dry_run",               False)
    verbose   = getattr(args, "verbose",               False)
    no_prog   = getattr(args, "no_progress",           False)

    ffmpeg_bin  = getattr(config, "FFMPEG_BIN",  "ffmpeg")
    ffprobe_bin = getattr(config, "FFPROBE_BIN", "ffprobe")

    log.info(
        "convert-audio: src=%s  dst=%s  archive=%s  workers=%d  overwrite=%s  "
        "tolerance=%.2fs  dry_run=%s",
        src_path, dst_path, archive_path, workers, overwrite, tolerance, dry_run,
    )
    log_action(
        f"CONVERT-AUDIO {'DRY-RUN' if dry_run else 'START'}: "
        f"src={src_path}  dst={dst_path}  archive={archive_path}"
    )

    rc = convert_audio.run(
        src           = src_path,
        dst           = dst_path,
        archive       = archive_path,
        workers       = workers,
        overwrite     = overwrite,
        tolerance     = tolerance,
        dry_run       = dry_run,
        verbose       = verbose,
        show_progress = not no_prog,
        ffmpeg_bin    = ffmpeg_bin,
        ffprobe_bin   = ffprobe_bin,
    )

    log_action(f"CONVERT-AUDIO {'DRY-RUN' if dry_run else 'DONE'}: rc={rc}")
    return rc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="DJ Toolkit — automated library preparation pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Artist Folder Clean (retroactive bad-name fix):\n"
            "  python pipeline.py artist-folder-clean --dry-run # scan + report, no moves\n"
            "  python pipeline.py artist-folder-clean --apply   # fix recoverable folders\n\n"
            "Artist Merge:\n"
            "  python pipeline.py artist-merge --dry-run        # scan + report, no moves\n"
            "  python pipeline.py artist-merge --apply          # apply safe merges\n\n"
            "Duplicate Detection and Cleanup:\n"
            "  python pipeline.py dedupe --dry-run              # preview duplicate groups\n"
            "  python pipeline.py dedupe                        # quarantine duplicates\n"
            "  python pipeline.py dedupe --path /mnt/music/     # scan custom directory\n\n"
            "Metadata Clean (global junk removal):\n"
            "  python pipeline.py metadata-clean --dry-run      # preview all field changes\n"
            "  python pipeline.py metadata-clean                # apply changes to library\n\n"
            "Label Clean (local, Phase 1):\n"
            "  python pipeline.py label-clean                   # scan + report, no writes\n"
            "  python pipeline.py label-clean --write-tags      # write high-confidence labels\n"
            "  python pipeline.py label-clean --review-only     # export unresolved only\n"
            "  python pipeline.py label-clean --confidence-threshold 0.75  # broader writes\n\n"
            "Label Intelligence (web scrape):\n"
            "  python pipeline.py label-intel\n"
            "  python pipeline.py label-intel --label-seeds /music/data/labels/seeds.txt\n\n"
            "Label Enrichment from Library:\n"
            "  python pipeline.py --label-enrich-from-library\n\n"
            "Playlist Generation and Rekordbox Export:\n"
            "  python pipeline.py playlists --dry-run              # preview all outputs\n"
            "  python pipeline.py playlists                        # write M3U + XML\n"
            "  python pipeline.py playlists --no-xml               # M3U only\n"
            "  python pipeline.py playlists --no-key --no-route    # skip Key/Route\n\n"
            "Cue Point Suggestion (intro / drop / outro detection):\n"
            "  python pipeline.py cue-suggest --dry-run            # analyse, no writes\n"
            "  python pipeline.py cue-suggest                      # analyse + store in DB\n"
            "  python pipeline.py cue-suggest --path /music/inbox/ # custom directory\n\n"
            "Set Builder (energy-curve auto set):\n"
            "  python pipeline.py set-builder --dry-run            # preview set, no files\n"
            "  python pipeline.py set-builder --vibe peak          # build a peak-energy set\n"
            "  python pipeline.py set-builder --duration 90 --vibe warm --genre 'afro house'\n\n"
            "Harmonic Mixing Suggestions:\n"
            "  python pipeline.py harmonic-suggest --track /path/to/track.mp3\n"
            "  python pipeline.py harmonic-suggest --key 8A --bpm 128\n"
            "  python pipeline.py harmonic-suggest --track ... --strategy energy_lift --json\n"
        ),
    )
    # ----- existing pipeline flags -----
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Run all detection/analysis but make no file changes"
    )
    parser.add_argument(
        "--skip-beets", action="store_true",
        help="Skip beets import (use pure-Python organizer only)"
    )
    parser.add_argument(
        "--skip-analysis", action="store_true",
        help=(
            "[legacy] Force-skip all BPM/key analysis, even for tracks missing those values. "
            "Normally not needed — the pipeline is MIK-first and only fills gaps by default."
        ),
    )
    parser.add_argument(
        "--reanalyze", action="store_true",
        help="Re-run BPM+key analysis on sorted library tracks missing those values"
    )
    parser.add_argument(
        "--skip-cue-suggest", action="store_true",
        help=(
            "[deprecated — no-op] Cue suggest is now disabled by default. "
            "Use --force-cue-suggest to enable it."
        ),
    )
    parser.add_argument(
        "--force-cue-suggest", action="store_true",
        help=(
            "Enable cue point suggestion after tag writing. "
            "Disabled by default (MIK-first policy: cues are owned by Mixed In Key). "
            "Only use this if you are not using Mixed In Key."
        ),
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging"
    )
    parser.add_argument(
        "--label-enrich-from-library", action="store_true",
        help=(
            "Enrich the label database using BPM/genre data from your local library. "
            "Reads the label tag (TPUB/organization) from all OK tracks — no re-analysis. "
            "Example: python pipeline.py --label-enrich-from-library"
        ),
    )
    parser.add_argument(
        "--path", metavar="DIR",
        help=(
            "Override the music root directory. Replaces DJ_MUSIC_ROOT / config defaults. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- subcommands -----
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    p_li = subparsers.add_parser(
        "label-intel",
        help="Scrape and export label metadata from Beatport/Traxsource",
    )
    p_li.add_argument(
        "--label-seeds", metavar="FILE",
        default=config.LABEL_INTEL_SEEDS,
        help=f"Seeds file (one label name per line). Default: {config.LABEL_INTEL_SEEDS}",
    )
    p_li.add_argument(
        "--label-output", metavar="DIR",
        default=config.LABEL_INTEL_OUTPUT,
        help=f"Output directory for exported files. Default: {config.LABEL_INTEL_OUTPUT}",
    )
    p_li.add_argument(
        "--label-cache", metavar="DIR",
        default=config.LABEL_INTEL_CACHE,
        help=f"HTTP cache directory. Default: {config.LABEL_INTEL_CACHE}",
    )
    p_li.add_argument(
        "--label-sources", nargs="+", metavar="SOURCE",
        default=config.LABEL_INTEL_SOURCES,
        choices=["beatport", "traxsource"],
        help="Sources to scrape. Default: beatport traxsource",
    )
    p_li.add_argument(
        "--label-delay", type=float, metavar="SECS",
        default=config.LABEL_INTEL_DELAY,
        help=f"Per-host request delay in seconds. Default: {config.LABEL_INTEL_DELAY}",
    )
    p_li.add_argument(
        "--label-skip-enrich", action="store_true",
        help="Skip label page enrichment (faster; search results only)",
    )
    p_li.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- artist-folder-clean subcommand -----
    p_afc = subparsers.add_parser(
        "artist-folder-clean",
        help="Fix bad artist folder names already on disk (Camelot prefixes, bracket junk, etc.)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Retroactively clean up artist folder names that were created before\n"
            "parser/sanitization fixes were in place.\n\n"
            "Detection rules:\n"
            "  pure_camelot    e.g. '10B', '1A'                  → review\n"
            "  camelot_prefix  e.g. '1A - Afrikan Roots'         → rename/merge\n"
            "  bracket_junk    e.g. '[HouseGrooveSA]'            → review\n"
            "  url_junk        e.g. 'djcity.com'                 → review\n"
            "  symbol_heavy    < 40%% alphanumeric chars         → review\n\n"
            "Outcomes:\n"
            "  rename  — cleaned name is valid, target folder does not exist\n"
            "  merge   — cleaned name is valid, target folder already exists\n"
            "  review  — no valid name can be recovered; written to report only\n\n"
            "Examples:\n"
            "  python pipeline.py artist-folder-clean --dry-run\n"
            "  python pipeline.py artist-folder-clean --apply\n"
        ),
    )
    p_afc.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file moves (default behavior)",
    )
    p_afc.add_argument(
        "--apply", action="store_true",
        help=(
            "Apply all recoverable renames and merges. "
            "Unrecoverable folders go to the review report."
        ),
    )
    p_afc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_afc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of the default sorted library. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- label-clean subcommand -----
    p_lc = subparsers.add_parser(
        "label-clean",
        help="Detect, normalize, and optionally write back label metadata (Phase 1: local only)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan all processed tracks for label metadata.\n\n"
            "Detection order:\n"
            "  1. organization/TPUB embedded tag    (confidence 0.95)\n"
            "  2. grouping tag fallback             (confidence 0.75)\n"
            "  3. comment tag fallback              (confidence 0.60)\n"
            "  4. filename pattern parsing          (confidence 0.55-0.70)\n"
            "  5. unresolved                        (confidence 0.00)\n\n"
            "Write-back (--write-tags) only applies when confidence >= threshold (default 0.85).\n"
            "At the default threshold only embedded-tag results are written automatically.\n"
        ),
    )
    p_lc.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file changes (default behavior)",
    )
    p_lc.add_argument(
        "--write-tags", action="store_true",
        help=(
            f"Write high-confidence labels (>= {config.LABEL_CLEAN_THRESHOLD}) "
            "back to the organization/TPUB tag"
        ),
    )
    p_lc.add_argument(
        "--review-only", action="store_true",
        help="Only export the review file (unresolved / low-confidence tracks)",
    )
    p_lc.add_argument(
        "--confidence-threshold", type=float, metavar="FLOAT",
        default=config.LABEL_CLEAN_THRESHOLD,
        help=f"Minimum confidence for write-back. Default: {config.LABEL_CLEAN_THRESHOLD}",
    )
    p_lc.add_argument(
        "--use-discogs", action="store_true",
        help="[Phase 2 — not yet implemented] Match unresolved labels via Discogs API",
    )
    p_lc.add_argument(
        "--use-beatport", action="store_true",
        help="[Phase 2 — not yet implemented] Match unresolved labels via Beatport",
    )
    p_lc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_lc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan audio files in this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- artist-merge subcommand -----
    p_am = subparsers.add_parser(
        "artist-merge",
        help="Merge artist folder variants (capitalisation / feat / collab suffixes)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the sorted library for artist folders that represent the same\n"
            "base artist and merge them into a single canonical folder.\n\n"
            "Safe merges (only case / feat / collaborator differences) are applied\n"
            "automatically with --apply.  Uncertain merges (primary artist differs)\n"
            "are written to the review report only.\n\n"
            "Examples:\n"
            "  python pipeline.py artist-merge --dry-run   # scan + report, no moves\n"
            "  python pipeline.py artist-merge --apply     # apply safe merges\n"
        ),
    )
    p_am.add_argument(
        "--dry-run", action="store_true",
        help="Scan and report only — make no file moves (default behavior)",
    )
    p_am.add_argument(
        "--apply", action="store_true",
        help="Apply safe merges. Uncertain merges go to the review report.",
    )
    p_am.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_am.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of the default sorted library. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- metadata-clean subcommand -----
    p_mc = subparsers.add_parser(
        "metadata-clean",
        help="Remove URL/promo junk from ALL metadata fields across the library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan every processed track for junk metadata and optionally write\n"
            "cleaned values back.\n\n"
            "Fields cleaned:\n"
            "  title, artist, album, albumartist, genre, comment,\n"
            "  label (organization/TPUB), grouping (TIT1), catalog number\n\n"
            "What is removed:\n"
            "  URLs / domains    https://djsoundtop.com, TraxCrate.com, www.djcity.com\n"
            "  DJ pool phrases   fordjonly, djcity, zipdj, musicafresca, promo only\n"
            "  Promo phrases     official audio, free download, downloaded from\n"
            "  Comment noise     Camelot keys (6A), BPM strings (121 BPM),\n"
            "                    combinations like '6A | Gm | 121 BPM'\n\n"
            "Field-specific behaviour:\n"
            "  albumartist     — cleared entirely when the value is a bare URL/domain\n"
            "  catalog number  — cleared entirely when the value is a bare URL/domain\n"
            "  comment         — URL/promo stripped; Camelot + BPM tokens also removed\n\n"
            "Examples:\n"
            "  python pipeline.py metadata-clean --dry-run   # preview, no writes\n"
            "  python pipeline.py metadata-clean             # apply changes\n"
        ),
    )
    p_mc.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be cleaned — make no file changes",
    )
    p_mc.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )
    p_mc.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan audio files in this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )

    # ----- tag-normalize subcommand -----
    p_tn = subparsers.add_parser(
        "tag-normalize",
        help="Standardize MP3 ID3 tags for Rekordbox (ID3v2.4→v2.3, remove ID3v1)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan MP3 files and normalize their ID3 tag format for Rekordbox compatibility.\n\n"
            "What is fixed:\n"
            "  ID3v2.4 → ID3v2.3  — Rekordbox reads v2.3 correctly on all platforms\n"
            "  ID3v1 removed      — 128-byte end-of-file block, never needed\n\n"
            "Log tags emitted per file:\n"
            "  [ID3V24_DOWNGRADED]   — was ID3v2.4, converted to v2.3\n"
            "  [ID3V1_REMOVED]       — ID3v1 block stripped\n"
            "  [ID3V23_NORMALIZED]   — file saved as ID3v2.3\n\n"
            "Non-MP3 files (FLAC, WAV, AIFF, M4A, OGG, OPUS) are always skipped.\n\n"
            "Examples:\n"
            "  python pipeline.py tag-normalize --dry-run\n"
            "  python pipeline.py tag-normalize\n"
            "  python pipeline.py tag-normalize --path /mnt/music_ssd/KKDJ/sorted/\n"
        ),
    )
    p_tn.add_argument(
        "--dry-run", action="store_true",
        help="Detect issues without writing any files",
    )
    p_tn.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )
    p_tn.add_argument(
        "--path", metavar="DIR",
        help="Scan this directory instead of the default sorted library",
    )

    # ----- db-prune-stale subcommand -----
    p_dps = subparsers.add_parser(
        "db-prune-stale",
        help="Mark DB rows stale when the file no longer exists on the current SSD library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the database for rows whose filepath no longer exists on disk\n"
            "and cannot be located by filename anywhere under the library root.\n\n"
            "Stale rows are marked status='stale' — they are NEVER deleted.\n"
            "After pruning, rekordbox-export will no longer warn about them.\n\n"
            "Examples:\n"
            "  python3 pipeline.py db-prune-stale --dry-run\n"
            "  python3 pipeline.py db-prune-stale --path /mnt/music_ssd/KKDJ/\n"
        ),
    )
    p_dps.add_argument(
        "--dry-run", action="store_true",
        help="Report stale rows without marking them",
    )
    p_dps.add_argument(
        "--path", metavar="DIR",
        help=(
            "Library root to search for files "
            "(default: RB_LINUX_ROOT from config, typically /mnt/music_ssd)"
        ),
    )
    p_dps.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging",
    )

    # ----- convert-audio subcommand -----
    p_ca = subparsers.add_parser(
        "convert-audio",
        help="Convert .m4a files to .aiff, preserve metadata, archive originals",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Convert all .m4a files under --src to .aiff under --dst.\n"
            "Relative folder structure is preserved in both --dst and --archive.\n\n"
            "Workflow per file:\n"
            "  1. ffprobe validates source — corrupt files are skipped\n"
            "  2. ffmpeg converts: pcm_s16be AIFF with metadata copied (-map_metadata 0)\n"
            "  3. Output is verified: ffprobe check + duration delta <= --verify-tolerance-sec\n"
            "  4. On success: original .m4a moved to --archive (relative path preserved)\n"
            "  5. On failure: original left untouched; broken output removed\n\n"
            "Output codec: pcm_s16be (16-bit big-endian PCM AIFF).\n"
            "Override ffmpeg/ffprobe paths via FFMPEG_BIN / FFPROBE_BIN env vars\n"
            "or config_local.py.\n\n"
            "Examples:\n"
            "  python3 pipeline.py convert-audio \\\n"
            "      --src /downloads/m4a \\\n"
            "      --dst /mnt/music_ssd/KKDJ/inbox \\\n"
            "      --archive /mnt/music_ssd/originals_m4a\n\n"
            "  python3 pipeline.py convert-audio --src /downloads --dst /music --archive /archive \\\n"
            "      --workers 8 --verify-tolerance-sec 2.0 --dry-run\n"
        ),
    )
    p_ca.add_argument(
        "--src", metavar="PATH", required=True,
        help="Root directory containing .m4a files to convert (scanned recursively)",
    )
    p_ca.add_argument(
        "--dst", metavar="PATH", required=True,
        help="Root directory for output .aiff files (relative folder structure preserved)",
    )
    p_ca.add_argument(
        "--archive", metavar="PATH", required=True,
        help=(
            "Root directory where original .m4a files are moved after successful conversion. "
            "Relative folder structure from --src is preserved. Files are MOVED, never deleted."
        ),
    )
    p_ca.add_argument(
        "--workers", metavar="N", type=int, default=4,
        help="Number of parallel ffmpeg workers (default: 4)",
    )
    p_ca.add_argument(
        "--overwrite", action="store_true",
        help="Re-convert files that already have a .aiff output in --dst",
    )
    p_ca.add_argument(
        "--verify-tolerance-sec", metavar="SECS", type=float, default=1.0,
        dest="verify_tolerance_sec",
        help=(
            "Maximum allowed duration difference (seconds) between source and output. "
            "Conversions outside this tolerance are treated as failures (default: 1.0)"
        ),
    )
    p_ca.add_argument(
        "--dry-run", action="store_true",
        help="Probe sources and show what would be converted — write no files",
    )
    p_ca.add_argument(
        "--no-progress", action="store_true",
        help="Disable the tqdm progress bar even when tqdm is installed",
    )
    p_ca.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- dedupe subcommand -----
    p_dd = subparsers.add_parser(
        "dedupe",
        help="Detect and quarantine duplicate audio files across the library",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the library for duplicate audio files and optionally move\n"
            "them to a quarantine folder (never deleted outright).\n\n"
            "Detection cases:\n"
            "  Case A — Exact duplicate   : same SHA-256 hash\n"
            "                               → keep one, quarantine the rest\n"
            "  Case B — Quality duplicate : same track, different format/bitrate\n"
            "                               → keep best quality, quarantine rest\n"
            "  Case C — Different versions: 'Extended Mix' vs 'Radio Edit' etc.\n"
            "                               → keep all, reported only\n\n"
            "Quality priority (highest first):\n"
            "  WAV / AIFF  >  FLAC  >  MP3 320  >  MP3 256  >  M4A  >\n"
            "  MP3 192  >  OGG / OPUS  >  MP3 128  >  MP3 <128\n\n"
            "Safety rules:\n"
            "  • Files are MOVED, never deleted — always recoverable\n"
            "  • Ambiguous quality ties are skipped (manual review)\n"
            "  • Case C (versions) is never auto-removed\n\n"
            "Examples:\n"
            "  python pipeline.py dedupe --dry-run\n"
            "  python pipeline.py dedupe\n"
            "  python pipeline.py dedupe --path /mnt/music_ssd/KKDJ/\n"
            "  python pipeline.py dedupe --quarantine-dir /music/review/\n"
        ),
    )
    p_dd.add_argument(
        "--dry-run", action="store_true",
        help="Preview duplicate groups — move no files",
    )
    p_dd.add_argument(
        "--path", metavar="DIR",
        help=(
            "Scan this directory instead of pulling from the database. "
            "Example: --path /mnt/music_ssd/KKDJ/"
        ),
    )
    p_dd.add_argument(
        "--quarantine-dir", metavar="DIR",
        default=None,
        help=(
            f"Directory to move duplicate files into. "
            f"Default: {config.DEDUPE_QUARANTINE_DIR}"
        ),
    )
    p_dd.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- playlists subcommand -----
    p_pl = subparsers.add_parser(
        "playlists",
        help="Generate all M3U playlists and Rekordbox XML from the library DB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate all playlist files from the current library database without\n"
            "running the full inbox pipeline.  Useful after manual library edits,\n"
            "after running dedupe, or any time you want a fresh export.\n\n"
            "Output structure:\n"
            "  M3U_DIR/           letter playlists (A.m3u8 … Z.m3u8) + _all_tracks.m3u8\n"
            "  M3U_DIR/Genre/     Afro House.m3u8, Amapiano.m3u8 …\n"
            "  M3U_DIR/Energy/    Peak.m3u8, Mid.m3u8, Chill.m3u8\n"
            "  M3U_DIR/Combined/  Peak Afro House.m3u8, Chill Deep House.m3u8 …\n"
            "  M3U_DIR/Key/       1A.m3u8, 1B.m3u8 … 12A.m3u8, 12B.m3u8\n"
            "  M3U_DIR/Route/     Acapella.m3u8, Tool.m3u8, Vocal.m3u8\n"
            "  XML_DIR/           rekordbox_library.xml\n\n"
            "Examples:\n"
            "  python pipeline.py playlists --dry-run\n"
            "  python pipeline.py playlists\n"
            "  python pipeline.py playlists --no-xml\n"
            "  python pipeline.py playlists --path /mnt/music_ssd/\n"
        ),
    )
    p_pl.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be written — create no files",
    )
    p_pl.add_argument(
        "--no-genre", action="store_true",
        help="Skip Genre/ playlists",
    )
    p_pl.add_argument(
        "--no-energy", action="store_true",
        help="Skip Energy/ playlists",
    )
    p_pl.add_argument(
        "--no-combined", action="store_true",
        help="Skip Combined/ playlists",
    )
    p_pl.add_argument(
        "--no-key", action="store_true",
        help="Skip Key/ (Camelot) playlists",
    )
    p_pl.add_argument(
        "--no-route", action="store_true",
        help="Skip Route/ playlists (Acapella, Tool, Vocal)",
    )
    p_pl.add_argument(
        "--no-xml", action="store_true",
        help="Skip Rekordbox XML export",
    )
    p_pl.add_argument(
        "--path", metavar="DIR",
        help=(
            "Override the music root directory for all output paths. "
            "Example: --path /mnt/music_ssd/"
        ),
    )
    p_pl.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- rekordbox-export subcommand -----
    p_rb = subparsers.add_parser(
        "rekordbox-export",
        help="Export library as Rekordbox-ready M3U playlists for Windows (M: drive)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Generate M3U playlists for Windows with Linux→M: drive path mapping.\n\n"
            "MIK-FIRST POLICY:\n"
            "  Rekordbox XML is owned by Rekordbox + Mixed In Key.\n"
            "  XML export is DISABLED by default to prevent data loss.\n"
            "  Use --force-xml only if you are not using Mixed In Key.\n\n"
            "Default outputs:\n"
            "  _PLAYLISTS_M3U_EXPORT/Genre/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Energy/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Combined/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Key/*.m3u8\n"
            "  _PLAYLISTS_M3U_EXPORT/Route/*.m3u8\n\n"
            "With --force-xml also outputs:\n"
            "  _REKORDBOX_XML_EXPORT/rekordbox_library.xml\n\n"
            "Tracks missing BPM or Camelot key are EXCLUDED (fast, predictable).\n"
            "To recover them inline use --recover-missing-analysis.\n"
            "For large libraries, run analysis separately first:\n"
            "  python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/\n\n"
            "Path mapping (defaults):\n"
            "  Linux root : /mnt/music_ssd   (= root of M: drive on Windows)\n"
            "  Windows    : M:\\\n\n"
            "Override via env vars:  export RB_LINUX_ROOT=/mnt/music_ssd\n"
            "                        export RB_WIN_DRIVE=M\n"
            "Or via config_local.py: RB_LINUX_ROOT = Path('/mnt/music_ssd')\n"
            "                        RB_WINDOWS_DRIVE = 'M'\n\n"
            "Examples:\n"
            "  python3 pipeline.py rekordbox-export --dry-run\n"
            "  python3 pipeline.py rekordbox-export\n"
            "  python3 pipeline.py rekordbox-export --no-m3u\n"
            "  python3 pipeline.py rekordbox-export --force-xml  # NOT recommended with MIK\n"
            "  python3 pipeline.py rekordbox-export --recover-missing-analysis\n"
            "  python3 pipeline.py rekordbox-export --recover-missing-analysis "
            "--recover-limit 50 --recover-timeout-sec 300\n"
        ),
    )
    p_rb.add_argument(
        "--dry-run", action="store_true",
        help="Preview what would be exported — create no files (tag warnings still shown)",
    )
    p_rb.add_argument(
        "--no-xml", action="store_true",
        help="[no-op] Rekordbox XML is now disabled by default. Use --force-xml to enable it.",
    )
    p_rb.add_argument(
        "--force-xml", action="store_true", dest="force_xml",
        help=(
            "Enable Rekordbox XML generation. NOT RECOMMENDED when using Mixed In Key — "
            "the toolkit XML will overwrite MIK cue data on next Rekordbox import. "
            "Use only if you are not using Mixed In Key."
        ),
    )
    p_rb.add_argument(
        "--no-m3u", action="store_true",
        help="Skip M3U playlist generation",
    )
    p_rb.add_argument(
        "--win-drive", metavar="LETTER", default=None,
        help="Windows drive letter (default: M, from RB_WIN_DRIVE env or config)",
    )
    p_rb.add_argument(
        "--linux-root", metavar="PATH", default=None,
        help="Linux path that is the root of the Windows drive (default: /mnt/music_ssd)",
    )
    p_rb.add_argument(
        "--export-root", metavar="PATH", default=None,
        help=(
            "Override the export output root (default: /mnt/music_ssd/KKDJ/). "
            "XML lands in <root>/_REKORDBOX_XML_EXPORT/ and M3U in "
            "<root>/_PLAYLISTS_M3U_EXPORT/"
        ),
    )
    p_rb.add_argument(
        "--recover-missing-analysis",
        action="store_true",
        dest="recover_missing_analysis",
        help=(
            "Run aubio BPM detection and keyfinder-cli key detection for tracks "
            "missing those values before deciding to exclude them. "
            "Off by default — export is fast and predictable without it. "
            "For large libraries, prefer running 'analyze-missing' separately first."
        ),
    )
    p_rb.add_argument(
        "--recover-limit",
        metavar="N",
        type=int,
        default=None,
        dest="recover_limit",
        help=(
            "Maximum number of tracks to attempt analysis on when "
            "--recover-missing-analysis is active (default: unlimited)."
        ),
    )
    p_rb.add_argument(
        "--recover-timeout-sec",
        metavar="N",
        type=float,
        default=None,
        dest="recover_timeout_sec",
        help=(
            "Stop inline analysis after this many seconds when "
            "--recover-missing-analysis is active (default: no timeout)."
        ),
    )
    p_rb.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- analyze-missing subcommand -----
    p_am = subparsers.add_parser(
        "analyze-missing",
        help="Detect BPM and key for tracks missing that data — writes to DB and audio tags",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Scan the library for tracks where BPM or Camelot key is absent,\n"
            "run aubio (BPM) and keyfinder-cli (key) only on those tracks,\n"
            "and write the results back to the database and audio file tags.\n\n"
            "Safe to run multiple times — will not overwrite valid existing values.\n\n"
            "Examples:\n"
            "  python3 pipeline.py analyze-missing\n"
            "  python3 pipeline.py analyze-missing --path /mnt/music_ssd/KKDJ/\n"
            "  python3 pipeline.py analyze-missing --limit 50 --timeout-sec 300\n"
            "  python3 pipeline.py analyze-missing --dry-run --verbose\n"
        ),
    )
    p_am.add_argument(
        "--path", metavar="PATH", default=None,
        help="Restrict analysis to tracks under this directory (default: entire library)",
    )
    p_am.add_argument(
        "--dry-run", action="store_true",
        help="Run detection but do not write to DB or audio file tags",
    )
    p_am.add_argument(
        "--limit", metavar="N", type=int, default=None,
        help="Maximum number of tracks to process in this run",
    )
    p_am.add_argument(
        "--timeout-sec", metavar="N", type=float, default=None, dest="timeout_sec",
        help="Stop processing after this many seconds (default: no timeout)",
    )
    p_am.add_argument(
        "--min-confidence", metavar="FLOAT", type=float, default=0.0, dest="min_confidence",
        help="Minimum BPM confidence score to accept a result (default: 0.0 — accept all)",
    )
    p_am.add_argument(
        "--file-timeout-sec", metavar="N", type=float, default=10.0, dest="file_timeout_sec",
        help=(
            "Hard per-file wall-clock timeout in seconds (default: 10). "
            "Files that exceed this limit are skipped immediately — prevents "
            "corrupt MP3s from causing multi-hour aubio/librosa resync loops."
        ),
    )
    p_am.add_argument(
        "--no-isolate-corrupt",
        action="store_false",
        dest="isolate_corrupt",
        help=(
            "Disable automatic corrupt-file isolation (isolation is ON by default). "
            "By default, files that fail analysis are moved to <corrupt-dir>/. "
            "Bad/non-file paths are always logged but never moved. "
            "A persistent log is written to logs/analyze_missing/corrupt_moves.txt."
        ),
    )
    p_am.add_argument(
        "--corrupt-dir",
        metavar="PATH",
        default=None,
        dest="corrupt_dir",
        help=(
            "Base directory for quarantined files (default: <--path>/_corrupt when "
            "--path is given, otherwise config.CORRUPT_DIR). "
            "Corrupt audio goes into <corrupt-dir>/audio_failures/. "
            "Example: --corrupt-dir /mnt/music_ssd/KKDJ/_corrupt"
        ),
    )
    p_am.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- cue-suggest subcommand -----
    p_cs = subparsers.add_parser(
        "cue-suggest",
        help="Auto-detect cue points (intro / drop / outro) for library tracks",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Analyse audio to detect cue point positions for every track\n"
            "in the library and store results in the database.\n\n"
            "NOTE: These are SUGGESTED positions only. Native Rekordbox\n"
            "hot-cues are NOT written. Review all cues in Rekordbox.\n\n"
            "Cue types detected:\n"
            "  intro_start  — bar 1 (always present, confidence 1.0)\n"
            "  mix_in       — first stable DJ entry point\n"
            "  groove_start — first full-arrangement section\n"
            "  drop         — main energy arrival / impact\n"
            "  breakdown    — energy/density reduction after peak\n"
            "  outro_start  — beginning of mix-out section\n\n"
            "Signal features used (full mode):\n"
            "  RMS energy, low-frequency energy (< 250 Hz, bass/kick proxy),\n"
            "  spectral flux (onset strength). All bar-grid aligned via BPM.\n\n"
            "Fallback: BPM-only heuristic when audio decode fails.\n\n"
            "Output files:\n"
            "  logs/cue_suggest/cue_suggestions.json   (master, all tracks)\n"
            "  logs/cue_suggest/cue_suggestions.csv    (wide format, 1 row/track)\n"
            "  logs/cue_suggest/runs/cues_TIMESTAMP.csv (per-run detail log)\n\n"
            "Examples:\n"
            "  python pipeline.py cue-suggest --dry-run\n"
            "  python pipeline.py cue-suggest\n"
            "  python pipeline.py cue-suggest --limit 20 --track 'Black Coffee'\n"
            "  python pipeline.py cue-suggest --export-format json\n"
        ),
    )
    p_cs.add_argument(
        "--dry-run", action="store_true",
        help="Analyse and print cue points — make no DB writes",
    )
    p_cs.add_argument(
        "--min-confidence", type=float, metavar="FLOAT",
        default=config.CUE_SUGGEST_MIN_CONFIDENCE,
        help=(
            f"Minimum confidence score to store a cue point. "
            f"Default: {config.CUE_SUGGEST_MIN_CONFIDENCE}"
        ),
    )
    p_cs.add_argument(
        "--limit", type=int, metavar="N",
        default=None,
        help="Stop after analysing this many tracks (useful for testing).",
    )
    p_cs.add_argument(
        "--track", metavar="NAME",
        default=None,
        help=(
            "Only analyse tracks whose artist, title, or filename contains NAME "
            "(case-insensitive substring). Example: --track 'Enoo Napa'"
        ),
    )
    p_cs.add_argument(
        "--export-format", metavar="FMT",
        default=None,
        help=(
            "Comma-separated list of master output formats to write: json, csv. "
            "Default: both. Example: --export-format json,csv"
        ),
    )
    p_cs.add_argument(
        "--path", metavar="DIR",
        help="Analyse audio files in this directory instead of the library DB.",
    )
    p_cs.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- set-builder subcommand -----
    p_sb = subparsers.add_parser(
        "set-builder",
        help="Build an energy-curve DJ set from the library database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Automatically build a DJ set from tracks in the library database,\n"
            "arranging them across energy phases with harmonic transitions.\n\n"
            "Phases (always in order):\n"
            "  warmup  — gentle intro, Chill/Mid energy\n"
            "  build   — rising energy\n"
            "  peak    — high-energy section\n"
            "  release — brief energy drop after peak\n"
            "  outro   — wind-down / closing\n\n"
            "Vibe presets control how much time each phase gets:\n"
            "  warm     — extended warmup/build, light peak\n"
            "  peak     — strong peak section (40% of set)\n"
            "  deep     — melodic/organic genres preferred, relaxed pacing\n"
            "  driving  — sustained mid-to-peak energy throughout\n\n"
            "Transition strategies:\n"
            "  safest       — highest Camelot × BPM composite\n"
            "  energy_lift  — incoming energy or BPM is higher\n"
            "  smooth_blend — very close BPM + Camelot\n"
            "  best_warmup  — Chill/Mid energy, relaxed BPM\n"
            "  best_late_set — Peak energy, high BPM, strong Camelot\n\n"
            "Output:\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.m3u8   — playable playlist\n"
            "  SET_BUILDER_OUTPUT_DIR/<name>.csv    — full metadata + transition notes\n\n"
            "Examples:\n"
            "  python pipeline.py set-builder --dry-run\n"
            "  python pipeline.py set-builder --vibe peak --duration 90\n"
            "  python pipeline.py set-builder --vibe deep --genre 'afro house'\n"
            "  python pipeline.py set-builder --strategy energy_lift --name my_set\n"
        ),
    )
    p_sb.add_argument(
        "--dry-run", action="store_true",
        help="Preview the set — write no files",
    )
    p_sb.add_argument(
        "--vibe", metavar="VIBE",
        default="peak",
        choices=["warm", "peak", "deep", "driving"],
        help="Phase-weight preset (warm / peak / deep / driving). Default: peak",
    )
    p_sb.add_argument(
        "--duration", type=int, metavar="MINS",
        default=60,
        help="Target set duration in minutes. Default: 60",
    )
    p_sb.add_argument(
        "--genre", metavar="GENRE",
        default=None,
        help="Restrict track selection to this genre (substring match, e.g. 'afro house')",
    )
    p_sb.add_argument(
        "--strategy", metavar="STRATEGY",
        default="safest",
        choices=["safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"],
        help="Harmonic transition ranking strategy. Default: safest",
    )
    p_sb.add_argument(
        "--structure", metavar="STRUCTURE",
        default="full",
        choices=["full", "simple", "peak_only"],
        help=(
            "Phase structure of the set. "
            "full=warmup→build→peak→release→outro (default), "
            "simple=build→peak→outro, "
            "peak_only=peak only"
        ),
    )
    p_sb.add_argument(
        "--max-bpm-jump", metavar="BPM", type=float, default=3.0,
        dest="max_bpm_jump",
        help=(
            "Maximum allowed absolute BPM difference between consecutive tracks. "
            "Candidates exceeding this are hard-rejected. Default: 3. "
            "Set to 0 to disable."
        ),
    )
    p_sb.add_argument(
        "--no-strict-harmonic", action="store_false", dest="strict_harmonic",
        help=(
            "Disable strict harmonic key validation. By default only same key, "
            "±1 same mode, and relative major/minor (A↔B) transitions are allowed; "
            "this flag falls back to scoring-only."
        ),
    )
    p_sb.add_argument(
        "--artist-repeat-window", metavar="N", type=int, default=3,
        dest="artist_repeat_window",
        help=(
            "Hard-reject any candidate whose primary artist appeared within the "
            "last N tracks. Default: 3. Set to 0 to disable."
        ),
    )
    p_sb.add_argument(
        "--start-energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Preferred energy tier for the first track",
    )
    p_sb.add_argument(
        "--end-energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Preferred energy tier for the last track",
    )
    p_sb.add_argument(
        "--name", metavar="NAME",
        default=None,
        help="Base name for output files (no extension). Default: auto-generated timestamp",
    )
    p_sb.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    # ----- harmonic-suggest subcommand -----
    p_hs = subparsers.add_parser(
        "harmonic-suggest",
        help="Suggest the best next tracks using harmonic + BPM + energy scoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            "Given a track (or a key + BPM pair), rank every track in the\n"
            "library by harmonic compatibility and print the top suggestions.\n\n"
            "Scoring factors:\n"
            "  Camelot compatibility  (35%)  — Camelot wheel distance\n"
            "  BPM compatibility      (30%)  — tempo delta, halftime/doubletime aware\n"
            "  Energy compatibility   (20%)  — Peak / Mid / Chill tier match\n"
            "  Genre compatibility    (15%)  — exact / related / different\n\n"
            "Ranking strategies:\n"
            "  safest       — highest Camelot × BPM composite\n"
            "  energy_lift  — incoming energy or BPM is higher\n"
            "  smooth_blend — very close BPM + Camelot\n"
            "  best_warmup  — Chill/Mid energy, relaxed BPM, harmonic\n"
            "  best_late_set — Peak energy, high BPM, strong Camelot\n\n"
            "Examples:\n"
            "  python pipeline.py harmonic-suggest --track '/music/.../track.mp3'\n"
            "  python pipeline.py harmonic-suggest --key 8A --bpm 128\n"
            "  python pipeline.py harmonic-suggest --track ... --strategy energy_lift\n"
            "  python pipeline.py harmonic-suggest --key 5B --bpm 124 --top-n 20 --json\n"
        ),
    )
    _hs_group = p_hs.add_mutually_exclusive_group()
    _hs_group.add_argument(
        "--track", metavar="PATH",
        help="Path to a track already in the library DB to suggest from",
    )
    p_hs.add_argument(
        "--key", metavar="KEY",
        help="Camelot key of the current track (e.g. 8A, 5B) — used with --bpm",
    )
    p_hs.add_argument(
        "--bpm", type=float, metavar="BPM",
        help="BPM of the current track — used with --key",
    )
    p_hs.add_argument(
        "--strategy", metavar="STRATEGY",
        default="safest",
        choices=["safest", "energy_lift", "smooth_blend", "best_warmup", "best_late_set"],
        help="Ranking strategy. Default: safest",
    )
    p_hs.add_argument(
        "--top-n", type=int, metavar="N",
        default=10,
        help="Number of suggestions to return. Default: 10",
    )
    p_hs.add_argument(
        "--energy", metavar="TIER",
        default=None,
        choices=["Chill", "Mid", "Peak"],
        help="Treat the current track as this energy tier (used with --key/--bpm)",
    )
    p_hs.add_argument(
        "--genre", metavar="GENRE",
        default=None,
        help="Genre of the current track (used with --key/--bpm for genre scoring)",
    )
    p_hs.add_argument(
        "--json", action="store_true",
        help="Write suggestions to a JSON file in HARMONIC_SUGGEST_OUTPUT_DIR",
    )
    p_hs.add_argument(
        "--dry-run", action="store_true",
        help="Print suggestions only — do not write JSON output",
    )
    p_hs.add_argument(
        "--verbose", "-v", action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.command == "artist-merge":
        sys.exit(run_artist_merge(args))

    if args.command == "artist-folder-clean":
        sys.exit(run_artist_folder_clean(args))

    if args.command == "label-intel":
        sys.exit(run_label_intel(args))

    if args.command == "label-clean":
        sys.exit(run_label_clean(args))

    if args.command == "metadata-clean":
        sys.exit(run_metadata_clean(args))

    if args.command == "tag-normalize":
        sys.exit(run_tag_normalize(args))

    if args.command == "db-prune-stale":
        sys.exit(run_db_prune_stale(args))

    if args.command == "convert-audio":
        sys.exit(run_convert_audio(args))

    if args.command == "dedupe":
        sys.exit(run_dedupe(args))

    if args.command == "playlists":
        sys.exit(run_playlists(args))

    if args.command == "analyze-missing":
        sys.exit(run_analyze_missing(args))

    if args.command == "rekordbox-export":
        sys.exit(run_rekordbox_export(args))

    if args.command == "cue-suggest":
        sys.exit(run_cue_suggest(args))

    if args.command == "set-builder":
        sys.exit(run_set_builder(args))

    if args.command == "harmonic-suggest":
        sys.exit(run_harmonic_suggest(args))

    if args.label_enrich_from_library:
        sys.exit(run_label_enrichment_from_library(args.verbose))

    sys.exit(run_pipeline(
        dry_run          = args.dry_run,
        skip_beets       = args.skip_beets,
        skip_analysis    = args.skip_analysis,
        verbose          = args.verbose,
        reanalyze        = args.reanalyze,
        custom_path      = _resolve_path(getattr(args, "path", None)),
        # MIK-first: cue suggest is OFF by default; --force-cue-suggest enables it.
        # --skip-cue-suggest is a deprecated no-op (now the default).
        skip_cue_suggest = not getattr(args, "force_cue_suggest", False),
    ))


if __name__ == "__main__":
    main()
