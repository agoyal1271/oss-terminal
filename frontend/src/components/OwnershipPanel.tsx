import { useEffect, useState } from "react";
import { api, type OwnershipData } from "../api/client";
import { fmtNumber } from "../format";

export function OwnershipPanel({ ticker }: { ticker: string }) {
  const [data, setData] = useState<OwnershipData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api.companyOwnership(ticker).then(setData).catch((e) => setError(e.message));
  }, [ticker]);

  if (error) {
    return <div className="empty-note">Ownership data unavailable: {error}</div>;
  }
  if (!data) {
    return <div className="empty-note">Loading…</div>;
  }

  return (
    <div className="ownership-panel">
      <div className="ownership-headline">
        <div className="ownership-count">{fmtNumber(data.holder_count_estimate)}</div>
        <div className="ownership-count-label">
          institutional filers reported holding this stock
          <br />
          in Form 13F for the quarter ended {data.quarter_end}
        </div>
      </div>
      {data.sample_holders.length > 0 && (
        <div className="ownership-holders">
          <h5>Filers found (sample)</h5>
          <ul>
            {data.sample_holders.map((name) => (
              <li key={name}>{name}</li>
            ))}
          </ul>
        </div>
      )}
      <p className="source-note">{data.method_note}</p>
    </div>
  );
}
