import { useEffect, useMemo, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type PriceHistory } from "../api/client";
import { computeTechnicalRead, type Signal } from "../technicals";
import { fmtPercent, fmtUsd } from "../format";

const SIGNAL_LABEL: Record<Signal, string> = { bullish: "Bullish", bearish: "Bearish", neutral: "Neutral" };

function SignalBadge({ signal }: { signal: Signal }) {
  return <span className={`signal-badge signal-${signal}`}>{SIGNAL_LABEL[signal]}</span>;
}

export function TechnicalPanel({ ticker }: { ticker: string }) {
  const [history, setHistory] = useState<PriceHistory | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setHistory(null);
    setError(null);
    api.companyPrices(ticker, "2y").then(setHistory).catch((e) => setError(e.message));
  }, [ticker]);

  const read = useMemo(() => (history ? computeTechnicalRead(history.points) : null), [history]);

  if (error) return <div className="empty-note">Technical data unavailable: {error}</div>;
  if (!history || !read) return <div className="empty-note">Loading…</div>;

  const chartData = read.chartSeries.slice(-260); // ~1y of overlay for readability

  return (
    <div className="technical-panel">
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--gridline)" />
          <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} minTickGap={50} />
          <YAxis domain={["auto", "auto"]} tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtUsd(v, 0)} width={56} />
          <Tooltip
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
            formatter={(value, name) => [value === null ? "—" : fmtUsd(Number(value)), name]}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />
          <Line type="monotone" dataKey="close" name="Price" stroke="var(--series-1)" strokeWidth={2} dot={false} />
          <Line type="monotone" dataKey="sma50" name="50-day SMA" stroke="var(--series-3)" strokeWidth={1.5} dot={false} />
          <Line type="monotone" dataKey="sma200" name="200-day SMA" stroke="var(--series-5)" strokeWidth={1.5} dot={false} />
        </LineChart>
      </ResponsiveContainer>

      <div className="technical-checklist">
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">Trend (50/200-day SMA)</span>
            <SignalBadge signal={read.trend.signal} />
          </div>
          <p>{read.trend.label}</p>
        </div>
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">Momentum (RSI-14)</span>
            <SignalBadge signal={read.momentum.signal} />
          </div>
          <p>{read.momentum.label}</p>
        </div>
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">Volume confirmation</span>
            <SignalBadge signal={read.volume.signal} />
          </div>
          <p>{read.volume.label}</p>
        </div>
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">52-week range position</span>
          </div>
          <p>
            {fmtUsd(read.range52w.low, 0)} – {fmtUsd(read.range52w.high, 0)} · currently {fmtPercent(read.range52w.pctFromHigh)} from the high and{" "}
            {fmtPercent(read.range52w.pctFromLow)} from the low.
          </p>
        </div>
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">Near-term support / resistance (20-day)</span>
          </div>
          <p>
            Support around {fmtUsd(read.nearTerm.support, 0)}, resistance around {fmtUsd(read.nearTerm.resistance, 0)} — the recent trading range price has
            respected over the last month.
          </p>
        </div>
        <div className="tech-row">
          <div className="tech-row-head">
            <span className="tech-row-label">Volatility (ATR-14)</span>
          </div>
          <p>{read.volatility.label}</p>
        </div>
      </div>
      <p className="source-note">
        Computed client-side from Yahoo Finance daily price history (SMA, RSI-14, ATR-14 — standard formulas, not
        fabricated signals). Technical indicators describe recent price/volume behavior, not a prediction — pair with
        the fundamentals above, not instead of them.
      </p>
    </div>
  );
}
