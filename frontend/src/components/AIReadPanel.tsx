import { useState } from "react";
import { api, type AnnualRow, type OwnershipData } from "../api/client";
import { computeTechnicalRead, type TechnicalRead } from "../technicals";
import { generateWithOllama, listOllamaModels } from "../localLlm";

type Status = "idle" | "working" | "done" | "unreachable" | "error";
type CopyStatus = "idle" | "copying" | "copied" | "error";

const GEMINI_URL = "https://gemini.google.com/app";

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

async function loadPrompt(ticker: string, companyName: string): Promise<string> {
  const [financials, prices, ownership] = await Promise.all([
    api.companyFinancials(ticker),
    api.companyPrices(ticker, "2y"),
    api.companyOwnership(ticker).catch(() => null),
  ]);
  const latest = [...financials.annual].sort((a, b) => b.fy - a.fy)[0];
  const tech = computeTechnicalRead(prices.points);
  return buildPrompt(companyName, ticker, latest, tech, ownership);
}

export function AIReadPanel({ ticker, companyName }: { ticker: string; companyName: string }) {
  const [baseUrl, setBaseUrl] = useState("http://localhost:11434");
  const [model, setModel] = useState("llama3.2");
  const [status, setStatus] = useState<Status>("idle");
  const [output, setOutput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<CopyStatus>("idle");

  async function run() {
    setStatus("working");
    setError(null);
    setOutput("");
    try {
      const detected = await listOllamaModels(baseUrl);
      if (detected.length === 0) {
        throw new Error("Ollama is reachable but has no usable chat model pulled yet -- run: ollama pull llama3.2");
      }
      const names = detected.map((m) => m.name);
      const useModel = names.includes(model) ? model : names[0];
      setModel(useModel);

      const prompt = await loadPrompt(ticker, companyName);
      const text = await generateWithOllama(baseUrl, useModel, prompt);
      setOutput(text.trim());
      setStatus("done");
    } catch (e) {
      setError((e as Error).message);
      setStatus("unreachable");
    }
  }

  async function copyPrompt() {
    setCopyStatus("copying");
    try {
      const prompt = await loadPrompt(ticker, companyName);
      await navigator.clipboard.writeText(prompt);
      setCopyStatus("copied");
      setTimeout(() => setCopyStatus("idle"), 2500);
    } catch (e) {
      setError((e as Error).message);
      setCopyStatus("error");
    }
  }

  return (
    <div className="ai-read-panel">
      <p className="ai-read-intro">
        Synthesizes the data on this page into a short plain-English read using a large language model running{" "}
        <strong>entirely on your machine</strong> via <a href="https://ollama.com" target="_blank" rel="noreferrer">Ollama</a>. No API key, no cost, and
        nothing on this page is sent to any cloud service — the browser talks directly to Ollama on localhost. Local
        model too slow? Copy the same prompt and run it against a hosted model instead.
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
        <button className="secondary-btn" onClick={copyPrompt} disabled={copyStatus === "copying"}>
          {copyStatus === "copied" ? "Copied ✓" : copyStatus === "copying" ? "Copying…" : "Copy prompt"}
        </button>
        <a className="secondary-btn link-like" href={GEMINI_URL} target="_blank" rel="noreferrer">
          Open Gemini ↗
        </a>
      </div>
      {copyStatus === "copied" && <p className="empty-note">Prompt copied — paste it into Gemini, ChatGPT, or any other AI tool.</p>}
      {copyStatus === "error" && <p className="empty-note">Couldn't build the prompt: {error}</p>}

      {status === "working" && (
        <p className="empty-note">
          Running locally — larger models can take several minutes on CPU-only hardware. Not stuck, just slow; use
          "Copy prompt" above if you don't want to wait.
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
              If the browser console shows a plain CORS error (not the Private Network one below), Ollama is
              rejecting requests from this page's origin — set <code>OLLAMA_ORIGINS</code> to include it (or{" "}
              <code>*</code> for any origin) and restart Ollama.
            </li>
            <li>
              <strong>Loading this page over HTTPS from a non-localhost URL (like the hosted version of this app)?</strong>{" "}
              Chrome and Edge block that combination outright with "No 'Access-Control-Allow-Private-Network' header
              was present" — Ollama's server doesn't send that header yet (
              <a href="https://github.com/ollama/ollama/issues/7000" target="_blank" rel="noreferrer">open upstream issue</a>
              ), and no setting on this page or in Ollama's config can work around it. Verified: Firefox and Safari
              don't enforce this the same way and should work; Chrome/Edge won't until Ollama ships a fix. Loading
              this app from <code>localhost</code> (e.g. running the frontend locally) sidesteps it entirely, since
              that's no longer a public-page-to-local-network request.
            </li>
          </ol>
        </div>
      )}
    </div>
  );
}
