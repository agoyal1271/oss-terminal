import { useEffect, useState } from "react";
import { api, type IvRank } from "../api/client";

export function IvRankTile({ ticker }: { ticker: string }) {
  const [data, setData] = useState<IvRank | null>(null);

  useEffect(() => {
    setData(null);
    api.companyOptionsIvRank(ticker).then(setData).catch(() => setData(null));
  }, [ticker]);

  if (!data) {
    return (
      <div className="stat-tile">
        <div className="stat-label">IV Rank</div>
        <div className="stat-value">—</div>
      </div>
    );
  }

  const collecting = data.iv_rank == null;

  return (
    <div className="stat-tile" title={data.note}>
      <div className="stat-label">IV Rank {collecting ? "" : `(${data.history_days}d history)`}</div>
      <div className="stat-value">{collecting ? `Collecting (day ${data.history_days})` : `${data.iv_rank!.toFixed(0)}%`}</div>
    </div>
  );
}
