from __future__ import annotations

from pathlib import Path
from urllib.robotparser import RobotFileParser
from urllib.parse import urlparse
import hashlib
import time

import requests


USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 "
    "HybridLabelScraper/1.0"
)


class HttpClient:
    def __init__(self, cache_dir: Path, delay: float = 2.0, timeout: int = 30):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})
        self._robots: dict[str, RobotFileParser] = {}
        self._last_request_at: dict[str, float] = {}

    def _cache_key(self, url: str) -> Path:
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()}.html"

    def _respect_delay(self, host: str) -> None:
        last = self._last_request_at.get(host)
        if last is None:
            return
        elapsed = time.time() - last
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)

    def allowed_by_robots(self, url: str) -> bool:
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{base}/robots.txt"
        if base not in self._robots:
            rp = RobotFileParser()
            try:
                rp.set_url(robots_url)
                rp.read()
            except Exception:
                rp = RobotFileParser()
            self._robots[base] = rp
        rp = self._robots[base]
        try:
            return rp.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    def get(self, url: str, use_cache: bool = True) -> str:
        cache_file = self._cache_key(url)
        if use_cache and cache_file.exists():
            return cache_file.read_text(encoding="utf-8", errors="ignore")

        if not self.allowed_by_robots(url):
            raise PermissionError(f"Blocked by robots.txt: {url}")

        parsed = urlparse(url)
        self._respect_delay(parsed.netloc)
        resp = self.session.get(url, timeout=self.timeout)
        self._last_request_at[parsed.netloc] = time.time()
        resp.raise_for_status()
        text = resp.text
        cache_file.write_text(text, encoding="utf-8")
        return text
