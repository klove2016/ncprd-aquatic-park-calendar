"""Polite HTTP fetching: identifying user-agent, timeouts, retries with backoff, rate limiting."""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class Fetched:
    requested_url: str
    url: str  # final URL after redirects
    status: int
    content_type: str
    content: bytes
    fetched_at: datetime

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")


class Fetcher:
    def __init__(self, http_cfg: dict):
        self.cfg = http_cfg
        self.session = requests.Session()
        retry = Retry(
            total=int(http_cfg["retries"]),
            backoff_factor=float(http_cfg["backoff_factor"]),
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET", "HEAD"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers["User-Agent"] = http_cfg["user_agent"]
        self._last_request = 0.0

    def get(self, url: str, accept: str = "*/*") -> Fetched:
        wait = float(self.cfg["min_interval_seconds"]) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()
        try:
            resp = self.session.get(
                url,
                headers={"Accept": accept},
                timeout=(float(self.cfg["connect_timeout"]), float(self.cfg["read_timeout"])),
                allow_redirects=True,
                stream=True,
            )
        except requests.RequestException as e:
            raise FetchError(f"request to {url} failed: {e}") from e
        with resp:
            if resp.status_code != 200:
                raise FetchError(f"{url} returned HTTP {resp.status_code}")
            max_bytes = int(self.cfg["max_bytes"])
            chunks, size = [], 0
            for chunk in resp.iter_content(64 * 1024):
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(f"{url} exceeded {max_bytes} bytes")
                chunks.append(chunk)
            return Fetched(
                requested_url=url,
                url=resp.url,
                status=resp.status_code,
                content_type=resp.headers.get("Content-Type", "").split(";")[0].strip().lower(),
                content=b"".join(chunks),
                fetched_at=datetime.now(timezone.utc),
            )
