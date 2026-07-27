"""Black-Scholes option Greeks and probability estimates -- stdlib only
(matching every other script in this directory), no numpy/scipy.

Every number this module produces is DERIVED, not invented: it's computed
from inputs already sitting in the options chain we fetch anyway (strike,
underlying price, implied volatility, days to expiration) plus one assumed
constant (a short-term risk-free rate). That's a meaningfully different
claim than "the model estimated a Greek" -- these are the same formulas a
broker's platform uses, just run here in the open on the same IV the chain
already reports.

Two honest caveats worth keeping in mind wherever these are surfaced:
  1. Black-Scholes assumes no dividends and constant volatility to expiry.
     A broker's own Greeks may differ slightly (dividend adjustment, a
     different vol surface model) -- these are theoretical estimates, not
     a quoted number from any exchange or broker.
  2. Probability-of-profit here is the *risk-neutral* probability the
     underlying finishes beyond a breakeven price, not a real-world
     forecast probability. The two differ (risk-neutral pricing embeds a
     volatility/variance risk premium), but risk-neutral is the standard,
     defensible convention for this kind of estimate and is what most
     retail platforms show under "probability of profit."
"""

from __future__ import annotations

import math

# Approximate short-term T-bill yield. Greeks/POP are not highly sensitive
# to small errors here over few-month tenors, so a fixed constant (rather
# than fetching a live rate, which would add a network dependency for
# minimal accuracy gain) is a deliberate simplification.
RISK_FREE_RATE = 0.045


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-x * x / 2.0) / math.sqrt(2.0 * math.pi)


def _d1_d2(spot: float, strike: float, years: float, sigma: float, r: float) -> tuple[float, float] | tuple[None, None]:
    if spot <= 0 or strike <= 0 or years <= 0 or sigma <= 0:
        return None, None
    d1 = (math.log(spot / strike) + (r + sigma * sigma / 2.0) * years) / (sigma * math.sqrt(years))
    d2 = d1 - sigma * math.sqrt(years)
    return d1, d2


def years_from_days(days_to_expiry: float) -> float:
    return max(days_to_expiry, 0.0) / 365.0


def expected_move(spot: float, sigma: float, days_to_expiry: float) -> float | None:
    """One-standard-deviation implied move by expiration: spot * sigma *
    sqrt(t). This is the same "market-implied expected move" convention
    options desks use, just computed straight from IV rather than needing
    a liquid ATM straddle price (which the near-term two-week window uses,
    but which gets thin/unreliable at 3-6 month tenors on a name like
    SPCX)."""
    t = years_from_days(days_to_expiry)
    if spot is None or sigma is None or t <= 0:
        return None
    return spot * sigma * math.sqrt(t)


def bs_price(spot: float, strike: float, days_to_expiry: float, sigma: float, option_type: str, r: float = RISK_FREE_RATE) -> float | None:
    d1, d2 = _d1_d2(spot, strike, years_from_days(days_to_expiry), sigma, r)
    if d1 is None:
        return None
    t = years_from_days(days_to_expiry)
    if option_type == "call":
        return spot * _norm_cdf(d1) - strike * math.exp(-r * t) * _norm_cdf(d2)
    return strike * math.exp(-r * t) * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def greeks(spot: float, strike: float, days_to_expiry: float, sigma: float, option_type: str, r: float = RISK_FREE_RATE) -> dict | None:
    """Delta, gamma, theta (per calendar day, not per year), and vega (per
    1 percentage point of IV, e.g. 30% -> 31%) for a single contract."""
    t = years_from_days(days_to_expiry)
    d1, d2 = _d1_d2(spot, strike, t, sigma, r)
    if d1 is None:
        return None
    pdf_d1 = _norm_pdf(d1)
    if option_type == "call":
        delta = _norm_cdf(d1)
        theta_annual = (-(spot * pdf_d1 * sigma) / (2 * math.sqrt(t))) - r * strike * math.exp(-r * t) * _norm_cdf(d2)
        prob_itm = _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1.0
        theta_annual = (-(spot * pdf_d1 * sigma) / (2 * math.sqrt(t))) + r * strike * math.exp(-r * t) * _norm_cdf(-d2)
        prob_itm = _norm_cdf(-d2)
    gamma = pdf_d1 / (spot * sigma * math.sqrt(t))
    vega = spot * pdf_d1 * math.sqrt(t) / 100.0
    return {
        "delta": delta,
        "gamma": gamma,
        "theta_per_day": theta_annual / 365.0,
        "vega": vega,
        "prob_itm_at_expiry": prob_itm,
    }


def prob_above(spot: float, price_level: float, days_to_expiry: float, sigma: float, r: float = RISK_FREE_RATE) -> float | None:
    """Risk-neutral probability the underlying finishes ABOVE `price_level`
    at expiration -- N(d2) with `price_level` standing in for the strike.
    Used to turn a breakeven price into a probability-of-profit estimate
    for any single- or multi-leg structure."""
    d1, d2 = _d1_d2(spot, price_level, years_from_days(days_to_expiry), sigma, r)
    if d2 is None:
        return None
    return _norm_cdf(d2)


def prob_below(spot: float, price_level: float, days_to_expiry: float, sigma: float, r: float = RISK_FREE_RATE) -> float | None:
    p = prob_above(spot, price_level, days_to_expiry, sigma, r)
    return None if p is None else 1.0 - p


def intrinsic_value(spot: float, strike: float, option_type: str) -> float:
    return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)
