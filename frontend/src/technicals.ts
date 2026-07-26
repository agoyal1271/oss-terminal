import type { PricePoint } from "./api/client";

export type Signal = "bullish" | "bearish" | "neutral";

function sma(values: (number | null)[], period: number): (number | null)[] {
  const out: (number | null)[] = new Array(values.length).fill(null);
  let sum = 0;
  let count = 0;
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    if (v !== null) {
      sum += v;
      count++;
    }
    if (i >= period) {
      const drop = values[i - period];
      if (drop !== null) {
        sum -= drop;
        count--;
      }
    }
    out[i] = i >= period - 1 && count === period ? sum / period : null;
  }
  return out;
}

// Wilder's RSI (the standard 14-period formula used by most charting platforms).
function rsi(closes: number[], period = 14): (number | null)[] {
  const out: (number | null)[] = new Array(closes.length).fill(null);
  if (closes.length < period + 1) return out;

  let avgGain = 0;
  let avgLoss = 0;
  for (let i = 1; i <= period; i++) {
    const change = closes[i] - closes[i - 1];
    avgGain += Math.max(change, 0);
    avgLoss += Math.max(-change, 0);
  }
  avgGain /= period;
  avgLoss /= period;
  out[period] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);

  for (let i = period + 1; i < closes.length; i++) {
    const change = closes[i] - closes[i - 1];
    const gain = Math.max(change, 0);
    const loss = Math.max(-change, 0);
    avgGain = (avgGain * (period - 1) + gain) / period;
    avgLoss = (avgLoss * (period - 1) + loss) / period;
    out[i] = avgLoss === 0 ? 100 : 100 - 100 / (1 + avgGain / avgLoss);
  }
  return out;
}

function averageTrueRangePct(points: PricePoint[], period = 14): number | null {
  if (points.length < period + 1) return null;
  const trueRanges: number[] = [];
  for (let i = 1; i < points.length; i++) {
    const { high, low } = points[i];
    const prevClose = points[i - 1].close;
    if (high === null || low === null) continue;
    trueRanges.push(Math.max(high - low, Math.abs(high - prevClose), Math.abs(low - prevClose)));
  }
  const recent = trueRanges.slice(-period);
  if (recent.length < period) return null;
  const atr = recent.reduce((a, b) => a + b, 0) / recent.length;
  return atr / points[points.length - 1].close;
}

export interface TechnicalRead {
  latestClose: number;
  sma20: number | null;
  sma50: number | null;
  sma200: number | null;
  rsi14: number | null;
  trend: { signal: Signal; label: string };
  momentum: { signal: Signal; label: string };
  volume: { signal: Signal; label: string; ratio: number | null };
  range52w: { high: number; low: number; pctFromHigh: number; pctFromLow: number };
  nearTerm: { support: number; resistance: number };
  volatility: { atrPct: number | null; label: string };
  chartSeries: { date: string; close: number; sma50: number | null; sma200: number | null }[];
}

