import type { OptionsChain, TermStructurePoint } from "./api/client";

export function daysToExpiry(expirationSeconds: number): number {
  return Math.round((expirationSeconds * 1000 - Date.now()) / 86_400_000);
}

export function formatExpirationShort(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

export interface TermStructureRead {
  hasEventSignal: boolean;
  spikeLabel: string | null;
  spikeIv: number | null;
  summary: string;
}

/** Backwardation -- a near-term IV spike that falls back for later
 * expirations -- is the reliable, free signal that the market is pricing a
 * specific known event (earnings, a ruling) into that expiration, rather
 * than uncertainty smoothly increasing with time. Shared between the chart
 * caption and the AI panel's prompt so both describe the same finding. */
export function describeTermStructure(points: TermStructurePoint[]): TermStructureRead {
  const avgIvs = points.map((p) => ((p.call_iv ?? 0) + (p.put_iv ?? 0)) / 2);
  let spikeIndex = -1;
  for (let i = 0; i < avgIvs.length - 1; i++) {
    if (avgIvs[i] > avgIvs[i + 1] * 1.15) {
      spikeIndex = i;
      break;
    }
  }

  if (spikeIndex >= 0) {
    const p = points[spikeIndex];
    const label = `${formatExpirationShort(p.expiration)} (${daysToExpiry(p.expiration)}d out)`;
    return {
      hasEventSignal: true,
      spikeLabel: label,
      spikeIv: avgIvs[spikeIndex],
      summary: `IV spikes at the ${label} expiration then falls back for later ones (backwardation) — the market is pricing a specific event into that expiration, not a smooth increase in uncertainty over time.`,
    };
  }
  return {
    hasEventSignal: false,
    spikeLabel: null,
    spikeIv: null,
    summary: "IV rises smoothly across expirations — normal term structure, no sign of a specific event being priced into a near-term date.",
  };
}

export interface SkewRead {
  putWingIv: number | null;
  atmIv: number | null;
  callWingIv: number | null;
  direction: "normal" | "flat" | "inverted";
  summary: string;
}

/** Equity skew is normally negative: downside (put) protection costs more
 * than equivalent upside (call) exposure, because crashes are faster and
 * scarier than rallies. A flattening or inverted skew (calls bid richer
 * than puts) is the speculative-chasing pattern. Shared between the chart
 * caption and the AI panel's prompt. */
export function describeSkew(chain: OptionsChain, windowPct = 0.2): SkewRead {
  const underlying = chain.underlying_price ?? 0;
  const lo = underlying * (1 - windowPct);
  const hi = underlying * (1 + windowPct);

  const callsInWindow = chain.calls.filter((c) => c.strike >= lo && c.strike <= hi && c.implied_volatility != null);
  const putsInWindow = chain.puts.filter((p) => p.strike >= lo && p.strike <= hi && p.implied_volatility != null);

  const wingSize = 3;
  const putWing = [...putsInWindow].sort((a, b) => a.strike - b.strike).slice(0, wingSize);
  const callWing = [...callsInWindow].sort((a, b) => b.strike - a.strike).slice(0, wingSize);
  const avg = (arr: { implied_volatility: number | null }[]) =>
    arr.length ? arr.reduce((s, c) => s + (c.implied_volatility ?? 0), 0) / arr.length : null;

  const putWingIv = avg(putWing);
  const callWingIv = avg(callWing);
  const atmStrike = chain.summary.atm_strike;
  const atmIv = atmStrike != null ? chain.summary.atm_call_iv ?? chain.summary.atm_put_iv : null;

  let direction: SkewRead["direction"] = "flat";
  let summary = "Not enough near-the-money strikes with quoted IV on both sides to read skew direction.";
  if (putWingIv != null && callWingIv != null) {
    const diff = putWingIv - callWingIv;
    if (diff > 0.03) {
      direction = "normal";
      summary = `Put-side IV runs ${((diff) * 100).toFixed(0)} points above call-side IV at the wings — normal downside skew (crash protection costs more than equivalent upside).`;
    } else if (diff < -0.03) {
      direction = "inverted";
      summary = `Call-side IV runs ${((-diff) * 100).toFixed(0)} points above put-side IV at the wings — inverted skew, an unusual pattern that typically shows up during speculative upside chasing.`;
    } else {
      direction = "flat";
      summary = "Put- and call-side IV are roughly even at the wings — flat skew, no strong directional hedging or speculative bias visible.";
    }
  }

  return { putWingIv, atmIv, callWingIv, direction, summary };
}
