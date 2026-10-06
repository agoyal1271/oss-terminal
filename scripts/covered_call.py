#!/usr/bin/env python3
"""Yahoo-only covered-call research for any ticker.

The script deliberately separates two outputs:

* a market screen, which can rank liquid OTM calls without knowing who owns
  the stock; and
* a position plan, which is only actionable when shares, cost basis, and the
  number of shares the owner is willing to have called away are supplied.

Yahoo is free/keyless but unofficial and delayed. Quotes have no exchange
timestamp in this app's normalized response, so every candidate must be
rechecked in the broker immediately before placing a limit order.

Examples:
  python scripts/covered_call.py BMNR
  python scripts/covered_call.py BMNR --json
  python scripts/covered_call.py BMNR --shares 500 --cost-basis 18 \
      --protected-core-shares 300 --max-assignment-shares 200
"""

from __future__ import annotations

import argparse
import concurrent.futures
import datetime as dt
import json
import math
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

import options_math as optmath


BACKEND_URL = os.environ.get("BACKEND_URL", "https://backend-gules-iota-44.vercel.app")
DEFAULT_MIN_DTE = 14
DEFAULT_MAX_DTE = 45


def fetch_json(path: str, timeout: int = 45) -> dict:
    url = f"{BACKEND_URL.rstrip('/')}{path}"
    request = urllib.request.Request(url, headers={"User-Agent": "OSS-Terminal-covered-call/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")
        raise RuntimeError(f"HTTP {exc.code} fetching {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not fetch {url}: {exc}") from exc


def _last(values: list[float | None]) -> float | None:
    return values[-1] if values else None


def sma(values: list[float], period: int) -> float | None:
    return sum(values[-period:]) / period if len(values) >= period else None


def ema_series(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2.0 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(alpha * value + (1 - alpha) * out[-1])
    return out


def rsi(values: list[float], period: int = 14) -> float | None:
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for index in range(1, period + 1):
        change = values[index] - values[index - 1]
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    reading = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    for index in range(period + 1, len(values)):
        change = values[index] - values[index - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        reading = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return reading


def annualized_volatility(values: list[float], period: int) -> float | None:
    if len(values) < period + 1:
        return None
    returns = [math.log(values[i] / values[i - 1]) for i in range(len(values) - period, len(values))]
    return statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else None


def atr_pct(points: list[dict], period: int = 14) -> float | None:
    if len(points) < period + 1:
        return None
    true_ranges = []
    for index in range(len(points) - period, len(points)):
        high, low = points[index].get("high"), points[index].get("low")
        previous_close = points[index - 1].get("close")
        if high is None or low is None or previous_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    close = points[-1].get("close")
    return sum(true_ranges) / len(true_ranges) / close if len(true_ranges) == period and close else None


def technical_snapshot(price_data: dict) -> dict:
    points = price_data.get("points") or []
    closes = [float(point["close"]) for point in points if point.get("close") is not None]
    macd = signal = histogram = None
    if len(closes) >= 35:
        ema12, ema26 = ema_series(closes, 12), ema_series(closes, 26)
        macd_series = [fast - slow for fast, slow in zip(ema12, ema26)]
        signal_series = ema_series(macd_series, 9)
        macd, signal = macd_series[-1], signal_series[-1]
        histogram = macd - signal

    def period_return(days: int) -> float | None:
        if len(closes) <= days or closes[-days - 1] == 0:
            return None
        return closes[-1] / closes[-days - 1] - 1

    latest = _last(closes)
    sma20, sma50, sma200 = sma(closes, 20), sma(closes, 50), sma(closes, 200)
    trend = "mixed"
    if latest and sma20 and sma50 and latest > sma20 > sma50:
        trend = "bullish"
    elif latest and sma20 and sma50 and latest < sma20 < sma50:
        trend = "bearish"
    return {
        "latest_close": latest,
        "sma20": sma20,
        "sma50": sma50,
        "sma200": sma200,
        "rsi14": rsi(closes),
        "macd": macd,
        "macd_signal": signal,
        "macd_histogram": histogram,
        "atr14_pct": atr_pct(points),
        "realized_volatility_20d": annualized_volatility(closes, 20),
        "realized_volatility_60d": annualized_volatility(closes, 60),
        "return_20d": period_return(20),
        "return_60d": period_return(60),
        "trend": trend,
        "fifty_two_week_high": price_data.get("fifty_two_week_high"),
        "fifty_two_week_low": price_data.get("fifty_two_week_low"),
    }


def summarize_fundamentals(financials: dict | None) -> dict | None:
    if not financials:
        return None
    annual = financials.get("annual") or []
    if not annual:
        return {"note": "No normalized annual SEC financials available."}
    latest = annual[0]
    metrics = latest.get("metrics") or {}
    derived = latest.get("derived") or {}
    revenue_growth = None
    if len(annual) > 1:
        prior_revenue = (annual[1].get("metrics") or {}).get("revenue")
        revenue = metrics.get("revenue")
        if revenue is not None and prior_revenue not in (None, 0):
            revenue_growth = revenue / prior_revenue - 1
    anomaly_flags = []
    for label, value in (
        ("operating margin", derived.get("operating_margin")),
        ("net margin", derived.get("net_margin")),
    ):
        if value is not None and abs(value) > 2:
            anomaly_flags.append(
                f"{label} exceeds 200%; unusual/non-operating accounting likely dominates, so inspect the latest 10-K before comparing it with an ordinary operating company."
            )
    return {
        "fiscal_year_end": latest.get("fiscal_year_end"),
        "revenue": metrics.get("revenue"),
        "revenue_growth_yoy": revenue_growth,
        "net_income": metrics.get("net_income"),
        "cash": metrics.get("cash_and_equivalents"),
        "long_term_debt": metrics.get("long_term_debt"),
        "free_cash_flow": derived.get("free_cash_flow"),
        "gross_margin": derived.get("gross_margin"),
        "operating_margin": derived.get("operating_margin"),
        "net_margin": derived.get("net_margin"),
        "current_ratio": derived.get("current_ratio"),
        "debt_to_equity": derived.get("debt_to_equity"),
        "anomaly_flags": anomaly_flags,
        "note": "SEC annual statements can lag the market and do not include valuation multiples.",
    }


def midpoint(contract: dict) -> float | None:
    bid, ask = contract.get("bid"), contract.get("ask")
    if bid is None or ask is None or bid < 0 or ask < bid:
        return None
    return (bid + ask) / 2


def expiration_date(expiration: int) -> dt.date:
    return dt.datetime.fromtimestamp(expiration, tz=dt.timezone.utc).date()


def days_to_expiration(expiration: int, today: dt.date | None = None) -> int:
    return (expiration_date(expiration) - (today or dt.datetime.now(dt.timezone.utc).date())).days


def enrich_candidate(
    contract: dict,
    *,
    spot: float,
    expiration: int,
    cost_basis: float | None,
    target_delta: float,
) -> dict | None:
    bid, ask = contract.get("bid"), contract.get("ask")
    strike, iv = contract.get("strike"), contract.get("implied_volatility")
    dte = days_to_expiration(expiration)
    if not all(isinstance(value, (int, float)) for value in (bid, ask, strike, iv)):
        return None
    if bid <= 0 or ask < bid or strike <= spot or iv <= 0 or dte <= 0:
        return None
    mid = midpoint(contract)
    if not mid:
        return None
    greeks = optmath.greeks(spot, strike, dte, iv, "call")
    if not greeks:
        return None
    spread_pct = (ask - bid) / mid
    premium_yield = bid / spot
    upside = strike / spot - 1
    annualized_yield = premium_yield * 365 / dte
    called_return = None if cost_basis is None else (strike + bid - cost_basis) / cost_basis
    score = (
        35 * max(0.0, 1 - abs(greeks["delta"] - target_delta) / 0.20)
        + 25 * max(0.0, 1 - spread_pct / 0.20)
        + 20 * min(1.0, math.log10(max(contract.get("open_interest") or 0, 1)) / 4)
        + 10 * min(1.0, math.log10(max(contract.get("volume") or 0, 1)) / 3)
        + 10 * min(1.0, annualized_yield / 0.35)
    )
    return {
        "contract_symbol": contract.get("contract_symbol"),
        "expiration": expiration_date(expiration).isoformat(),
        "expiration_timestamp": expiration,
        "dte": dte,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "open_interest": contract.get("open_interest") or 0,
        "volume": contract.get("volume") or 0,
        "implied_volatility": iv,
        "delta_estimate": greeks["delta"],
        "theta_per_day_estimate": greeks["theta_per_day"],
        "probability_itm_estimate": greeks["prob_itm_at_expiry"],
        "premium_yield_on_spot_bid": premium_yield,
        "simple_annualized_premium_yield": annualized_yield,
        "upside_to_strike": upside,
        "return_if_called_from_cost_basis": called_return,
        "score": score,
    }


def screen_candidates(
    chains: list[dict],
    *,
    spot: float,
    cost_basis: float | None = None,
    min_delta: float = 0.15,
    max_delta: float = 0.35,
    target_delta: float = 0.25,
    min_open_interest: int = 100,
    min_volume: int = 10,
    max_spread_pct: float = 0.15,
) -> tuple[list[dict], dict]:
    candidates: list[dict] = []
    rejected = {"unusable_quote": 0, "delta": 0, "liquidity": 0, "spread": 0, "below_cost_basis": 0}
    for chain in chains:
        expiration = chain.get("selected_expiration")
        if not expiration:
            continue
        for contract in chain.get("calls") or []:
            item = enrich_candidate(
                contract, spot=spot, expiration=expiration, cost_basis=cost_basis, target_delta=target_delta
            )
            if not item:
                rejected["unusable_quote"] += 1
                continue
            if not min_delta <= item["delta_estimate"] <= max_delta:
                rejected["delta"] += 1
                continue
            if item["open_interest"] < min_open_interest or item["volume"] < min_volume:
                rejected["liquidity"] += 1
                continue
            if item["spread_pct"] > max_spread_pct:
                rejected["spread"] += 1
                continue
            if cost_basis is not None and item["strike"] < cost_basis:
                rejected["below_cost_basis"] += 1
                continue
            candidates.append(item)
    candidates.sort(key=lambda item: (-item["score"], item["dte"], item["strike"]))
    return candidates, rejected


def position_plan(
    *,
    shares: int | None,
    cost_basis: float | None,
    protected_core_shares: int,
    max_assignment_shares: int | None,
    candidate: dict | None,
) -> dict:
    missing = []
    if shares is None:
        missing.append("shares")
    if cost_basis is None:
        missing.append("cost_basis")
    if missing:
        return {
            "actionable": False,
            "missing": missing,
            "message": "Market candidates are a watchlist only; exact covered-call sizing requires shares and cost basis.",
        }
    eligible_shares = max(0, shares - protected_core_shares)
    if max_assignment_shares is not None:
        eligible_shares = min(eligible_shares, max_assignment_shares)
    contracts = eligible_shares // 100
    result: dict[str, Any] = {
        "actionable": contracts > 0 and candidate is not None,
        "shares": shares,
        "cost_basis": cost_basis,
        "protected_core_shares": protected_core_shares,
        "max_assignment_shares": max_assignment_shares,
        "eligible_covered_shares": contracts * 100,
        "max_covered_call_contracts": contracts,
    }
    if candidate and contracts:
        result.update({
            "illustrative_contract": candidate["contract_symbol"],
            "illustrative_contract_count": contracts,
            "gross_bid_credit": candidate["bid"] * 100 * contracts,
            "effective_exit_price_if_called": candidate["strike"] + candidate["bid"],
            "return_if_called_from_cost_basis": candidate["return_if_called_from_cost_basis"],
            "order_note": "Use a limit order and recheck the live broker quote; do not use the delayed Yahoo bid as an executable price.",
        })
    elif contracts == 0:
        result["message"] = "No complete 100-share lot remains after the protected-core/assignment limits."
    return result


def _safe_fetch(path: str) -> dict | None:
    try:
        return fetch_json(path)
    except RuntimeError:
        return None


def analyze(
    ticker: str,
    *,
    shares: int | None = None,
    cost_basis: float | None = None,
    protected_core_shares: int = 0,
    max_assignment_shares: int | None = None,
    min_dte: int = DEFAULT_MIN_DTE,
    max_dte: int = DEFAULT_MAX_DTE,
) -> dict:
    ticker = ticker.upper()
    captured = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()
    profile = fetch_json(f"/api/companies/{urllib.parse.quote(ticker)}")
    base_chain = fetch_json(f"/api/companies/{ticker}/options")
    price_data = fetch_json(f"/api/companies/{ticker}/prices?range=1y")
    spot = base_chain.get("underlying_price")
    if not spot:
        raise RuntimeError(f"Yahoo returned no underlying price for {ticker}")
    expirations = [
        exp for exp in base_chain.get("expiration_dates") or []
        if min_dte <= days_to_expiration(exp) <= max_dte
    ]

    def get_chain(expiration: int) -> dict | None:
        if expiration == base_chain.get("selected_expiration"):
            return base_chain
        return _safe_fetch(f"/api/companies/{ticker}/options?expiration={expiration}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        chains = [chain for chain in pool.map(get_chain, expirations) if chain]

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        financials_future = pool.submit(_safe_fetch, f"/api/companies/{ticker}/financials")
        two_week_future = pool.submit(_safe_fetch, f"/api/companies/{ticker}/options/two-week?horizon_days=14")
        iv_rank_future = pool.submit(_safe_fetch, f"/api/companies/{ticker}/options/iv-rank")
        filings_future = pool.submit(_safe_fetch, f"/api/companies/{ticker}/filings?limit=8")
        financials = financials_future.result()
        two_week = two_week_future.result()
        iv_rank = iv_rank_future.result()
        filings = filings_future.result()

    candidates, rejected = screen_candidates(chains, spot=spot, cost_basis=cost_basis)
    top = candidates[:8]
    evidence = (two_week or {}).get("evidence") or {}
    tally = evidence.get("tally") or {}
    trend = technical_snapshot(price_data)
    rsi14 = trend.get("rsi14")
    interpretation = []
    if trend["trend"] == "bullish":
        interpretation.append("Price is above rising short/intermediate moving-average structure; calls cap upside, so partial coverage is safer than covering the full position.")
    elif trend["trend"] == "bearish":
        interpretation.append("Trend structure is weak; premium does not protect against a large stock decline, so covered calls should not be treated as downside insurance.")
    else:
        interpretation.append("Trend structure is mixed; favor defined assignment rules over a directional forecast.")
    if rsi14 is not None and rsi14 >= 70:
        interpretation.append("RSI is overbought by the conventional 70 threshold, which can support selling strength but is not itself a reversal signal.")
    elif rsi14 is not None and rsi14 <= 30:
        interpretation.append("RSI is oversold; selling calls after weakness risks capping a rebound.")
    if top:
        interpretation.append("The ranked list passed delta, liquidity, and spread filters; ranking is relative, not a trade instruction.")
    else:
        interpretation.append("No contract passed every default filter; widening liquidity/spread rules would trade data quality for more choices.")

    return {
        "ticker": ticker,
        "company_name": profile.get("name") or profile.get("title") or ticker,
        "captured_at_utc": captured,
        "source": {
            "provider": "Yahoo Finance via the OSS Terminal backend",
            "backend_url": BACKEND_URL,
            "free_and_keyless": True,
            "official_feed": False,
            "quote_timestamp_available": False,
        },
        "market": {
            "spot": spot,
            "currency": price_data.get("currency"),
            "exchange": price_data.get("exchange"),
            "screen_dte_range": [min_dte, max_dte],
            "expirations_screened": [expiration_date(exp).isoformat() for exp in expirations],
        },
        "technicals": trend,
        "fundamentals": summarize_fundamentals(financials),
        "recent_sec_filings": (filings or {}).get("filings") or [],
        "near_term_evidence": {
            "up": tally.get("up"),
            "down": tally.get("down"),
            "sideways": tally.get("sideways"),
            "event_week": evidence.get("event_week"),
            "details": evidence,
        },
        "iv_context": iv_rank,
        "covered_call_filters": {
            "delta": [0.15, 0.35],
            "target_delta": 0.25,
            "minimum_open_interest": 100,
            "minimum_volume": 10,
            "maximum_bid_ask_spread_pct": 0.15,
            "strike_must_be_above_spot": True,
            "strike_must_be_at_or_above_cost_basis_when_supplied": True,
        },
        "top_market_candidates": top,
        "rejected_contract_counts": rejected,
        "position_plan": position_plan(
            shares=shares,
            cost_basis=cost_basis,
            protected_core_shares=protected_core_shares,
            max_assignment_shares=max_assignment_shares,
            candidate=top[0] if top else None,
        ),
        "interpretation": interpretation,
        "risk_controls": [
            "Only sell one covered call per 100 owned shares; never count pending or borrowed shares.",
            "Choose a strike at which assignment is acceptable, including tax consequences.",
            "Use a limit order after checking the current broker bid/ask and Greeks.",
            "Check earnings, corporate actions, and ex-dividend dates separately; this feed has no confirmed event calendar.",
            "Covered-call premium offers limited downside cushion and sacrifices upside above the strike.",
        ],
    }


def _money(value: float | None) -> str:
    return "n/a" if value is None else f"${value:,.2f}"


def _pct(value: float | None, digits: int = 1) -> str:
    return "n/a" if value is None else f"{value * 100:.{digits}f}%"


def render_report(result: dict) -> str:
    market, tech = result["market"], result["technicals"]
    lines = [
        f"{result['ticker']} Yahoo covered-call research",
        f"Captured: {result['captured_at_utc']} | Spot: {_money(market['spot'])}",
        "",
        "Technical read",
        f"  Trend: {tech['trend']} | RSI-14: {tech['rsi14']:.1f}" if tech.get("rsi14") is not None else f"  Trend: {tech['trend']} | RSI-14: n/a",
        f"  SMA20 / SMA50 / SMA200: {_money(tech.get('sma20'))} / {_money(tech.get('sma50'))} / {_money(tech.get('sma200'))}",
        f"  20d return: {_pct(tech.get('return_20d'))} | 20d realized vol: {_pct(tech.get('realized_volatility_20d'))} | ATR-14: {_pct(tech.get('atr14_pct'))}",
        "",
        "Covered-call watchlist (conservative credit uses bid)",
    ]
    candidates = result["top_market_candidates"]
    if not candidates:
        lines.append("  No calls passed every default filter.")
    for index, item in enumerate(candidates[:5], 1):
        lines.append(
            f"  {index}. {item['expiration']} {item['strike']:g}C ({item['dte']} DTE): "
            f"bid/ask {_money(item['bid'])}/{_money(item['ask'])}, delta {item['delta_estimate']:.2f}, "
            f"IV {_pct(item['implied_volatility'], 0)}, OI {item['open_interest']:,}, volume {item['volume']:,}, "
            f"spread {_pct(item['spread_pct'])}, premium/spot {_pct(item['premium_yield_on_spot_bid'])}, "
            f"upside to strike {_pct(item['upside_to_strike'])}"
        )
    lines.extend(["", "Assessment"])
    lines.extend(f"  - {item}" for item in result["interpretation"])
    plan = result["position_plan"]
    lines.extend(["", "Position sizing"])
    if plan.get("actionable"):
        lines.append(
            f"  At most {plan['max_covered_call_contracts']} contract(s) under the supplied assignment limits; "
            f"illustrative gross bid credit {_money(plan.get('gross_bid_credit'))}."
        )
    else:
        lines.append(f"  Not position-sized: {plan.get('message', 'position inputs are incomplete')}")
    lines.extend([
        "",
        "Data warning",
        "  Yahoo is unofficial/delayed and supplies no quote timestamp here. Recheck the live broker chain, events, and limit price before any order.",
    ])
    return "\n".join(lines)


def build_llm_prompt(result: dict, question: str | None = None) -> str:
    """Package computed facts for narration without asking the model to do
    arithmetic or invent a broker quote. This is also the Slack bot's
    covered-call path."""
    question = question or f"Evaluate the covered-call setup for {result['ticker']}."
    return "\n".join([
        f"You are reviewing a covered-call market screen for {result['ticker']}. Use ONLY the JSON DATA below.",
        "Every candidate already passed deterministic filters for OTM strike, delta, liquidity, and bid/ask spread.",
        "The Greeks are Black-Scholes estimates from Yahoo IV, not broker-quoted Greeks. Yahoo is unofficial/delayed and has no quote timestamp here.",
        "Classify the current setup as FAVORABLE, MARGINAL, or NO SETUP for further review; this is a research classification, not an order instruction.",
        "Compare at most the top three candidates by assignment risk, upside retained, premium yield, liquidity, and time exposure.",
        "Use the bid for credit. Never invent an earnings date, quote, position size, tax fact, or probability.",
        "If position_plan.actionable is false, explicitly say exact contracts cannot be selected without shares, cost basis, protected-core shares, and assignment tolerance.",
        "Never imply covered calls protect against a large decline. End with a short broker recheck checklist.",
        "",
        f"QUESTION: {question}",
        "",
        "DATA:",
        json.dumps(result, indent=2),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("ticker")
    parser.add_argument("--shares", type=int)
    parser.add_argument("--cost-basis", type=float)
    parser.add_argument("--protected-core-shares", type=int, default=0)
    parser.add_argument("--max-assignment-shares", type=int)
    parser.add_argument("--min-dte", type=int, default=DEFAULT_MIN_DTE)
    parser.add_argument("--max-dte", type=int, default=DEFAULT_MAX_DTE)
    output = parser.add_mutually_exclusive_group()
    output.add_argument("--json", action="store_true", help="emit model-ready JSON instead of the trader report")
    output.add_argument("--prompt", action="store_true", help="emit the grounded prompt that can be sent to an LLM")
    output.add_argument("--run", action="store_true", help="send the grounded prompt to local Ollama")
    output.add_argument("--openai", action="store_true", help="send the grounded prompt to the OpenAI Responses API")
    parser.add_argument("--question", help="question appended to --prompt/--run")
    parser.add_argument("--model", default="martain7r/finance-llama-8b:q4_k_m")
    parser.add_argument("--ollama-url", default="http://localhost:11434")
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_MODEL", "gpt-5.5"))
    args = parser.parse_args()
    if args.shares is not None and args.shares < 0:
        parser.error("--shares must be non-negative")
    if args.cost_basis is not None and args.cost_basis <= 0:
        parser.error("--cost-basis must be positive")
    if args.protected_core_shares < 0:
        parser.error("--protected-core-shares must be non-negative")
    if args.max_assignment_shares is not None and args.max_assignment_shares < 0:
        parser.error("--max-assignment-shares must be non-negative")
    if args.min_dte < 1 or args.max_dte < args.min_dte:
        parser.error("DTE range is invalid")
    result = analyze(
        args.ticker,
        shares=args.shares,
        cost_basis=args.cost_basis,
        protected_core_shares=args.protected_core_shares,
        max_assignment_shares=args.max_assignment_shares,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
    )
    if args.json:
        print(json.dumps(result, indent=2))
    elif args.prompt:
        print(build_llm_prompt(result, args.question))
    elif args.run:
        import ask as ask_lib

        print(ask_lib.run_ollama(args.ollama_url, args.model, build_llm_prompt(result, args.question)))
    elif args.openai:
        import ask as ask_lib

        print(ask_lib.run_openai(build_llm_prompt(result, args.question), args.openai_model))
    else:
        print(render_report(result))


if __name__ == "__main__":
    main()
