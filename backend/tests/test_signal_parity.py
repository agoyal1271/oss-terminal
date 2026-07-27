"""Cross-language correctness tests: prove backend/app/signals/*.py (hand-
ported from TypeScript for the daily scanner) produces the SAME numbers as
frontend/src/technicals.ts and optionsAnalysis.ts (the shipping UI code)
on identical input.

Why this exists: the Python side was hand-translated line by line and
never actually run side-by-side against the TypeScript until now. A silent
divergence here -- a threshold typo, an off-by-one window, a sign flip --
would corrupt every downstream finding (the daily Slack digest, the
two-week evidence tally in scripts/ask.py) while looking completely
normal, because nothing crashes when a formula is subtly wrong. This is
what "check for accuracy of signal" means before adding a forward-return
hit-rate layer on top: if the signal itself is miscomputed, a forward-
return study would just be measuring the wrong thing precisely.

Run: cd backend && ../venv/bin/pytest tests/test_signal_parity.py -v
(Needs node/npx on PATH -- see conftest.py for the skip behavior if not.)
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.signals import options_signals
from app.signals import technicals as py_technicals
from tests import fixtures

APPROX = pytest.approx

# ---------------------------------------------------------------------------
# Primitives: sma / rsi / ATR%
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy", "volume_spike"])
@pytest.mark.parametrize("period", [20, 50, 200])
def test_sma_parity(call_ts, kind, period):
    points = fixtures.price_series(n=320, kind=kind)
    closes = [p["close"] for p in points]

    ts_result = call_ts("technicals", "sma", closes, period)
    py_result = py_technicals.sma(closes, period)

    assert len(ts_result) == len(py_result)
    for i, (ts_v, py_v) in enumerate(zip(ts_result, py_result)):
        assert ts_v == APPROX(py_v, abs=1e-9) if ts_v is not None else py_v is None, (
            f"sma(period={period}, kind={kind}) diverges at index {i}: ts={ts_v} py={py_v}"
        )


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy", "volume_spike"])
def test_rsi_parity(call_ts, kind):
    points = fixtures.price_series(n=320, kind=kind)
    closes = [p["close"] for p in points]

    ts_result = call_ts("technicals", "rsi", closes, 14)
    py_result = py_technicals.rsi(closes, 14)

    assert len(ts_result) == len(py_result)
    for i, (ts_v, py_v) in enumerate(zip(ts_result, py_result)):
        if ts_v is None or py_v is None:
            assert ts_v is None and py_v is None, f"rsi diverges on None-ness at index {i}: ts={ts_v} py={py_v}"
        else:
            assert ts_v == APPROX(py_v, abs=1e-6), f"rsi(kind={kind}) diverges at index {i}: ts={ts_v} py={py_v}"


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy"])
def test_atr_pct_parity(call_ts, kind):
    points = fixtures.price_series(n=320, kind=kind)

    ts_val = call_ts("technicals", "averageTrueRangePct", points, 14)
    py_val = py_technicals.average_true_range_pct(points, 14)

    assert ts_val == APPROX(py_val, rel=1e-6)


# ---------------------------------------------------------------------------
# compute_technical_read: the derived "current state" snapshot AND, via a
# truncated-series trick, the "prev" values and the 52-week PRIOR window --
# the two things that make the Python version deliberately structurally
# different from the TS one (see technicals.py's module docstring).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy", "volume_spike"])
def test_compute_technical_read_current_values(call_ts, kind):
    points = fixtures.price_series(n=320, kind=kind)

    ts_read = call_ts("technicals", "computeTechnicalRead", points)
    py_read = py_technicals.compute_technical_read(points)

    assert py_read is not None and ts_read is not None
    assert ts_read["latestClose"] == APPROX(py_read.latest_close)
    assert ts_read["sma50"] == APPROX(py_read.sma50)
    assert ts_read["sma200"] == APPROX(py_read.sma200)
    assert ts_read["rsi14"] == APPROX(py_read.rsi14, abs=1e-6)
    assert ts_read["volume"]["ratio"] == APPROX(py_read.volume_ratio, rel=1e-6)
    assert ts_read["volatility"]["atrPct"] == APPROX(py_read.atr_pct, rel=1e-6)


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy"])
def test_compute_technical_read_prev_values_match_yesterdays_snapshot(call_ts, kind):
    """Python's sma50_prev/sma200_prev/rsi14_prev (computed on the full
    series) should equal the TS *current* values computed on the series
    with today's bar removed -- "yesterday's SMA" is the same number
    whether you (a) look at index -2 of an array computed over N bars, or
    (b) recompute the array over N-1 bars and take the last one. If the
    Python port's indexing is off by one, this is the check that would
    catch it even though the single-series parity tests above would not
    (they'd both be internally self-consistent, just consistently wrong
    together)."""
    points = fixtures.price_series(n=320, kind=kind)

    py_read = py_technicals.compute_technical_read(points)
    ts_read_yesterday = call_ts("technicals", "computeTechnicalRead", points[:-1])

    assert py_read.sma50_prev == APPROX(ts_read_yesterday["sma50"])
    assert py_read.sma200_prev == APPROX(ts_read_yesterday["sma200"])
    assert py_read.rsi14_prev == APPROX(ts_read_yesterday["rsi14"], abs=1e-6)
    assert py_read.prev_close == APPROX(ts_read_yesterday["latestClose"])


@pytest.mark.parametrize("kind", ["uptrend", "downtrend", "choppy"])
def test_52w_prior_window_matches_ts_window_excluding_today(call_ts, kind):
    """Same trick for the 52-week extremes: Python's high_52w_prior/
    low_52w_prior (computed on the full series, deliberately excluding
    today) should equal the TS range52w.high/low computed on the series
    with today's bar removed."""
    points = fixtures.price_series(n=320, kind=kind)

    py_read = py_technicals.compute_technical_read(points)
    ts_read_yesterday = call_ts("technicals", "computeTechnicalRead", points[:-1])

    assert py_read.high_52w_prior == APPROX(ts_read_yesterday["range52w"]["high"])
    assert py_read.low_52w_prior == APPROX(ts_read_yesterday["range52w"]["low"])


# ---------------------------------------------------------------------------
# Options: term structure (backwardation) + skew
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["backwardation", "normal"])
def test_term_structure_parity(call_ts, scenario):
    points = fixtures.term_structure_points(scenario)

    ts_read = call_ts("optionsAnalysis", "describeTermStructure", points)
    py_read = options_signals.describe_term_structure(points)

    assert ts_read["hasEventSignal"] == py_read.has_event_signal
    if py_read.has_event_signal:
        assert ts_read["spikeIv"] == APPROX(py_read.spike_iv, abs=1e-9)
    else:
        assert ts_read["spikeIv"] is None and py_read.spike_iv is None


@pytest.mark.parametrize("scenario", ["normal_skew", "inverted_skew", "flat_skew"])
def test_skew_parity(call_ts, scenario):
    chain = fixtures.options_chain(scenario)

    ts_read = call_ts("optionsAnalysis", "describeSkew", chain)
    py_read = options_signals.describe_skew(chain)

    assert ts_read["putWingIv"] == APPROX(py_read.put_wing_iv, abs=1e-9)
    assert ts_read["callWingIv"] == APPROX(py_read.call_wing_iv, abs=1e-9)
    assert ts_read["direction"] == py_read.direction
