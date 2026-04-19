"""
intelligence/enrichment/deezer_lookup.py

Deezer API client for metadata lookup — used as a fallback when Spotify
returns no result or a low-confidence match.

No authentication required: Deezer's public /search endpoint is open.
Rate limit: ~50 requests / 5 seconds per IP (unauthenticated).

Supports:
  search_by_artist_title(artist, title) → List[EnrichmentCandidate]

Deezer does not support ISRC lookup via a public query parameter, so all
searches go through artist + title.  The returned candidates may include
an ISRC field from the track object, which the matcher will use for scoring.

Extension point for Traxsource:
  When traxsource_lookup.py is added, expose:
    search_by_artist_title(artist, title) → List[EnrichmentCandidate]
  and wire it into runner.py's _search_with_fallback() alongside this module.
"""
from __future__ import annotations

import logging
from typing import List

import requests

from intelligence.enrichment.enrichment_schema import EnrichmentCandidate

log = logging.getLogger(__name__)

_DEEZER_API_BASE = "https://api.deezer.com"
_SEARCH_URL      = f"{_DEEZER_API_BASE}/search"
_MAX_RESULTS     = 5


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class DeezerClient:
    """
    Thin wrapper around the Deezer public search API.

    Usage:
        client = DeezerClient()
        candidates = client.search_by_artist_title("Black Coffee", "Drive")
    """

    def __init__(self, timeout: int = 10) -> None:
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    def search_by_artist_title(
        self,
        artist: str,
        title: str,
    ) -> List[EnrichmentCandidate]:
        """
        Search Deezer by artist and title.

        Deezer supports advanced query syntax:
            artist:"Heavy K" track:"Sgwili Sgwili"

        Returns up to _MAX_RESULTS candidates sorted by Deezer relevance.
        """
        if not artist and not title:
            return []

        parts = []
        if artist:
            parts.append(f'artist:"{_q(artist)}"')
        if title:
            from intelligence.enrichment.metadata_matcher import _strip_version
            parts.append(f'track:"{_q(_strip_version(title) or title)}"')

        query = " ".join(parts)
        log.debug("Deezer search: %s", query)

        try:
            resp = self._session.get(
                _SEARCH_URL,
                params={"q": query, "limit": _MAX_RESULTS},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Deezer search failed: %s", exc)
            return []

        return _parse_tracks(resp.json())


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------

def _q(s: str) -> str:
    """Strip double quotes that would break Deezer query syntax."""
    return s.replace('"', "")


def _parse_tracks(body: dict) -> List[EnrichmentCandidate]:
    """
    Convert a Deezer /search response into EnrichmentCandidate objects.

    Deezer track object keys used:
      title           — track title
      artist.name     — primary artist
      album.title     — album name
      isrc            — ISRC (present on most tracks)
      release_date    — ISO date (may be absent on older catalogue)
      contributors    — list of contributing artists (not always present)

    Label note: Deezer does not expose the record label on the /search
    endpoint.  A separate GET /album/{id} call would return album.label —
    this is left as a future enhancement and kept consistent with Spotify.
    """
    items = body.get("data") or []
    results: List[EnrichmentCandidate] = []

    for item in items:
        if not item:
            continue

        artist_data = item.get("artist") or {}
        album_data  = item.get("album")  or {}

        results.append(EnrichmentCandidate(
            source       = "deezer",
            artist       = artist_data.get("name") or None,
            title        = item.get("title") or item.get("title_short") or None,
            album        = album_data.get("title") or None,
            label        = None,  # not available from /search (see note above)
            isrc         = item.get("isrc") or None,
            release_date = item.get("release_date") or None,
            raw          = item,
        ))

    log.debug("Deezer returned %d track(s)", len(results))
    return results
