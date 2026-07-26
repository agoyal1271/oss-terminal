import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type CompanyProfile, type OptionsChain } from "../api/client";
import { CompanyHeader } from "../components/CompanyHeader";
import { OptionsChainTable } from "../components/OptionsChainTable";
import { OptionsAIPanel } from "../components/OptionsAIPanel";
import { IVTermStructureChart } from "../components/IVTermStructureChart";
import { IVSkewChart } from "../components/IVSkewChart";
import { IvRankTile } from "../components/IvRankTile";
import { fmtPercent, fmtUsd } from "../format";

function formatExpiration(unixSeconds: number): string {
  return new Date(unixSeconds * 1000).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
}

export function OptionsPage() {
  const { ticker = "" } = useParams();
  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  const [chain, setChain] = useState<OptionsChain | null>(null);
  const [expiration, setExpiration] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setProfile(null);
    setChain(null);
    setExpiration(null);

    Promise.all([
      api.companyProfile(ticker).then((p) => !cancelled && setProfile(p)),
      api.companyOptions(ticker).then((c) => {
        if (cancelled) return;
        setChain(c);
        setExpiration(c.selected_expiration);
      }),
    ])
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  function onExpirationChange(newExp: number) {
    setExpiration(newExp);
    api.companyOptions(ticker, newExp).then(setChain).catch((e) => setError(e.message));
  }

  if (error) {
    return (
      <div className="page-content">
        <div className="error-box">Couldn't load options for {ticker}: {error}</div>
      </div>
    );
  }

  if (loading && !chain) {
    return <div className="page-content"><div className="loading">Loading {ticker} options…</div></div>;
  }

  const s = chain?.summary;

  return (
    <div className="page-content">
      <CompanyHeader profile={profile} ticker={ticker} active="options" />

      {chain && (
        <>
          <div className="panel">
            <div className="options-toolbar">
              <div>
                <span className="options-underlying">{fmtUsd(chain.underlying_price ?? 0)}</span>
                <span className="options-underlying-label"> underlying · {chain.symbol}</span>
              </div>
              <label className="expiration-select">
                Expiration
                <select value={expiration ?? ""} onChange={(e) => onExpirationChange(Number(e.target.value))}>
                  {chain.expiration_dates.map((exp) => (
                    <option key={exp} value={exp}>
                      {formatExpiration(exp)}
                    </option>
                  ))}
                </select>
              </label>
            </div>

            {s && (
              <div className="key-stats options-stats">
                <div className="stat-tile">
                  <div className="stat-label">Put/Call volume ratio</div>
                  <div className="stat-value">{s.put_call_volume_ratio?.toFixed(2) ?? "—"}</div>
                </div>
                <div className="stat-tile">
                  <div className="stat-label">Put/Call open interest ratio</div>
                  <div className="stat-value">{s.put_call_oi_ratio?.toFixed(2) ?? "—"}</div>
                </div>
                <div className="stat-tile">
                  <div className="stat-label">ATM strike</div>
                  <div className="stat-value">{s.atm_strike ? fmtUsd(s.atm_strike) : "—"}</div>
                </div>
                <div className="stat-tile">
                  <div className="stat-label">ATM implied volatility</div>
                  <div className="stat-value">
                    {s.atm_call_iv ? fmtPercent(s.atm_call_iv) : "—"} call / {s.atm_put_iv ? fmtPercent(s.atm_put_iv) : "—"} put
                  </div>
                </div>
                <div className="stat-tile">
                  <div className="stat-label">Expected move (ATM straddle)</div>
                  <div className="stat-value">{s.expected_move_atm_straddle ? `± ${fmtUsd(s.expected_move_atm_straddle)}` : "—"}</div>
                </div>
                <IvRankTile ticker={ticker} />
              </div>
            )}
            <p className="source-note">
              Source: Yahoo Finance (unofficial, delayed). Expected move is the at-the-money call + put price, a
              standard trader's rule-of-thumb for the market-implied move by expiration — not a prediction.
            </p>
          </div>

          <div className="analysis-grid">
            <div className="panel">
              <h3>IV term structure</h3>
              <IVTermStructureChart ticker={ticker} />
            </div>
            <div className="panel">
              <h3>IV skew ({formatExpiration(chain.selected_expiration)})</h3>
              <IVSkewChart chain={chain} />
            </div>
          </div>

          <div className="panel">
            <h3>Options chain</h3>
            <OptionsChainTable title="Calls" contracts={chain.calls} atmStrike={s?.atm_strike ?? null} />
            <OptionsChainTable title="Puts" contracts={chain.puts} atmStrike={s?.atm_strike ?? null} />
          </div>

          {profile && (
            <div className="panel">
              <h3>AI analysis (local LLM)</h3>
              <OptionsAIPanel ticker={ticker} chain={chain} companyName={profile.name} />
            </div>
          )}
        </>
      )}

      <p className="disclaimer">
        Options data sourced live from Yahoo Finance. This is a research tool, not investment advice — options
        involve substantial risk of loss and are not suitable for all investors.
      </p>
    </div>
  );
}
