/**
 * The five /api/try providers. Request shapes are ported from clipbench/providers/{cloudflare,groq,deepgram,
 * elevenlabs}.py (the Python adapters are the reference, checked 2026-10-19), with the same opt-out flags:
 * Deepgram and Workers AI Nova-3 send mip_opt_out=true, ElevenLabs sends tag_audio_events=false.
 *
 * Privacy: audio lives only in memory for the length of the request; nothing here logs or stores text.
 * Error messages are fixed sentences plus an HTTP status, never a provider body (which could echo input).
 */
import { modelCost, TRY_MODELS, type TryLanguage, type TryModel } from "./models";

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

export type ProviderInput = { audio: Uint8Array; seconds: number; language: TryLanguage };

export class ProviderFailure extends Error {
  constructor(
    message: string,
    /** True when the provider may still have billed (a timeout after upload). */
    readonly billed = false,
  ) {
    super(message);
  }
}

export const PROVIDER_TIMEOUT_MS = 45_000;

type Run = (input: ProviderInput, env: Env, signal: AbortSignal) => Promise<{ text: string; words: TryWord[]; detected_language: string | null }>;

// ---------------------------------------------------------------- helpers

const ISO3: Record<string, string> = { eng: "en", fra: "fr", fre: "fr", jpn: "ja", kor: "ko", spa: "es", hin: "hi" };
const NAMES: Record<string, string> = { english: "en", french: "fr", japanese: "ja", korean: "ko", spanish: "es", hindi: "hi" };

/** "japanese", "jpn", "ja-JP" and "ja" all become "ja" (clipbench.providers.base.normalize_language_code). */
export function normalizeLanguage(code: unknown): string | null {
  if (typeof code !== "string" || !code.trim()) return null;
  const c = code.trim().toLowerCase();
  if (NAMES[c]) return NAMES[c]!;
  if (ISO3[c]) return ISO3[c]!;
  const base = c.split(/[-_]/)[0]!;
  if (base.length === 2) return base;
  return ISO3[base] ?? (base.length <= 16 ? base : null);
}

const ms = (s: unknown) => Math.round(Number(s) * 1000);
const okTime = (w: { start?: unknown; end?: unknown }) => Number.isFinite(Number(w.start)) && Number.isFinite(Number(w.end)) && w.start != null && w.end != null;

function words(list: unknown, key: string): TryWord[] {
  if (!Array.isArray(list)) return [];
  const out: TryWord[] = [];
  for (const w of list as Record<string, unknown>[]) {
    if (!w || typeof w !== "object" || !okTime(w)) continue;
    const text = String(w[key] ?? w.word ?? "").trim();
    if (text) out.push({ text, start_ms: ms(w.start), end_ms: ms(w.end) });
  }
  return out;
}

function toBase64(bytes: Uint8Array): string {
  let bin = "";
  const step = 0x8000;
  for (let i = 0; i < bytes.length; i += step) bin += String.fromCharCode(...bytes.subarray(i, i + step));
  return btoa(bin);
}

const wavBlob = (audio: Uint8Array) => new Blob([audio], { type: "audio/wav" });

async function post(label: string, url: string, init: RequestInit, signal: AbortSignal): Promise<Record<string, unknown>> {
  const res = await fetch(url, { ...init, method: "POST", signal });
  if (!res.ok) {
    await res.body?.cancel();
    const why = res.status === 401 || res.status === 403 ? "rejected the server's key" : res.status === 429 ? "is rate limiting this server" : "returned an error";
    throw new ProviderFailure(`${label} ${why} (HTTP ${res.status}).`);
  }
  const body: unknown = await res.json().catch(() => null);
  if (!body || typeof body !== "object") throw new ProviderFailure(`${label} returned a response that is not JSON.`);
  return body as Record<string, unknown>;
}

const obj = (v: unknown): Record<string, unknown> => (v && typeof v === "object" ? (v as Record<string, unknown>) : {});
const first = (v: unknown): Record<string, unknown> => obj(Array.isArray(v) ? v[0] : undefined);