export function computeTechnicalRead(points: PricePoint[]): TechnicalRead | null {
  if (points.length < 30) return null;

  const closes = points.map((p) => p.close);
  const sma20Arr = sma(closes, 20);
  const sma50Arr = sma(closes, 50);
  const sma200Arr = sma(closes, 200);
  const rsiArr = rsi(closes, 14);

  const last = closes.length - 1;
  const latestClose = closes[last];
  const sma20v = sma20Arr[last];
  const sma50v = sma50Arr[last];
  const sma200v = sma200Arr[last];
  const rsiV = rsiArr[last];

  // Trend: classic moving-average structure a technical trader checks first.
  let trendSignal: Signal = "neutral";
  let trendLabel = "Not enough history to assess trend (need 200+ trading days).";
  if (sma50v !== null && sma200v !== null) {
    const aboveBoth = latestClose > sma50v && latestClose > sma200v;
    const belowBoth = latestClose < sma50v && latestClose < sma200v;
    const goldenCross = sma50v > sma200v;
    if (aboveBoth && goldenCross) {
      trendSignal = "bullish";
      trendLabel = "Uptrend: price is above both the 50- and 200-day moving averages, and the 50-day is above the 200-day (golden cross) — bullish trend structure.";
    } else if (belowBoth && !goldenCross) {
      trendSignal = "bearish";
      trendLabel = "Downtrend: price is below both moving averages, and the 50-day is below the 200-day (death cross) — bearish trend structure.";
    } else {
      trendSignal = "neutral";
      trendLabel = `Mixed trend: price and moving averages disagree (${goldenCross ? "50-day above" : "50-day below"} 200-day) — no clean directional structure right now.`;
    }
  }

  // Momentum: RSI(14), the standard overbought/oversold oscillator.
  let momentumSignal: Signal = "neutral";
  let momentumLabel = "RSI unavailable (need 15+ trading days).";
  if (rsiV !== null) {
    if (rsiV >= 70) {
      momentumSignal = "bearish";
      momentumLabel = `RSI ${rsiV.toFixed(0)} — overbought (>70). Momentum has run hot; often precedes a pullback or consolidation, though strong trends can stay overbought a while.`;
    } else if (rsiV <= 30) {
      momentumSignal = "bullish";
      momentumLabel = `RSI ${rsiV.toFixed(0)} — oversold (<30). Selling has been aggressive; often precedes a bounce, though downtrends can stay oversold a while.`;
    } else {
      momentumSignal = "neutral";
      momentumLabel = `RSI ${rsiV.toFixed(0)} — neutral range (30–70), no momentum extreme.`;
    }
  }

  // Volume: is recent participation confirming the move, or is the move on thin volume?
  const volumes = points.map((p) => p.volume ?? 0);
  const recentVol = volumes.slice(-10);
  const baselineVol = volumes.slice(-60, -10);
  const recentAvg = recentVol.reduce((a, b) => a + b, 0) / (recentVol.length || 1);
  const baselineAvg = baselineVol.length ? baselineVol.reduce((a, b) => a + b, 0) / baselineVol.length : null;
  const volRatio = baselineAvg && baselineAvg > 0 ? recentAvg / baselineAvg : null;
  let volumeSignal: Signal = "neutral";
  let volumeLabel = "Not enough volume history to compare.";
  if (volRatio !== null) {
    if (volRatio >= 1.3) {
      volumeSignal = trendSignal === "bearish" ? "bearish" : "bullish";
      volumeLabel = `10-day average volume is ${volRatio.toFixed(1)}x the prior 50-day average — elevated participation, which tends to confirm the current move rather than a low-conviction drift.`;
    } else if (volRatio <= 0.7) {
      volumeSignal = "neutral";
      volumeLabel = `10-day average volume is only ${volRatio.toFixed(1)}x the prior 50-day average — the recent move is happening on light volume, which is a weaker signal.`;
    } else {
      volumeSignal = "neutral";
      volumeLabel = `10-day average volume is in line with the prior 50-day average (${volRatio.toFixed(1)}x) — no unusual participation either way.`;
    }
  }

  // Position in the 52-week range (from actual daily highs/lows, not just closes).
  const highs = points.map((p) => p.high ?? p.close);
  const lows = points.map((p) => p.low ?? p.close);
  const window52w = Math.min(points.length, 252);
  const high52w = Math.max(...highs.slice(-window52w));
  const low52w = Math.min(...lows.slice(-window52w));
  const pctFromHigh = (latestClose - high52w) / high52w;
  const pctFromLow = (latestClose - low52w) / low52w;

  // Near-term support/resistance: simple 20-trading-day (~1 month) swing range.
  const window20 = Math.min(points.length, 20);
  const support = Math.min(...lows.slice(-window20));
  const resistance = Math.max(...highs.slice(-window20));

  const atrPct = averageTrueRangePct(points, 14);
  let volatilityLabel = "Not enough data to compute ATR.";
  if (atrPct !== null) {
    const level = atrPct > 0.035 ? "high" : atrPct < 0.015 ? "low" : "moderate";
    volatilityLabel = `Average true range is ${(atrPct * 100).toFixed(1)}% of price per day (${level} volatility) — sets a rough expectation for normal day-to-day swings.`;
  }

  const chartSeries = points.map((p, i) => ({
    date: p.date,
    close: p.close,
    sma50: sma50Arr[i],
    sma200: sma200Arr[i],
  }));

  return {
    latestClose,
    sma20: sma20v,
    sma50: sma50v,
    sma200: sma200v,
    rsi14: rsiV,
    trend: { signal: trendSignal, label: trendLabel },
    momentum: { signal: momentumSignal, label: momentumLabel },
    volume: { signal: volumeSignal, label: volumeLabel, ratio: volRatio },
    range52w: { high: high52w, low: low52w, pctFromHigh, pctFromLow },
    nearTerm: { support, resistance },
    volatility: { atrPct, label: volatilityLabel },
    chartSeries,
  };
}
