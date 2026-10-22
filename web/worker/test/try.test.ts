import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { app } from "../src/index";
import { fakeMode } from "../src/routes/try";
import { clipCost, TRY_MODELS } from "../src/try/models";
import { normalizeLanguage } from "../src/try/providers";
import { parseWav } from "../src/try/wav";
import { ORIGIN } from "./helpers";

// ---------------------------------------------------------------- fixtures

/** A silent PCM WAV. Defaults match what the browser sends: 16 kHz, mono, 16-bit. */
function wav(seconds: number, rate = 16_000, channels = 1, bits = 16): Uint8Array {
  const data = Math.round(seconds * rate) * channels * (bits / 8);
  const b = new ArrayBuffer(44 + data);
  const v = new DataView(b);
  const w = (at: number, s: string) => [...s].forEach((ch, i) => v.setUint8(at + i, ch.charCodeAt(0)));
  w(0, "RIFF");
  v.setUint32(4, 36 + data, true);
  w(8, "WAVE");
  w(12, "fmt ");
  v.setUint32(16, 16, true);
  v.setUint16(20, 1, true);
  v.setUint16(22, channels, true);
  v.setUint32(24, rate, true);
  v.setUint32(28, rate * channels * (bits / 8), true);
  v.setUint16(32, channels * (bits / 8), true);
  v.setUint16(34, bits, true);
  w(36, "data");
  v.setUint32(40, data, true);
  return new Uint8Array(b);
}

let ipN = 0;
const ip = () => `198.51.100.${++ipN}`;

type Calls = { url: string; init?: RequestInit }[];
let calls: Calls;
let turnstileOk: boolean;
let groqFails: boolean;
let aiInputs: { model: string; input: Record<string, unknown> }[];

const fakeAI = {
  async run(model: string, input: Record<string, unknown>) {
    aiInputs.push({ model, input });
    if (model === "@cf/openai/whisper-large-v3-turbo") {
      return { text: " hello there world ", transcription_info: { language: "en" }, segments: [{ words: [{ word: "hello", start: 0.1, end: 0.4 }, { word: "there", start: 0.5, end: 0.8 }, { word: "world", start: 0.9, end: 1.2 }] }] };
    }
    return { results: { channels: [{ detected_language: "en", alternatives: [{ transcript: "Hello world.", words: [{ word: "hello", punctuated_word: "Hello", start: 0.1, end: 0.4 }, { word: "world", punctuated_word: "world.", start: 0.5, end: 0.9 }] }] }] } };
  },
};

const json = (o: unknown, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json" } });

beforeEach(() => {
  calls = [];
  aiInputs = [];
  turnstileOk = true;
  groqFails = false;
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = input instanceof Request ? input.url : String(input);
    calls.push({ url, init });
    if (url.startsWith("https://challenges.cloudflare.com/turnstile/v0/siteverify")) {
      return json(turnstileOk ? { success: true } : { success: false, "error-codes": ["invalid-input-response"] });
    }
    if (url.startsWith("https://api.groq.com/")) {
      if (groqFails) return json({ error: { message: "boom" } }, 500);
      return json({ text: "Hello, world.", language: "english", words: [{ word: "Hello,", start: 0.1, end: 0.4 }, { word: "world.", start: 0.5, end: 0.9 }] });
    }
    if (url.startsWith("https://api.deepgram.com/")) {
      return json({ results: { channels: [{ alternatives: [{ transcript: "Hello world.", words: [{ word: "hello", punctuated_word: "Hello", start: 0.1, end: 0.4 }, { word: "world", punctuated_word: "world.", start: 0.5, end: 0.9 }] }] }] } });
    }
    if (url.startsWith("https://api.elevenlabs.io/")) {
      return json({ text: "Hello world.", language_code: "eng", words: [{ text: "Hello", type: "word", start: 0.1, end: 0.4 }, { text: " ", type: "spacing", start: 0.4, end: 0.5 }, { text: "world.", type: "word", start: 0.5, end: 0.9 }] });
    }
    throw new Error(`unexpected fetch ${url}`);
  });
});

