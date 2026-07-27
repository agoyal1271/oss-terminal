import { Link } from "react-router-dom";
import type { CompanyProfile } from "../api/client";

export function CompanyHeader({ profile, ticker, active }: { profile: CompanyProfile | null; ticker: string; active: "overview" | "options" }) {
  if (!profile) return null;
  return (
    <>
      <div className="company-header">
        <div>
          <h1>
            {profile.name} <span className="ticker-pill">{profile.ticker}</span>
          </h1>
          <div className="company-meta">
            {profile.sic_description} · {profile.exchanges.join(", ") || "OTC/unlisted"}
            {profile.cik ? ` · CIK ${profile.cik}` : ""}
          </div>
        </div>
        {profile.cik ? (
          <a
            className="edgar-link"
            href={`https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=${profile.cik}&type=&dateb=&owner=include&count=40`}
            target="_blank"
            rel="noreferrer"
          >
            All filings on EDGAR ↗
          </a>
        ) : (
          <span className="edgar-link edgar-link-disabled" title="Not an SEC operating-company filer">
            No SEC filings (ETF)
          </span>
        )}
      </div>
      <div className="screen-tabs">
        <Link to={`/c/${ticker}`} className={active === "overview" ? "active" : ""}>
          Overview
        </Link>
        <Link to={`/c/${ticker}/options`} className={active === "options" ? "active" : ""}>
          Options
        </Link>
      </div>
    </>
  );
}
