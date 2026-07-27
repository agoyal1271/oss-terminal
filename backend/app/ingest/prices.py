"""EOD/intraday-ish price history via Yahoo Finance's unofficial chart endpoint.

No API key, no published rate limit, widely used by open-source tools
(e.g. yfinance) -- but it's an unofficial endpoint Yahoo could change or
block at any time, and its use for anything beyond personal/research
purposes is a legal gray area. Swap this module out for a licensed feed
(Tiingo, Polygon, IEX) if this project is ever used commercially.
"""

from __future__ import annotations

from app.config import settings
from app.core.http_cache import cached_get_json

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

RANGE_MAP = {
    "1m": ("1mo", "1d"),
    "6m": ("6mo", "1d"),
    "1y": ("1y", "1d"),
    "2y": ("2y", "1d"),  # daily resolution, long enough to seed a 200-day SMA
    "5y": ("5y", "1wk"),
    "max": ("max", "1mo"),
}


def resolve_via_yahoo(ticker: str) -> dict | None:
    """Lightweight existence + display-name check via this same keyless
    chart endpoint -- no SEC dependency, which is exactly why it exists:
    SEC's company_tickers.json only covers operating-company 10-K/10-Q
    filers. Most ETFs (SOXL, NUGT, ...) are registered investment
    companies filing under a different regime entirely and are absent
    from that file (confirmed live -- /api/search returns zero results
    for either), even though Yahoo has full price/options data for them.
    Used as a fallback so price/options endpoints work for ETFs while
    financials/filings/ownership (which genuinely need a CIK) still don't
    pretend to.
    """
    url = CHART_URL.format(symbol=ticker.upper())
    data = cached_get_json(
        namespace="yahoo_prices",
        url=url,
        ttl=settings.ttl_prices,
        headers={"User-Agent": "Mozilla/5.0 (compatible; OSS-Terminal/0.1)"},
        params={"range": "5d", "interval": "1d"},
    )
    result = (data.get("chart") or {}).get("result") or []
    if not result:
        return None
    meta = result[0].get("meta", {})
    symbol = meta.get("symbol")
    if not symbol:
        return None
    return {
        "ticker": symbol.upper(),
        "cik": None,
        "cik_str": None,
        "title": meta.get("longName") or meta.get("shortName") or symbol,
        "instrument_type": meta.get("instrumentType"),
        "source": "yahoo",
    }


def get_price_history(ticker: str, range_key: str = "1y") -> dict:
    yahoo_range, interval = RANGE_MAP.get(range_key, RANGE_MAP["1y"])
    url = CHART_URL.format(symbol=ticker.upper())

    data = cached_get_json(
        namespace="yahoo_prices",
        url=url,
        ttl=settings.ttl_prices,
        headers={"User-Agent": "Mozilla/5.0 (compatible; OSS-Terminal/0.1)"},
        params={"range": yahoo_range, "interval": interval},
    )

    result = (data.get("chart") or {}).get("result") or []
    if not result:
        error = (data.get("chart") or {}).get("error")
        raise ValueError(f"no price data for {ticker}: {error}")

    r = result[0]
    timestamps = r.get("timestamp", [])
    quote = r["indicators"]["quote"][0]
    closes = quote.get("close", [])
    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    volumes = quote.get("volume", [])

    points = []
    for i, ts in enumerate(timestamps):
        close = closes[i] if i < len(closes) else None
        if close is None:
            continue
        points.append({
            "t": ts,
            "date": _ts_to_date(ts),
            "open": opens[i] if i < len(opens) else None,
            "high": highs[i] if i < len(highs) else None,
            "low": lows[i] if i < len(lows) else None,
            "close": close,
            "volume": volumes[i] if i < len(volumes) else None,
        })

    meta = r.get("meta", {})
    return {
        "symbol": meta.get("symbol", ticker.upper()),
        "currency": meta.get("currency"),
        "exchange": meta.get("exchangeName"),
        "regular_market_price": meta.get("regularMarketPrice"),
        "fifty_two_week_high": meta.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": meta.get("fiftyTwoWeekLow"),
        "points": points,
    }


def _ts_to_date(ts: int) -> str:
    import datetime
    return datetime.datetime.utcfromtimestamp(ts).date().isoformat()