afterEach(() => vi.restoreAllMocks());

function testEnv(over: Partial<Env> = {}): Env {
  return {
    ...env,
    ACCOUNTS: "false",
    AI: fakeAI as unknown as Ai,
    TURNSTILE_SECRET: "test-turnstile-secret",
    GROQ_API_KEY: "test-groq",
    DEEPGRAM_API_KEY: "test-deepgram",
    ELEVENLABS_API_KEY: "test-elevenlabs",
    TRY_DAILY_BUDGET_USD: "1.00",
    TRY_FAKE_PROVIDERS: "",
    PROXY_KEY: "test-proxy-key",
    ...over,
  } as Env;
}

async function post(opts: { body?: Uint8Array; type?: string; lang?: string; token?: string | null; ip?: string; headers?: Record<string, string>; env?: Env; host?: string } = {}) {
  const headers = new Headers({ origin: opts.host ?? ORIGIN, "content-type": opts.type ?? "audio/wav", "cf-connecting-ip": opts.ip ?? ip(), ...opts.headers });
  if (opts.token !== null) headers.set("x-turnstile-token", opts.token ?? "token-from-widget");
  const req = new Request(`${opts.host ?? ORIGIN}/api/try?lang=${opts.lang ?? "en"}`, { method: "POST", headers, body: opts.body ?? wav(2) });
  const ctx = createExecutionContext();
  const res = await app.fetch(req, opts.env ?? testEnv(), ctx);
  const text = await res.text();
  await waitOnExecutionContext(ctx);
  return { res, text, lines: res.headers.get("content-type")?.includes("ndjson") ? text.trim().split("\n").map((l) => JSON.parse(l)) : [], body: res.headers.get("content-type")?.includes("application/json") ? JSON.parse(text) : null };
}

const budgetRow = (day = new Date().toISOString().slice(0, 10)) =>
  env.DB.prepare("SELECT committed_usd, spent_usd, runs, refused FROM try_budget WHERE day = ?").bind(day).first<{ committed_usd: number; spent_usd: number; runs: number; refused: number }>();

// ---------------------------------------------------------------- tests

describe("WAV header", () => {
  it("reads duration from a 16 kHz mono 16-bit header", () => {
    expect(parseWav(wav(3).buffer as ArrayBuffer).seconds).toBeCloseTo(3, 5);
  });
});

describe("POST /api/try validation", () => {
  it("refuses a body that is not audio/wav", async () => {
    const { res, body } = await post({ type: "audio/mpeg" });
    expect(res.status).toBe(415);
    expect(body.error.code).toBe("unsupported_media_type");
    expect(res.headers.get("cache-control")).toBe("no-store");
  });

  it("refuses a clip longer than 60.5 s", async () => {
    const { res, body } = await post({ body: wav(61) });
    expect(res.status).toBe(413);
    expect(body.error.code).toBe("clip_too_long");
  });

  it("refuses the wrong sample rate, stereo, 8-bit and junk", async () => {
    for (const b of [wav(1, 44_100), wav(1, 16_000, 2), wav(1, 16_000, 1, 8), new TextEncoder().encode("not a wav file at all, just some text here ok")]) {
      const { res, body } = await post({ body: b });
      expect(res.status).toBe(400);
      expect(body.error.code).toBe("invalid_audio");
    }
  });

  it("refuses a language outside the list", async () => {
    const { res, body } = await post({ lang: "de" });
    expect(res.status).toBe(400);
    expect(body.error.code).toBe("invalid_language");
  });

  it("needs a Turnstile token", async () => {
    const { res, body } = await post({ token: null });
    expect(res.status).toBe(400);
    expect(body.error.code).toBe("turnstile_missing");
    expect(calls).toHaveLength(0);
  });

  it("refuses a failed Turnstile check before any provider is called", async () => {
    turnstileOk = false;
    const { res, body } = await post();
    expect(res.status).toBe(403);
    expect(body.error.code).toBe("turnstile_failed");
    expect(calls.map((c) => new URL(c.url).host)).toEqual(["challenges.cloudflare.com"]);
    expect(aiInputs).toHaveLength(0);
  });

  it("refuses a cross-origin request", async () => {
    const { res } = await post({ headers: { origin: "https://evil.example" } });
    expect(res.status).toBe(403);
  });

  it("answers 503 when no Turnstile secret is set", async () => {
    const { res, body } = await post({ env: testEnv({ TURNSTILE_SECRET: "" }) });
    expect(res.status).toBe(503);
    expect(body.error.code).toBe("try_unavailable");
  });
});

