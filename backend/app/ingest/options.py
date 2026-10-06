"""Options chain with a Tradier production feed and Yahoo fallback.

Set OPTIONS_PROVIDER=tradier and TRADIER_TOKEN in production. `auto` selects
Tradier when the token exists and preserves the old Yahoo adapter for local
development. Both adapters return the same public response shape; provenance
and timestamps make it impossible for the UI/model to mistake the fallback
for an entitled real-time feed.
"""

from __future__ import annotations

import concurrent.futures
import datetime
from typing import Any

import httpx

from app.config import settings
from app.core.http_cache import UpstreamError, cached_call_json, cached_get_json

OPTIONS_URL = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"

# Same list as the frontend homepage's "Popular" tickers -- the only tickers
# the daily snapshot job tracks. Snapshotting the whole 10,400-ticker
# universe daily isn't realistic (Yahoo would rate-limit/block it, and
# almost none of it would ever be looked at); this keeps the job bounded
# and pointed at what's actually used. Extend by adding to this list.
IV_WATCHLIST = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "JPM", "BRK-B"]
COOKIE_SEED_URL = "https://fc.yahoo.com"
YAHOO_UA = "Mozilla/5.0 (compatible; OSS-Terminal/0.1)"

_session: dict[str, str] | None = None


def _captured_payload(fetch_fn) -> dict:
    """Put capture time inside cached data, not outside it.

    Otherwise a cached quote would receive a new timestamp on every read and
    appear fresh even though its bid/ask came from an older upstream call.
    """
    return {
        "captured_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "payload": fetch_fn(),
    }


def _epoch_to_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if numeric > 10_000_000_000:  # Tradier dates are commonly epoch milliseconds.
        numeric /= 1000
    try:
        return datetime.datetime.fromtimestamp(numeric, datetime.timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


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
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "rho": None,
        "quote_at": None,
        "last_trade_at": _epoch_to_iso(c.get("lastTradeDate")),
    }


def _get_yahoo_options_chain(ticker: str, expiration: int | None = None) -> dict:
    cache_key = f"{ticker}:{expiration or 'nearest'}"
    wrapper = cached_call_json(
        namespace="yahoo_options_v2",
        key=cache_key,
        ttl=10 * 60,
        fetch_fn=lambda: _captured_payload(lambda: _fetch_chain_json(ticker, expiration)),
    )
    data = wrapper["payload"]

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
        "source": "yahoo_finance",
        "feed_status": "delayed_or_unknown",
        "captured_at_utc": wrapper["captured_at_utc"],
        "underlying_quote_at": _epoch_to_iso((r.get("quote") or {}).get("regularMarketTime")),
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


def _tradier_headers() -> dict[str, str]:
    if not settings.tradier_token:
        raise UpstreamError("TRADIER_TOKEN is required when OPTIONS_PROVIDER=tradier")
    return {
        "Authorization": f"Bearer {settings.tradier_token}",
        "Accept": "application/json",
        "User-Agent": "OSS-Terminal/0.2",
    }


def _fetch_tradier(path: str, params: dict[str, Any]) -> dict:
    response = httpx.get(
        f"{settings.tradier_base_url.rstrip('/')}/{path.lstrip('/')}",
        headers=_tradier_headers(),
        params=params,
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict) and data.get("fault"):
        raise UpstreamError(f"Tradier returned an API fault: {data['fault']}")
    return data


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _date_to_expiration(date_value: str) -> int:
    parsed = datetime.date.fromisoformat(date_value)
    return int(datetime.datetime.combine(parsed, datetime.time.min, datetime.timezone.utc).timestamp())


def _expiration_to_date(expiration: int) -> str:
    return datetime.datetime.fromtimestamp(expiration, datetime.timezone.utc).date().isoformat()


