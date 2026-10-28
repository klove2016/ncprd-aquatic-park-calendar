from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from ncprd_pool.config import load_config
from ncprd_pool.fetch import FetchError, Fetched
from ncprd_pool.parse import extract_pdf, interpret

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
FALL_URL = "https://cms-assets.ncprd.org/Fall_2026_Discovery_Guide_NCAP_Hours_Schedule_a56507b7f3.pdf"
LANDING_URL = "https://ncprd.org/aquatic-park"
TODAY = date(2026, 10, 28)
NOW = datetime(2026, 10, 28, 13, 17, tzinfo=timezone.utc)


@pytest.fixture(scope="session")
def cfg():
    return load_config(ROOT / "config" / "config.toml")


@pytest.fixture(scope="session")
def fall_pdf() -> bytes:
    return (FIXTURES / "fall_2026_ncap_schedule.pdf").read_bytes()


@pytest.fixture(scope="session")
def landing_html() -> str:
    return (FIXTURES / "landing_fall_2026.html").read_text()


@pytest.fixture(scope="session")
def fall_content(fall_pdf):
    return extract_pdf(fall_pdf)


@pytest.fixture(scope="session")
def fall_schedule(fall_content, cfg):
    return interpret(fall_content, cfg, FALL_URL, TODAY)


class FakeFetcher:
    """Maps URL -> (content_type, bytes). Unknown URLs fail like a network error."""

    def __init__(self, routes: dict[str, tuple[str, bytes]]):
        self.routes = dict(routes)
        self.calls: list[str] = []

    def get(self, url, accept="*/*"):
        self.calls.append(url)
        if url not in self.routes:
            raise FetchError(f"{url} returned HTTP 404")
        ctype, body = self.routes[url]
        return Fetched(url, url, 200, ctype, body, NOW)


@pytest.fixture
def make_fetcher(landing_html, fall_pdf):
    def _make(landing=None, pdf_url=FALL_URL, pdf=None, pdf_type="application/pdf"):
        html = landing if landing is not None else landing_html
        return FakeFetcher({
            LANDING_URL: ("text/html", html.encode()),
            pdf_url: (pdf_type, fall_pdf if pdf is None else pdf),
        })
    return _make
