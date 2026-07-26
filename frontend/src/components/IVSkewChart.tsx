import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { OptionsChain } from "../api/client";
import { fmtPercent, fmtUsd } from "../format";

export function IVSkewChart({ chain }: { chain: OptionsChain }) {
  const underlying = chain.underlying_price ?? 0;
  // Deep ITM/OTM strikes have thin liquidity and wildly noisy IV (a single
  // far-OTM strike spiking to 200%+ was enough to flatten the entire
  // near-the-money curve into an unreadable line at the bottom of the
  // chart). Limiting to +/-20% keeps the y-axis scaled to the skew that
  // actually matters.
  const lo = underlying * 0.8;
  const hi = underlying * 1.2;

  const byStrike = new Map<number, { strike: number; callIv: number | null; putIv: number | null }>();
  for (const c of chain.calls) {
    if (c.strike < lo || c.strike > hi) continue;
    byStrike.set(c.strike, { strike: c.strike, callIv: c.implied_volatility, putIv: byStrike.get(c.strike)?.putIv ?? null });
  }
  for (const p of chain.puts) {
    if (p.strike < lo || p.strike > hi) continue;
    const existing = byStrike.get(p.strike);
    byStrike.set(p.strike, { strike: p.strike, callIv: existing?.callIv ?? null, putIv: p.implied_volatility });
  }
  const chartData = [...byStrike.values()].sort((a, b) => a.strike - b.strike);

  if (chartData.length < 2) {
    return <div className="empty-note">Not enough near-the-money strikes with quoted IV to chart skew.</div>;
  }

  const atmStrike = chain.summary.atm_strike;

  return (
    <div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--gridline)" />
          <XAxis
            dataKey="strike"
            type="number"
            domain={["dataMin", "dataMax"]}
            tick={{ fill: "var(--text-muted)", fontSize: 11 }}
            axisLine={{ stroke: "var(--baseline)" }}
            tickLine={false}
            tickFormatter={(v) => fmtUsd(v, 0)}
          />
          <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtPercent(v)} width={50} />
          <Tooltip
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
            formatter={(value, name) => [value === null ? "—" : fmtPercent(Number(value)), name]}
            labelFormatter={(v) => `Strike ${fmtUsd(Number(v), 0)}`}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />
          {atmStrike != null && <ReferenceLine x={atmStrike} stroke="var(--text-muted)" strokeDasharray="3 3" />}
          <Line type="monotone" dataKey="callIv" name="Call IV" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 2 }} connectNulls />
          <Line type="monotone" dataKey="putIv" name="Put IV" stroke="var(--series-2)" strokeWidth={2} dot={{ r: 2 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      <p className="source-note">
        Dashed line marks the at-the-money strike. Rising IV toward lower strikes (put side) is normal equity skew —
        crash protection costs more than equivalent upside. Skew flattening or inverting toward the call side is the
        speculative-chasing pattern.
      </p>
    </div>
  );
}
