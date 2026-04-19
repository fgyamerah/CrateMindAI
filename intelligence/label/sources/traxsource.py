from __future__ import annotations

import re
from urllib.parse import quote_plus, urljoin

from bs4 import BeautifulSoup

from .base import HttpClient
from ..utils import unique_preserve


class TraxsourceSource:
    source_name = "traxsource"
    site_root = "https://www.traxsource.com"

    def __init__(self, client: HttpClient):
        self.client = client

    def search_url(self, label_name: str) -> str:
        return f"{self.site_root}/search/tracks?term={quote_plus(label_name)}"

    def extract_candidates(self, html: str, base_url: str) -> list[dict]:
        soup = BeautifulSoup(html, "html.parser")
        candidates = []
        for a in soup.select("a[href*='/label/']"):
            href = a.get("href", "").strip()
            text = a.get_text(" ", strip=True)
            if not href or not text:
                continue
            full = urljoin(base_url, href)
            m = re.search(r"/label/(\\d+)/", full)
            candidates.append({
                "label_name": text,
                "url": full,
                "traxsource_id": m.group(1) if m else None,
            })

        seen = set()
        out = []
        for item in candidates:
            if item["url"] not in seen:
                seen.add(item["url"])
                out.append(item)
        return out

    def enrich_label_page(self, url: str) -> dict:
        html = self.client.get(url)
        soup = BeautifulSoup(html, "html.parser")

        title = None
        meta_title = soup.find("meta", attrs={"property": "og:title"})
        if meta_title and meta_title.get("content"):
            title = meta_title["content"].strip()
        if not title:
            h1 = soup.find(["h1", "h2"])
            title = h1.get_text(" ", strip=True) if h1 else None

        genres = []
        subgenres = []
        text_blob = soup.get_text(" ", strip=True)
        for g in [
            "Afro House", "Afro Tech", "Amapiano", "Deep House", "Tech House",
            "Soulful House", "Organic House", "Melodic House", "House",
        ]:
            if re.search(rf"\\b{re.escape(g)}\\b", text_blob, flags=re.I):
                genres.append(g)
                if g != "House":
                    subgenres.append(g)

        m = re.search(r"/label/(\\d+)/", url)
        return {
            "label_name": title,
            "traxsource_url": url,
            "traxsource_id": m.group(1) if m else None,
            "genres": unique_preserve(genres),
            "subgenres": unique_preserve(subgenres),
            "source_pages": [url],
            "verification_score": 0.7 if title else 0.4,
        }
