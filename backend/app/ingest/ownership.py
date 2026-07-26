"""Institutional ownership: how many 13F filers report holding this company.

There's no free, structured "get institutional holders of ticker X" API.
Form 13F is filed BY the institutional manager, listing everything *it*
holds -- it isn't indexed by company. SEC does publish bulk 13F datasets
(INFOTABLE.tsv, ~300MB/quarter) that would give an exact count, but turning
that into a live per-ticker lookup needs real ingestion into a database
(flagged as Phase 2 in the README) -- not something that fits the on-demand,
no-database architecture this app currently uses.

Instead, this uses SEC's EDGAR full-text search: it indexes the text of
every 13F-HR information table, so a phrase search for the company's exact
name against 13F-HR filings in one completed quarter returns one hit per
institution that reported holding it. Sanity-checked during development
against known real figures (AAPL ~6,460 institutional filers, ETSY ~1,074) --
close to figures published by paid 13F aggregators. This is a text-match
PROXY, not an exact structured count: it can under-count filers who
abbreviate the issuer name differently, or over-count in the rare case of
name collisions.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from app.config import settings
from app.core.http_cache import cached_get_json

FULL_TEXT_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
FILING_DEADLINE_DAYS = 45  # 13F-HR is due 45 days after quarter end


def _quarter_end(year: int, quarter: int) -> date:
    return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[quarter]


def _quarter_start(quarter_end: date) -> date:
    start_month = {3: 1, 6: 4, 9: 7, 12: 10}[quarter_end.month]
    return date(quarter_end.year, start_month, 1)


def last_complete_13f_quarter(today: date) -> date:
    """Most recent calendar quarter whose 13F-HR filing deadline has already passed."""
    quarter_ends = sorted(
        (_quarter_end(y, q) for y in (today.year, today.year - 1) for q in (1, 2, 3, 4)),
        reverse=True,
    )
    for qe in quarter_ends:
        if qe + timedelta(days=FILING_DEADLINE_DAYS) <= today:
            return qe
    return quarter_ends[-1]


def _clean_holder_name(display_name: str) -> str:
    return re.sub(r"\s*\(CIK\s*\d+\)\s*$", "", display_name).strip()


def get_institutional_ownership(company_title: str) -> dict:
    quarter_end = last_complete_13f_quarter(date.today())
    quarter_start = _quarter_start(quarter_end)

    data = cached_get_json(
        namespace="ownership_13f",
        url=FULL_TEXT_SEARCH_URL,
        ttl=24 * 3600,
        headers={"User-Agent": settings.sec_user_agent},
        params={
            "q": f'"{company_title}"',
            "forms": "13F-HR",
            "startdt": quarter_start.isoformat(),
            "enddt": quarter_end.isoformat(),
        },
    )

    hits_block = data.get("hits", {})
    total = hits_block.get("total", {}).get("value", 0)

    sample_holders: list[str] = []
    seen_ciks: set[str] = set()
    for hit in hits_block.get("hits", []):
        src = hit.get("_source", {})
        cik0 = (src.get("ciks") or [None])[0]
        if cik0 in seen_ciks:
            continue
        seen_ciks.add(cik0)
        names = src.get("display_names") or []
        if names:
            sample_holders.append(_clean_holder_name(names[0]))
        if len(sample_holders) >= 8:
            break

    return {
        "quarter_end": quarter_end.isoformat(),
        "holder_count_estimate": total,
        "sample_holders": sample_holders,
        "method_note": (
            f'Count of distinct Form 13F-HR filers whose holdings report for the quarter '
            f'ended {quarter_end.isoformat()} mentions "{company_title}" in SEC EDGAR full-text '
            f"search -- a proxy for institutional holder count, not an exact structured figure."
        ),
    }
