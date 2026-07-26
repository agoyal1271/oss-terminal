import { useState } from "react";
import type { OptionsChain } from "../api/client";
import { generateWithOllama, listOllamaModels } from "../localLlm";

type Status = "idle" | "working" | "done" | "unreachable";

function buildOptionsPrompt(chain: OptionsChain, companyName: string): string {
  const s = chain.summary;
  const daysToExpiry = Math.round((chain.selected_expiration * 1000 - Date.now()) / 86_400_000);
  const topByVolume = (contracts: OptionsChain["calls"]) =>
    [...contracts].sort((a, b) => b.volume - a.volume).slice(0, 3).map((c) => `strike $${c.strike} (vol ${c.volume}, OI ${c.open_interest}, IV ${((c.implied_volatility ?? 0) * 100).toFixed(0)}%)`).join("; ");

  const lines = [
    `You are a research assistant describing the options market for ${companyName} for a retail investor.`,
    `Write 4-6 plain-English sentences on what this options activity implies (sentiment lean, expected volatility, notable positioning).`,
    `Do not give buy/sell advice or recommend a specific options strategy -- describe only what the data shows.`,
    ``,
    `DATA (expiration in ~${daysToExpiry} days, underlying price $${chain.underlying_price}):`,
    `- Put/call volume ratio: ${s.put_call_volume_ratio?.toFixed(2) ?? "n/a"} (>1 means more put volume than call volume today)`,
    `- Put/call open interest ratio: ${s.put_call_oi_ratio?.toFixed(2) ?? "n/a"} (reflects existing positioning, not just today's activity)`,
    `- At-the-money strike ~$${s.atm_strike}: call IV ${s.atm_call_iv ? (s.atm_call_iv * 100).toFixed(0) + "%" : "n/a"}, put IV ${s.atm_put_iv ? (s.atm_put_iv * 100).toFixed(0) + "%" : "n/a"}`,
    `- Market-implied expected move by expiration (ATM straddle price): ${s.expected_move_atm_straddle ? "$" + s.expected_move_atm_straddle.toFixed(2) : "n/a"}`,
    `- Highest-volume calls: ${topByVolume(chain.calls) || "none"}`,
    `- Highest-volume puts: ${topByVolume(chain.puts) || "none"}`,
  ];
  return lines.join("\n");
}

export function OptionsAIPanel({ chain, companyName }: { chain: OptionsChain; companyName: string }) {
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

      const prompt = buildOptionsPrompt(chain, companyName);
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
        Analyzes the options chain above (put/call ratios, IV skew, implied move, unusual volume) using a language
        model running <strong>entirely on your machine</strong> via <a href="https://ollama.com" target="_blank" rel="noreferrer">Ollama</a>.
        Nothing on this page is sent to any cloud service.
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
      </div>
      {status === "working" && (
        <p className="empty-note">Running locally — larger models can take a minute or more on CPU-only hardware.</p>
      )}
      {status === "done" && <div className="ai-read-output">{output}</div>}
      {status === "unreachable" && (
        <div className="ai-read-setup">
          <p className="empty-note">Couldn't reach Ollama at {baseUrl}: {error}</p>
          <ol className="setup-steps">
            <li>Install Ollama from <a href="https://ollama.com" target="_blank" rel="noreferrer">ollama.com</a>.</li>
            <li>Pull a model: <code>ollama pull llama3.2</code></li>
            <li>Make sure it's running, then try again.</li>
            <li>CORS error in the console? Restart with <code>OLLAMA_ORIGINS=* ollama serve</code>.</li>
          </ol>
        </div>
      )}
    </div>
  );
}
