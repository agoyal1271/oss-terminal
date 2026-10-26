import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { computeTechnicalRead, type TechnicalRead } from "../technicals";

type Severity = "critical" | "warn" | "info";

type AlertRule = {
  ticker: string;
  minSeverity: Severity;
};

type Finding = {
  key: string;
  ticker: string;
  category: string;
  severity: Severity;
  title: string;
  detail: string;
};

type ScanSnapshot = {
  date: string;
  findings: Finding[];
  subscribed_findings?: Finding[];
};

const STORAGE_KEY = "oss-terminal.my-signals.v1";
const DEFAULT_RULES: AlertRule[] = [
  { ticker: "GLD", minSeverity: "info" },
  { ticker: "BMNR", minSeverity: "info" },
];
const RAW_SCAN_URL = "https://raw.githubusercontent.com/agoyal1271/oss-terminal/main/data/scans/latest.json";
const severityRank: Record<Severity, number> = { critical: 0, warn: 1, info: 2 };

function readRules(): AlertRule[] {
  try {
    const saved = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]") as AlertRule[];
    return saved.length ? saved : DEFAULT_RULES;
  } catch {
    return DEFAULT_RULES;
  }
}

function persistRules(rules: AlertRule[]) {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(rules));
}

export function MySignalsPanel() {
  const [rules, setRules] = useState<AlertRule[]>(DEFAULT_RULES);
  const [input, setInput] = useState("");
  const [snapshot, setSnapshot] = useState<ScanSnapshot | null>(null);
  const [scanError, setScanError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);
  const syncStarted = useRef(false);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [reads, setReads] = useState<Record<string, { date: string; tech: TechnicalRead | null; error?: string }>>({});

  useEffect(() => {
    setRules(readRules());
    fetch(RAW_SCAN_URL)
      .then((res) => {
        if (!res.ok) throw new Error("Daily scan is unavailable");
        return res.json() as Promise<ScanSnapshot>;
      })
      .then((data) => { if (!syncStarted.current) setSnapshot(data); })
      .catch(() => { if (!syncStarted.current) setScanError("Daily scan will appear after the next scheduled run."); });
  }, []);

  async function syncSignals() {
    if (syncing || !rules.length) return;
    syncStarted.current = true;
    setSyncing(true);
    setScanError(null);
    const findings: Finding[] = [];
    const failed: string[] = [];
    const nextReads: typeof reads = {};
    try {
      for (const rule of rules) {
        const [scan, prices] = await Promise.allSettled([
          api.companySignals(rule.ticker),
          api.companyPrices(rule.ticker, "2y", true),
        ]);
        if (prices.status === "fulfilled") {
          const tech = computeTechnicalRead(prices.value.points);
          nextReads[rule.ticker] = { date: prices.value.points.at(-1)?.date ?? "", tech,
            error: tech ? undefined : "Not enough price history." };
        } else {
          nextReads[rule.ticker] = { date: "", tech: null, error: "Price snapshot unavailable. Try Sync again." };
        }
        try {
          if (scan.status === "rejected") throw scan.reason;
          const result = scan.value;
          findings.push(...result.findings);
          if (result.errors.length) failed.push(rule.ticker);
        } catch {
          failed.push(rule.ticker);
        }
      }
      setReads(nextReads);
      setSnapshot({ date: new Date().toISOString().slice(0, 10), findings });
      setSyncedAt(new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }));
      if (failed.length) setScanError(`Incomplete scan for ${failed.join(", ")}. Try Sync again.`);
    } finally {
      setSyncing(false);
    }
  }

  const saveRules = (next: AlertRule[]) => {
    setRules(next);
    persistRules(next);
  };

  const addTicker = () => {
    const ticker = input.trim().toUpperCase();
    if (!/^[A-Z]{1,6}(?:-[A-Z])?$/.test(ticker) || rules.some((rule) => rule.ticker === ticker)) return;
    saveRules([...rules, { ticker, minSeverity: "warn" }]);
    setInput("");
  };

  const matchingFindings = useMemo(() => {
    if (!snapshot) return [];
    return snapshot.findings.filter((finding) => {
      const rule = rules.find((candidate) => candidate.ticker === finding.ticker);
      return rule && severityRank[finding.severity] <= severityRank[rule.minSeverity];
    });
  }, [rules, snapshot]);

  return (
    <section className="my-signals" aria-labelledby="my-signals-title">
      <div className="my-signals-heading">
        <div>
          <p className="eyebrow">Personal research queue</p>
          <h2 id="my-signals-title">My Signals</h2>
          <p>Saved locally in this browser. Shared GLD and BMNR scan findings are also highlighted in Slack.</p>
        </div>
        <div className="signal-sync-controls">
          {snapshot && <span className="scan-date">{syncedAt ? `Checked at ${syncedAt}` : `Latest scan: ${snapshot.date}`}</span>}
          <button className="secondary-btn" onClick={syncSignals} disabled={syncing || !rules.length} aria-busy={syncing}>
            {syncing ? "Syncing..." : "Sync"}
          </button>
        </div>
      </div>

      <div className="signal-rule-grid">
        {rules.map((rule) => (
          <article className="signal-rule" key={rule.ticker}>
            <div>
              <strong>{rule.ticker}</strong>
              <Link to={`/c/${rule.ticker}/options`}>Options</Link>
            </div>
            {reads[rule.ticker] && (() => {
              const { tech, date, error } = reads[rule.ticker];
              if (!tech) return <p role="status">{error}</p>;
              const price = (n: number | null) => n == null ? "Unavailable" : n.toFixed(2);
              return <div className="signal-current-read">
                <strong>{price(tech.latestClose)} · {tech.trend.signal === "bullish" ? "Uptrend" : tech.trend.signal === "bearish" ? "Downtrend" : "Mixed trend"}</strong>
                <p>RSI {price(tech.rsi14)} · SMA50 {price(tech.sma50)} · SMA200 {price(tech.sma200)}</p>
                <p>Watch the 20-session range: {price(tech.nearTerm.support)}–{price(tech.nearTerm.resistance)}.</p>
                <small>Price bar: {date}</small>
              </div>;
            })()}
            <label>
              Alert on
              <select
                value={rule.minSeverity}
                onChange={(event) => saveRules(rules.map((current) => current.ticker === rule.ticker
                  ? { ...current, minSeverity: event.target.value as Severity }
                  : current))}
              >
                <option value="critical">Critical only</option>
                <option value="warn">Warnings and critical</option>
                <option value="info">All tracked signals</option>
              </select>
            </label>
            <button className="remove-rule" onClick={() => saveRules(rules.filter((current) => current.ticker !== rule.ticker))}>
              Remove
            </button>
          </article>
        ))}
        <div className="add-rule">
          <input value={input} onChange={(event) => setInput(event.target.value)} placeholder="Add ticker" maxLength={7} onKeyDown={(event) => event.key === "Enter" && addTicker()} />
          <button onClick={addTicker}>Add</button>
        </div>
      </div>

      <div className="signal-events">
        <div className="event-heading">
          <h3>Recent options and event signals</h3>
          <span>Daily scanner: technical, options, and SEC events</span>
        </div>
        {scanError && <p className="signal-empty" role="status">{scanError}</p>}
        {!scanError && !syncing && snapshot && matchingFindings.length === 0 && <p className="signal-empty">{
          snapshot.findings.some(f => rules.some(r => r.ticker === f.ticker))
            ? "Alerts were found but hidden by your filters. Select All tracked signals to view them."
            : "Scan complete. No new alert events for your tracked symbols."
        }</p>}
        {matchingFindings.slice(0, 8).map((finding) => (
          <article className={`signal-event ${finding.severity}`} key={finding.key}>
            <div className="event-meta"><span>{finding.ticker}</span><span>{finding.category}</span><span>{finding.severity}</span></div>
            <strong>{finding.title}</strong>
            <p>{finding.detail}</p>
          </article>
        ))}
      </div>
    </section>
  );
}