describe("POST /api/try run", () => {
  it("streams one NDJSON line per model with the normalised shape, accounts off", async () => {
    const { res, lines } = await post({ lang: "en" });
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toContain("application/x-ndjson");
    expect(res.headers.get("cache-control")).toBe("no-store");
    expect(lines[0]).toMatchObject({ type: "start", language: "en", models: TRY_MODELS.map(({ provider, label, model_id }) => ({ provider, label, model_id })) });
    expect(lines[0].seconds).toBeCloseTo(2, 5);
    const results = lines.filter((l) => l.type === "result");
    expect(results.map((r) => r.provider).sort()).toEqual(TRY_MODELS.map((m) => m.provider).sort());
    for (const r of results) {
      expect(Object.keys(r).sort()).toEqual(["cost_usd", "detected_language", "error", "label", "latency_ms", "model_id", "provider", "text", "type", "words"]);
      expect(r.error).toBeNull();
      expect(r.text.length).toBeGreaterThan(0);
      expect(r.words.length).toBeGreaterThan(0);
      for (const w of r.words) expect(Object.keys(w).sort()).toEqual(["end_ms", "start_ms", "text"]);
      expect(r.cost_usd).toBeGreaterThan(0);
    }
    expect(results.find((r) => r.provider === "elevenlabs-scribe").words.map((w: { text: string }) => w.text)).toEqual(["Hello", "world."]);
    expect(results.find((r) => r.provider === "deepgram").words[0]).toEqual({ text: "Hello", start_ms: 100, end_ms: 400 });
    expect(lines.at(-1)).toMatchObject({ type: "done" });
  });

  it("sends the opt-out flags and request shapes from the Python adapters", async () => {
    await post({ lang: "ja" });
    const dg = new URL(calls.find((c) => c.url.startsWith("https://api.deepgram.com/"))!.url);
    expect(dg.pathname).toBe("/v1/listen");
    expect(Object.fromEntries(dg.searchParams)).toEqual({ model: "nova-3", language: "ja", smart_format: "true", punctuate: "true", mip_opt_out: "true" });
    const groq = calls.find((c) => c.url.startsWith("https://api.groq.com/"))!.init!.body as FormData;
    expect(groq.get("model")).toBe("whisper-large-v3");
    expect(groq.get("language")).toBe("ja");
    expect(groq.get("response_format")).toBe("verbose_json");
    expect(groq.get("timestamp_granularities[]")).toBe("word");
    const el = calls.find((c) => c.url.startsWith("https://api.elevenlabs.io/"))!.init!.body as FormData;
    expect(el.get("model_id")).toBe("scribe_v2");
    expect(el.get("language_code")).toBe("ja");
    expect(el.get("tag_audio_events")).toBe("false");
    const nova = aiInputs.find((a) => a.model === "@cf/deepgram/nova-3")!.input;
    expect(nova).toMatchObject({ language: "ja", mip_opt_out: true, smart_format: true, punctuate: true });
    const whisper = aiInputs.find((a) => a.model === "@cf/openai/whisper-large-v3-turbo")!.input;
    expect(whisper).toMatchObject({ language: "ja", task: "transcribe" });
    expect(typeof whisper.audio).toBe("string");
  });

  it("auto language omits the language and asks for detection", async () => {
    const { lines } = await post({ lang: "auto" });
    const dg = new URL(calls.find((c) => c.url.startsWith("https://api.deepgram.com/"))!.url);
    expect(dg.searchParams.get("detect_language")).toBe("true");
    expect(dg.searchParams.has("language")).toBe(false);
    expect((calls.find((c) => c.url.startsWith("https://api.groq.com/"))!.init!.body as FormData).has("language")).toBe(false);
    expect(aiInputs.find((a) => a.model === "@cf/deepgram/nova-3")!.input).toMatchObject({ detect_language: true });
    const groq = lines.find((l) => l.provider === "groq-whisper");
    expect(groq.detected_language).toBe("en");
    expect(lines.find((l) => l.provider === "elevenlabs-scribe").detected_language).toBe("en");
  });

  it("one provider failing does not break the others", async () => {
    groqFails = true;
    const { res, lines } = await post();
    expect(res.status).toBe(200);
    const results = lines.filter((l) => l.type === "result");
    expect(results).toHaveLength(TRY_MODELS.length);
    const groq = results.find((r) => r.provider === "groq-whisper");
    expect(groq.error).toMatch(/HTTP 500/);
    expect(groq.text).toBe("");
    expect(groq.cost_usd).toBe(0);
    expect(results.filter((r) => r.error === null)).toHaveLength(TRY_MODELS.length - 1);
  });

  it("a provider without a key reports an error card and costs nothing", async () => {
    const { lines } = await post({ env: testEnv({ ELEVENLABS_API_KEY: "" }) });
    const el = lines.find((l) => l.provider === "elevenlabs-scribe");
    expect(el.error).toBe("Not configured on this server.");
    expect(el.cost_usd).toBe(0);
  });
});

