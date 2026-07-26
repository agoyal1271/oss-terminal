import type { Filing } from "../api/client";

// The forms a retail investor actually cares about. Frequent debt issuers
// (banks, in particular) file hundreds of routine prospectus supplements
// (424B2, FWP) per year -- without this filter those bury the 10-K/10-Q/8-K
// filings that matter under noise.
export const SUBSTANTIVE_FORMS = ["10-K", "10-K/A", "10-Q", "10-Q/A", "8-K", "DEF 14A", "S-1", "4", "SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"];
const SUBSTANTIVE_SET = new Set(SUBSTANTIVE_FORMS);

export function FilingsList({ filings, showAllFilings, onToggleShowAll }: { filings: Filing[]; showAllFilings: boolean; onToggleShowAll: () => void }) {
  return (
    <div>
      <div className="filings-toolbar">
        <span className="filings-hint">
          {showAllFilings ? "Showing all filing types." : "Showing 10-K/10-Q/8-K, proxy, registration, insider (Form 4), and beneficial-ownership filings."}
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
            </tr>
          </thead>
          <tbody>
            {filings.map((f) => (
              <tr key={f.accession_number}>
                <td>
                  <span className={`form-badge ${SUBSTANTIVE_SET.has(f.form) ? "important" : ""}`}>{f.form}</span>
                </td>
                <td>{f.filing_date}</td>
                <td>{f.report_date || "—"}</td>
                <td>
                  <a href={f.filing_index_url} target="_blank" rel="noreferrer">
                    View on EDGAR ↗
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
