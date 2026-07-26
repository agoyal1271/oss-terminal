import { useEffect, useState } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api, type TermStructure } from "../api/client";
import { fmtPercent } from "../format";

function daysOut(expirationSeconds: number): number {
  return Math.round((expirationSeconds * 1000 - Date.now()) / 86_400_000);
}

function formatExpirationShort(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

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
    label: `${formatExpirationShort(p.expiration)} (${daysOut(p.expiration)}d)`,
    days: daysOut(p.expiration),
    callIv: p.call_iv,
    putIv: p.put_iv,
  }));

  // Backwardation (a near-term IV spike above the surrounding points) is the
  // signal worth calling out -- it usually means the market is pricing a
  // known event (earnings, a ruling) into that specific expiration.
  const avgIvs = chartData.map((d) => ((d.callIv ?? 0) + (d.putIv ?? 0)) / 2);
  let spikeIndex = -1;
  for (let i = 0; i < avgIvs.length - 1; i++) {
    if (avgIvs[i] > avgIvs[i + 1] * 1.15) {
      spikeIndex = i;
      break;
    }
  }

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
      {spikeIndex >= 0 ? (
        <p className="source-note">
          IV jumps at {chartData[spikeIndex].label} then falls back for later expirations — that shape (backwardation)
          usually means the market is pricing a specific event into that expiration, not a smooth increase in
          uncertainty over time.
        </p>
      ) : (
        <p className="source-note">
          IV rises smoothly further out — normal term structure, no sign of a specific event being priced into a
          near-term expiration.
        </p>
      )}
    </div>
  );
}
