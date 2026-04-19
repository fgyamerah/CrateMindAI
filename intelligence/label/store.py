from __future__ import annotations

from .models import LabelRecord
from .utils import normalize_label_name, parse_energy, soft_bpm_hint, unique_preserve, utc_now_iso


class LabelStore:
    def __init__(self) -> None:
        self.records: dict[str, LabelRecord] = {}

    def upsert(self, partial: dict, source_name: str = "unknown") -> None:
        raw_name = (partial.get("label_name") or "").strip()
        if not raw_name:
            return

        key = normalize_label_name(raw_name)
        if not key:
            return

        rec = self.records.get(key)
        if rec is None:
            rec = LabelRecord(
                label_name=raw_name,
                normalized_name=key,
                last_seen_utc=utc_now_iso(),
            )
            self.records[key] = rec

        if len(raw_name) > len(rec.label_name):
            rec.label_name = raw_name

        rec.aliases = unique_preserve(rec.aliases + partial.get("aliases", []))
        rec.countries = unique_preserve(rec.countries + partial.get("countries", []))
        rec.genres = unique_preserve(rec.genres + partial.get("genres", []))
        rec.subgenres = unique_preserve(rec.subgenres + partial.get("subgenres", []))
        rec.source_pages = unique_preserve(rec.source_pages + partial.get("source_pages", []))
        rec.notes = unique_preserve(rec.notes + partial.get("notes", []))
        rec.discovered_from = unique_preserve(rec.discovered_from + [source_name])
        rec.last_seen_utc = utc_now_iso()

        if partial.get("beatport_id"):
            rec.beatport_id = partial["beatport_id"]
        if partial.get("traxsource_id"):
            rec.traxsource_id = partial["traxsource_id"]
        if partial.get("beatport_url"):
            rec.beatport_url = partial["beatport_url"]
        if partial.get("traxsource_url"):
            rec.traxsource_url = partial["traxsource_url"]

        rec.verification_score = max(rec.verification_score, float(partial.get("verification_score", 0.0)))

        if rec.bpm_min is None and rec.bpm_max is None:
            bpm_min, bpm_max = partial.get("bpm_min"), partial.get("bpm_max")
            if bpm_min is None and bpm_max is None:
                bpm_min, bpm_max = soft_bpm_hint(rec.subgenres or rec.genres)
            rec.bpm_min, rec.bpm_max = bpm_min, bpm_max

        if not rec.energy_profile:
            rec.energy_profile = partial.get("energy_profile") or parse_energy(
                rec.subgenres or rec.genres,
                rec.bpm_min,
                rec.bpm_max,
            )

    def values(self) -> list[LabelRecord]:
        return sorted(self.records.values(), key=lambda r: (r.label_name.lower(), r.normalized_name))
