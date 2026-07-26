import { SearchBar } from "../components/SearchBar";
import { useNavigate } from "react-router-dom";

const POPULAR = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "JPM", "BRK-B"];

export function HomePage() {
  const navigate = useNavigate();
  return (
    <div className="home">
      <h1 className="home-title">OSS Terminal</h1>
      <p className="home-sub">A free, open-data research terminal. Every number traces back to a live SEC filing.</p>
      <div className="home-search">
        <SearchBar autoFocus />
      </div>
      <div className="popular">
        <span className="popular-label">Popular:</span>
        {POPULAR.map((t) => (
          <button key={t} className="chip" onClick={() => navigate(`/c/${t}`)}>
            {t}
          </button>
        ))}
      </div>
      <div className="sources-note">
        <h3>Data sources</h3>
        <ul>
          <li>Financial statements — SEC EDGAR XBRL company facts (every US public filer, 2009–present)</li>
          <li>Filings — SEC EDGAR submissions API</li>
          <li>Prices — Yahoo Finance (delayed, unofficial)</li>
        </ul>
      </div>
    </div>
  );
}
