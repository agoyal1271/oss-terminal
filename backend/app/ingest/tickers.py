"""Ticker <-> CIK universe, sourced from SEC's own canonical mapping file.

This is the full set of ~10,400 entities that file with the SEC (not just
listed operating companies -- also funds, SPAC shells, etc.) and is the
cheapest reliable way to resolve a ticker to a CIK, which every other SEC
endpoint requires.
"""

from __future__ import annotations

from app.config import settings
from app.core.http_cache import cached_get_json

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"

_cache: dict[str, dict] | None = None  # ticker -> {cik, ticker, title}


def _load() -> dict[str, dict]:
    global _cache
    if _cache is not None:
        return _cache

    raw = cached_get_json(
        namespace="sec_tickers",
        url=TICKERS_URL,
        ttl=settings.ttl_ticker_universe,
        headers={"User-Agent": settings.sec_user_agent},
    )
    by_ticker: dict[str, dict] = {}
    for row in raw.values():
        ticker = row["ticker"].upper()
        by_ticker[ticker] = {
            "cik": row["cik_str"],
            "cik_str": str(row["cik_str"]).zfill(10),
            "ticker": ticker,
            "title": row["title"],
        }
    _cache = by_ticker
    return _cache


def resolve(ticker: str) -> dict | None:
    return _load().get(ticker.upper())


def search(query: str, limit: int = 10) -> list[dict]:
    q = query.strip().upper()
    if not q:
        return []
    universe = _load()

    starts_with = []
    contains = []
    for ticker, row in universe.items():
        if ticker.startswith(q):
            starts_with.append(row)
        elif q in row["title"].upper():
            contains.append(row)

    starts_with.sort(key=lambda r: len(r["ticker"]))
    results = starts_with + contains
    return results[:limit]
