import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";

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

  useEffect(() => {
    setRules(readRules());
    fetch(RAW_SCAN_URL)
      .then((res) => {
        if (!res.ok) throw new Error("Daily scan is unavailable");
        return res.json() as Promise<ScanSnapshot>;
      })
      .then(setSnapshot)
      .catch(() => setScanError("Daily scan will appear after the next scheduled run."));
  }, []);

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
        {snapshot && <span className="scan-date">Latest scan: {snapshot.date}</span>}
      </div>

      <div className="signal-rule-grid">
        {rules.map((rule) => (
          <article className="signal-rule" key={rule.ticker}>
            <div>
              <strong>{rule.ticker}</strong>
              <Link to={`/c/${rule.ticker}/options`}>Options</Link>
            </div>
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
        {scanError && <p className="signal-empty">{scanError}</p>}
        {snapshot && matchingFindings.length === 0 && <p className="signal-empty">No matching alerts in the latest scan. That is a quiet read, not a buy or sell signal.</p>}
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
