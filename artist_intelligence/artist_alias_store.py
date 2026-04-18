"""
artist_intelligence/artist_alias_store.py — Persistent alias store and
uncertain-candidate review queue.

Alias store  (data/intelligence/artist_aliases.json):
  {
    "Canonical Artist Name": ["variant 1", "variant 2"],
    ...
  }

  - Keys are canonical / preferred spellings.
  - Values are known alternate spellings / typos / formatting variants.
  - The canonical name is also indexed, so it matches itself.

Review queue (data/intelligence/artist_review_queue.json):
  [
    {
      "file":                 "/path/to/file.mp3",
      "raw_artist":           "Heavy-K",
      "normalized_candidate": "Heavy K",
      "existing_title":       "Track Title",
      "confidence":           0.60,
      "notes":                "hyphen-vs-space variant"
    },
    ...
  ]

  - Append-only; duplicate entries (same file + raw_artist) are updated
    in-place rather than appended again.

Lookup priority:
  lookup_exact      — raw string equality check
  lookup_normalized — normalize_artist_string() applied to both sides
  lookup_ci         — lowercased equality, no other normalization
  lookup_any        — tries all three in order, returns first match
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Dict, List, Optional

from artist_intelligence.artist_normalizer import normalize_artist_string

log = logging.getLogger(__name__)


class ArtistAliasStore:
    """
    In-memory alias store backed by a JSON file.

    The internal reverse index maps  normalize(variant).lower() → canonical
    for O(1) normalized lookups.  Rebuilt whenever the store is mutated.

    Not thread-safe — single-process CLI use only.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        # canonical → [variant, ...]
        self._store: Dict[str, List[str]] = {}
        # normalized_lower → canonical  (reverse index)
        self._index: Dict[str, str] = {}
        self._load()

    # ------------------------------------------------------------------
    # Load / save
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            log.debug("Alias store not found at %s — starting empty", self.path)
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                log.warning("Alias store at %s is not a JSON object — ignoring", self.path)
                return
            self._store = {str(k): [str(v) for v in vs] for k, vs in data.items()}
            self._rebuild_index()
            log.debug(
                "Loaded alias store: %d canonical artists, %d total entries",
                len(self._store),
                sum(len(vs) for vs in self._store.values()),
            )
        except Exception as exc:
            log.warning("Could not load alias store from %s: %s", self.path, exc)

    def _rebuild_index(self) -> None:
        """Rebuild the normalized reverse-lookup index from scratch."""
        self._index = {}
        for canonical, variants in self._store.items():
            # Canonical name indexes to itself
            self._index_one(canonical, canonical)
            for variant in variants:
                self._index_one(variant, canonical)

    def _index_one(self, variant: str, canonical: str) -> None:
        key = normalize_artist_string(variant).lower()
        existing = self._index.get(key)
        if existing and existing != canonical:
            log.warning(
                "Alias conflict: normalized %r maps to both %r and %r — keeping %r",
                variant, existing, canonical, existing,
            )
        else:
            self._index[key] = canonical

    def save(self) -> None:
        """Persist the current store to disk, creating parent dirs as needed."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._store, fh, indent=2, ensure_ascii=False, sort_keys=True)
        except Exception as exc:
            log.error("Could not save alias store to %s: %s", self.path, exc)

    # ------------------------------------------------------------------
    # Lookups
    # ------------------------------------------------------------------

    def lookup_exact(self, name: str) -> Optional[str]:
        """
        Return the canonical name if name matches exactly as a key or variant.
        Case-sensitive, no normalization applied.
        """
        if name in self._store:
            return name
        for canonical, variants in self._store.items():
            if name in variants:
                return canonical
        return None

    def lookup_normalized(self, name: str) -> Optional[str]:
        """
        Return canonical after applying normalize_artist_string() to both sides.
        This is the primary lookup used during processing.
        """
        key = normalize_artist_string(name).lower()
        return self._index.get(key)

    def lookup_ci(self, name: str) -> Optional[str]:
        """
        Return canonical using simple case-insensitive exact comparison.
        No other normalization applied — useful as a fallback.
        """
        name_lower = name.strip().lower()
        for canonical, variants in self._store.items():
            if canonical.lower() == name_lower:
                return canonical
            if any(v.lower() == name_lower for v in variants):
                return canonical
        return None

    def lookup_any(self, name: str) -> Optional[str]:
        """Try exact → normalized → ci lookups in order. Return first match."""
        return (
            self.lookup_exact(name)
            or self.lookup_normalized(name)
            or self.lookup_ci(name)
        )

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def add_variant(self, canonical: str, variant: str) -> None:
        """
        Add variant to an existing canonical entry, or create a new entry.
        Saves nothing to disk — call save() explicitly.
        """
        if canonical not in self._store:
            self._store[canonical] = []
        if variant not in self._store[canonical]:
            self._store[canonical].append(variant)
            self._rebuild_index()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def all_canonicals(self) -> List[str]:
        return sorted(self._store.keys())

    def __len__(self) -> int:
        return len(self._store)


class ArtistReviewQueue:
    """
    Append-only persistent queue for uncertain artist normalization candidates.

    Existing entries for the same (file, raw_artist) pair are updated
    in-place to avoid accumulating duplicate review requests.

    Call save() explicitly after adding all entries for the current run.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._queue: List[dict] = []
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, list):
                self._queue = data
        except Exception as exc:
            log.warning("Could not load review queue from %s: %s", self.path, exc)

    def add(
        self,
        file: str,
        raw_artist: str,
        normalized_candidate: str,
        existing_title: str,
        confidence: float,
        notes: str = "",
    ) -> None:
        entry = {
            "file":                 file,
            "raw_artist":           raw_artist,
            "normalized_candidate": normalized_candidate,
            "existing_title":       existing_title,
            "confidence":           round(confidence, 3),
            "notes":                notes,
        }
        # Update in-place if this file+artist was already queued
        for existing in self._queue:
            if (
                existing.get("file") == file
                and existing.get("raw_artist") == raw_artist
            ):
                existing.update(entry)
                return
        self._queue.append(entry)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(self._queue, fh, indent=2, ensure_ascii=False)
        except Exception as exc:
            log.error("Could not save review queue to %s: %s", self.path, exc)

    def __len__(self) -> int:
        return len(self._queue)
