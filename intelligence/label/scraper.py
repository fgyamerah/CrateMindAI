from __future__ import annotations

from pathlib import Path

from .store import LabelStore
from .sources.base import HttpClient
from .sources.beatport import BeatportSource
from .sources.traxsource import TraxsourceSource
from .utils import normalize_label_name


def load_seed_labels(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sources(client: HttpClient, source_names: list[str]):
    out = []
    for name in source_names:
        if name == "beatport":
            out.append(BeatportSource(client))
        elif name == "traxsource":
            out.append(TraxsourceSource(client))
        else:
            raise ValueError(f"Unknown source: {name}")
    return out


def scrape_labels(
    seed_path: Path,
    cache_dir: Path,
    source_names: list[str] | None = None,
    delay: float = 2.0,
    timeout: int = 30,
    skip_enrich: bool = False,
) -> LabelStore:
    source_names = source_names or ["beatport", "traxsource"]
    seeds = load_seed_labels(seed_path)
    client = HttpClient(cache_dir=cache_dir, delay=delay, timeout=timeout)
    store = LabelStore()

    for seed in seeds:
        store.upsert({"label_name": seed, "verification_score": 0.2}, source_name="seed")

    for seed in seeds:
        seed_norm = normalize_label_name(seed)
        for source in _sources(client, source_names):
            try:
                html = client.get(source.search_url(seed))
                candidates = source.extract_candidates(html, source.search_url(seed))
            except Exception as exc:
                store.upsert({"label_name": seed, "notes": [f"{source.source_name}_search_failed:{type(exc).__name__}"]}, source_name=source.source_name)
                continue

            for cand in candidates:
                cand_norm = normalize_label_name(cand.get("label_name", ""))
                if not cand_norm or not seed_norm or (seed_norm not in cand_norm and cand_norm not in seed_norm):
                    continue

                if skip_enrich:
                    store.upsert(cand, source_name=source.source_name)
                    continue

                try:
                    enriched = source.enrich_label_page(cand["url"])
                    for k, v in cand.items():
                        if k not in enriched or enriched.get(k) is None:
                            enriched[k] = v
                    store.upsert(enriched, source_name=source.source_name)
                except Exception as exc:
                    cand.setdefault("notes", []).append(f"enrich_failed:{type(exc).__name__}")
                    store.upsert(cand, source_name=source.source_name)

    return store
