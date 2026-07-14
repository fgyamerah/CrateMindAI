"""
intelligence/enrichment/traxsource_lookup.py

Traxsource HTML scraper — dance-music specialist metadata source.

Traxsource is a specialist EDM/house/Afro music store with the richest version,
label, and genre metadata for dance music.  It has no public JSON API, so this
module scrapes the HTML search endpoint.

Used as a FALLBACK only — never the first source queried.  Triggered when:
  a) Spotify + Deezer combined confidence is below the apply threshold, OR
  b) The file's genre tag signals house / Afro / deep / soulful territory where
     Traxsource's dance-specialist data is likely to be more precise.

Exposes:
    TraxsourceClient.search_by_artist_title(artist, title)
        → List[EnrichmentCandidate]

Extraction targets per track row:
    artist        primary artist string
    title         track title (combined with version if stored separately)
    version       mix/remix suffix, e.g. "(Original Mix)"
    label         record label — Traxsource always provides this
    release       release/EP/album title
    genre         genre / subgenre string (stored in EnrichmentCandidate.genre)
    release_date  ISO-ish date string

Robustness notes:
    - All parsing is wrapped in try/except; a broken row is skipped, not fatal.
    - The CSS selectors below were verified against Traxsource's search HTML as
      of 2026-04.  If Traxsource restructures its frontend, update the _SEL_*
      constants and re-verify with `python3 -m intelligence.enrichment.traxsource_lookup`.
    - Traxsource's search is server-side rendered (SEO), so plain requests.get()
      returns the full HTML without a headless browser.
    - Rate limit: be conservative — this is a fallback, called infrequently.

IMPORTANT: scraping is only permissible for personal library enrichment use,
not commercial aggregation.  Review Traxsource's terms of service before use.
"""
from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse
from typing import List, Optional

try:
    import requests
    from bs4 import BeautifulSoup, Tag
    _DEPS_AVAILABLE = True
except ImportError:
    _DEPS_AVAILABLE = False

from intelligence.enrichment.enrichment_schema import EnrichmentCandidate

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SEARCH_BASE = "https://www.traxsource.com/search"
_USER_AGENT  = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_REFERER           = "https://www.traxsource.com/"
_MAX_RESULTS       = 5
_REQUEST_DELAY_MIN = 0.5   # seconds — minimum inter-request gap
_REQUEST_DELAY_MAX = 1.0   # seconds — maximum inter-request gap (random jitter)
_TIMEOUT           = 15    # seconds

# CSS selectors — update these if Traxsource changes its HTML structure.
# Multiple alternatives listed where the site uses different classes on
# different page variants; BeautifulSoup select() tries each in order.
_SEL_TRACK_ROW  = ".trk-row"
_SEL_ARTISTS    = ".trk-artists a"
_SEL_TITLE      = [".trk-info a.title-lnk", ".trk-info a.ttl-lnk",
                   ".trk-info .title-lnk", ".trk-info a[href*='/title/']"]
