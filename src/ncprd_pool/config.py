"""Load and type the TOML configuration."""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Category:
    key: str
    title: str
    row_pattern: re.Pattern
    keywords: tuple[str, ...]
    feed: str


@dataclass(frozen=True)
class Feed:
    key: str
    file: str
    name: str


@dataclass(frozen=True)
class Config:
    tz: ZoneInfo
    landing_url: str
    facility_name: str
    location: str
    horizon_days: int
    max_validity_days: int
    stale_after_hours: int
    http: dict
    discovery: dict
    validation: dict
    categories: tuple[Category, ...]
    feeds: tuple[Feed, ...]

    @property
    def earliest_start(self) -> time:
        return time.fromisoformat(self.validation["earliest_start"])

    @property
    def latest_end(self) -> time:
        return time.fromisoformat(self.validation["latest_end"])

    def category(self, key: str) -> Category:
        return next(c for c in self.categories if c.key == key)


def load_config(path: str | Path) -> Config:
    raw = tomllib.loads(Path(path).read_text())
    categories = tuple(
        Category(
            key=c["key"],
            title=c["title"],
            row_pattern=re.compile(c["row_pattern"], re.I),
            keywords=tuple(k.lower() for k in c["keywords"]),
            feed=c["feed"],
        )
        for c in raw["categories"]
    )
    feeds = tuple(Feed(**f) for f in raw["feeds"])
    feed_keys = {f.key for f in feeds}
    for c in categories:
        if c.feed not in feed_keys:
            raise ValueError(f"category {c.key} references unknown feed {c.feed}")
    if "all" not in feed_keys:
        raise ValueError("config must define an 'all' feed")
    return Config(
        tz=ZoneInfo(raw["timezone"]),
        landing_url=raw["landing_url"],
        facility_name=raw["facility_name"],
        location=raw["location"],
        horizon_days=int(raw["horizon_days"]),
        max_validity_days=int(raw["max_validity_days"]),
        stale_after_hours=int(raw["stale_after_hours"]),
        http=raw["http"],
        discovery=raw["discovery"],
        validation=raw["validation"],
        categories=categories,
        feeds=feeds,
    )
