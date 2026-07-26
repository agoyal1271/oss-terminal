import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { AnnualRow } from "../api/client";
import { fmtUsdCompact } from "../format";

export function RevenueChart({ annual }: { annual: AnnualRow[] }) {
  const data = [...annual]
    .filter((r) => r.metrics.revenue !== null)
    .sort((a, b) => a.fy - b.fy)
    .map((r) => ({
      fy: `FY${r.fy}`,
      revenue: r.metrics.revenue,
      net_income: r.metrics.net_income,
    }));

  if (data.length === 0) {
    return <div className="empty-note">No revenue data reported under standard XBRL tags for this filer.</div>;
  }

  return (
    <ResponsiveContainer width="100%" height={280}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 8, bottom: 0 }} barGap={4}>
        <CartesianGrid vertical={false} stroke="var(--gridline)" />
        <XAxis dataKey="fy" tick={{ fill: "var(--text-muted)", fontSize: 12 }} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
        <YAxis
          tick={{ fill: "var(--text-muted)", fontSize: 12 }}
          axisLine={false}
          tickLine={false}
          tickFormatter={(v) => fmtUsdCompact(v)}
          width={56}
        />
        <Tooltip
          contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 13 }}
          labelStyle={{ color: "var(--text-primary)" }}
          formatter={(value, name) => [fmtUsdCompact(Number(value)), name === "revenue" ? "Revenue" : "Net income"]}
        />
        <Legend
          formatter={(value) => (value === "revenue" ? "Revenue" : "Net income")}
          wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }}
        />
        <Bar dataKey="revenue" fill="var(--series-1)" radius={[3, 3, 0, 0]} maxBarSize={28} />
        <Bar dataKey="net_income" fill="var(--series-2)" radius={[3, 3, 0, 0]} maxBarSize={28} />
      </BarChart>
    </ResponsiveContainer>
  );
}
