import type { AnnualRow, PriceHistory } from "../api/client";
import { fmtPercent, fmtUsd, fmtUsdCompact } from "../format";

export function KeyStats({ latest, prices }: { latest: AnnualRow | undefined; prices: PriceHistory | null }) {
  if (!latest && !prices) return null;

  const eps = latest?.metrics.eps_diluted ?? null;
  const price = prices?.regular_market_price ?? null;
  const pe = price !== null && eps !== null && eps > 0 ? price / eps : null;
  const shares = latest?.metrics.shares_outstanding ?? null;
  const marketCap = price !== null && shares !== null ? price * shares : null;

  const items: { label: string; value: string; hint?: string }[] = [
    { label: "Price", value: prices ? fmtUsd(prices.regular_market_price) : "—" },
    { label: "52W range", value: prices ? `${fmtUsd(prices.fifty_two_week_low, 0)} – ${fmtUsd(prices.fifty_two_week_high, 0)}` : "—" },
    { label: "Market cap (approx)", value: marketCap ? fmtUsdCompact(marketCap) : "—", hint: "price × diluted shares outstanding, last reported balance sheet" },
    { label: "P/E (trailing)", value: pe ? pe.toFixed(1) : "—", hint: "price ÷ last fiscal year diluted EPS" },
    { label: `Revenue (FY${latest?.fy ?? ""})`, value: latest ? fmtUsdCompact(latest.metrics.revenue) : "—" },
    { label: "Net margin", value: latest ? fmtPercent(latest.derived.net_margin) : "—" },
    { label: "Return on equity", value: latest ? fmtPercent(latest.derived.return_on_equity) : "—" },
    { label: "Free cash flow", value: latest ? fmtUsdCompact(latest.derived.free_cash_flow) : "—" },
  ];

  return (
    <div className="key-stats">
      {items.map((item) => (
        <div key={item.label} className="stat-tile" title={item.hint}>
          <div className="stat-label">{item.label}</div>
          <div className="stat-value">{item.value}</div>
        </div>
      ))}
    </div>
  );
}
