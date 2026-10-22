/**
 * Client for POST /api/try (web/worker/src/routes/try.ts). One request carries the WAV; the Worker answers with
 * NDJSON, one line per model as each provider finishes, so cards fill in as they arrive.
 */
import { TRY_MODELS, type TryLanguage } from "../../worker/src/try/models";

export { TRY_MODELS, TRY_LANGUAGES, clipCost, modelCost, type TryLanguage, type TryModel } from "../../worker/src/try/models";

export type TryWord = { text: string; start_ms: number; end_ms: number };
export type TryResult = {
  provider: string;
  label: string;
  model_id: string;
  text: string;
  words: TryWord[];
  latency_ms: number | null;
  cost_usd: number;
  detected_language: string | null;
  error: string | null;
};
export type TryEvent =
  | { type: "start"; seconds: number; language: TryLanguage; estimated_usd: number; models: { provider: string; label: string; model_id: string }[] }
  | ({ type: "result" } & TryResult)
  | { type: "done"; cost_usd: number };

export class TryError extends Error {
  constructor(
    readonly code: string,
    message: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/** Split an NDJSON byte stream into parsed lines. Exported for tests. */
export async function* ndjson(body: ReadableStream<Uint8Array>): AsyncGenerator<unknown> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    let nl: number;
    while ((nl = buf.indexOf("\n")) >= 0) {
      const line = buf.slice(0, nl).trim();
      buf = buf.slice(nl + 1);
      if (line) yield JSON.parse(line);
    }
    if (done) break;
  }
  if (buf.trim()) yield JSON.parse(buf);
}

export async function runTry(wav: ArrayBuffer, language: TryLanguage, token: string, onEvent: (e: TryEvent) => void, signal?: AbortSignal): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`/api/try?lang=${encodeURIComponent(language)}`, {
      method: "POST",
      headers: { "content-type": "audio/wav", "x-turnstile-token": token, accept: "application/x-ndjson" },
      body: wav,
      credentials: "same-origin",
      signal,
    });
  } catch (e) {
    if (signal?.aborted) throw e;
    throw new TryError("unavailable", "The playground service did not answer. Check your connection and try again.", 0);
  }
  const type = res.headers.get("content-type") ?? "";
  if (!res.ok || !type.includes("ndjson")) {
    const body = type.includes("json") ? ((await res.json().catch(() => null)) as { error?: { code: string; message: string } } | null) : null;
    throw new TryError(body?.error?.code ?? "unavailable", body?.error?.message ?? `The playground service is not available right now (HTTP ${res.status}).`, res.status);
  }
  if (!res.body) throw new TryError("unavailable", "The response was empty.", res.status);
  for await (const line of ndjson(res.body)) onEvent(line as TryEvent);
}

export const MODEL_ORDER = TRY_MODELS.map((m) => m.provider);
