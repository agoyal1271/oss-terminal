import { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { api, type CompanyProfile, type Filing, type FinancialsResponse, type PriceHistory } from "../api/client";
import { KeyStats } from "../components/KeyStats";
import { RevenueChart } from "../components/RevenueChart";
import { PriceChart } from "../components/PriceChart";
import { FinancialsTable } from "../components/FinancialsTable";
import { FilingsList, SUBSTANTIVE_FORMS } from "../components/FilingsList";
import { TechnicalPanel } from "../components/TechnicalPanel";
import { OwnershipPanel } from "../components/OwnershipPanel";
import { AIReadPanel } from "../components/AIReadPanel";
import { CompanyHeader } from "../components/CompanyHeader";

export function CompanyPage() {
  const { ticker = "" } = useParams();
  const [profile, setProfile] = useState<CompanyProfile | null>(null);
  const [financials, setFinancials] = useState<FinancialsResponse | null>(null);
  const [prices, setPrices] = useState<PriceHistory | null>(null);
  const [filings, setFilings] = useState<Filing[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAllFilings, setShowAllFilings] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setProfile(null);
    setFinancials(null);
    setPrices(null);
    setFilings([]);
    setShowAllFilings(false);

    Promise.all([
      api.companyProfile(ticker).then((p) => !cancelled && setProfile(p)),
      api.companyFinancials(ticker).then((f) => !cancelled && setFinancials(f)),
      api.companyPrices(ticker, "1y").then((p) => !cancelled && setPrices(p)).catch(() => {}),
      api.companyFilings(ticker, 15, [...SUBSTANTIVE_FORMS]).then((r) => !cancelled && setFilings(r.filings)),
    ])
      .catch((e) => !cancelled && setError(e.message))
      .finally(() => !cancelled && setLoading(false));

    return () => {
      cancelled = true;
    };
  }, [ticker]);

  function toggleShowAll() {
    const next = !showAllFilings;
    setShowAllFilings(next);
    api.companyFilings(ticker, 20, next ? undefined : [...SUBSTANTIVE_FORMS]).then((r) => setFilings(r.filings));
  }

  if (error) {
    return (
      <div className="page-content">
        <div className="error-box">Couldn't load {ticker}: {error}</div>
      </div>
    );
  }

  if (loading && !profile) {
    return <div className="page-content"><div className="loading">Loading {ticker}…</div></div>;
  }

  const latest = financials?.annual?.length ? [...financials.annual].sort((a, b) => b.fy - a.fy)[0] : undefined;

  return (
    <div className="page-content">
      <CompanyHeader profile={profile} ticker={ticker} active="overview" />

      <KeyStats latest={latest} prices={prices} />

      <div className="panel-grid">
        <div className="panel">
          <h3>Price {prices ? `— ${prices.symbol}` : ""}</h3>
          <PriceChart ticker={ticker} />
          <p className="source-note">Source: Yahoo Finance (unofficial, delayed). Not a licensed real-time feed.</p>
        </div>
        <div className="panel">
          <h3>Revenue & net income</h3>
          {financials && <RevenueChart annual={financials.annual} />}
          <p className="source-note">Source: SEC EDGAR XBRL company facts, annual (10-K) figures only.</p>
        </div>
      </div>

      <div className="panel">
        <h3>Technical read</h3>
        <TechnicalPanel ticker={ticker} />
      </div>

      <div className="panel">
        <h3>Financial statements (annual, as reported)</h3>
        {financials && <FinancialsTable data={financials} />}
      </div>

      <div className="panel">
        <h3>Institutional ownership</h3>
        <OwnershipPanel ticker={ticker} />
      </div>

      <div className="panel">
        <h3>Recent SEC filings</h3>
        <FilingsList filings={filings} showAllFilings={showAllFilings} onToggleShowAll={toggleShowAll} />
      </div>

      {profile && (
        <div className="panel">
          <h3>AI read (local LLM)</h3>
          <AIReadPanel ticker={ticker} companyName={profile.name} />
        </div>
      )}

      <p className="disclaimer">
        All data sourced live from SEC EDGAR and Yahoo Finance. This is a research tool, not investment advice —
        verify anything material against the underlying filing before acting on it.
      </p>
    </div>
  );
}