def _tradier_expirations(ticker: str) -> tuple[list[int], str]:
    wrapper = cached_call_json(
        namespace="tradier_option_expirations_v1",
        key=ticker.upper(),
        ttl=15 * 60,
        fetch_fn=lambda: _captured_payload(
            lambda: _fetch_tradier("markets/options/expirations", {"symbol": ticker.upper(), "includeAllRoots": "true"})
        ),
    )
    dates = _as_list(((wrapper.get("payload") or {}).get("expirations") or {}).get("date"))
    expirations = sorted(_date_to_expiration(value) for value in dates if value)
    return expirations, wrapper["captured_at_utc"]


def _tradier_quote(ticker: str) -> tuple[dict, str]:
    wrapper = cached_call_json(
        namespace="tradier_underlying_quote_v1",
        key=ticker.upper(),
        ttl=settings.ttl_options,
        fetch_fn=lambda: _captured_payload(
            lambda: _fetch_tradier("markets/quotes", {"symbols": ticker.upper(), "greeks": "false"})
        ),
    )
    quote = ((wrapper.get("payload") or {}).get("quotes") or {}).get("quote") or {}
    if isinstance(quote, list):
        quote = quote[0] if quote else {}
    return quote, wrapper["captured_at_utc"]


def _tradier_iv(greeks: dict) -> float | None:
    for key in ("mid_iv", "smv_vol", "ask_iv", "bid_iv"):
        value = greeks.get(key)
        if value not in (None, ""):
            return float(value)
    return None


def _normalize_tradier_contract(contract: dict, underlying_price: float | None) -> dict:
    greeks = contract.get("greeks") or {}
    side = str(contract.get("option_type") or contract.get("type") or "").lower()
    strike = float(contract.get("strike") or 0)
    in_the_money = bool(
        underlying_price is not None
        and ((side == "call" and underlying_price > strike) or (side == "put" and underlying_price < strike))
    )
    bid_at = contract.get("bid_date")
    ask_at = contract.get("ask_date")
    quote_at = max((v for v in (bid_at, ask_at) if v not in (None, "")), default=None)
    return {
        "contract_symbol": contract.get("symbol"),
        "strike": strike,
        "last_price": contract.get("last"),
        "bid": contract.get("bid"),
        "ask": contract.get("ask"),
        "change": contract.get("change"),
        "percent_change": contract.get("change_percentage"),
        "volume": contract.get("volume") or 0,
        "open_interest": contract.get("open_interest") or 0,
        "implied_volatility": _tradier_iv(greeks),
        "in_the_money": in_the_money,
        "delta": greeks.get("delta"),
        "gamma": greeks.get("gamma"),
        "theta": greeks.get("theta"),
        "vega": greeks.get("vega"),
        "rho": greeks.get("rho"),
        "quote_at": _epoch_to_iso(quote_at),
        "last_trade_at": _epoch_to_iso(contract.get("trade_date")),
        "greeks_updated_at": greeks.get("updated_at"),
    }


