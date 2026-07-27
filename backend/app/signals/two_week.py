"""Next-two-weeks options window: every expiration inside a 14-day horizon,
plus the deterministic evidence tally that feeds the three-bucket
(up / down / sideways) prompt.

Why a *window* rather than the single chain the rest of the app uses: the
company page shows one expiration at a time, which answers "what does the
market think about this specific date." The question people actually ask --
"what's going on with SNOW over the next couple of weeks" -- spans several
weeklies, and the interesting part is usually how they DIFFER from each
other (one expiration carrying much richer IV than its neighbours is the
market pricing a dated event into that week specifically).

The evidence tally below is computed here, in Python, on purpose. The
language model that consumes it only NARRATES the three cases; it never
decides which signals exist or which direction they point. That split is
the same one Layer 1 / Layer 2 of the daily scanner uses, and it exists
because an LLM handed a raw options chain will happily invent a confident
bull case out of nothing.
"""

from __future__ import annotations

import concurrent.futures
import datetime

from app.core.http_cache import UpstreamError
from app.ingest import options as options_ingest
from app.ingest import prices as prices_ingest
from app.signals.options_signals import describe_skew
from app.signals.technicals import compute_technical_read

DEFAULT_HORIZON_DAYS = 14

# Same thresholds as detect.py's _scan_options, deliberately: two different
# definitions of "unusual" across the app would make the daily alert and the
# ad-hoc lookup disagree about the same contract on the same day.
UNUSUAL_MIN_VOLUME = 1000
UNUSUAL_OI_MULTIPLE = 3


def _expirations_within(expiration_dates: list[int], horizon_days: int) -> list[int]:
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    cutoff = now + horizon_days * 86400
    within = [e for e in sorted(expiration_dates) if e <= cutoff]
    # Every ticker has at least one expiration worth showing even if the
    # next one falls just past the horizon (thinly-traded names sometimes
    # only list monthlies), so never return an empty window.
    if not within and expiration_dates:
        return [min(expiration_dates)]
    return within


def _days_out(expiration: int) -> int:
    now = datetime.datetime.now(datetime.timezone.utc).timestamp()
    return max(0, round((expiration - now) / 86400))


def _summarize_expiration(chain: dict) -> dict:
    s = chain["summary"]
    ivs = [v for v in (s.get("atm_call_iv"), s.get("atm_put_iv")) if v is not None]
    expiration = chain["selected_expiration"]
    underlying = chain.get("underlying_price")
    expected_move = s.get("expected_move_atm_straddle")
    skew = describe_skew(chain)
    # describe_skew reports put_wing - call_wing; flip the sign so a POSITIVE
    # skew_value means the call side is richer (inverted skew, upside being
    # chased), which reads more naturally in the evidence bucketing below.
    skew_value = (
        skew.call_wing_iv - skew.put_wing_iv
        if skew.call_wing_iv is not None and skew.put_wing_iv is not None
        else None
    )
    return {
        "expiration": expiration,
        "expiration_date": datetime.date.fromtimestamp(expiration).isoformat(),
        "days_out": _days_out(expiration),
        "atm_strike": s.get("atm_strike"),
        "atm_iv": (sum(ivs) / len(ivs)) if ivs else None,
        "call_iv": s.get("atm_call_iv"),
        "put_iv": s.get("atm_put_iv"),
        "expected_move": expected_move,
        "expected_move_pct": (expected_move / underlying * 100) if expected_move and underlying else None,
        "put_call_volume_ratio": s.get("put_call_volume_ratio"),
        "call_volume": s.get("call_volume"),
        "put_volume": s.get("put_volume"),
        "skew": skew.summary,
        "skew_direction": skew.direction,
        "skew_value": skew_value,
    }


