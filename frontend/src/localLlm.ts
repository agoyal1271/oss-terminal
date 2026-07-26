// Talks directly to a local Ollama server from the browser -- the backend is
// never involved. Since Ollama runs on the user's own machine, the tab (also
// on their machine) can reach http://localhost:11434 directly, so this works
// even against the hosted deployment: nothing about the request or the
// generated text ever leaves the user's computer.

export interface OllamaModelInfo {
  name: string;
  sizeBytes: number;
  paramSize: string | null;
}

// Embedding models (nomic-embed-text, etc.) show up in /api/tags alongside
// chat models but don't generate text -- calling /api/generate on one either
// errors or returns garbage. Filtered out here so callers only see models
// actually usable for this feature.
function isEmbeddingModel(name: string, family: string | undefined): boolean {
  const s = (name + " " + (family ?? "")).toLowerCase();
  return s.includes("embed") || s.includes("bert");
}

export async function listOllamaModels(baseUrl: string): Promise<OllamaModelInfo[]> {
  const res = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(2500) });
  if (!res.ok) throw new Error(`Ollama responded ${res.status}`);
  const data = await res.json();
  type RawModel = { name: string; size: number; details?: { family?: string; parameter_size?: string } };
  return ((data.models ?? []) as RawModel[])
    .filter((m) => !isEmbeddingModel(m.name, m.details?.family))
    .map((m) => ({ name: m.name, sizeBytes: m.size, paramSize: m.details?.parameter_size ?? null }));
}

export async function generateWithOllama(baseUrl: string, model: string, prompt: string): Promise<string> {
  const res = await fetch(`${baseUrl}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      model,
      prompt,
      stream: false,
      // think:false skips the extended reasoning phase some models (e.g.
      // Qwen3) run by default -- ignored harmlessly by models that don't
      // support it, and makes this noticeably faster for a short synthesis.
      think: false,
      // Bounds worst-case duration regardless of hardware speed -- without
      // this a rambling model has no built-in stopping point, and a slow
      // machine can otherwise run well past any timeout we pick.
      options: { num_predict: 400 },
    }),
    // Measured live against a 36B model on this machine: a request that
    // legitimately needed more than 5 minutes got killed by an earlier,
    // shorter timeout and surfaced as a confusing "signal timed out" even
    // though nothing was actually broken -- just slow. 10 minutes gives
    // large local models real headroom; num_predict above is the primary
    // guard against a truly runaway request.
    signal: AbortSignal.timeout(600_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Ollama generate failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  return (data.response as string) ?? "";
}
