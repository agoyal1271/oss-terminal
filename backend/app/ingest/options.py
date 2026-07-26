"""Options chain via Yahoo Finance's unofficial endpoint.

Unlike the price-chart endpoint used elsewhere in this app, options requires
a session cookie plus a CSRF "crumb" token -- Yahoo tightened access to this
endpoint independently of the chart one. The session is fetched once per
backend process (module-level, so a warm serverless instance reuses it) and
refetched automatically if a request comes back 401 (the crumb/cookie pair
expired).
"""

from __future__ import annotations

import httpx

from app.core.http_cache import cached_call_json

OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
COOKIE_SEED_URL = "https://fc.yahoo.com"
YAHOO_UA = "Mozilla/5.0 (compatible; OSS-Terminal/0.1)"

_session: dict[str, str] | None = None


def _fetch_session() -> dict[str, str]:
    client = httpx.Client(headers={"User-Agent": YAHOO_UA}, timeout=10)
    client.get(COOKIE_SEED_URL)  # seeds a session cookie even though this itself 404s
    crumb = client.get(CRUMB_URL).text.strip()
    cookie_header = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
    return {"cookie": cookie_header, "crumb": crumb}


def _get_session() -> dict[str, str]:
    global _session
    if _session is None:
        _session = _fetch_session()
    return _session


def _request_chain(ticker: str, expiration: int | None, session: dict[str, str]) -> httpx.Response:
    params: dict[str, str | int] = {"crumb": session["crumb"]}
    if expiration:
        params["date"] = expiration
    return httpx.get(
        OPTIONS_URL.format(symbol=ticker),
        headers={"User-Agent": YAHOO_UA, "Cookie": session["cookie"]},
        params=params,
        timeout=15,
    )


def _fetch_chain_json(ticker: str, expiration: int | None) -> dict:
    global _session
    session = _get_session()
    resp = _request_chain(ticker, expiration, session)
    if resp.status_code == 401:
        session = _fetch_session()
        _session = session
        resp = _request_chain(ticker, expiration, session)
    resp.raise_for_status()
    return resp.json()


def _normalize_contract(c: dict) -> dict:
    return {
        "contract_symbol": c.get("contractSymbol"),
        "strike": c.get("strike"),
        "last_price": c.get("lastPrice"),
        "bid": c.get("bid"),
        "ask": c.get("ask"),
        "change": c.get("change"),
        "percent_change": c.get("percentChange"),
        "volume": c.get("volume") or 0,
        "open_interest": c.get("openInterest") or 0,
        "implied_volatility": c.get("impliedVolatility"),
        "in_the_money": c.get("inTheMoney", False),
    }


def get_options_chain(ticker: str, expiration: int | None = None) -> dict:
    cache_key = f"{ticker}:{expiration or 'nearest'}"
    data = cached_call_json(
        namespace="yahoo_options",
        key=cache_key,
        ttl=10 * 60,
        fetch_fn=lambda: _fetch_chain_json(ticker, expiration),
    )

    result_list = (data.get("optionChain") or {}).get("result") or []
    if not result_list:
        error = (data.get("optionChain") or {}).get("error")
        raise ValueError(f"no options data for {ticker}: {error}")

    r = result_list[0]
    options_block = (r.get("options") or [{}])[0]
    calls = sorted((_normalize_contract(c) for c in options_block.get("calls", [])), key=lambda x: x["strike"])
    puts = sorted((_normalize_contract(c) for c in options_block.get("puts", [])), key=lambda x: x["strike"])

    call_volume = sum(c["volume"] for c in calls)
    put_volume = sum(p["volume"] for p in puts)
    call_oi = sum(c["open_interest"] for c in calls)
    put_oi = sum(p["open_interest"] for p in puts)

    underlying_price = (r.get("quote") or {}).get("regularMarketPrice")
    atm_call = min(calls, key=lambda c: abs(c["strike"] - underlying_price)) if calls and underlying_price else None
    atm_put = min(puts, key=lambda p: abs(p["strike"] - underlying_price)) if puts and underlying_price else None
    expected_move = None
    if atm_call and atm_put and atm_call["last_price"] and atm_put["last_price"]:
        expected_move = atm_call["last_price"] + atm_put["last_price"]

    return {
        "symbol": r.get("underlyingSymbol", ticker.upper()),
        "underlying_price": underlying_price,
        "expiration_dates": r.get("expirationDates", []),
        "selected_expiration": options_block.get("expirationDate"),
        "calls": calls,
        "puts": puts,
        "summary": {
            "call_volume": call_volume,
            "put_volume": put_volume,
            "call_open_interest": call_oi,
            "put_open_interest": put_oi,
            "put_call_volume_ratio": (put_volume / call_volume) if call_volume else None,
            "put_call_oi_ratio": (put_oi / call_oi) if call_oi else None,
            "atm_strike": atm_call["strike"] if atm_call else None,
            "atm_call_iv": atm_call["implied_volatility"] if atm_call else None,
            "atm_put_iv": atm_put["implied_volatility"] if atm_put else None,
            "expected_move_atm_straddle": expected_move,
        },
    }
