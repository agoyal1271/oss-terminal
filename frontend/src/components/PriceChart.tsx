import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type PriceHistory } from "../api/client";
import { fmtUsd } from "../format";

interface PivotLevels {
  pivot: number;
  r1: number;
  r2: number;
  s1: number;
  s2: number;
}

function calculatePivotPoints(high: number, low: number, close: number): PivotLevels {
  const pivot = (high + low + close) / 3;
  return {
    pivot,
    r1: 2 * pivot - low,
    r2: pivot + (high - low),
    s1: 2 * pivot - high,
    s2: pivot - (high - low),
  };
}

const RANGES = [
  { key: "1m", label: "1M" },
  { key: "6m", label: "6M" },
  { key: "1y", label: "1Y" },
  { key: "5y", label: "5Y" },
  { key: "max", label: "Max" },
];

export function PriceChart({ ticker }: { ticker: string }) {
  const [range, setRange] = useState("1y");
  const [data, setData] = useState<PriceHistory | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showPivots, setShowPivots] = useState(true);

  useEffect(() => {
    setData(null);
    setError(null);
    api.companyPrices(ticker, range).then(setData).catch((e) => setError(e.message));
  }, [ticker, range]);

  const points = (data?.points ?? []).map((p) => ({ date: p.date, close: p.close }));
  const isUp = points.length > 1 && points[points.length - 1].close >= points[0].close;

  // Calculate pivot points from yesterday's OHLC (second-to-last point if available)
  const pivots: PivotLevels | null =
    data?.points && data.points.length >= 2
      ? (() => {
          const yesterday = data.points[data.points.length - 2];
          const high = yesterday.high ?? data.fifty_two_week_high;
          const low = yesterday.low ?? data.fifty_two_week_low;
          const close = yesterday.close;
          return calculatePivotPoints(high, low, close);
        })()
      : null;

  return (
    <div>
      <div className="price-header">
        <div>
          {data && (
            <>
              <span className="price-now">{fmtUsd(data.regular_market_price)}</span>
              <span className="price-meta"> {data.currency} · {data.exchange}</span>
            </>
          )}
        </div>
        <div className="range-tabs">
          {RANGES.map((r) => (
            <button key={r.key} className={r.key === range ? "active" : ""} onClick={() => setRange(r.key)}>
              {r.label}
            </button>
          ))}
          {pivots && (
            <label style={{ marginLeft: "12px", display: "flex", alignItems: "center", gap: "6px", fontSize: "13px", color: "var(--text-secondary)" }}>
              <input type="checkbox" checked={showPivots} onChange={(e) => setShowPivots(e.target.checked)} style={{ cursor: "pointer" }} />
              Pivot Points
            </label>
          )}
        </div>
      </div>

      {error && <div className="empty-note">Price data unavailable: {error}</div>}

      {points.length > 0 && (
        <ResponsiveContainer width="100%" height={240}>
          <AreaChart data={points} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
            <defs>
              <linearGradient id="priceFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor={isUp ? "var(--series-1)" : "var(--danger)"} stopOpacity={0.25} />
                <stop offset="100%" stopColor={isUp ? "var(--series-1)" : "var(--danger)"} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--gridline)" />
            <XAxis dataKey="date" tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} minTickGap={40} />
            <YAxis
              domain={["auto", "auto"]}
              tick={{ fill: "var(--text-muted)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
              tickFormatter={(v) => fmtUsd(v, 0)}
              width={56}
            />
            <Tooltip
              contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 13 }}
              labelStyle={{ color: "var(--text-primary)" }}
              formatter={(value) => [fmtUsd(Number(value)), "Close"]}
            />
            <Area type="monotone" dataKey="close" stroke={isUp ? "var(--series-1)" : "var(--danger)"} strokeWidth={2} fill="url(#priceFill)" />

            {showPivots && pivots && (
              <>
                <ReferenceLine y={pivots.r2} stroke="var(--danger)" strokeDasharray="4 4" opacity={0.6} label={{ value: `R2 ${fmtUsd(pivots.r2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={pivots.r1} stroke="var(--series-2)" strokeDasharray="4 4" opacity={0.6} label={{ value: `R1 ${fmtUsd(pivots.r1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={pivots.pivot} stroke="var(--series-3)" strokeWidth={1} opacity={0.7} label={{ value: `P ${fmtUsd(pivots.pivot, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11, fontWeight: 600 }} />
                <ReferenceLine y={pivots.s1} stroke="var(--series-2)" strokeDasharray="4 4" opacity={0.6} label={{ value: `S1 ${fmtUsd(pivots.s1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={pivots.s2} stroke="var(--danger)" strokeDasharray="4 4" opacity={0.6} label={{ value: `S2 ${fmtUsd(pivots.s2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
              </>
            )}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