_SEL_VERSION    = [".trk-info .version", ".trk-info .trk-mix", ".trk-mix"]
_SEL_LABEL      = [".trk-label a", ".label a", ".trk-label"]
_SEL_GENRE      = [".trk-genre", ".genre-list", ".genres"]
_SEL_DATE       = [".trk-date", ".tdate", ".trk-released"]
_SEL_RELEASE    = [".trk-release a", ".release a", ".trk-info .release"]


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class TraxsourceClient:
    """
    Scraper-backed Traxsource search client.

    Usage:
        client = TraxsourceClient()
        candidates = client.search_by_artist_title("Black Coffee", "Drive")

    Returns an empty list (never raises) when:
      - bs4 / requests are not installed
      - The HTTP request fails
      - HTML parsing yields no usable results
    """

    def __init__(self, timeout: int = _TIMEOUT) -> None:
        self._timeout = timeout
        self._last_request_at: float = 0.0
        if _DEPS_AVAILABLE:
            self._session = requests.Session()
            self._session.headers.update({
                "User-Agent":      _USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
                "Accept":          "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
                "Referer":         _REFERER,
            })
        else:
            self._session = None  # type: ignore[assignment]
            log.warning(
                "TraxsourceClient: 'requests' and/or 'beautifulsoup4' are not "
                "installed — Traxsource lookups disabled.  "
                "Install with: pip install requests beautifulsoup4"
            )

    def search_by_artist_title(
        self,
        artist: str,
        title: str,
    ) -> List[EnrichmentCandidate]:
        """
        Search Traxsource by artist and base title (version stripped).

        Returns up to _MAX_RESULTS candidates in Traxsource relevance order.
        """
        if not _DEPS_AVAILABLE or self._session is None:
            return []
        if not artist and not title:
            return []

        # Strip version terms before searching — Traxsource's internal search
        # behaves better without mix/version qualifiers in the query.
        from intelligence.enrichment.metadata_matcher import _strip_version
        clean_title = _strip_version(title) or title

        query = " ".join(filter(None, [artist, clean_title]))
        log.debug("Traxsource search: %r", query)

        html = self._fetch(query)
        if not html:
            return []

        candidates = _parse_html(html)
        log.debug("Traxsource returned %d track(s)", len(candidates))
        return candidates[:_MAX_RESULTS]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch(self, query: str) -> Optional[str]:
        """
        Fetch search results HTML.  Returns None on any error.

        Rate limiting: enforces a random delay of _REQUEST_DELAY_MIN–_REQUEST_DELAY_MAX
        seconds between successive calls so we don't hammer the server.

        403 handling: logged as a warning (not an exception) so a blocked request
        doesn't surface as a pipeline error.  The caller receives [] candidates.
        """
        # Enforce random inter-request delay
        delay = random.uniform(_REQUEST_DELAY_MIN, _REQUEST_DELAY_MAX)
        elapsed = time.time() - self._last_request_at
        if elapsed < delay:
            time.sleep(delay - elapsed)

        url = f"{_SEARCH_BASE}?term={urllib.parse.quote(query)}"
        log.debug("Traxsource GET %s", url)

        try:
            resp = self._session.get(url, timeout=self._timeout)
        except Exception as exc:
            log.warning("Traxsource request failed: %s", exc)
            return None

        self._last_request_at = time.time()

        if resp.status_code == 403:
            log.warning(
                "Traxsource returned 403 Forbidden — the scraper may be blocked. "
                "Skipping Traxsource for this track."
            )
            return None

        if not resp.ok:
            log.warning("Traxsource returned HTTP %d for query %r", resp.status_code, query)
            return None

        return resp.text


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------

