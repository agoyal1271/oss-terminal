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

function getPreviousWeeklyBar(points: any[]): any | null {
  if (points.length < 2) return null;
  const lastDate = new Date(points[points.length - 1].date);
  for (let i = points.length - 2; i >= 0; i--) {
    const pointDate = new Date(points[i].date);
    if (lastDate.getTime() - pointDate.getTime() >= 2 * 24 * 60 * 60 * 1000) {
      return points[i];
    }
  }
  return null;
}

function getPreviousMonthlyBar(points: any[]): any | null {
  if (points.length < 2) return null;
  const lastDate = new Date(points[points.length - 1].date);
  const lastMonth = lastDate.getMonth();
  for (let i = points.length - 2; i >= 0; i--) {
    const pointDate = new Date(points[i].date);
    if (pointDate.getMonth() !== lastMonth) {
      return points[i];
    }
  }
  return null;
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
  const [showDaily, setShowDaily] = useState(true);
  const [showWeekly, setShowWeekly] = useState(true);
  const [showMonthly, setShowMonthly] = useState(false);

  useEffect(() => {
    setData(null);
    setError(null);
    api.companyPrices(ticker, range).then(setData).catch((e) => setError(e.message));
  }, [ticker, range]);

  const points = (data?.points ?? []).map((p) => ({ date: p.date, close: p.close }));
  const isUp = points.length > 1 && points[points.length - 1].close >= points[0].close;

  // Calculate daily pivot points (yesterday's OHLC)
  const dailyPivots: PivotLevels | null =
    data?.points && data.points.length >= 2
      ? (() => {
          const yesterday = data.points[data.points.length - 2];
          const high = yesterday.high ?? data.fifty_two_week_high;
          const low = yesterday.low ?? data.fifty_two_week_low;
          const close = yesterday.close;
          return calculatePivotPoints(high, low, close);
        })()
      : null;

  // Calculate weekly pivot points (previous week's OHLC)
  const weeklyPivots: PivotLevels | null =
    data?.points
      ? (() => {
          const prevWeek = getPreviousWeeklyBar(data.points);
          if (!prevWeek) return null;
          const high = prevWeek.high ?? data.fifty_two_week_high;
          const low = prevWeek.low ?? data.fifty_two_week_low;
          const close = prevWeek.close;
          return calculatePivotPoints(high, low, close);
        })()
      : null;

  // Calculate monthly pivot points (previous month's OHLC)
  const monthlyPivots: PivotLevels | null =
    data?.points
      ? (() => {
          const prevMonth = getPreviousMonthlyBar(data.points);
          if (!prevMonth) return null;
          const high = prevMonth.high ?? data.fifty_two_week_high;
          const low = prevMonth.low ?? data.fifty_two_week_low;
          const close = prevMonth.close;
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
          {(dailyPivots || weeklyPivots || monthlyPivots) && (
            <div style={{ marginLeft: "12px", display: "flex", alignItems: "center", gap: "12px", fontSize: "13px" }}>
              {dailyPivots && (
                <label style={{ display: "flex", alignItems: "center", gap: "6px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <input type="checkbox" checked={showDaily} onChange={(e) => setShowDaily(e.target.checked)} />
                  Daily
                </label>
              )}
              {weeklyPivots && (
                <label style={{ display: "flex", alignItems: "center", gap: "6px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <input type="checkbox" checked={showWeekly} onChange={(e) => setShowWeekly(e.target.checked)} />
                  Weekly
                </label>
              )}
              {monthlyPivots && (
                <label style={{ display: "flex", alignItems: "center", gap: "6px", color: "var(--text-secondary)", cursor: "pointer" }}>
                  <input type="checkbox" checked={showMonthly} onChange={(e) => setShowMonthly(e.target.checked)} />
                  Monthly
                </label>
              )}
            </div>
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

            {showDaily && dailyPivots && (
              <>
                <ReferenceLine y={dailyPivots.r2} stroke="var(--danger)" strokeDasharray="4 4" strokeWidth={1} opacity={0.4} />
                <ReferenceLine y={dailyPivots.r1} stroke="var(--series-2)" strokeDasharray="4 4" strokeWidth={1} opacity={0.4} />
                <ReferenceLine y={dailyPivots.pivot} stroke="var(--series-3)" strokeWidth={0.5} opacity={0.35} />
                <ReferenceLine y={dailyPivots.s1} stroke="var(--series-2)" strokeDasharray="4 4" strokeWidth={1} opacity={0.4} />
                <ReferenceLine y={dailyPivots.s2} stroke="var(--danger)" strokeDasharray="4 4" strokeWidth={1} opacity={0.4} />
              </>
            )}

            {showWeekly && weeklyPivots && (
              <>
                <ReferenceLine y={weeklyPivots.r2} stroke="var(--danger)" strokeDasharray="4 4" strokeWidth={1.5} opacity={0.7} label={{ value: `WR2 ${fmtUsd(weeklyPivots.r2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={weeklyPivots.r1} stroke="var(--series-2)" strokeDasharray="4 4" strokeWidth={1.5} opacity={0.7} label={{ value: `WR1 ${fmtUsd(weeklyPivots.r1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={weeklyPivots.pivot} stroke="var(--series-3)" strokeWidth={1.5} opacity={0.8} label={{ value: `WP ${fmtUsd(weeklyPivots.pivot, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11, fontWeight: 600 }} />
                <ReferenceLine y={weeklyPivots.s1} stroke="var(--series-2)" strokeDasharray="4 4" strokeWidth={1.5} opacity={0.7} label={{ value: `WS1 ${fmtUsd(weeklyPivots.s1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
                <ReferenceLine y={weeklyPivots.s2} stroke="var(--danger)" strokeDasharray="4 4" strokeWidth={1.5} opacity={0.7} label={{ value: `WS2 ${fmtUsd(weeklyPivots.s2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 11 }} />
              </>
            )}

            {showMonthly && monthlyPivots && (
              <>
                <ReferenceLine y={monthlyPivots.r2} stroke="var(--danger)" strokeWidth={2.5} opacity={0.8} label={{ value: `MR2 ${fmtUsd(monthlyPivots.r2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 12, fontWeight: 700 }} />
                <ReferenceLine y={monthlyPivots.r1} stroke="var(--series-2)" strokeWidth={2.5} opacity={0.8} label={{ value: `MR1 ${fmtUsd(monthlyPivots.r1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 12, fontWeight: 700 }} />
                <ReferenceLine y={monthlyPivots.pivot} stroke="var(--series-3)" strokeWidth={2.5} opacity={0.9} label={{ value: `MP ${fmtUsd(monthlyPivots.pivot, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 12, fontWeight: 700 }} />
                <ReferenceLine y={monthlyPivots.s1} stroke="var(--series-2)" strokeWidth={2.5} opacity={0.8} label={{ value: `MS1 ${fmtUsd(monthlyPivots.s1, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 12, fontWeight: 700 }} />
                <ReferenceLine y={monthlyPivots.s2} stroke="var(--danger)" strokeWidth={2.5} opacity={0.8} label={{ value: `MS2 ${fmtUsd(monthlyPivots.s2, 0)}`, position: "right", fill: "var(--text-muted)", fontSize: 12, fontWeight: 700 }} />
              </>
            )}
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
