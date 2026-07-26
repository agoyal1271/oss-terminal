// Talks directly to a local Ollama server from the browser -- the backend is
// never involved. Since Ollama runs on the user's own machine, the tab (also
// on their machine) can reach http://localhost:11434 directly, so this works
// even against the hosted deployment: nothing about the request or the
// generated text ever leaves the user's computer.

export async function listOllamaModels(baseUrl: string): Promise<string[]> {
  const res = await fetch(`${baseUrl}/api/tags`, { signal: AbortSignal.timeout(2500) });
  if (!res.ok) throw new Error(`Ollama responded ${res.status}`);
  const data = await res.json();
  return ((data.models ?? []) as { name: string }[]).map((m) => m.name);
}

export async function generateWithOllama(baseUrl: string, model: string, prompt: string): Promise<string> {
  const res = await fetch(`${baseUrl}/api/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // think:false skips the extended reasoning phase some models (e.g.
    // Qwen3) run by default -- ignored harmlessly by models that don't
    // support it, and makes this noticeably faster for a short synthesis.
    body: JSON.stringify({ model, prompt, stream: false, think: false }),
    // Local inference speed varies enormously by hardware and model size --
    // a large model on a CPU-only machine can genuinely take minutes for a
    // few hundred tokens. 5 minutes rather than guessing a "reasonable" cap.
    signal: AbortSignal.timeout(300_000),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`Ollama generate failed (${res.status}): ${text.slice(0, 200)}`);
  }
  const data = await res.json();
  return (data.response as string) ?? "";
}
