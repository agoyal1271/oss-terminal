from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.core.http_cache import UpstreamError
from app.ingest import tickers as tickers_ingest
from app.ingest import financials as financials_ingest
from app.ingest import prices as prices_ingest
from app.ingest import filings as filings_ingest
from app.ingest import ownership as ownership_ingest
from app.ingest import filing_content as filing_content_ingest
from app.ingest import options as options_ingest
from app.signals import detect as detect_signals
from app.signals import two_week

router = APIRouter()


def _resolve_or_404(ticker: str) -> dict:
    """SEC-only resolution -- for routes that genuinely need a CIK
    (financials, filings, ownership). If the ticker IS a real Yahoo
    symbol just not an SEC operating-company filer (typically an ETF),
    say so explicitly rather than a generic "unknown ticker"."""
    row = tickers_ingest.resolve(ticker)
    if row:
        return row
    try:
        yahoo_row = prices_ingest.resolve_via_yahoo(ticker)
    except UpstreamError:
        yahoo_row = None
    if yahoo_row:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{ticker}' isn't covered here -- it's not an SEC operating-company filer "
                f"(likely an ETF: {yahoo_row['title']}), so financials/filings/ownership don't apply. "
                f"Price and options data ARE available at /api/companies/{ticker}/prices and /options."
            ),
        )
    raise HTTPException(status_code=404, detail=f"Unknown ticker '{ticker}'. Try /api/search?q=... first.")


def _resolve_market_or_404(ticker: str) -> dict:
    """For routes that only need price/options data (Yahoo), not SEC
    filings -- tries SEC resolution first (keeps the real name/CIK for
    equities), falls back to a lightweight Yahoo existence+name check so
    ETFs like SOXL/NUGT resolve too. Yahoo-sourced rows have cik=None;
    routes must not assume cik_str is present."""
    row = tickers_ingest.resolve(ticker)
    if row:
        return row
    try:
        yahoo_row = prices_ingest.resolve_via_yahoo(ticker)
    except UpstreamError:
        yahoo_row = None
    if not yahoo_row:
        raise HTTPException(status_code=404, detail=f"Unknown ticker '{ticker}'. Try /api/search?q=... first.")
    return yahoo_row


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 10):
    results = tickers_ingest.search(q, limit=limit)

    # SEC's universe doesn't include ETFs (they're registered funds, not
    # operating-company filers), so a query like "SOXL" or "NUGT" comes back
    # empty even though price/options data exists for them via Yahoo. If the
    # query looks like a bare ticker and isn't already in the SEC results,
    # try resolving it directly against Yahoo so ETFs are searchable too.
    query_ticker = q.strip().upper()
    if query_ticker.isalpha() and len(query_ticker) <= 5 and not any(r["ticker"] == query_ticker for r in results):
        try:
            yahoo_row = prices_ingest.resolve_via_yahoo(query_ticker)
        except UpstreamError:
            yahoo_row = None
        if yahoo_row:
            results = [
                {
                    "cik": yahoo_row["cik"],
                    "cik_str": yahoo_row["cik_str"] or yahoo_row["ticker"],
                    "ticker": yahoo_row["ticker"],
                    "title": yahoo_row["title"],
                },
                *results,
            ][:limit]

    return {"results": results}


@router.get("/companies/{ticker}")
def company_overview(ticker: str):
    row = _resolve_market_or_404(ticker)
    if not row.get("cik_str"):
        # Yahoo-only resolution (typically an ETF) -- SEC's company-facts
        # profile doesn't exist for these. Return what IS available
        # (name, instrument type) instead of 404ing outright; callers that
        # need financials/filings/ownership get an explicit explanation
        # from those routes' own _resolve_or_404, not a crash here.
        #
        # Fill every field the frontend's CompanyProfile type expects
        # (even ones that don't apply) rather than omitting them -- the
        # frontend does `profile.exchanges.join(...)` unconditionally, and
        # a missing key there is `undefined`, not an empty array, which
        # crashes the page. Confirmed live: this was the exact cause of
        # the options page showing broken for an ETF.
        instrument_type = row.get("instrument_type") or "ETF"
        return {
            "ticker": row["ticker"],
            "name": row.get("title"),
            "cik": None,
            "cik_str": None,
            "sic": None,
            "sic_description": instrument_type,
            "exchanges": [],
            "ein": None,
            "category": f"{instrument_type} -- not an SEC operating-company filer",
            "fiscal_year_end": None,
            "website": None,
            "investor_website": None,
            "address": None,
            "instrument_type": instrument_type,
            "sec_coverage": False,
        }
    try:
        profile = filings_ingest.get_company_profile(row["cik_str"])
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ticker": row["ticker"],
        "cik": row["cik"],
        "cik_str": row["cik_str"],
        "sec_coverage": True,
        **profile,
    }


