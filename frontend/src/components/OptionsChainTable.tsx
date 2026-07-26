import type { OptionContract } from "../api/client";
import { fmtNumber, fmtPercent, fmtUsd } from "../format";

const WIDE_SPREAD_THRESHOLD = 0.15; // >15% of mid is a rule-of-thumb "hard to trade cleanly" cutoff

function spreadPct(c: OptionContract): number | null {
  if (c.bid == null || c.ask == null || c.bid <= 0 || c.ask <= 0) return null;
  const mid = (c.bid + c.ask) / 2;
  if (mid <= 0) return null;
  return (c.ask - c.bid) / mid;
}

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
              <th title="Bid-ask spread as % of mid price -- wide spreads mean you lose real money just entering/exiting">Spread</th>
              <th>Change</th>
              <th title="Volume exceeding open interest means most of today's activity is new positions being opened, not existing ones changing hands">Volume</th>
              <th>Open Int.</th>
              <th>IV</th>
            </tr>
          </thead>
          <tbody>
            {contracts.map((c) => {
              const spread = spreadPct(c);
              const isNewPositioning = c.volume > 0 && c.volume > c.open_interest;
              return (
                <tr key={c.contract_symbol} className={[c.in_the_money ? "itm-row" : "", c.strike === atmStrike ? "atm-row" : ""].join(" ")}>
                  <td className="strike-cell">{fmtUsd(c.strike, 2)}</td>
                  <td>{c.last_price != null ? fmtUsd(c.last_price) : "—"}</td>
                  <td>{c.bid != null ? fmtUsd(c.bid) : "—"}</td>
                  <td>{c.ask != null ? fmtUsd(c.ask) : "—"}</td>
                  <td className={spread != null && spread > WIDE_SPREAD_THRESHOLD ? "wide-spread" : ""}>
                    {spread != null ? fmtPercent(spread, 0) : "—"}
                  </td>
                  <td className={c.change != null && c.change < 0 ? "neg" : c.change != null && c.change > 0 ? "pos" : ""}>
                    {c.percent_change != null ? fmtPercent(c.percent_change / 100) : "—"}
                  </td>
                  <td>
                    {fmtNumber(c.volume)}
                    {isNewPositioning && (
                      <span className="new-flag" title="Volume exceeds open interest -- new positioning today">
                        {" "}
                        ●
                      </span>
                    )}
                  </td>
                  <td>{fmtNumber(c.open_interest)}</td>
                  <td>{c.implied_volatility != null ? fmtPercent(c.implied_volatility) : "—"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
