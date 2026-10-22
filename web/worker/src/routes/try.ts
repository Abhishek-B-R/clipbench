import { Hono } from "hono";
import { trustedOrigins } from "../auth";
import { ApiError, clientIp, viaProxy, type AppEnv } from "../http";
import { fakeRun } from "../try/fake";
import { clipCost, TRY_LANGUAGES, TRY_MODELS, type TryLanguage } from "../try/models";
import { dailyBudget, reserveBudget, secondsToUtcMidnight, settleBudget, takeVisitorSlot, utcDay } from "../try/limits";
import { RUNNERS, runModel, type TryResult } from "../try/providers";
import { TURNSTILE_DUMMY_TOKEN, verifyTurnstile } from "../try/turnstile";
import { parseWav, TRY_MAX_BYTES, TRY_MAX_SECONDS, TRY_MIN_SECONDS, WavError } from "../try/wav";

/**
 * POST /api/try: the /try playground. Works with ACCOUNTS off.
 *
 * Request: the body is a 16 kHz mono 16-bit WAV (`content-type: audio/wav`, at most TRY_MAX_BYTES and
 * TRY_MAX_SECONDS), `?lang=auto|en|ja|ko|es|hi`, and the Turnstile token in `x-turnstile-token`.
 *
 * Response: errors before any provider runs are the usual JSON `{error: {code, message}}` with a 4xx. Otherwise
 * 200 with NDJSON (`application/x-ndjson`), one JSON object per line, flushed as each provider finishes:
 *   {"type":"start","seconds":12.3,"language":"auto","estimated_usd":0.0029,"models":[{provider,label,model_id}]}
 *   {"type":"result", provider, label, model_id, text, words:[{text,start_ms,end_ms}], latency_ms, cost_usd, detected_language, error}
 *   ... one per model, in finishing order ...
 *   {"type":"done","cost_usd":0.0028}
 * One request carries all five models so one Turnstile token, one visitor slot and one budget reservation cover
 * the run, and each card still fills in as soon as its provider answers.
 *
 * Privacy: the audio is held in memory for this request only; transcripts are streamed to the visitor and
 * never logged or stored. D1 gets counts and costs (try_limits, try_budget, try_provider_day).
 */
export const tryRoute = new Hono<AppEnv>();

const WAV_TYPES = new Set(["audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"]);
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]"]);

/** TRY_FAKE_PROVIDERS only counts on a localhost request, so a stray production var cannot switch real calls off. */
export function fakeMode(env: Env, url: string): string | null {
  const v = (env.TRY_FAKE_PROVIDERS ?? "").trim();
  if (!v || v === "0" || v === "false") return null;
  return LOCAL_HOSTS.has(new URL(url).hostname) ? v : null;
}

async function checkOrigin(req: Request, env: Env): Promise<void> {
  const origin = req.headers.get("origin");
  if (!origin) {
    if (req.headers.get("sec-fetch-site") === "same-origin") return;
    throw new ApiError(403, "forbidden_origin", "Missing Origin header.");
  }
  const allowed = new Set([new URL(req.url).origin, ...trustedOrigins(env)]);
  // Behind web/proxy/ the visitor's origin is the custom domain the proxy names in x-forwarded-host.
  const host = req.headers.get("x-forwarded-host");
  if (host && (await viaProxy(req, env))) allowed.add(`https://${host}`);
  if (!allowed.has(origin)) throw new ApiError(403, "forbidden_origin", "Cross-origin request refused.");
}

