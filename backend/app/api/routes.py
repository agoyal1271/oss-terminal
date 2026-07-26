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

router = APIRouter()


def _resolve_or_404(ticker: str) -> dict:
    row = tickers_ingest.resolve(ticker)
    if not row:
        raise HTTPException(status_code=404, detail=f"Unknown ticker '{ticker}'. Try /api/search?q=... first.")
    return row


@router.get("/search")
def search(q: str = Query(..., min_length=1), limit: int = 10):
    return {"results": tickers_ingest.search(q, limit=limit)}


@router.get("/companies/{ticker}")
def company_overview(ticker: str):
    row = _resolve_or_404(ticker)
    try:
        profile = filings_ingest.get_company_profile(row["cik_str"])
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "ticker": row["ticker"],
        "cik": row["cik"],
        "cik_str": row["cik_str"],
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
    _resolve_or_404(ticker)  # validate against SEC universe even though price comes from Yahoo
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
    row = _resolve_or_404(ticker)
    try:
        return options_ingest.get_options_chain(row["ticker"], expiration)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/options/term-structure")
def company_options_term_structure(ticker: str, max_expirations: int = 8):
    row = _resolve_or_404(ticker)
    try:
        return options_ingest.get_iv_term_structure(row["ticker"], max_expirations=max_expirations)
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/companies/{ticker}/options/iv-rank")
def company_options_iv_rank(ticker: str):
    row = _resolve_or_404(ticker)
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