/** Deepgram shaped output (direct API and Workers AI nova-3). */
function deepgramShape(body: Record<string, unknown>) {
  const channel = first(obj(body.results).channels);
  const alt = first(channel.alternatives);
  if (typeof alt.transcript !== "string") throw new ProviderFailure("Unexpected response shape.");
  return { text: alt.transcript.trim(), words: words(alt.words, "punctuated_word"), detected_language: normalizeLanguage(channel.detected_language) };
}

// ---------------------------------------------------------------- providers

/** @cf/openai/whisper-large-v3-turbo: base64 JSON, takes a language, words inside segments (cloudflare.py). */
const cfWhisper: Run = async ({ audio, language }, env) => {
  if (!env.AI) throw new ProviderFailure("Workers AI is not bound on this server.");
  const input: Record<string, unknown> = { audio: toBase64(audio), task: "transcribe" };
  if (language !== "auto") input.language = language;
  const out = obj(await env.AI.run("@cf/openai/whisper-large-v3-turbo", input as unknown as Ai_Cf_Openai_Whisper_Large_V3_Turbo_Input));
  if (typeof out.text !== "string") throw new ProviderFailure("Unexpected response shape.");
  const segs = Array.isArray(out.segments) ? (out.segments as unknown[]) : [];
  return {
    text: out.text.trim(),
    words: segs.flatMap((s) => words(obj(s).words, "word")),
    detected_language: normalizeLanguage(obj(out.transcription_info).language),
  };
};

/** @cf/deepgram/nova-3: raw bytes with a content type, Deepgram options (cloudflare.py CfDeepgramNova3). */
const cfNova: Run = async ({ audio, language }, env) => {
  if (!env.AI) throw new ProviderFailure("Workers AI is not bound on this server.");
  const input: Ai_Cf_Deepgram_Nova_3_Input = {
    audio: { body: new Response(audio).body!, contentType: "audio/wav" },
    smart_format: true,
    punctuate: true,
    mip_opt_out: true, // out of Deepgram's Model Improvement Program
    ...(language === "auto" ? { detect_language: true } : { language }),
  };
  return deepgramShape(obj(await env.AI.run("@cf/deepgram/nova-3", input)));
};

/** Groq hosted Whisper large-v3, verbose_json with word timestamps (groq.py). */
const groq: Run = async ({ audio, language }, env, signal) => {
  if (!env.GROQ_API_KEY) throw new ProviderFailure("Not configured on this server.");
  const form = new FormData();
  form.set("file", wavBlob(audio), "clip.wav");
  form.set("model", "whisper-large-v3");
  if (language !== "auto") form.set("language", language);
  form.set("response_format", "verbose_json");
  form.set("timestamp_granularities[]", "word");
  form.set("temperature", "0.0");
  const body = await post("Groq", "https://api.groq.com/openai/v1/audio/transcriptions", { headers: { authorization: `Bearer ${env.GROQ_API_KEY}` }, body: form }, signal);
  if (typeof body.text !== "string") throw new ProviderFailure("Unexpected response shape.");
  return { text: body.text.trim(), words: words(body.words, "word"), detected_language: normalizeLanguage(body.language) };
};

/** Deepgram pre-recorded, mip_opt_out=true on every request (deepgram.py). */
const deepgram: Run = async ({ audio, language }, env, signal) => {
  if (!env.DEEPGRAM_API_KEY) throw new ProviderFailure("Not configured on this server.");
  const q = new URLSearchParams({ model: "nova-3" });
  if (language === "auto") q.set("detect_language", "true");
  else q.set("language", language);
  q.set("smart_format", "true");
  q.set("punctuate", "true");
  q.set("mip_opt_out", "true");
  const body = await post(
    "Deepgram",
    `https://api.deepgram.com/v1/listen?${q}`,
    { headers: { authorization: `Token ${env.DEEPGRAM_API_KEY}`, "content-type": "audio/wav" }, body: audio },
    signal,
  );
  return deepgramShape(body);
};