describe("budget and limits", () => {
  it("records the actual cost against today's budget and keeps no text", async () => {
    const before = (await budgetRow()) ?? { committed_usd: 0, spent_usd: 0, runs: 0, refused: 0 };
    groqFails = true;
    const { lines } = await post({ body: wav(30) });
    const spent = lines.filter((l) => l.type === "result").reduce((s, r) => s + r.cost_usd, 0);
    expect(lines.at(-1).cost_usd).toBeCloseTo(spent, 9);
    const after = (await budgetRow())!;
    expect(after.runs - before.runs).toBe(1);
    expect(after.spent_usd - before.spent_usd).toBeCloseTo(spent, 9);
    // The reservation (every model at list price) is replaced by the actual spend.
    expect(after.committed_usd - before.committed_usd).toBeCloseTo(spent, 9);
    expect(spent).toBeLessThan(clipCost(30));
    const rows = await env.DB.prepare("SELECT * FROM try_provider_day WHERE provider = 'groq-whisper'").all();
    expect(rows.results[0]).toMatchObject({ provider: "groq-whisper" });
    expect((rows.results[0] as { errors: number }).errors).toBeGreaterThan(0);
    // Only counts and costs: no column can hold audio or text.
    for (const t of ["try_limits", "try_budget", "try_provider_day"]) {
      const cols = await env.DB.prepare(`SELECT name FROM pragma_table_info('${t}')`).all<{ name: string }>();
      expect(cols.results.map((c) => c.name).join(",")).not.toMatch(/text|audio_b|transcript|ip\b/);
    }
  });

  it("refuses with a friendly 429 when the day's budget is spent, before calling providers", async () => {
    const before = (await budgetRow())?.refused ?? 0;
    const { res, body } = await post({ env: testEnv({ TRY_DAILY_BUDGET_USD: "0.000001" }) });
    expect(res.status).toBe(429);
    expect(body.error.code).toBe("budget_spent");
    expect(body.error.message).toMatch(/00:00 UTC/);
    expect(Number(res.headers.get("retry-after"))).toBeGreaterThan(0);
    expect(calls.filter((c) => !c.url.includes("challenges.cloudflare.com"))).toHaveLength(0);
    expect(aiInputs).toHaveLength(0);
    expect((await budgetRow())!.refused).toBe(before + 1);
  });

  it("limits each visitor to 5 runs per 10 minutes", async () => {
    const me = ip();
    for (let i = 0; i < 5; i++) expect((await post({ ip: me })).res.status).toBe(200);
    const sixth = await post({ ip: me });
    expect(sixth.res.status).toBe(429);
    expect(sixth.body.error.code).toBe("rate_limited");
    expect(Number(sixth.res.headers.get("retry-after"))).toBeGreaterThan(0);
    // Someone else is unaffected.
    expect((await post()).res.status).toBe(200);
  });

  it("ignores a spoofed client IP header without the proxy key", async () => {
    const me = ip();
    for (let i = 0; i < 5; i++) await post({ ip: me, headers: { "x-clipbench-client-ip": `203.0.113.${i}` } });
    const spoof = await post({ ip: me, headers: { "x-clipbench-client-ip": "203.0.113.99", "x-clipbench-proxy-key": "wrong" } });
    expect(spoof.res.status).toBe(429);
  });

  it("uses the forwarded client IP with the right proxy key, and accepts the proxied origin", async () => {
    const proxy = ip();
    for (let i = 0; i < 7; i++) {
      const r = await post({
        ip: proxy,
        headers: { origin: "https://clipbench.abhishekbr.com", "x-forwarded-host": "clipbench.abhishekbr.com", "x-clipbench-client-ip": `192.0.2.${100 + i}`, "x-clipbench-proxy-key": "test-proxy-key" },
      });
      expect(r.res.status).toBe(200);
    }
    // Without the key the custom domain origin is refused.
    const r = await post({ headers: { origin: "https://clipbench.abhishekbr.com", "x-forwarded-host": "clipbench.abhishekbr.com" } });
    expect(r.res.status).toBe(403);
  });
});