def _unusual_contracts(chains: list[dict]) -> list[dict]:
    """Contracts across the whole window where volume dwarfs open interest.

    Carries an explicit `expiring_this_week` flag rather than filtering
    those out. Near-dated weeklies reset open interest every week, so a
    high volume:OI ratio there is the STRUCTURAL NORM, not a signal -- the
    daily scanner avoids the problem entirely by only ever looking at a
    ~30-day tenor. A two-week window can't dodge it, so the honest move is
    to surface the flag and let the prompt tell the model to discount it.
    """
    out: list[dict] = []
    for chain in chains:
        expiration = chain["selected_expiration"]
        days_out = _days_out(expiration)
        for side, contracts in (("call", chain["calls"]), ("put", chain["puts"])):
            for c in contracts:
                oi = c["open_interest"]
                if c["volume"] < UNUSUAL_MIN_VOLUME:
                    continue
                if oi != 0 and c["volume"] < oi * UNUSUAL_OI_MULTIPLE:
                    continue
                out.append({
                    "contract_symbol": c["contract_symbol"],
                    "side": side,
                    "strike": c["strike"],
                    "expiration_date": datetime.date.fromtimestamp(expiration).isoformat(),
                    "days_out": days_out,
                    "volume": c["volume"],
                    "open_interest": oi,
                    "volume_oi_ratio": (c["volume"] / oi) if oi else None,
                    "implied_volatility": c["implied_volatility"],
                    "expiring_this_week": days_out <= 7,
                })
    return sorted(out, key=lambda c: -c["volume"])[:8]


def _build_evidence(points: list[dict], unusual: list[dict], tech, underlying: float | None) -> dict:
    """Deterministic bucketing of every computed signal into the case(s) it
    supports, each with the reason it supports it.

    Some signals genuinely support two cases at once and are recorded in
    both -- RSI below 30 is a bounce setup to a mean-reversion trader and
    evidence of sustained selling to a trend follower, and forcing it into
    one bucket to make the tally look tidy would be dishonest. The counts
    are an evidence TALLY, not a probability; nothing here predicts.
    """
    up: list[dict] = []
    down: list[dict] = []
    sideways: list[dict] = []

    # --- Term shape inside the window -------------------------------------
    ivs = [(p["days_out"], p["atm_iv"]) for p in points if p["atm_iv"] is not None]
    event_week = None
    if len(ivs) >= 2:
        peak_days, peak_iv = max(ivs, key=lambda x: x[1])
        others = [iv for d, iv in ivs if d != peak_days]
        if others and peak_iv > (sum(others) / len(others)) * 1.15:
            event_week = next(p for p in points if p["days_out"] == peak_days)
            note = {
                "signal": "Event pricing",
                "why": (
                    f"The {event_week['expiration_date']} expiration carries {peak_iv * 100:.0f}% IV vs "
                    f"~{sum(others) / len(others) * 100:.0f}% for the other weeks in the window -- the market is "
                    "pricing a dated event into that specific week."
                ),
            }
            # A concentrated IV bump says a large move is expected but is
            # silent on direction, so it lands in both directional cases and
            # counts AGAINST the quiet case.
            up.append(note)
            down.append(note)
            sideways.append({
                "signal": "Event pricing (argues against a quiet fortnight)",
                "why": "Elevated IV in one week means the market expects a large move, which is the opposite of a range-bound expectation.",
            })

    # --- Put/call flow ----------------------------------------------------
    ratios = [p["put_call_volume_ratio"] for p in points if p["put_call_volume_ratio"] is not None]
    if ratios:
        avg_pc = sum(ratios) / len(ratios)
        if avg_pc < 0.7:
            up.append({
                "signal": "Call-heavy flow",
                "why": f"Put/call volume ratio averages {avg_pc:.2f} across the window -- notably more call volume than put volume.",
            })
        elif avg_pc > 1.2:
            down.append({
                "signal": "Put-heavy flow",
                "why": f"Put/call volume ratio averages {avg_pc:.2f} across the window -- more put volume than call volume, which is hedging or bearish positioning (the two are indistinguishable in this data).",
            })
        else:
            sideways.append({
                "signal": "Balanced flow",
                "why": f"Put/call volume ratio averages {avg_pc:.2f} -- neither side of the book is being pressed.",
            })

    # --- Skew -------------------------------------------------------------
    skew_vals = [p["skew_value"] for p in points if p.get("skew_value") is not None]
    if skew_vals:
        avg_skew = sum(skew_vals) / len(skew_vals)
        if avg_skew > 0.03:
            up.append({
                "signal": "Inverted (call-side) skew",
                "why": f"Out-of-the-money calls carry {avg_skew * 100:.1f} IV points more than equidistant puts -- upside is being chased, which is unusual and often precedes or accompanies a squeeze.",
            })
        elif avg_skew < -0.03:
            down.append({
                "signal": "Steep put skew",
                "why": f"Out-of-the-money puts carry {abs(avg_skew) * 100:.1f} IV points more than equidistant calls -- downside protection is in demand.",
            })
        else:
            sideways.append({
                "signal": "Flat skew",
                "why": "Calls and puts are priced at similar implied volatility, so the options market has no clear directional lean.",
            })

    # --- Technicals -------------------------------------------------------
    if tech is not None:
        if tech.sma50 and tech.sma200:
            if tech.sma50 > tech.sma200:
                up.append({"signal": "Uptrend structure", "why": f"50-day SMA (${tech.sma50:.2f}) is above the 200-day (${tech.sma200:.2f})."})
            else:
                down.append({"signal": "Downtrend structure", "why": f"50-day SMA (${tech.sma50:.2f}) is below the 200-day (${tech.sma200:.2f})."})
        if tech.rsi14 is not None:
            if tech.rsi14 >= 70:
                up.append({"signal": "Strong momentum", "why": f"RSI-14 is {tech.rsi14:.0f} -- buyers in control."})
                down.append({"signal": "Overbought", "why": f"RSI-14 is {tech.rsi14:.0f}, a level from which pullbacks are common."})
            elif tech.rsi14 <= 30:
                up.append({"signal": "Oversold", "why": f"RSI-14 is {tech.rsi14:.0f} -- stretched to the downside, a level bounces often start from."})
                down.append({"signal": "Sustained selling", "why": f"RSI-14 is {tech.rsi14:.0f} -- persistent downside pressure, and oversold can stay oversold."})
            else:
                sideways.append({"signal": "Neutral RSI", "why": f"RSI-14 is {tech.rsi14:.0f} -- neither stretched nor washed out."})
        if tech.high_52w_prior and underlying and underlying >= tech.high_52w_prior:
            up.append({"signal": "At/through 52-week high", "why": f"Price ${underlying:.2f} is at or above the prior 52-week high of ${tech.high_52w_prior:.2f}."})
        if tech.low_52w_prior and underlying and underlying <= tech.low_52w_prior:
            down.append({"signal": "At/through 52-week low", "why": f"Price ${underlying:.2f} is at or below the prior 52-week low of ${tech.low_52w_prior:.2f}."})

    # --- Unusual activity -------------------------------------------------
    real_unusual = [c for c in unusual if not c["expiring_this_week"]]
    if real_unusual:
        calls = sum(1 for c in real_unusual if c["side"] == "call")
        puts = len(real_unusual) - calls
        if calls > puts:
            up.append({"signal": "New call positioning", "why": f"{calls} call contract(s) beyond this week traded well above their open interest -- new bullish positioning, not just churn."})
        elif puts > calls:
            down.append({"signal": "New put positioning", "why": f"{puts} put contract(s) beyond this week traded well above their open interest -- new downside positioning."})

    return {
        "up": up,
        "down": down,
        "sideways": sideways,
        "tally": {"up": len(up), "down": len(down), "sideways": len(sideways)},
        "event_week": event_week["expiration_date"] if event_week else None,
    }


