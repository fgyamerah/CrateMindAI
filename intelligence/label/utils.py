from __future__ import annotations

import re
import time
from typing import Iterable, Optional


def utc_now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize_label_name(name: str) -> str:
    name = name.strip().lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[‘’´`']", "", name)
    name = re.sub(r"[^a-z0-9]+", " ", name)
    noise = {"records", "recordings", "music", "label"}
    tokens = [t for t in name.split() if t not in noise]
    return " ".join(tokens).strip()


def unique_preserve(seq: Iterable[str]) -> list[str]:
    seen = set()
    out = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def soft_bpm_hint(genres: list[str]) -> tuple[Optional[int], Optional[int]]:
    g = " ".join(x.lower() for x in genres)
    if "afro tech" in g:
        return 122, 126
    if "tech house" in g:
        return 124, 128
    if "afro house" in g:
        return 118, 124
    if "organic house" in g:
        return 115, 122
    if "deep house" in g:
        return 112, 122
    if "soulful house" in g:
        return 112, 120
    if "amapiano" in g:
        return 110, 116
    if "melodic house" in g:
        return 118, 124
    if "house" in g:
        return 118, 126
    return None, None


def parse_energy(genres: list[str], bpm_min: Optional[int], bpm_max: Optional[int]) -> Optional[str]:
    if bpm_min is None and bpm_max is None:
        return None

    avg = ((bpm_min or 0) + (bpm_max or 0)) / 2
    genre_text = " ".join(g.lower() for g in genres)

    if "organic house" in genre_text or "deep house" in genre_text:
        if avg <= 118:
            return "warmup"
        if avg <= 122:
            return "groove"

    if avg >= 126:
        return "peak"
    if avg >= 122:
        return "groove"
    return "closing"
