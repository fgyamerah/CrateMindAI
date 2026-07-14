"""
Label-clean report generation.

Produces four output files in the configured output directory:

  label_clean_report.json    — full per-track details
  label_clean_report.csv     — spreadsheet-friendly version
  label_clean_review.json    — only unresolved / low-confidence cases
  label_clean_summary.txt    — human-readable run summary with stats

All paths are returned so the caller can log them.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Optional

from .cleaner import TrackLabelResult


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _row_dict(r: TrackLabelResult) -> dict:
    return {
        "filepath":        r.filepath,
        "artist":          r.artist,
        "title":           r.title,
        "raw_label":       r.raw_label       or "",
        "cleaned_label":   r.cleaned_label   or "",
        "canonical_label": r.canonical_label or "",
        "source":          r.source,
        "confidence":      round(r.confidence, 3),
        "action_taken":    r.action_taken,
        "writable":        r.writable,
        "junk_count":      r.junk_count,
        "notes":           " | ".join(r.notes),
    }


_FIELDNAMES = [
    "filepath", "artist", "title",
    "raw_label", "cleaned_label", "canonical_label",
    "source", "confidence", "action_taken", "writable", "junk_count", "notes",
]


# ---------------------------------------------------------------------------
# Individual exporters
# ---------------------------------------------------------------------------

def export_report_json(results: list[TrackLabelResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([_row_dict(r) for r in results], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def export_report_csv(results: list[TrackLabelResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDNAMES)
        w.writeheader()
        for r in results:
            w.writerow(_row_dict(r))


def export_review_json(results: list[TrackLabelResult], path: Path) -> None:
    """Only unresolved or low-confidence (non-writable) tracks."""
    review = [r for r in results if r.source == "unresolved" or not r.writable]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([_row_dict(r) for r in review], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _summary_text(results: list[TrackLabelResult], written: int) -> str:
    total          = len(results)
    good           = sum(1 for r in results if r.source == "embedded_tag")
    filled         = sum(1 for r in results if r.source in ("fallback_tag", "filename"))
    unresolved     = sum(1 for r in results if r.source == "unresolved")
    high_conf      = sum(1 for r in results if r.writable)
    junk_total     = sum(r.junk_count for r in results)
    junk_tracks    = sum(1 for r in results if r.junk_count > 0)

    label_counter = Counter(
        r.canonical_label for r in results if r.canonical_label
    )
    top_labels = label_counter.most_common(15)

    source_breakdown = Counter(r.source for r in results)

    lines = [
        "=" * 62,
        "  Label Clean — Summary",
        "=" * 62,
        f"  Total tracks scanned        : {total}",
        f"  Good embedded label tags    : {good}",
        f"  Filled from fallback fields : {filled}",
        f"  Unresolved (no label found) : {unresolved}",
        f"  Junk labels rejected        : {junk_total}  (across {junk_tracks} tracks)",
        f"  High-confidence (>= 0.85)  : {high_conf}",
        f"  Tags written this run       : {written}",
        "",
        "  Source breakdown:",
    ]
    for src, count in source_breakdown.most_common():
        lines.append(f"    {count:>5}  {src}")
    if top_labels:
        lines += ["", "  Most common labels found:"]
        for label, count in top_labels:
            lines.append(f"    {count:>5}  {label}")
    lines.append("=" * 62)
    return "\n".join(lines)


def export_summary_txt(
    results: list[TrackLabelResult],
    path: Path,
    written: int = 0,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_summary_text(results, written) + "\n", encoding="utf-8")


def print_summary(results: list[TrackLabelResult], written: int = 0) -> None:
    print(_summary_text(results, written))


# ---------------------------------------------------------------------------
# Convenience: generate all four reports at once
# ---------------------------------------------------------------------------

def generate_all(
    results: list[TrackLabelResult],
    output_dir: Path,
    written: int = 0,
    review_only: bool = False,
) -> dict[str, Path]:
    """
    Write all report files to output_dir.

    Returns a dict mapping report name → Path.
    When review_only=True, only the review JSON is written.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    p_review = output_dir / "label_clean_review.json"
    export_review_json(results, p_review)
    paths["review_json"] = p_review

    if not review_only:
        p_json = output_dir / "label_clean_report.json"
        p_csv  = output_dir / "label_clean_report.csv"
        p_txt  = output_dir / "label_clean_summary.txt"

        export_report_json(results, p_json)
        export_report_csv(results,  p_csv)
        export_summary_txt(results, p_txt, written)

        paths["full_json"] = p_json
        paths["csv"]       = p_csv
        paths["summary"]   = p_txt

    return paths