def get_two_week_window(ticker: str, horizon_days: int = DEFAULT_HORIZON_DAYS) -> dict:
    """Every expiration inside `horizon_days`, plus unusual contracts and
    the deterministic three-bucket evidence tally."""
    first = options_ingest.get_options_chain(ticker)
    expirations = _expirations_within(first["expiration_dates"], horizon_days)

    def fetch_one(exp: int) -> dict | None:
        try:
            return first if exp == first["selected_expiration"] else options_ingest.get_options_chain(ticker, exp)
        except (UpstreamError, ValueError):
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        chains = [c for c in pool.map(fetch_one, expirations) if c is not None]
    chains.sort(key=lambda c: c["selected_expiration"])

    points = [_summarize_expiration(c) for c in chains]
    unusual = _unusual_contracts(chains)

    tech = None
    try:
        history = prices_ingest.get_price_history(ticker)
        tech = compute_technical_read(history["points"])
    except (UpstreamError, ValueError, KeyError):
        tech = None  # options view still works without the technical overlay

    underlying = first.get("underlying_price")
    evidence = _build_evidence(points, unusual, tech, underlying)

    return {
        "ticker": ticker.upper(),
        "underlying_price": underlying,
        "horizon_days": horizon_days,
        "as_of": datetime.date.today().isoformat(),
        "expirations": points,
        "unusual_contracts": unusual,
        "evidence": evidence,
    }
