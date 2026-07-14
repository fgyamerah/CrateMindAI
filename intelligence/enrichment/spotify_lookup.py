"""
intelligence/enrichment/spotify_lookup.py

Spotify Web API client for metadata lookup.

Auth: Client Credentials OAuth2 flow — no user login required.
      Credentials must be set via:
        SPOTIFY_CLIENT_ID     env var  (or config.SPOTIFY_CLIENT_ID)
        SPOTIFY_CLIENT_SECRET env var  (or config.SPOTIFY_CLIENT_SECRET)

      To obtain credentials:
        1. Log in at https://developer.spotify.com/dashboard
        2. Create an app (name/description can be anything)
        3. Copy the Client ID and Client Secret into your environment or
           config_local.py

Supports:
  search_by_isrc(isrc)             → List[EnrichmentCandidate]
  search_by_artist_title(a, t)     → List[EnrichmentCandidate]

The token is cached in-process and refreshed automatically when it expires.

Extension point for Traxsource:
  When traxsource_lookup.py is added, it must expose the same two-method
  interface as this module so runner.py can treat all sources uniformly.
"""
from __future__ import annotations

import base64
import logging
import time
from typing import List, Optional

import requests

import config
from intelligence.enrichment.enrichment_schema import EnrichmentCandidate

log = logging.getLogger(__name__)

_TOKEN_URL  = "https://accounts.spotify.com/api/token"
_API_BASE   = "https://api.spotify.com/v1"
_SEARCH_URL = f"{_API_BASE}/search"

# Maximum candidates to return per search
_MAX_RESULTS = 5


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class SpotifyClient:
    """
    Thin wrapper around the Spotify Web API search endpoint.

    Usage:
        client = SpotifyClient(client_id, client_secret)
        candidates = client.search_by_isrc("GBUM71029604")
        candidates = client.search_by_artist_title("Heavy K", "Sgwili Sgwili")
    """

    def __init__(self, client_id: str, client_secret: str, timeout: int = 10) -> None:
        if not client_id or not client_secret:
            raise ValueError(
                "Spotify client_id and client_secret are required.\n"
                "  Set env vars SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET,\n"
                "  or add them to config_local.py.\n"
                "  Obtain credentials at: https://developer.spotify.com/dashboard"
            )
        self._client_id     = client_id
        self._client_secret = client_secret
        self._timeout       = timeout
        self._access_token: Optional[str] = None
        self._token_expiry: float          = 0.0
        self._session       = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

    # ------------------------------------------------------------------
    # Token management
    # ------------------------------------------------------------------

    def _ensure_token(self) -> None:
        """Fetch or refresh the access token if it has expired."""
        if self._access_token and time.time() < self._token_expiry - 30:
            return

        creds   = base64.b64encode(
            f"{self._client_id}:{self._client_secret}".encode()
        ).decode()

        try:
            resp = self._session.post(
                _TOKEN_URL,
                headers={"Authorization": f"Basic {creds}"},
                data={"grant_type": "client_credentials"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise SpotifyAuthError(
                f"Spotify token request failed: {exc}\n"
                "  Check SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET."
            ) from exc

        body = resp.json()
        self._access_token = body["access_token"]
        self._token_expiry = time.time() + body.get("expires_in", 3600)
        log.debug("Spotify token refreshed, expires in %ds", body.get("expires_in", 3600))

    def _auth_header(self) -> dict:
        self._ensure_token()
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # ISRC lookup
    # ------------------------------------------------------------------

    def search_by_isrc(self, isrc: str) -> List[EnrichmentCandidate]:
        """
        Search Spotify for a track by ISRC.

        ISRC is the most reliable lookup key — an exact ISRC match is
        almost always the correct track.  Returns an empty list if the
        ISRC is not in the Spotify catalogue.
        """
        isrc = isrc.strip().upper()
        log.debug("Spotify ISRC lookup: %s", isrc)

        try:
            resp = self._session.get(
                _SEARCH_URL,
                headers=self._auth_header(),
                params={"q": f"isrc:{isrc}", "type": "track", "limit": 1},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Spotify ISRC search failed for %s: %s", isrc, exc)
            return []

        return _parse_tracks(resp.json(), source="spotify")

    # ------------------------------------------------------------------
    # Artist + title search
    # ------------------------------------------------------------------

    def search_by_artist_title(
        self,
        artist: str,
        title: str,
    ) -> List[EnrichmentCandidate]:
        """
        Search Spotify by artist and title.

        Returns up to _MAX_RESULTS candidates sorted by Spotify relevance.
        The caller (metadata_matcher.py) scores them and picks the best.
        """
        if not artist and not title:
            return []

        # Build a targeted query: field-specific filters give better precision
        # than a plain text search, especially for common titles.
        parts = []
        if artist:
            parts.append(f'artist:"{_q(artist)}"')
        if title:
            # Strip version info from the title for the query — Spotify's
            # catalogue often omits "(Original Mix)" etc.
            from intelligence.enrichment.metadata_matcher import _strip_version
            parts.append(f'track:"{_q(_strip_version(title) or title)}"')

        query = " ".join(parts)
        log.debug("Spotify search: %s", query)

        try:
            resp = self._session.get(
                _SEARCH_URL,
                headers=self._auth_header(),
                params={"q": query, "type": "track", "limit": _MAX_RESULTS},
                timeout=self._timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Spotify artist+title search failed: %s", exc)
            return []

        return _parse_tracks(resp.json(), source="spotify")


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SpotifyError(Exception):
    """Base class for Spotify client errors."""


class SpotifyAuthError(SpotifyError):
    """Credentials missing or token exchange failed."""


# ---------------------------------------------------------------------------
# Response parsing helpers
# ---------------------------------------------------------------------------

def _q(s: str) -> str:
    """Escape double quotes inside a Spotify query field value."""
    return s.replace('"', "")


def _parse_tracks(body: dict, source: str) -> List[EnrichmentCandidate]:
    """
    Convert a Spotify /search response body into EnrichmentCandidate objects.

    Spotify note on label: the `label` field lives on the Album object, not
    the Track object.  The search endpoint does NOT return album.label.
    We skip a second album-detail call by default to stay within rate limits.
    If you need labels, make an additional GET /v1/albums/{album_id} call and
    set candidate.label from body["label"].
    """
    tracks_data = body.get("tracks") or {}
    items = tracks_data.get("items") or []
    results: List[EnrichmentCandidate] = []

    for item in items:
        if not item:
            continue

        # Artists: join multiple artists with ", "
        artists_list = item.get("artists") or []
        artist_str   = ", ".join(a.get("name", "") for a in artists_list if a.get("name"))

        album_data = item.get("album") or {}

        results.append(EnrichmentCandidate(
            source       = source,
            artist       = artist_str or None,
            title        = item.get("name") or None,
            album        = album_data.get("name") or None,
            label        = None,   # not available from /v1/search (see note above)
            isrc         = (item.get("external_ids") or {}).get("isrc") or None,
            release_date = album_data.get("release_date") or None,
            raw          = item,
        ))

    log.debug("Spotify returned %d track(s)", len(results))
    return results
