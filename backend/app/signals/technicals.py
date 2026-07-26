"""Python port of frontend/src/technicals.ts, for the daily scanner.

The formulas and thresholds are deliberately identical to the TypeScript
version that powers the Technical Read panel -- same SMA, same Wilder RSI,
same ATR -- so the scanner can never disagree with what the UI shows for
the same ticker on the same day.

The one deliberate difference: the UI only needs *today's* values to
describe current state, while the scanner needs **yesterday's too**, so it
can fire on a *crossing* (sma50 crossed sma200 today) rather than a state
(sma50 is above sma200, which would alert every day forever). Everything
here therefore returns series or explicit prev/current pairs rather than
collapsing to a single reading.
"""

from __future__ import annotations

from dataclasses import dataclass


def sma(values: list[float | None], period: int) -> list[float | None]:
    """Simple moving average; None until a full window of real values."""
    out: list[float | None] = [None] * len(values)
    total = 0.0
    count = 0
    for i, v in enumerate(values):
        if v is not None:
            total += v
            count += 1
        if i >= period:
            drop = values[i - period]
            if drop is not None:
                total -= drop
                count -= 1
        out[i] = (total / period) if (i >= period - 1 and count == period) else None
    return out


def rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """Wilder's RSI -- the standard 14-period formula charting platforms use."""
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out

    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = closes[i] - closes[i - 1]
        avg_gain += max(change, 0.0)
        avg_loss += max(-change, 0.0)
    avg_gain /= period
    avg_loss /= period
    out[period] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)

    for i in range(period + 1, len(closes)):
        change = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(change, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0.0)) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1 + avg_gain / avg_loss)
    return out


def average_true_range_pct(points: list[dict], period: int = 14) -> float | None:
    if len(points) < period + 1:
        return None
    true_ranges: list[float] = []
    for i in range(1, len(points)):
        high, low = points[i].get("high"), points[i].get("low")
        prev_close = points[i - 1]["close"]
        if high is None or low is None:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    recent = true_ranges[-period:]
    if len(recent) < period:
        return None
    return (sum(recent) / len(recent)) / points[-1]["close"]


@dataclass
class TechnicalRead:
    latest_close: float
    prev_close: float | None
    sma50: float | None
    sma50_prev: float | None
    sma200: float | None
    sma200_prev: float | None
    rsi14: float | None
    rsi14_prev: float | None
    volume_ratio: float | None
    # 52-week extremes computed EXCLUDING today, so "today broke the high"
    # is a real breakout rather than trivially true (today's own high is
    # always part of a window that includes today).
    high_52w_prior: float | None
    low_52w_prior: float | None
    atr_pct: float | None


def compute_technical_read(points: list[dict]) -> TechnicalRead | None:
    """`points` are price bars from /api/companies/{ticker}/prices, oldest first."""
    if len(points) < 30:
        return None

    closes = [p["close"] for p in points]
    sma50_arr = sma(list(closes), 50)
    sma200_arr = sma(list(closes), 200)
    rsi_arr = rsi(closes, 14)

    def at(arr: list, idx: int):
        return arr[idx] if len(arr) >= abs(idx) else None

    volumes = [p.get("volume") or 0 for p in points]
    recent_vol = volumes[-10:]
    baseline_vol = volumes[-60:-10]
    recent_avg = sum(recent_vol) / len(recent_vol) if recent_vol else 0.0
    baseline_avg = (sum(baseline_vol) / len(baseline_vol)) if baseline_vol else None
    vol_ratio = (recent_avg / baseline_avg) if (baseline_avg and baseline_avg > 0) else None

    highs = [(p.get("high") if p.get("high") is not None else p["close"]) for p in points]
    lows = [(p.get("low") if p.get("low") is not None else p["close"]) for p in points]
    # Exclude the final bar: the window is the *prior* 52 weeks.
    prior_highs = highs[-253:-1]
    prior_lows = lows[-253:-1]

    return TechnicalRead(
        latest_close=closes[-1],
        prev_close=at(closes, -2),
        sma50=at(sma50_arr, -1),
        sma50_prev=at(sma50_arr, -2),
        sma200=at(sma200_arr, -1),
        sma200_prev=at(sma200_arr, -2),
        rsi14=at(rsi_arr, -1),
        rsi14_prev=at(rsi_arr, -2),
        volume_ratio=vol_ratio,
        high_52w_prior=max(prior_highs) if prior_highs else None,
        low_52w_prior=min(prior_lows) if prior_lows else None,
        atr_pct=average_true_range_pct(points, 14),
    )
