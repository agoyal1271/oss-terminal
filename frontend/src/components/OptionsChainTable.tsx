import type { OptionContract } from "../api/client";
import { fmtNumber, fmtPercent, fmtUsd } from "../format";

export function OptionsChainTable({ title, contracts, atmStrike }: { title: string; contracts: OptionContract[]; atmStrike: number | null }) {
  if (contracts.length === 0) {
    return <div className="empty-note">No {title.toLowerCase()} contracts for this expiration.</div>;
  }
  return (
    <div className="options-chain-block">
      <h5>{title}</h5>
      <div className="table-scroll">
        <table className="options-table">
          <thead>
            <tr>
              <th>Strike</th>
              <th>Last</th>
              <th>Bid</th>
              <th>Ask</th>
              <th>Change</th>
              <th>Volume</th>
              <th>Open Int.</th>
              <th>IV</th>
            </tr>
          </thead>
          <tbody>
            {contracts.map((c) => (
              <tr key={c.contract_symbol} className={[c.in_the_money ? "itm-row" : "", c.strike === atmStrike ? "atm-row" : ""].join(" ")}>
                <td className="strike-cell">{fmtUsd(c.strike, 2)}</td>
                <td>{c.last_price != null ? fmtUsd(c.last_price) : "—"}</td>
                <td>{c.bid != null ? fmtUsd(c.bid) : "—"}</td>
                <td>{c.ask != null ? fmtUsd(c.ask) : "—"}</td>
                <td className={c.change != null && c.change < 0 ? "neg" : c.change != null && c.change > 0 ? "pos" : ""}>
                  {c.percent_change != null ? fmtPercent(c.percent_change / 100) : "—"}
                </td>
                <td>{fmtNumber(c.volume)}</td>
                <td>{fmtNumber(c.open_interest)}</td>
                <td>{c.implied_volatility != null ? fmtPercent(c.implied_volatility) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
