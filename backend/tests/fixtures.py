"""Deterministic fixtures shared by the signal-parity tests.

Hand-built (not random) on purpose: a fixed, readable series makes a
failing assertion legible ("day 40 should have been an RSI overbought
day") instead of forcing you to debug a seeded RNG to understand a
regression.
"""

from __future__ import annotations

import math


def price_series(n: int = 320, kind: str = "uptrend") -> list[dict]:
    """Bars shaped like /api/companies/{ticker}/prices points: {date, close,
    high, low, volume}. `n` >= 260 so sma200 and the 52-week window both
    have enough history to produce non-null values.

    kind:
      "uptrend"   -- steady climb with a sine wobble, tail RSI stays warm
      "downtrend" -- steady decline, tail RSI stays cold
      "choppy"    -- flat with noise, no trend structure, RSI mid-range
      "volume_spike" -- like uptrend but last 10 bars get a volume surge
    """
    closes: list[float] = []
    price = 100.0
    for i in range(n):
        wobble = 2.0 * math.sin(i / 7.0)
        if kind in ("uptrend", "volume_spike"):
            price = 100.0 + i * 0.35 + wobble
        elif kind == "downtrend":
            price = 100.0 - i * 0.30 + wobble
        else:  # choppy
            price = 100.0 + wobble + (3.0 if i % 5 == 0 else -1.5 if i % 3 == 0 else 0.0)
        closes.append(round(max(price, 1.0), 4))

    points = []
    for i, close in enumerate(closes):
        prev = closes[i - 1] if i > 0 else close
        high = round(max(close, prev) + 0.6, 4)
        low = round(min(close, prev) - 0.6, 4)
        base_volume = 1_000_000 + (i % 13) * 15_000
        volume = base_volume
        if kind == "volume_spike" and i >= n - 10:
            volume = base_volume * 3
        points.append({
            "date": f"2025-01-{(i % 28) + 1:02d}",
            "close": close,
            "high": high,
            "low": low,
            "volume": volume,
        })
    return points


def options_chain(scenario: str = "normal_skew", underlying: float = 100.0) -> dict:
    """A single-expiration chain shaped like get_options_chain()'s return
    value -- {underlying_price, calls, puts, summary, selected_expiration,
    expiration_dates}. Strikes run 70-130 in $5 steps so the +/-20% skew
    window always has enough same-side strikes to fill both wings.

    scenario:
      "normal_skew"   -- puts richer than calls (the equity-market norm)
      "inverted_skew" -- calls richer than puts (speculative upside chasing)
      "flat_skew"     -- roughly even both sides
    """
    strikes = [70 + 5 * i for i in range(13)]  # 70..130

    def iv_for(strike: float, side: str) -> float:
        moneyness = (strike - underlying) / underlying  # negative below spot
        base = 0.30
        if scenario == "normal_skew":
            # puts get richer the further OTM (moneyness more negative -> higher put IV)
            return base + (0.15 * max(-moneyness, 0) if side == "put" else 0.05 * max(moneyness, 0))
        if scenario == "inverted_skew":
            return base + (0.15 * max(moneyness, 0) if side == "call" else 0.05 * max(-moneyness, 0))
        return base  # flat_skew: same IV everywhere

    def contract(strike: float, side: str) -> dict:
        return {
            "contract_symbol": f"TEST{'C' if side == 'call' else 'P'}{int(strike)}",
            "strike": float(strike),
            "last_price": 1.5,
            "bid": 1.4,
            "ask": 1.6,
            "change": 0.0,
            "percent_change": 0.0,
            "volume": 500,
            "open_interest": 1000,
            "implied_volatility": round(iv_for(strike, side), 4),
            "in_the_money": False,
        }

    calls = [contract(s, "call") for s in strikes]
    puts = [contract(s, "put") for s in strikes]
    atm_strike = min(strikes, key=lambda s: abs(s - underlying))
    atm_call = next(c for c in calls if c["strike"] == atm_strike)
    atm_put = next(p for p in puts if p["strike"] == atm_strike)

    return {
        "symbol": "TEST",
        "underlying_price": underlying,
        "expiration_dates": [1_800_000_000],
        "selected_expiration": 1_800_000_000,
        "calls": calls,
        "puts": puts,
        "summary": {
            "call_volume": sum(c["volume"] for c in calls),
            "put_volume": sum(p["volume"] for p in puts),
            "call_open_interest": sum(c["open_interest"] for c in calls),
            "put_open_interest": sum(p["open_interest"] for p in puts),
            "put_call_volume_ratio": 1.0,
            "put_call_oi_ratio": 1.0,
            "atm_strike": atm_strike,
            "atm_call_iv": atm_call["implied_volatility"],
            "atm_put_iv": atm_put["implied_volatility"],
            "expected_move_atm_straddle": atm_call["last_price"] + atm_put["last_price"],
        },
    }


def term_structure_points(scenario: str = "backwardation") -> list[dict]:
    """Points shaped like get_iv_term_structure()'s `points` list, sorted by
    expiration ascending.

    scenario:
      "backwardation" -- front-month IV spikes above the rest (event pricing)
      "normal"        -- IV rises smoothly with time (no event signal)
    """
    now = 1_800_000_000
    day = 86400
    days_out = [3, 10, 17, 24, 45, 75]
    if scenario == "backwardation":
        ivs = [0.65, 0.35, 0.37, 0.40, 0.44, 0.50]  # front month spikes, rest rises smoothly
    else:
        ivs = [0.30, 0.33, 0.36, 0.39, 0.44, 0.50]  # smooth increase, no spike

    return [
        {"expiration": now + d * day, "atm_strike": 100.0, "call_iv": iv, "put_iv": iv}
        for d, iv in zip(days_out, ivs)
    ]
