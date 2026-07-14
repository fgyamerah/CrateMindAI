from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional


@dataclass
class LabelRecord:
    label_name: str
    normalized_name: str
    aliases: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    subgenres: list[str] = field(default_factory=list)
    bpm_min: Optional[int] = None
    bpm_max: Optional[int] = None
    energy_profile: Optional[str] = None
    beatport_id: Optional[str] = None
    traxsource_id: Optional[str] = None
    beatport_url: Optional[str] = None
    traxsource_url: Optional[str] = None
    source_pages: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    verification_score: float = 0.0
    discovered_from: list[str] = field(default_factory=list)
    last_seen_utc: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)
