import { useState } from "react";
import { api, type AnnualRow, type OwnershipData } from "../api/client";
import { computeTechnicalRead, type TechnicalRead } from "../technicals";
import { generateWithOllama, listOllamaModels } from "../localLlm";

type Status = "idle" | "working" | "done" | "unreachable" | "error";

function buildPrompt(companyName: string, ticker: string, latest: AnnualRow | undefined, tech: TechnicalRead | null, ownership: OwnershipData | null): string {
  const lines: string[] = [
    `You are a research assistant summarizing publicly reported data about ${companyName} (${ticker}) for a retail investor.`,
    `Write 4-6 plain-English sentences covering both supportive and concerning signals in the data below.`,
    `Do not give buy/sell advice, price targets, or a recommendation -- describe only what the data shows.`,
    ``,
    `DATA:`,
  ];

  if (latest && latest.metrics.revenue != null) {
    const rev = (latest.metrics.revenue / 1e9).toFixed(1);
    const ni = latest.metrics.net_income != null ? (latest.metrics.net_income / 1e9).toFixed(1) : "n/a";
    const margin = latest.derived.net_margin != null ? (latest.derived.net_margin * 100).toFixed(1) + "%" : "n/a";
    const roe = latest.derived.return_on_equity != null ? (latest.derived.return_on_equity * 100).toFixed(1) + "%" : "n/a";
    lines.push(`- Latest fiscal year (FY${latest.fy}): revenue $${rev}B, net income $${ni}B, net margin ${margin}, return on equity ${roe}.`);
  }
  if (tech) {
    lines.push(`- Trend: ${tech.trend.label}`);
    lines.push(`- Momentum: ${tech.momentum.label}`);
    lines.push(`- Volume: ${tech.volume.label}`);
    lines.push(`- 52-week range: ${(tech.range52w.pctFromHigh * 100).toFixed(1)}% from the high, ${(tech.range52w.pctFromLow * 100).toFixed(1)}% from the low.`);
  }
  if (ownership) {
    lines.push(`- Institutional ownership: ~${ownership.holder_count_estimate} institutional filers (Form 13F, quarter ended ${ownership.quarter_end}).`);
  }
  return lines.join("\n");
}

export function AIReadPanel({ ticker, companyName }: { ticker: string; companyName: string }) {
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434");
  const [model, setModel] = useState("llama3.2");
  const [status, setStatus] = useState<Status>("idle");
  const [output, setOutput] = useState("");
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setStatus("working");
    setError(null);
    setOutput("");
    try {
      const detected = await listOllamaModels(baseUrl);
      if (detected.length === 0) {
        throw new Error("Ollama is reachable but has no models pulled yet -- run: ollama pull llama3.2");
      }
      const useModel = detected.includes(model) ? model : detected[0];
      setModel(useModel);

      const [financials, prices, ownership] = await Promise.all([
        api.companyFinancials(ticker),
        api.companyPrices(ticker, "2y"),
        api.companyOwnership(ticker).catch(() => null),
      ]);
      const latest = [...financials.annual].sort((a, b) => b.fy - a.fy)[0];
      const tech = computeTechnicalRead(prices.points);

      const prompt = buildPrompt(companyName, ticker, latest, tech, ownership);
      const text = await generateWithOllama(baseUrl, useModel, prompt);
      setOutput(text.trim());
      setStatus("done");
    } catch (e) {
      setError((e as Error).message);
      setStatus("unreachable");
    }
  }

  return (
    <div className="ai-read-panel">
      <p className="ai-read-intro">
        Synthesizes the data on this page into a short plain-English read using a large language model running{" "}
        <strong>entirely on your machine</strong> via <a href="https://ollama.com" target="_blank" rel="noreferrer">Ollama</a>. No API key, no cost, and
        nothing on this page is sent to any cloud service — the browser talks directly to Ollama on localhost.
      </p>

      <div className="ai-read-controls">
        <label>
          Ollama URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        </label>
        <label>
          Model
          <input value={model} onChange={(e) => setModel(e.target.value)} />
        </label>
        <button className="primary-btn" onClick={run} disabled={status === "working"}>
          {status === "working" ? "Generating…" : "Generate AI read (local)"}
        </button>
      </div>

      {status === "working" && (
        <p className="empty-note">
          Running locally — larger models can take a minute or more on CPU-only hardware. Not stuck, just thinking.
        </p>
      )}

      {status === "done" && <div className="ai-read-output">{output}</div>}

      {status === "unreachable" && (
        <div className="ai-read-setup">
          <p className="empty-note">Couldn't reach Ollama at {baseUrl}: {error}</p>
          <ol className="setup-steps">
            <li>Install Ollama from <a href="https://ollama.com" target="_blank" rel="noreferrer">ollama.com</a> (free, runs locally).</li>
            <li>Pull a small model: <code>ollama pull llama3.2</code></li>
            <li>Make sure Ollama is running (it starts automatically after install, or run <code>ollama serve</code>).</li>
            <li>
              If this fails with a CORS error in the browser console, Ollama is rejecting requests from this page's
              origin — restart it with <code>OLLAMA_ORIGINS=* ollama serve</code> to allow browser access.
            </li>
          </ol>
        </div>
      )}
    </div>
  );
}
