from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Callable

from .store import LabelStore
from .utils import normalize_label_name, parse_energy


def enrich_store_from_tracks(
    store: LabelStore,
    tracks: list[dict],
    label_field: str = "label",
    bpm_field: str = "bpm",
    genre_field: str = "genre",
    subgenre_field: str = "subgenre",
) -> LabelStore:
    grouped = defaultdict(list)

    for track in tracks:
        raw_label = (track.get(label_field) or "").strip()
        if not raw_label:
            continue
        grouped[normalize_label_name(raw_label)].append(track)

    for norm_label, items in grouped.items():
        rec = store.records.get(norm_label)
        if rec is None:
            pretty = items[0].get(label_field, "").strip()
            if not pretty:
                continue
            store.upsert({"label_name": pretty, "verification_score": 0.1}, source_name="library")
            rec = store.records.get(norm_label)
            if rec is None:
                continue

        bpms = []
        genres = []
        subgenres = []

        for item in items:
            bpm = item.get(bpm_field)
            if isinstance(bpm, (int, float)) and bpm > 0:
                bpms.append(int(round(float(bpm))))
            genre = item.get(genre_field)
            subgenre = item.get(subgenre_field)
            if genre:
                genres.append(str(genre))
            if subgenre:
                subgenres.append(str(subgenre))

        if bpms:
            rec.bpm_min = min(bpms)
            rec.bpm_max = max(bpms)

        for g in genres:
            if g not in rec.genres:
                rec.genres.append(g)
        for sg in subgenres:
            if sg not in rec.subgenres:
                rec.subgenres.append(sg)

        if rec.energy_profile is None and (rec.bpm_min is not None or rec.bpm_max is not None):
            rec.energy_profile = parse_energy(rec.subgenres or rec.genres, rec.bpm_min, rec.bpm_max)

        rec.discovered_from = list(dict.fromkeys(rec.discovered_from + ["library"]))

    return store
