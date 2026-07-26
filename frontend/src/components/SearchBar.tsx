import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type TickerResult } from "../api/client";

export function SearchBar({ autoFocus = false }: { autoFocus?: boolean }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<TickerResult[]>([]);
  const [open, setOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(-1);
  const navigate = useNavigate();
  const boxRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query.trim().length === 0) {
      setResults([]);
      return;
    }
    const handle = setTimeout(() => {
      api.search(query).then((r) => {
        setResults(r.results);
        setOpen(true);
        setActiveIndex(-1);
      }).catch(() => setResults([]));
    }, 150);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    function onClickOutside(e: MouseEvent) {
      if (boxRef.current && !boxRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onClickOutside);
    return () => document.removeEventListener("mousedown", onClickOutside);
  }, []);

  function select(ticker: string) {
    setQuery("");
    setResults([]);
    setOpen(false);
    navigate(`/c/${ticker}`);
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (!open || results.length === 0) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActiveIndex((i) => Math.min(i + 1, results.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActiveIndex((i) => Math.max(i - 1, 0));
    } else if (e.key === "Enter") {
      const pick = activeIndex >= 0 ? results[activeIndex] : results[0];
      if (pick) select(pick.ticker);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  }

  return (
    <div className="search-box" ref={boxRef}>
      <input
        autoFocus={autoFocus}
        className="search-input"
        type="text"
        placeholder="Search ticker or company name (e.g. AAPL, Tesla)…"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onFocus={() => results.length > 0 && setOpen(true)}
        onKeyDown={onKeyDown}
      />
      {open && results.length > 0 && (
        <ul className="search-results">
          {results.map((r, i) => (
            <li
              key={r.cik_str}
              className={i === activeIndex ? "active" : ""}
              onMouseDown={() => select(r.ticker)}
              onMouseEnter={() => setActiveIndex(i)}
            >
              <span className="ticker">{r.ticker}</span>
              <span className="title">{r.title}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
