import { useEffect, useState } from "react";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type PriceHistory } from "../api/client";
import { fmtUsd } from "../format";

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

  useEffect(() => {
    setData(null);
    setError(null);
    api.companyPrices(ticker, range).then(setData).catch((e) => setError(e.message));
  }, [ticker, range]);

  const points = (data?.points ?? []).map((p) => ({ date: p.date, close: p.close }));
  const isUp = points.length > 1 && points[points.length - 1].close >= points[0].close;

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
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  );
}
