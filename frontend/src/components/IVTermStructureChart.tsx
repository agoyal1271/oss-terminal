import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type TermStructure } from "../api/client";
import { describeTermStructure, daysToExpiry, formatExpirationShort } from "../optionsAnalysis";
import { fmtPercent } from "../format";

export function IVTermStructureChart({ ticker }: { ticker: string }) {
  const [data, setData] = useState<TermStructure | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api.companyOptionsTermStructure(ticker).then(setData).catch((e) => setError(e.message));
  }, [ticker]);

  if (error) return <div className="empty-note">Term structure unavailable: {error}</div>;
  if (!data) return <div className="empty-note">Loading…</div>;
  if (data.points.length < 2) return <div className="empty-note">Not enough expirations with quoted IV to chart a term structure.</div>;

  const chartData = data.points.map((p) => ({
    label: `${formatExpirationShort(p.expiration)} (${daysToExpiry(p.expiration)}d)`,
    days: daysToExpiry(p.expiration),
    callIv: p.call_iv,
    putIv: p.put_iv,
  }));

  const read = describeTermStructure(data.points);

  return (
    <div>
      <ResponsiveContainer width="100%" height={220}>
        <LineChart data={chartData} margin={{ top: 8, right: 8, left: 8, bottom: 0 }}>
          <CartesianGrid vertical={false} stroke="var(--gridline)" />
          <XAxis dataKey="label" tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={{ stroke: "var(--baseline)" }} tickLine={false} />
          <YAxis tick={{ fill: "var(--text-muted)", fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => fmtPercent(v)} width={50} />
          <Tooltip
            contentStyle={{ background: "var(--surface-1)", border: "1px solid var(--border)", borderRadius: 6, fontSize: 12 }}
            labelStyle={{ color: "var(--text-primary)" }}
            formatter={(value, name) => [value === null ? "—" : fmtPercent(Number(value)), name]}
          />
          <Legend wrapperStyle={{ fontSize: 12, color: "var(--text-secondary)" }} />
          <Line type="monotone" dataKey="callIv" name="Call IV" stroke="var(--series-1)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
          <Line type="monotone" dataKey="putIv" name="Put IV" stroke="var(--series-2)" strokeWidth={2} dot={{ r: 3 }} connectNulls />
        </LineChart>
      </ResponsiveContainer>
      <p className="source-note">{read.summary}</p>
    </div>
  );
}