describe("helpers", () => {
  it("fake providers only on localhost", () => {
    const e = testEnv({ TRY_FAKE_PROVIDERS: "1" });
    expect(fakeMode(e, "http://localhost:8787/api/try")).toBe("1");
    expect(fakeMode(e, "http://127.0.0.1:8787/api/try")).toBe("1");
    expect(fakeMode(e, "https://clipbench.abhishekbr.workers.dev/api/try")).toBeNull();
    expect(fakeMode(testEnv(), "http://localhost:8787/api/try")).toBeNull();
  });

  it("fake mode runs without network on localhost with the dummy token", async () => {
    const { res, lines } = await post({ env: testEnv({ TRY_FAKE_PROVIDERS: "1" }), token: "XXXX.DUMMY.TOKEN.XXXX" });
    expect(res.status).toBe(200);
    expect(calls).toHaveLength(0);
    expect(lines.filter((l) => l.type === "result" && l.error === null)).toHaveLength(TRY_MODELS.length);
  });

  it("normalises language codes", () => {
    expect(["japanese", "jpn", "ja-JP", "ja"].map(normalizeLanguage)).toEqual(["ja", "ja", "ja", "ja"]);
    expect(normalizeLanguage("")).toBeNull();
  });

  it("estimates the cost of a full minute across every model", () => {
    expect(clipCost(60)).toBeCloseTo(0.00051293 + 0.0052 + 0.00185 + 0.0043 + 0.003667, 8);
    // Groq bills at least 10 s.
    expect(clipCost(1)).toBeGreaterThan((0.00051293 + 0.0052 + 0.0043 + 0.003667) / 60 + 0.00185 / 60);
  });
});

describe("failureSentence", () => {
  it("names the Workers AI daily allowance without echoing the provider text", async () => {
    const { failureSentence } = await import("../src/try/providers");
    const s = failureSentence(new Error("AiError: AiError: you have used up your daily free allocation of 10,000 neurons, please upgrade"));
    expect(s).toContain("free daily allowance");
    expect(s).not.toContain("neurons");
    expect(failureSentence(new Error("HTTP 429 Too Many Requests"))).toContain("rate limited");
    expect(failureSentence(new Error("boom"))).toBe("The call failed.");
  });
});
