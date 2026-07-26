from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.core.http_cache import UpstreamError
from app.ingest import tickers as tickers_ingest
from app.ingest import financials as financials_ingest
from app.ingest import prices as prices_ingest
from app.ingest import filings as filings_ingest
from app.ingest import ownership as ownership_ingest
from app.ingest import filing_content as filing_content_ingest
from app.ingest import options as options_ingest

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


@router.get("/filings/highlights")
def filing_highlights(url: str, form: str, items: str | None = None):
    item_list = [x.strip() for x in items.split(",")] if items else []
    try:
        return filing_content_ingest.get_filing_highlights(url, form, item_list)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UpstreamError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
