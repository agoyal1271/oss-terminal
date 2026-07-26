"""Python port of frontend/src/optionsAnalysis.ts, for the daily scanner.

Identical thresholds to the TypeScript version powering the IV term
structure and skew chart captions in the UI (1.15x backwardation spike,
+/-0.03 absolute IV skew direction, +/-20% strike window, 3-contract wings)
-- ported, not re-derived, so the scanner can't disagree with what a user
sees on the Options screen for the same chain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TermStructureRead:
    has_event_signal: bool
    spike_expiration: int | None
    spike_iv: float | None
    summary: str


def describe_term_structure(points: list[dict]) -> TermStructureRead:
    """`points` = [{expiration, atm_strike, call_iv, put_iv}, ...] sorted by
    expiration ascending, as returned by get_iv_term_structure()."""
    avg_ivs = [((p.get("call_iv") or 0) + (p.get("put_iv") or 0)) / 2 for p in points]
    spike_index = -1
    for i in range(len(avg_ivs) - 1):
        if avg_ivs[i] > avg_ivs[i + 1] * 1.15:
            spike_index = i
            break

    if spike_index >= 0:
        p = points[spike_index]
        return TermStructureRead(
            has_event_signal=True,
            spike_expiration=p["expiration"],
            spike_iv=avg_ivs[spike_index],
            summary=(
                f"IV spikes at expiration {p['expiration']} then falls back for later ones (backwardation) -- "
                "the market is pricing a specific event into that expiration, not a smooth increase in "
                "uncertainty over time."
            ),
        )
    return TermStructureRead(
        has_event_signal=False,
        spike_expiration=None,
        spike_iv=None,
        summary="IV rises smoothly across expirations -- normal term structure, no sign of a specific event.",
    )


@dataclass
class SkewRead:
    put_wing_iv: float | None
    call_wing_iv: float | None
    direction: str  # "normal" | "flat" | "inverted"
    summary: str


def describe_skew(chain: dict, window_pct: float = 0.2) -> SkewRead:
    """`chain` is the raw dict from get_options_chain() (calls/puts/summary)."""
    underlying = chain.get("underlying_price") or 0
    lo, hi = underlying * (1 - window_pct), underlying * (1 + window_pct)

    calls_in_window = [c for c in chain["calls"] if lo <= c["strike"] <= hi and c["implied_volatility"] is not None]
    puts_in_window = [p for p in chain["puts"] if lo <= p["strike"] <= hi and p["implied_volatility"] is not None]

    wing_size = 3
    put_wing = sorted(puts_in_window, key=lambda p: p["strike"])[:wing_size]
    call_wing = sorted(calls_in_window, key=lambda c: -c["strike"])[:wing_size]

    def avg_iv(contracts: list[dict]) -> float | None:
        return (sum(c["implied_volatility"] for c in contracts) / len(contracts)) if contracts else None

    put_wing_iv = avg_iv(put_wing)
    call_wing_iv = avg_iv(call_wing)

    direction = "flat"
    summary = "Not enough near-the-money strikes with quoted IV on both sides to read skew direction."
    if put_wing_iv is not None and call_wing_iv is not None:
        diff = put_wing_iv - call_wing_iv
        if diff > 0.03:
            direction = "normal"
            summary = f"Put-side IV runs {diff * 100:.0f} points above call-side IV at the wings -- normal downside skew."
        elif diff < -0.03:
            direction = "inverted"
            summary = f"Call-side IV runs {-diff * 100:.0f} points above put-side IV at the wings -- inverted skew (speculative upside chasing)."
        else:
            direction = "flat"
            summary = "Put- and call-side IV are roughly even at the wings -- flat skew."

    return SkewRead(put_wing_iv=put_wing_iv, call_wing_iv=call_wing_iv, direction=direction, summary=summary)