def _get_tradier_options_chain(ticker: str, expiration: int | None = None) -> dict:
    expirations, expirations_captured_at = _tradier_expirations(ticker)
    if not expirations:
        raise ValueError(f"no options expirations for {ticker}")
    selected = expiration or expirations[0]
    if selected not in expirations:
        raise ValueError(f"expiration {_expiration_to_date(selected)} is not available for {ticker}")

    expiration_date = _expiration_to_date(selected)
    wrapper = cached_call_json(
        namespace="tradier_options_v1",
        key=f"{ticker.upper()}:{selected}",
        ttl=settings.ttl_options,
        fetch_fn=lambda: _captured_payload(
            lambda: _fetch_tradier(
                "markets/options/chains",
                {"symbol": ticker.upper(), "expiration": expiration_date, "greeks": "true"},
            )
        ),
    )
    quote, quote_captured_at = _tradier_quote(ticker)
    underlying_price = quote.get("last")
    if underlying_price is None and quote.get("bid") is not None and quote.get("ask") is not None:
        underlying_price = (float(quote["bid"]) + float(quote["ask"])) / 2
    underlying_price = float(underlying_price) if underlying_price is not None else None

    raw_contracts = _as_list(((wrapper.get("payload") or {}).get("options") or {}).get("option"))
    normalized = [_normalize_tradier_contract(item, underlying_price) for item in raw_contracts]
    calls = sorted((item for item, raw in zip(normalized, raw_contracts) if str(raw.get("option_type") or raw.get("type")).lower() == "call"), key=lambda x: x["strike"])
    puts = sorted((item for item, raw in zip(normalized, raw_contracts) if str(raw.get("option_type") or raw.get("type")).lower() == "put"), key=lambda x: x["strike"])
    if not calls and not puts:
        raise ValueError(f"no options contracts for {ticker} on {expiration_date}")

    call_volume = sum(int(c["volume"]) for c in calls)
    put_volume = sum(int(p["volume"]) for p in puts)
    call_oi = sum(int(c["open_interest"]) for c in calls)
    put_oi = sum(int(p["open_interest"]) for p in puts)
    atm_call = min(calls, key=lambda c: abs(c["strike"] - underlying_price)) if calls and underlying_price else None
    atm_put = min(puts, key=lambda p: abs(p["strike"] - underlying_price)) if puts and underlying_price else None
    expected_move = None
    if atm_call and atm_put and atm_call["last_price"] is not None and atm_put["last_price"] is not None:
        expected_move = float(atm_call["last_price"]) + float(atm_put["last_price"])

    quote_at = max((v for v in (quote.get("bid_date"), quote.get("ask_date"), quote.get("trade_date")) if v not in (None, "")), default=None)
    return {
        "source": "tradier",
        "feed_status": "realtime_production",
        "captured_at_utc": wrapper["captured_at_utc"],
        "expirations_captured_at_utc": expirations_captured_at,
        "underlying_captured_at_utc": quote_captured_at,
        "underlying_quote_at": _epoch_to_iso(quote_at),
        "symbol": ticker.upper(),
        "underlying_price": underlying_price,
        "expiration_dates": expirations,
        "selected_expiration": selected,
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


def _selected_provider() -> str:
    provider = settings.options_provider.strip().lower()
    if provider == "auto":
        return "tradier" if settings.tradier_token else "yahoo"
    if provider not in {"tradier", "yahoo"}:
        raise ValueError("OPTIONS_PROVIDER must be auto, tradier, or yahoo")
    return provider


def get_options_chain(ticker: str, expiration: int | None = None) -> dict:
    """Return one normalized chain from the configured provider."""
    if _selected_provider() == "tradier":
        return _get_tradier_options_chain(ticker, expiration)
    return _get_yahoo_options_chain(ticker, expiration)


def get_iv_term_structure(ticker: str, max_expirations: int = 8) -> dict:
    """ATM implied volatility across the next several expirations.

    Normal shape slopes upward (further out = more time value/uncertainty).
    A front-month spike above the back months (backwardation) is the
    reliable, free signal that the market is pricing a known event -- an
    earnings date, an FDA decision -- into that specific expiration.

    Yahoo's options endpoint only returns one expiration's chain per
    request, so this fans out across several expirations in parallel
    (each individually disk-cached by get_options_chain, so repeat calls
    are fast) rather than one big request.
    """
    first = get_options_chain(ticker)
    expirations = first["expiration_dates"][:max_expirations]

    def fetch_one(exp: int) -> dict | None:
        try:
            chain = first if exp == first["selected_expiration"] else get_options_chain(ticker, exp)
        except (UpstreamError, ValueError):
            return None
        s = chain["summary"]
        if s["atm_call_iv"] is None and s["atm_put_iv"] is None:
            return None
        return {
            "expiration": chain["selected_expiration"],
            "atm_strike": s["atm_strike"],
            "call_iv": s["atm_call_iv"],
            "put_iv": s["atm_put_iv"],
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(fetch_one, expirations))

    points = sorted((p for p in results if p is not None), key=lambda p: p["expiration"])

    return {
        "symbol": first["symbol"],
        "underlying_price": first["underlying_price"],
        "points": points,
    }


def closest_expiration_to_days(expiration_dates: list[int], target_days: int = 30) -> int | None:
    """Pick the expiration closest to `target_days` out, so day-over-day
    snapshots compare a roughly consistent tenor rather than jumping between
    a 1-day weekly and a 45-day monthly depending on what happened to be
    nearest when the job happened to run."""
    if not expiration_dates:
        return None
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    target_seconds = target_days * 86400
    return min(expiration_dates, key=lambda exp: abs((exp - now) - target_seconds))


def get_iv_snapshot(ticker: str, target_days: int = 30) -> dict:
    """Today's ATM IV at the expiration closest to `target_days` out -- the
    one data point the daily snapshot job records per ticker."""
    first = get_options_chain(ticker)
    expiration = closest_expiration_to_days(first["expiration_dates"], target_days)
    if expiration is None:
        raise ValueError(f"no expirations available for {ticker}")
    chain = first if expiration == first["selected_expiration"] else get_options_chain(ticker, expiration)
    s = chain["summary"]
    return {
        "ticker": ticker.upper(),
        "date": datetime.date.today().isoformat(),
        "expiration": chain["selected_expiration"],
        "underlying_price": chain["underlying_price"],
        "atm_strike": s["atm_strike"],
        "call_iv": s["atm_call_iv"],
        "put_iv": s["atm_put_iv"],
    }


def get_watchlist_snapshot() -> list[dict]:
    """Today's IV snapshot for every tracked ticker -- what the daily
    GitHub Action calls and appends to data/iv-history/{ticker}.json."""
    results = []
    for ticker in IV_WATCHLIST:
        try:
            results.append(get_iv_snapshot(ticker))
        except (UpstreamError, ValueError):
            continue
    return results


def get_iv_rank(ticker: str) -> dict:
    """IV rank/percentile computed from the daily history accumulated in
    the repo's data/iv-history/{ticker}.json (committed by the daily
    snapshot Action), read live from GitHub's raw content CDN rather than
    bundled into the deployment so new data shows up without a redeploy.

    Honestly reports how many days of history actually exist -- a rank
    computed from 12 days is not the traditional 52-week metric, and
    callers/UI should say so rather than presenting it as if it were.
    """
    url = f"{settings.iv_history_repo_raw_base}/{ticker.upper()}.json"
    try:
        history = cached_get_json(namespace="iv_history", url=url, ttl=6 * 3600, headers={"User-Agent": "OSS-Terminal"})
    except UpstreamError:
        history = []

    if not isinstance(history, list):
        history = []

    ivs = [((h.get("call_iv") or 0) + (h.get("put_iv") or 0)) / 2 for h in history if h.get("call_iv") or h.get("put_iv")]

    if len(ivs) < 2:
        return {
            "ticker": ticker.upper(),
            "history_days": len(history),
            "current_iv": ivs[-1] if ivs else None,
            "iv_rank": None,
            "iv_percentile": None,
            "note": "Not enough history collected yet to compute a rank -- check back after a few more days of snapshots.",
        }

    current = ivs[-1]
    lo, hi = min(ivs), max(ivs)
    iv_rank = ((current - lo) / (hi - lo) * 100) if hi > lo else 50.0
    iv_percentile = sum(1 for v in ivs[:-1] if v < current) / (len(ivs) - 1) * 100

    return {
        "ticker": ticker.upper(),
        "history_days": len(history),
        "current_iv": current,
        "iv_rank": iv_rank,
        "iv_percentile": iv_percentile,
        "note": (
            f"Based on {len(history)} days of collected history. Traditional IV rank uses a trailing "
            "52-week (~252 trading day) window -- this project started collecting daily rather than "
            "having access to a year of backdated data (no free source of historical IV exists), so "
            "treat this as directional until more history accumulates."
        ),
    }
