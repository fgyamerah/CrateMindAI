from __future__ import annotations

import csv
import json
import sqlite3
from dataclasses import asdict
from pathlib import Path

from .models import LabelRecord


def export_json(records: list[LabelRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [asdict(r) for r in records]
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def export_txt(records: list[LabelRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(r.label_name for r in records) + "\n", encoding="utf-8")


def export_csv(records: list[LabelRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "label_name", "normalized_name", "aliases", "countries", "genres", "subgenres",
        "bpm_min", "bpm_max", "energy_profile", "beatport_id", "traxsource_id",
        "beatport_url", "traxsource_url", "source_pages", "notes", "verification_score",
        "discovered_from", "last_seen_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in records:
            row = asdict(r)
            for k in ["aliases", "countries", "genres", "subgenres", "source_pages", "notes", "discovered_from"]:
                row[k] = " | ".join(row[k])
            w.writerow(row)


def export_sqlite(records: list[LabelRecord], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE labels (
        label_name TEXT,
        normalized_name TEXT PRIMARY KEY,
        aliases_json TEXT,
        countries_json TEXT,
        genres_json TEXT,
        subgenres_json TEXT,
        bpm_min INTEGER,
        bpm_max INTEGER,
        energy_profile TEXT,
        beatport_id TEXT,
        traxsource_id TEXT,
        beatport_url TEXT,
        traxsource_url TEXT,
        source_pages_json TEXT,
        notes_json TEXT,
        verification_score REAL,
        discovered_from_json TEXT,
        last_seen_utc TEXT
    );
    CREATE INDEX idx_labels_label_name ON labels(label_name);
    CREATE INDEX idx_labels_bp_id ON labels(beatport_id);
    CREATE INDEX idx_labels_ts_id ON labels(traxsource_id);
    """)
    for r in records:
        cur.execute(
            """
            INSERT INTO labels VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                r.label_name,
                r.normalized_name,
                json.dumps(r.aliases, ensure_ascii=False),
                json.dumps(r.countries, ensure_ascii=False),
                json.dumps(r.genres, ensure_ascii=False),
                json.dumps(r.subgenres, ensure_ascii=False),
                r.bpm_min,
                r.bpm_max,
                r.energy_profile,
                r.beatport_id,
                r.traxsource_id,
                r.beatport_url,
                r.traxsource_url,
                json.dumps(r.source_pages, ensure_ascii=False),
                json.dumps(r.notes, ensure_ascii=False),
                r.verification_score,
                json.dumps(r.discovered_from, ensure_ascii=False),
                r.last_seen_utc,
            ),
        )
    conn.commit()
    conn.close()