tryRoute.post("/", async (c) => {
  c.header("Cache-Control", "no-store");
  const req = c.req.raw;
  await checkOrigin(req, c.env);

  const type = (c.req.header("content-type") ?? "").split(";")[0]!.trim().toLowerCase();
  if (!WAV_TYPES.has(type)) throw new ApiError(415, "unsupported_media_type", "Send the clip as audio/wav.");

  const lang = (c.req.query("lang") ?? "auto") as TryLanguage;
  if (!TRY_LANGUAGES.includes(lang)) throw new ApiError(400, "invalid_language", `Language must be one of ${TRY_LANGUAGES.join(", ")}.`);

  const declared = Number(c.req.header("content-length") ?? "0");
  if (declared > TRY_MAX_BYTES) throw new ApiError(413, "payload_too_large", "The clip is too large. Trim it to a minute or less.");
  const buf = await req.arrayBuffer();
  if (buf.byteLength > TRY_MAX_BYTES) throw new ApiError(413, "payload_too_large", "The clip is too large. Trim it to a minute or less.");

  let seconds: number;
  try {
    seconds = parseWav(buf).seconds;
  } catch (e) {
    throw new ApiError(400, "invalid_audio", e instanceof WavError ? e.message : "The body is not a WAV file.");
  }
  if (seconds > TRY_MAX_SECONDS) throw new ApiError(413, "clip_too_long", "The clip is longer than a minute. Trim it and try again.");
  if (seconds < TRY_MIN_SECONDS) throw new ApiError(400, "clip_too_short", "The clip is too short to transcribe.");

  const token = (c.req.header("x-turnstile-token") ?? "").trim();
  if (!token || token.length > 4096) throw new ApiError(400, "turnstile_missing", "Complete the verification check first.");

  const fake = fakeMode(c.env, req.url);
  const ip = await clientIp(req, c.env);
  if (!(fake && token === TURNSTILE_DUMMY_TOKEN)) {
    if (!c.env.TURNSTILE_SECRET) throw new ApiError(503, "try_unavailable", "The playground is not set up on this server yet.");
    const verdict = await verifyTurnstile(c.env.TURNSTILE_SECRET, token, ip);
    if (!verdict.ok) throw new ApiError(403, "turnstile_failed", "The verification check failed or expired. Run it again.");
  }

  const slot = await takeVisitorSlot(c.env.DB, ip);
  if (!slot.ok) {
    throw new ApiError(
      429,
      "rate_limited",
      slot.which === "burst" ? "You have run a few clips in a row. Wait a few minutes and try again." : "You have reached today's limit for this playground. Come back tomorrow.",
      { "Retry-After": String(slot.retryAfterS) },
    );
  }

  const day = utcDay();
  const estimate = clipCost(seconds);
  if (!(await reserveBudget(c.env.DB, dailyBudget(c.env), estimate, day))) {
    throw new ApiError(429, "budget_spent", "Today's playground budget is spent. It resets at 00:00 UTC; the benchmark results are always open.", {
      "Retry-After": String(secondsToUtcMidnight()),
    });
  }

  const audio = new Uint8Array(buf);
  const input = { audio, seconds, language: lang };
  const enc = new TextEncoder();
  const { readable, writable } = new TransformStream<Uint8Array, Uint8Array>();
  const writer = writable.getWriter();
  const send = (o: unknown) => writer.write(enc.encode(`${JSON.stringify(o)}\n`)).catch(() => undefined);

  const work = (async () => {
    await send({
      type: "start",
      seconds,
      language: lang,
      estimated_usd: estimate,
      models: TRY_MODELS.map(({ provider, label, model_id }) => ({ provider, label, model_id })),
    });
    const results = await Promise.all(
      TRY_MODELS.map(async (m) => {
        const run = fake ? fakeRun(m.provider, fake) : RUNNERS[m.provider]!;
        const { billed: _billed, ...r } = await runModel(m, run, input, c.env);
        await send({ type: "result", ...r } satisfies { type: string } & TryResult);
        return r;
      }),
    );
    const spent = results.reduce((s, r) => s + r.cost_usd, 0);
    await send({ type: "done", cost_usd: spent });
    await writer.close().catch(() => undefined);
    await settleBudget(c.env.DB, day, estimate, seconds, results).catch((e: unknown) =>
      console.error(JSON.stringify({ event: "try_settle_failed", error: String(e) })),
    );
    console.log(JSON.stringify({ event: "try_run", seconds: Math.round(seconds * 10) / 10, language: lang, fake: Boolean(fake), errors: results.filter((r) => r.error).length, cost_usd: spent }));
  })();
  c.executionCtx.waitUntil(work);

  return new Response(readable, {
    status: 200,
    headers: {
      "Content-Type": "application/x-ndjson; charset=utf-8",
      "Cache-Control": "no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
});