@router.get("/companies/{ticker}/financials")
def company_financials(ticker: str):
    row = _resolve_or_404(ticker)
    try:
        data = financials_ingest.get_normalized_financials(row["cik_str"])
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ticker": row["ticker"], **data}


@router.get("/companies/{ticker}/prices")
def company_prices(ticker: str, range: str = Query("1y", alias="range")):
    _resolve_market_or_404(ticker)  # SEC OR Yahoo -- price data itself is Yahoo either way
    try:
        return prices_ingest.get_price_history(ticker, range_key=range)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/filings")
def company_filings(ticker: str, limit: int = 25, forms: str | None = None):
    row = _resolve_or_404(ticker)
    form_list = [f.strip() for f in forms.split(",")] if forms else None
    try:
        return {"ticker": row["ticker"], "filings": filings_ingest.get_recent_filings(row["cik_str"], limit=limit, forms=form_list)}
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/companies/{ticker}/ownership")
def company_ownership(ticker: str):
    row = _resolve_or_404(ticker)
    try:
        data = ownership_ingest.get_institutional_ownership(row["title"])
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ticker": row["ticker"], **data}


@router.get("/companies/{ticker}/options")
def company_options(ticker: str, expiration: int | None = None):
    row = _resolve_market_or_404(ticker)
    try:
        return options_ingest.get_options_chain(row["ticker"], expiration)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/options/term-structure")
def company_options_term_structure(ticker: str, max_expirations: int = 8):
    row = _resolve_market_or_404(ticker)
    try:
        return options_ingest.get_iv_term_structure(row["ticker"], max_expirations=max_expirations)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/options/two-week")
def company_options_two_week(ticker: str, horizon_days: int = 14):
    """Every expiration inside a two-week horizon plus the deterministic
    up/down/sideways evidence tally -- what scripts/ask.py turns into a
    ready-to-run local-LLM prompt. Public (not secret-gated) like the rest
    of the /companies routes; it reads the same upstream data they do."""
    row = _resolve_market_or_404(ticker)
    try:
        return two_week.get_two_week_window(row["ticker"], horizon_days=horizon_days)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/options/iv-rank")
def company_options_iv_rank(ticker: str):
    row = _resolve_market_or_404(ticker)
    return options_ingest.get_iv_rank(row["ticker"])


@router.get("/internal/iv-snapshot")
def internal_iv_snapshot(secret: str | None = None):
    """Called once daily by .github/workflows/iv-snapshot.yml, not by the
    frontend. Returns today's ATM IV for the tracked watchlist so the
    Action can append it to data/iv-history/{ticker}.json."""
    if settings.iv_snapshot_secret and secret != settings.iv_snapshot_secret:
        raise HTTPException(status_code=403, detail="invalid or missing secret")
    return {"snapshots": options_ingest.get_watchlist_snapshot()}


@router.get("/internal/scan/{ticker}")
def internal_scan(ticker: str, secret: str | None = None):
    """Called once daily by .github/workflows/daily-scan.yml, not by the
    frontend. Per-ticker (not batch) so one bad ticker can't blow a whole
    run's Vercel function timeout. Returns findings for TODAY plus the
    state the caller should persist for tomorrow's diff -- see
    app/signals/detect.py for the full transition logic."""
    if settings.iv_snapshot_secret and secret != settings.iv_snapshot_secret:
        raise HTTPException(status_code=403, detail="invalid or missing secret")
    row = _resolve_or_404(ticker)
    return detect_signals.scan_ticker(row["ticker"])


@router.get("/filings/highlights")
def filing_highlights(url: str, form: str, items: str | None = None):
    item_list = [x.strip() for x in items.split(",")] if items else []
    try:
        return filing_content_ingest.get_filing_highlights(url, form, item_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
