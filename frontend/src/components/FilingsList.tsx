import { useState } from "react";
import { api, type Filing, type FilingHighlights } from "../api/client";

// The forms a retail investor actually cares about. Frequent debt issuers
// (banks, in particular) file hundreds of routine prospectus supplements
// (424B2, FWP) per year -- without this filter those bury the 10-K/10-Q/8-K
// filings that matter under noise.
export const SUBSTANTIVE_FORMS = ["10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "DEF 14A", "S-1", "4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"];
const SUBSTANTIVE_SET = new Set(SUBSTANTIVE_FORMS);
const HIGHLIGHTABLE = new Set(["10-K", "10-K/A", "10-Q", "10-Q/A", "8-K"]);

function FilingRow({ filing }: { filing: Filing }) {
  const [expanded, setExpanded] = useState(false);
  const [highlights, setHighlights] = useState<FilingHighlights | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const canExpand = HIGHLIGHTABLE.has(filing.form) && !!filing.document_url;

  function toggle() {
    if (!canExpand) return;
    const next = !expanded;
    setExpanded(next);
    if (next && !highlights && !loading) {
      setLoading(true);
      setError(null);
      api
        .filingHighlights(filing.document_url!, filing.form, filing.items)
        .then(setHighlights)
        .catch((e) => setError(e.message))
        .finally(() => setLoading(false));
    }
  }

  return (
    <>
      <tr className={canExpand ? "clickable-row" : ""} onClick={toggle}>
        <td>
          <span className={`form-badge ${SUBSTANTIVE_SET.has(filing.form) ? "important" : ""}`}>{filing.form}</span>
        </td>
        <td>{filing.filing_date}</td>
        <td>{filing.report_date || "—"}</td>
        <td>
          {canExpand && <span className="expand-caret">{expanded ? "▾ hide highlights" : "▸ show highlights"}</span>}
        </td>
        <td>
          <a href={filing.filing_index_url} target="_blank" rel="noreferrer" onClick={(e) => e.stopPropagation()}>
            EDGAR ↗
          </a>
        </td>
      </tr>
      {expanded && (
        <tr className="highlights-row">
          <td colSpan={5}>
            {loading && <div className="empty-note">Fetching and parsing the filing…</div>}
            {error && <div className="empty-note">Couldn't extract highlights: {error}</div>}
            {highlights && <HighlightsBody highlights={highlights} />}
          </td>
        </tr>
      )}
    </>
  );
}

function HighlightsBody({ highlights }: { highlights: FilingHighlights }) {
  if (highlights.note) {
    return <div className="empty-note">{highlights.note}</div>;
  }
  return (
    <div className="highlights-body">
      {highlights.items && highlights.items.length > 0 && (
        <div className="highlight-block">
          <h5>Items reported</h5>
          <ul className="item-codes">
            {highlights.items.map((it) => (
              <li key={it.code}>
                <span className="item-code">{it.code}</span> {it.description}
              </li>
            ))}
          </ul>
        </div>
      )}
      {highlights.excerpt && (
        <div className="highlight-block">
          <h5>Filing excerpt</h5>
          <p>{highlights.excerpt}</p>
        </div>
      )}
      {highlights.mda && (
        <div className="highlight-block">
          <h5>Management's Discussion & Analysis (excerpt)</h5>
          <p>{highlights.mda}</p>
        </div>
      )}
      {highlights.risk_factors && (
        <div className="highlight-block">
          <h5>Risk Factors (excerpt)</h5>
          <p>{highlights.risk_factors}</p>
        </div>
      )}
      {!highlights.excerpt && !highlights.mda && !highlights.risk_factors && (
        <div className="empty-note">No extractable section found in this document.</div>
      )}
      <p className="source-note">
        Auto-extracted from the filing's own text (SEC EDGAR) — an excerpt, not a substitute for reading the full
        document.
      </p>
    </div>
  );
}

export function FilingsList({ filings, showAllFilings, onToggleShowAll }: { filings: Filing[]; showAllFilings: boolean; onToggleShowAll: () => void }) {
  return (
    <div>
      <div className="filings-toolbar">
        <span className="filings-hint">
          {showAllFilings ? "Showing all filing types." : "Showing 10-K/10-Q/8-K, proxy, registration, insider (Form 4), and beneficial-ownership filings."}
          {" "}Click a 10-K, 10-Q, or 8-K row to pull out its key sections.
        </span>
        <button className="link-btn" onClick={onToggleShowAll}>
          {showAllFilings ? "Show substantive filings only" : "Show all filing types"}
        </button>
      </div>
      {filings.length === 0 ? (
        <div className="empty-note">No recent filings found.</div>
      ) : (
        <table className="filings-table">
          <thead>
            <tr>
              <th>Form</th>
              <th>Filed</th>
              <th>Period</th>
              <th></th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {filings.map((f) => (
              <FilingRow key={f.accession_number} filing={f} />
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
