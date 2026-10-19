import { Hono, type MiddlewareHandler } from "hono";
import { cors } from "hono/cors";
import { sha256Hex } from "../crypto";
import { ApiError, now, parse, runId, type AppEnv } from "../http";
import { allow, LIMITS } from "../ratelimit";

/**
 * Public data API (/api/v1). Serves the JSON written by `clipbench export` from the static assets.
 * Anonymous callers get a low per-IP limit; `Authorization: Bearer <api key>` gets a higher per-key one.
 * No cookies are read here, so any origin may call it.
 */
export const data = new Hono<AppEnv>();

data.use("*", cors({ origin: "*", allowMethods: ["GET", "HEAD", "OPTIONS"], allowHeaders: ["Authorization"], maxAge: 86400 }));

const limit: MiddlewareHandler<AppEnv> = async (c, next) => {
  if (c.req.method === "OPTIONS") return next();
  const auth = c.req.header("authorization");
  let ok: boolean;
  let tier: "anon" | "key";
  if (auth) {
    const m = /^Bearer\s+(cb_[A-Za-z0-9_-]{20,100})$/.exec(auth.trim());
    if (!m?.[1]) throw new ApiError(401, "invalid_api_key", "Authorization must be 'Bearer <api key>'.");
    const hash = await sha256Hex(m[1]);
    const key = await c.env.DB.prepare("SELECT id FROM api_keys WHERE hash = ?").bind(hash).first<{ id: string }>();
    if (!key) throw new ApiError(401, "invalid_api_key", "Unknown or revoked API key.");
    c.executionCtx.waitUntil(
      c.env.DB.prepare("UPDATE api_keys SET last_used_at = ? WHERE id = ?").bind(now(), key.id).run(),
    );
    tier = "key";
    ok = await allow(c.env, "key", key.id);
  } else {
    tier = "anon";
    const ip = c.req.header("cf-connecting-ip") ?? "unknown";
    ok = await allow(c.env, "anon", ip);
  }
  c.header("X-RateLimit-Limit", String(LIMITS[tier]));
  if (!ok) {
    throw new ApiError(
      429,
      "rate_limited",
      tier === "anon"
        ? `Rate limit is ${LIMITS.anon} requests per minute without an API key. Create a key on your account page for ${LIMITS.key}.`
        : `Rate limit is ${LIMITS.key} requests per minute per API key.`,
      { "Retry-After": "60" },
    );
  }
  await next();
};

data.use("*", limit);

/** Fetch a JSON file from the assets. With SPA fallback a missing file comes back as index.html, so check the type. */
async function asset(c: Parameters<MiddlewareHandler<AppEnv>>[0], path: string): Promise<Response | null> {
  const res = await c.env.ASSETS.fetch(new URL(path, c.req.url));
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return null;
  return res;
}

function passThrough(c: Parameters<MiddlewareHandler<AppEnv>>[0], res: Response): Response {
  const headers = new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "public, max-age=300",
  });
  const etag = res.headers.get("etag");
  if (etag) {
    headers.set("ETag", etag);
    // The docs promise an ETag, so honour it: a client sending it back gets an empty 304.
    const sent = c.req.header("if-none-match");
    if (sent && sent.split(",").some((t) => t.trim().replace(/^W\//, "") === etag.replace(/^W\//, ""))) {
      headers.delete("Content-Type");
      return new Response(null, { status: 304, headers });
    }
  }
  return new Response(res.body, { status: 200, headers });
}

data.get("/runs", async (c) => {
  // Only real exports are served; there is no demo fallback.
  const res = await asset(c, "/data/index.json");
  if (!res) throw new ApiError(404, "not_found", "No runs published yet.");
  return passThrough(c, res);
});

data.get("/runs/:runId", async (c) => {
  const id = parse(runId, c.req.param("runId"));
  const res = await asset(c, `/data/runs/${id}.json`);
  if (!res) throw new ApiError(404, "not_found", `Run ${id} not found.`);
  return passThrough(c, res);
});
