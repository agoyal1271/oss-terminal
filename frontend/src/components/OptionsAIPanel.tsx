import { useState } from "react";
import { api, type OptionsChain } from "../api/client";
import { describeSkew, describeTermStructure } from "../optionsAnalysis";
import { generateWithOllama, listOllamaModels } from "../localLlm";

type Status = "idle" | "working" | "done" | "unreachable";
type CopyStatus = "idle" | "copied" | "error";

const GEMINI_URL = "https://gemini.google.com/app";

async function loadOptionsPrompt(ticker: string, chain: OptionsChain, companyName: string): Promise<string> {
  const s = chain.summary;
  const daysToExpiry = Math.round((chain.selected_expiration * 1000 - Date.now()) / 86_400_000);
  const topByVolume = (contracts: OptionsChain["calls"]) =>
    [...contracts].sort((a, b) => b.volume - a.volume).slice(0, 3).map((c) => `strike $${c.strike} (vol ${c.volume}, OI ${c.open_interest}, IV ${((c.implied_volatility ?? 0) * 100).toFixed(0)}%)`).join("; ");

  const skew = describeSkew(chain);
  const termStructure = await api.companyOptionsTermStructure(ticker).catch(() => null);

  const lines = [
    `You are a research assistant describing the options market for ${companyName} for a retail investor.`,
    `Write 5-7 plain-English sentences on what this options activity implies (sentiment lean, expected volatility, term structure shape, skew, notable positioning).`,
    `RULES (do not break these, even if it feels natural to): Do not give buy/sell advice or recommend a specific options strategy (no "buy calls", "long call", "bear put spread", "collar", or similar) -- describe only what the data shows. Do not mention or invent options Greeks (Delta, Gamma, Theta, Vega) or any bid/ask price -- none are provided below and you must not make them up. Use ONLY the numbers listed in DATA below; if something isn't listed, say it's not available rather than estimating or inventing it.`,
    ``,
    `DATA (expiration in ~${daysToExpiry} days, underlying price $${chain.underlying_price}):`,
    `- Put/call volume ratio: ${s.put_call_volume_ratio?.toFixed(2) ?? "n/a"} (>1 means more put volume than call volume today)`,
    `- Put/call open interest ratio: ${s.put_call_oi_ratio?.toFixed(2) ?? "n/a"} (reflects existing positioning, not just today's activity)`,
    `- At-the-money strike ~$${s.atm_strike}: call IV ${s.atm_call_iv ? (s.atm_call_iv * 100).toFixed(0) + "%" : "n/a"}, put IV ${s.atm_put_iv ? (s.atm_put_iv * 100).toFixed(0) + "%" : "n/a"}`,
    `- Market-implied expected move by expiration (ATM straddle price): ${s.expected_move_atm_straddle ? "$" + s.expected_move_atm_straddle.toFixed(2) : "n/a"}`,
    `- IV skew (this expiration): ${skew.summary}`,
    termStructure ? `- IV term structure (across upcoming expirations): ${describeTermStructure(termStructure.points).summary}` : `- IV term structure: unavailable`,
    `- Highest-volume calls: ${topByVolume(chain.calls) || "none"}`,
    `- Highest-volume puts: ${topByVolume(chain.puts) || "none"}`,
    ``,
    `Reminder before you answer: no buy/sell advice, no options strategies, no invented Greeks or prices -- only the numbers listed above.`,
  ];
  return lines.join("\n");
}

export function OptionsAIPanel({ ticker, chain, companyName }: { ticker: string; chain: OptionsChain; companyName: string }) {
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

      const prompt = await loadOptionsPrompt(ticker, chain, companyName);
      const text = await generateWithOllama(baseUrl, useModel, prompt);
      setOutput(text.trim());
      setStatus("done");
    } catch (e) {
      setError((e as Error).message);
      setStatus("unreachable");
    }
  }

  async function copyPrompt() {
    try {
      const prompt = await loadOptionsPrompt(ticker, chain, companyName);
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
        Analyzes the options chain above — put/call ratios, IV term structure, IV skew, implied move, unusual volume —
        using a language model running <strong>entirely on your machine</strong> via{" "}
        <a href="https://ollama.com" target="_blank" rel="noreferrer">Ollama</a>. Nothing on this page is sent to any
        cloud service. Local model too slow? Copy the same prompt and run it against a hosted model instead.
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
          {status === "working" ? "Analyzing…" : "Analyze options chain (local)"}
        </button>
        <button className="secondary-btn" onClick={copyPrompt}>
          {copyStatus === "copied" ? "Copied ✓" : "Copy prompt"}
        </button>
        <a className="secondary-btn link-like" href={GEMINI_URL} target="_blank" rel="noreferrer">
          Open Gemini ↗
        </a>
      </div>
      {copyStatus === "copied" && <p className="empty-note">Prompt copied — paste it into Gemini, ChatGPT, or any other AI tool.</p>}
      {copyStatus === "error" && <p className="empty-note">Couldn't copy: {error}</p>}

      {status === "working" && (
        <p className="empty-note">
          Running locally — larger models can take several minutes on CPU-only hardware. Use "Copy prompt" above if
          you don't want to wait.
        </p>
      )}
      {status === "done" && <div className="ai-read-output">{output}</div>}
      {status === "unreachable" && (
        <div className="ai-read-setup">
          <p className="empty-note">Couldn't reach Ollama at {baseUrl}: {error}</p>
          <ol className="setup-steps">
            <li>Install Ollama from <a href="https://ollama.com" target="_blank" rel="noreferrer">ollama.com</a>.</li>
            <li>Pull a model: <code>ollama pull llama3.2</code></li>
            <li>Make sure it's running, then try again.</li>
            <li>Plain CORS error in the console? Set <code>OLLAMA_ORIGINS</code> to include this page's origin and restart Ollama.</li>
            <li>
              Loading this over HTTPS from a non-localhost URL? Chrome/Edge block that combination outright (
              <a href="https://github.com/ollama/ollama/issues/7000" target="_blank" rel="noreferrer">open Ollama issue</a>
              , no config fix exists yet) — Firefox and Safari work; running the frontend from <code>localhost</code>{" "}
              also sidesteps it.
            </li>
          </ol>
        </div>
      )}
    </div>
  );
}