def _parse_html(html: str) -> List[EnrichmentCandidate]:
    """
    Parse Traxsource search result HTML into EnrichmentCandidate objects.

    Each track row is parsed independently; a broken row is skipped with a
    debug log.  Returns an empty list if no rows are found.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows = soup.select(_SEL_TRACK_ROW)

    if not rows:
        # Traxsource may return results inside a different container on some
        # page variants.  Log to help diagnose selector drift.
        log.debug(
            "Traxsource: no rows found for selector %r — "
            "the page structure may have changed; update _SEL_TRACK_ROW",
            _SEL_TRACK_ROW,
        )

    results: List[EnrichmentCandidate] = []
    for row in rows:
        cand = _parse_row(row)
        if cand is not None:
            results.append(cand)
    return results


def _parse_row(row: "Tag") -> Optional[EnrichmentCandidate]:
    """
    Parse one .trk-row Tag into an EnrichmentCandidate.

    Returns None if neither artist nor title can be extracted.
    """
    try:
        artist      = _first_text(row, _SEL_ARTISTS, join=", ")
        title_text  = _first_text_multi(row, _SEL_TITLE)
        version     = _first_text_multi(row, _SEL_VERSION)
        label       = _first_text_multi(row, _SEL_LABEL)
        genre       = _first_text_multi(row, _SEL_GENRE)
        date_raw    = _first_text_multi(row, _SEL_DATE)
        release     = _first_text_multi(row, _SEL_RELEASE)

        if not artist and not title_text:
            return None

        # Combine title + version into a single canonical title string.
        # Traxsource often stores them separately; we reunite them so the
        # matcher can compare "Track (Original Mix)" against API results.
        title = _combine_title_version(title_text, version)

        # Normalise date — Traxsource sometimes returns "01 Jan 2024"
        release_date = _parse_date(date_raw) if date_raw else None

        return EnrichmentCandidate(
            source       = "traxsource",
            artist       = artist or None,
            title        = title or None,
            album        = release or None,
            label        = label or None,
            isrc         = None,   # Traxsource does not expose ISRCs on search pages
            release_date = release_date,
            genre        = genre or None,
            raw          = {},     # not storing raw HTML
        )

    except Exception as exc:
        log.debug("Traxsource: error parsing row: %s", exc)
        return None


# ---------------------------------------------------------------------------
# DOM helpers
# ---------------------------------------------------------------------------

def _first_text(row: "Tag", selector: str, join: str = " ") -> str:
    """Extract text from all elements matching selector, joined by `join`."""
    nodes = row.select(selector)
    texts = [n.get_text(strip=True) for n in nodes if n.get_text(strip=True)]
    return join.join(texts)


def _first_text_multi(row: "Tag", selectors: List[str]) -> str:
    """Try each selector in turn; return first non-empty text found."""
    for sel in selectors:
        nodes = row.select(sel)
        for node in nodes:
            text = node.get_text(strip=True)
            if text:
                return text
    return ""


def _combine_title_version(title: str, version: str) -> str:
    """
    Merge title and version into a single string.

    Examples:
        ("Drive", "Original Mix")   → "Drive (Original Mix)"
        ("Drive", "(Original Mix)") → "Drive (Original Mix)"
        ("Drive (Original Mix)", "") → "Drive (Original Mix)"
        ("Drive", "")               → "Drive"
    """
    if not version:
        return title
    # Normalise version bracket: ensure it is wrapped in parentheses
    v = version.strip()
    if not (v.startswith("(") and v.endswith(")")):
        v = f"({v})"
    # Avoid double-appending when title already ends with the version
    if title.strip().endswith(v):
        return title.strip()
    return f"{title.strip()} {v}".strip()


_DATE_MONTHS = {
    "jan": "01", "feb": "02", "mar": "03", "apr": "04",
    "may": "05", "jun": "06", "jul": "07", "aug": "08",
    "sep": "09", "oct": "10", "nov": "11", "dec": "12",
}

_RE_DMY = re.compile(
    r"(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})"
)  # "15 Apr 2024" or "1 Apr 2024"


def _parse_date(raw: str) -> Optional[str]:
    """
    Normalise a Traxsource date string to ISO-8601 (YYYY-MM-DD).

    Handles:
        "2024-04-15"  → "2024-04-15"  (already ISO)
        "15 Apr 2024" → "2024-04-15"  (month-name format)
        "Apr 2024"    → "2024-04"     (partial)
        Other formats → returned as-is (already a reasonable fallback)
    """
    raw = raw.strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", raw):
        return raw
    if re.match(r"^\d{4}-\d{2}$", raw):
        return raw
    if re.match(r"^\d{4}$", raw):
        return raw

    # "15 Apr 2024"
    m = _RE_DMY.match(raw)
    if m:
        day, mon, year = m.groups()
        mon_num = _DATE_MONTHS.get(mon.lower())
        if mon_num:
            return f"{year}-{mon_num}-{int(day):02d}"

    # "Apr 2024" partial
    parts = raw.split()
    if len(parts) == 2 and parts[0][:3].lower() in _DATE_MONTHS:
        mon_num = _DATE_MONTHS[parts[0][:3].lower()]
        return f"{parts[1]}-{mon_num}"

    return raw  # return raw rather than None so the caller can still log it


# ---------------------------------------------------------------------------
# CLI self-test (python3 -m intelligence.enrichment.traxsource_lookup)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s  %(message)s")
    query_artist = sys.argv[1] if len(sys.argv) > 1 else "Black Coffee"
    query_title  = sys.argv[2] if len(sys.argv) > 2 else "Drive"
    client = TraxsourceClient()
    results = client.search_by_artist_title(query_artist, query_title)
    if not results:
        print("No results.")
    else:
        for i, c in enumerate(results, 1):
            print(f"\n[{i}] {c.artist} — {c.title}")
            print(f"    label   : {c.label}")
            print(f"    genre   : {c.genre}")
            print(f"    album   : {c.album}")
            print(f"    released: {c.release_date}")