/** ElevenLabs Scribe v2, word timestamps, no audio event tags, no diarization (elevenlabs.py). */
const elevenlabs: Run = async ({ audio, language }, env, signal) => {
  if (!env.ELEVENLABS_API_KEY) throw new ProviderFailure("Not configured on this server.");
  const form = new FormData();
  form.set("file", wavBlob(audio), "clip.wav");
  form.set("model_id", "scribe_v2");
  if (language !== "auto") form.set("language_code", language);
  form.set("timestamps_granularity", "word");
  form.set("tag_audio_events", "false");
  form.set("diarize", "false");
  const body = await post("ElevenLabs", "https://api.elevenlabs.io/v1/speech-to-text", { headers: { "xi-api-key": env.ELEVENLABS_API_KEY }, body: form }, signal);
  if (typeof body.text !== "string") throw new ProviderFailure("Unexpected response shape.");
  const kept = (Array.isArray(body.words) ? (body.words as Record<string, unknown>[]) : []).filter((w) => (w?.type ?? "word") === "word" && w?.start != null);
  return { text: body.text.trim(), words: words(kept, "text"), detected_language: normalizeLanguage(body.language_code) };
};

export const RUNNERS: Record<string, Run> = {
  "cf-whisper-large-v3-turbo": cfWhisper,
  "cf-deepgram-nova-3": cfNova,
  "groq-whisper": groq,
  deepgram,
  "elevenlabs-scribe": elevenlabs,
};

// ---------------------------------------------------------------- one call, normalised

function timeout<T>(p: Promise<T>, ms: number, ctrl: AbortController): Promise<T> {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const t = new Promise<never>((_, reject) => {
    timer = setTimeout(() => {
      ctrl.abort();
      reject(new ProviderFailure(`No answer within ${Math.round(ms / 1000)} s.`, true));
    }, ms);
  });
  return Promise.race([p, t]).finally(() => clearTimeout(timer));
}

/** A fixed sentence for a thrown error. The provider's own text is only matched, never shown or logged. */
export function failureSentence(e: unknown): string {
  if (e instanceof Error && e.name === "AbortError") return "The request was cancelled.";
  const msg = e instanceof Error ? e.message : String(e ?? "");
  // Workers AI on a free account: "you have used up your daily free allocation of 10,000 neurons".
  if (/daily free allocation|neurons/i.test(msg)) {
    return "Workers AI's free daily allowance on this server is used up. It resets at 00:00 UTC.";
  }
  if (/quota|rate limit|too many requests|\b429\b/i.test(msg)) return "This provider is rate limited right now. Try again in a minute.";
  return "The call failed.";
}

/** Run one model and always resolve: failures become `error` with a plain sentence. */
export async function runModel(m: TryModel, run: Run, input: ProviderInput, env: Env, timeoutMs = PROVIDER_TIMEOUT_MS): Promise<TryResult & { billed: boolean }> {
  const base = { provider: m.provider, label: m.label, model_id: m.model_id };
  const ctrl = new AbortController();
  const t0 = Date.now();
  try {
    const out = await timeout(run(input, env, ctrl.signal), timeoutMs, ctrl);
    return { ...base, ...out, latency_ms: Date.now() - t0, cost_usd: modelCost(m, input.seconds), error: null, billed: true };
  } catch (e) {
    const failure = e instanceof ProviderFailure ? e : new ProviderFailure(failureSentence(e));
    // Status only, never text: no transcript or audio reaches the logs.
    console.log(JSON.stringify({ event: "try_provider_error", provider: m.provider, error: failure.message }));
    return {
      ...base,
      text: "",
      words: [],
      latency_ms: Date.now() - t0,
      cost_usd: failure.billed ? modelCost(m, input.seconds) : 0,
      detected_language: null,
      error: failure.message,
      billed: failure.billed,
    };
  }
}

export { TRY_MODELS };
