"""Fetch SEC XBRL company facts and normalize them into a standard annual
statement, regardless of which specific GAAP tags a given filer used.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.config import settings
from app.core.http_cache import cached_get_json, UpstreamError
from app.core.metric_map import ALL_METRICS, FLOW_METRICS

COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"

MAX_YEARS = 10


def fetch_company_facts(cik_str: str) -> dict:
    url = COMPANY_FACTS_URL.format(cik=cik_str)
    return cached_get_json(
        namespace="sec_facts",
        url=url,
        ttl=settings.ttl_company_facts,
        headers={"User-Agent": settings.sec_user_agent},
        timeout=30.0,
    )


def _extract_series(us_gaap: dict, tag_candidates: list[str], is_flow: bool) -> tuple[str | None, str | None, dict[str, dict]]:
    """Try each candidate tag in order; return (unit, tag_used, {fiscal_year_end: fact})."""
    for tag in tag_candidates:
        tag_data = us_gaap.get(tag)
        if not tag_data:
            continue
        units = tag_data.get("units", {})
        for unit_key, items in units.items():
            annual = [i for i in items if i.get("form") in ("10-K", "10-K/A") and i.get("fp") == "FY" and i.get("end")]

            if is_flow:
                filtered = []
                for item in annual:
                    start, end = item.get("start"), item.get("end")
                    if not start:
                        continue
                    try:
                        days = (date.fromisoformat(end) - date.fromisoformat(start)).days
                    except ValueError:
                        continue
                    if 300 <= days <= 400:  # full fiscal year, not a stub period
                        filtered.append(item)
                annual = filtered

            if not annual:
                continue

            # Restatements: multiple filings can report the same fiscal year end.
            # Keep whichever was filed most recently (latest-known value).
            by_end: dict[str, dict] = {}
            for item in annual:
                end = item["end"]
                prev = by_end.get(end)
                if prev is None or item.get("filed", "") >= prev.get("filed", ""):
                    by_end[end] = item

            if by_end:
                return unit_key, tag, by_end
    return None, None, {}


def normalize_company_facts(facts: dict) -> dict:
    us_gaap = facts.get("facts", {}).get("us-gaap", {})

    per_metric: dict[str, dict[str, dict]] = {}
    metric_sources: dict[str, str] = {}
    metric_units: dict[str, str] = {}

    for metric, (_, tag_candidates) in ALL_METRICS.items():
        is_flow = metric in FLOW_METRICS
        unit, tag, series = _extract_series(us_gaap, tag_candidates, is_flow)
        if series:
            per_metric[metric] = series
            metric_sources[metric] = tag
            metric_units[metric] = unit

    all_ends = sorted({end for series in per_metric.values() for end in series}, reverse=True)
    all_ends = all_ends[:MAX_YEARS]

    annual: list[dict[str, Any]] = []
    for end in all_ends:
        metrics_row: dict[str, float | None] = {}
        filed, form = None, None
        for metric, series in per_metric.items():
            item = series.get(end)
            if item:
                metrics_row[metric] = item["val"]
                # Prefer the filing with the latest `filed` date per metric: for
                # flow metrics reported as comparatives across multiple 10-Ks,
                # this favors the most recently restated figure.
                if filed is None or item.get("filed", "") > filed:
                    filed = item.get("filed")
                    form = item.get("form")
            else:
                metrics_row[metric] = None

        # SEC's own `fy` field is the filing's fiscal-year *focus*, not the
        # period's fiscal year (a 10-K for FY2025 tags its FY2023/FY2024
        # comparatives with fy=2025 too). Derive the human fiscal year from
        # the period end date instead, which is unambiguous.
        fy = date.fromisoformat(end).year

        derived = _derive_ratios(metrics_row)

        annual.append({
            "fiscal_year_end": end,
            "fy": fy,
            "filed": filed,
            "form": form,
            "metrics": metrics_row,
            "derived": derived,
        })

    return {
        "entity_name": facts.get("entityName"),
        "cik": facts.get("cik"),
        "annual": annual,
        "metric_sources": metric_sources,
        "metric_units": metric_units,
    }


def _safe_div(a: float | None, b: float | None) -> float | None:
    if a is None or b in (None, 0):
        return None
    return a / b


def _derive_ratios(m: dict[str, float | None]) -> dict[str, float | None]:
    total_debt = None
    if m.get("long_term_debt") is not None or m.get("short_term_debt") is not None:
        total_debt = (m.get("long_term_debt") or 0) + (m.get("short_term_debt") or 0)

    fcf = None
    if m.get("operating_cash_flow") is not None and m.get("capital_expenditures") is not None:
        fcf = m["operating_cash_flow"] - abs(m["capital_expenditures"])

    return {
        "gross_margin": _safe_div(m.get("gross_profit"), m.get("revenue")),
        "operating_margin": _safe_div(m.get("operating_income"), m.get("revenue")),
        "net_margin": _safe_div(m.get("net_income"), m.get("revenue")),
        "return_on_equity": _safe_div(m.get("net_income"), m.get("stockholders_equity")),
        "return_on_assets": _safe_div(m.get("net_income"), m.get("total_assets")),
        "current_ratio": _safe_div(m.get("current_assets"), m.get("current_liabilities")),
        "debt_to_equity": _safe_div(total_debt, m.get("stockholders_equity")),
        "free_cash_flow": fcf,
    }


def get_normalized_financials(cik_str: str) -> dict:
    facts = fetch_company_facts(cik_str)
    return normalize_company_facts(facts)
