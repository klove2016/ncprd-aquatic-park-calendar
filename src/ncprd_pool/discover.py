"""Find the currently linked drop-in schedule on the aquatic park landing page.

The PDF URL and filename change every season, so nothing here is hardcoded: each
link is scored by its anchor text and href, and the single best match wins.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urldefrag, urljoin

from bs4 import BeautifulSoup


class DiscoveryError(Exception):
    pass


@dataclass
class Candidate:
    url: str
    text: str
    score: int
    reasons: list[str] = field(default_factory=list)


def check_landing_page(html: str) -> None:
    """Reject error pages / unexpected content before trusting any links on it."""
    if not re.search(r"aquatic\s+park", html, re.I):
        raise DiscoveryError("landing page does not mention 'Aquatic Park'; refusing to trust it")
    if len(html) < 2000:
        raise DiscoveryError(f"landing page suspiciously small ({len(html)} bytes)")


def score_links(html: str, base_url: str, discovery_cfg: dict) -> list[Candidate]:
    soup = BeautifulSoup(html, "html.parser")
    text_rules = [(re.compile(r["pattern"], re.I), int(r["score"])) for r in discovery_cfg["text_rules"]]
    href_rules = [(re.compile(r["pattern"], re.I), int(r["score"])) for r in discovery_cfg["href_rules"]]
    best: dict[str, Candidate] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        url = urldefrag(urljoin(base_url, href)).url
        text = " ".join(a.get_text(" ").split())
        context = " ".join(filter(None, [text, a.get("title"), a.get("aria-label")]))
        score, reasons = 0, []
        for rx, pts in text_rules:
            if rx.search(context):
                score += pts
                reasons.append(f"text~/{rx.pattern}/{pts:+d}")
        for rx, pts in href_rules:
            if rx.search(url):
                score += pts
                reasons.append(f"href~/{rx.pattern}/{pts:+d}")
        prev = best.get(url)
        if prev is None or score > prev.score:
            best[url] = Candidate(url=url, text=text, score=score, reasons=reasons)
    return sorted(best.values(), key=lambda c: (-c.score, c.url))


def choose_schedule_link(html: str, base_url: str, discovery_cfg: dict) -> Candidate:
    check_landing_page(html)
    cands = score_links(html, base_url, discovery_cfg)
    min_score = int(discovery_cfg["min_score"])
    good = [c for c in cands if c.score >= min_score]
    if not good:
        top = ", ".join(f"{c.text!r}={c.score}" for c in cands[:3])
        raise DiscoveryError(f"no drop-in schedule link found on landing page (best: {top})")
    if len(good) > 1 and good[1].score == good[0].score:
        raise DiscoveryError(
            f"ambiguous schedule links: {good[0].url} and {good[1].url} both scored {good[0].score}"
        )
    return good[0]


def find_pdf_links(html: str, base_url: str, discovery_cfg: dict) -> Candidate:
    """Used when the landing link points at an intermediate HTML page rather than a PDF."""
    cands = [c for c in score_links(html, base_url, discovery_cfg) if re.search(r"\.pdf(\?|$)", c.url, re.I)]
    if not cands:
        raise DiscoveryError(f"intermediate page {base_url} links no PDF")
    if len(cands) > 1 and cands[1].score == cands[0].score:
        raise DiscoveryError(f"ambiguous PDF links on {base_url}")
    return cands[0]
