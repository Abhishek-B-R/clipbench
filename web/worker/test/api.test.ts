import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { app } from "../src/index";
import { describe, expect, it } from "vitest";
import { sha256Hex } from "../src/crypto";
import { d1FixedWindow } from "../src/ratelimit";
import { anon, json, ORIGIN, signIn } from "./helpers";

describe("public endpoints", () => {
  it("reports configured sign-in methods", async () => {
    const res = await anon("/api/auth-providers");
    expect(res.status).toBe(200);
    // GitHub has test credentials, Google has none, email works locally without Resend.
    expect(await json(res)).toEqual({ github: true, google: false, email: true });
  });

  it("returns a null user without a session", async () => {
    const res = await anon("/api/me");
    expect(res.status).toBe(200);
    expect(await json(res)).toEqual({ user: null });
  });

  it("sets security headers and JSON 404s", async () => {
    const res = await anon("/api/nope");
    expect(res.status).toBe(404);
    expect(await json(res)).toEqual({ error: { code: "not_found", message: "No such API route." } });
    expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    expect(res.headers.get("x-frame-options")).toBe("DENY");
    expect(res.headers.get("content-security-policy")).toContain("default-src 'none'");
  });
});

describe("unauthenticated access is rejected", () => {
  const cases: [string, string, unknown?][] = [
    ["GET", "/api/watchlist"],
    ["PUT", "/api/watchlist/deepgram"],
    ["DELETE", "/api/watchlist/deepgram"],
    ["GET", "/api/alerts"],
    ["PUT", "/api/alerts", { email_on_new_run: true, email_on_rank_change: false }],
    ["GET", "/api/comparisons"],
    ["POST", "/api/comparisons", { name: "x", providers: ["a"], tier: "verified", metric: "cer" }],
    ["POST", "/api/requests", { kind: "provider", title: "Add Speechmatics" }],
    ["POST", "/api/requests/00000000-0000-0000-0000-000000000000/vote"],
    ["GET", "/api/keys"],
    ["POST", "/api/keys", { name: "ci" }],
  ];
  for (const [method, path, body] of cases) {
    it(`${method} ${path} -> 401`, async () => {
      const res = await anon(path, { method, json: body });
      expect(res.status).toBe(401);
      expect(await json(res)).toEqual({ error: { code: "unauthorized", message: "Sign in first." } });
    });
  }

  it("admin notify needs the admin token", async () => {
    const res = await anon("/api/admin/notify-new-run", {
      method: "POST",
      json: { run_id: "r1" },
      headers: { authorization: "Bearer wrong" },
    });
    expect(res.status).toBe(401);
  });
});

describe("signed in", () => {
  it("/api/me returns the user", async () => {
    const u = await signIn("me@example.com");
    const body = await json(await u.fetch("/api/me"));
    expect(body.user).toMatchObject({ email: "me@example.com", image: null });
    expect(Object.keys(body.user).sort()).toEqual(["email", "id", "image", "name"]);
  });

  it("watchlist CRUD", async () => {
    const u = await signIn("watch@example.com");
    expect(await json(await u.fetch("/api/watchlist"))).toMatchObject({ providers: [] });

    const put = await u.fetch("/api/watchlist/deepgram", { method: "PUT" });
    expect(put.status).toBe(200);
    expect(await json(put)).toEqual({ provider_id: "deepgram", watching: true, added: true });
    // Idempotent.
    expect(await json(await u.fetch("/api/watchlist/deepgram", { method: "PUT" }))).toMatchObject({ added: false });
    await u.fetch("/api/watchlist/groq-whisper", { method: "PUT" });
    expect((await json(await u.fetch("/api/watchlist"))).providers.sort()).toEqual(["deepgram", "groq-whisper"]);

    // Another user sees only their own list.
    const other = await signIn("other@example.com");
    expect((await json(await other.fetch("/api/watchlist"))).providers).toEqual([]);

    const del = await u.fetch("/api/watchlist/deepgram", { method: "DELETE" });
    expect(await json(del)).toEqual({ provider_id: "deepgram", watching: false, removed: true });
    expect((await json(await u.fetch("/api/watchlist"))).providers).toEqual(["groq-whisper"]);
  });

  it("alerts default off and persist", async () => {
    const u = await signIn("alerts@example.com");
    expect(await json(await u.fetch("/api/alerts"))).toEqual({ email_on_new_run: false, email_on_rank_change: false });
    const put = await u.fetch("/api/alerts", { method: "PUT", json: { email_on_new_run: true, email_on_rank_change: false } });
    expect(put.status).toBe(200);
    expect(await json(await u.fetch("/api/alerts"))).toEqual({ email_on_new_run: true, email_on_rank_change: false });
  });

  it("refuses cross-origin and origin-less mutations", async () => {
    const u = await signIn("csrf@example.com");
    const evil = await u.fetch("/api/watchlist/deepgram", { method: "PUT", headers: { origin: "https://evil.example" } });
    expect(evil.status).toBe(403);
    expect((await json(evil)).error.code).toBe("forbidden_origin");
    const same = await u.fetch("/api/watchlist/deepgram", { method: "PUT", headers: { "sec-fetch-site": "same-origin", origin: ORIGIN } });
    expect(same.status).toBe(200);
  });
});

describe("validation errors", () => {
  it("rejects bad bodies and params with a JSON error", async () => {
    const u = await signIn("valid@example.com");
    const bad = await u.fetch("/api/comparisons", { method: "POST", json: { name: "", providers: [], tier: "gold", metric: "cer" } });
    expect(bad.status).toBe(400);
    const err = await json(bad);
    expect(err.error.code).toBe("invalid_request");
    expect(typeof err.error.message).toBe("string");

    const extra = await u.fetch("/api/alerts", { method: "PUT", json: { email_on_new_run: true, email_on_rank_change: false, x: 1 } });
    expect(extra.status).toBe(400);

    const notJson = await u.fetch("/api/keys", { method: "POST", body: "{", headers: { "content-type": "application/json" } });
    expect(notJson.status).toBe(400);
    expect((await json(notJson)).error.code).toBe("invalid_json");

    const wrongType = await u.fetch("/api/keys", { method: "POST", body: "name=x", headers: { "content-type": "text/plain" } });
    expect(wrongType.status).toBe(415);

    const badId = await u.fetch("/api/watchlist/Not%20An%20Id!", { method: "PUT" });
    expect(badId.status).toBe(400);

    const badKind = await u.fetch("/api/requests", { method: "POST", json: { kind: "model", title: "Add X" } });
    expect(badKind.status).toBe(400);
    expect((await json(badKind)).error.message).toMatch(/^kind: /);
  });
});

describe("requests board", () => {
  it("one vote per user, toggling", async () => {
    const a = await signIn("voter-a@example.com");
    const b = await signIn("voter-b@example.com");
    const created = await a.fetch("/api/requests", {
      method: "POST",
      json: { kind: "provider", title: "Add Speechmatics", body: "Good on noisy audio." },
    });
    expect(created.status).toBe(201);
    const { request } = await json(created);
    expect(request).toMatchObject({ votes: 1, voted: true, status: "open", kind: "provider" });

    // The author already voted: toggling removes it.
    expect(await json(await a.fetch(`/api/requests/${request.id}/vote`, { method: "POST" }))).toEqual({ id: request.id, voted: false, votes: 0 });
    expect(await json(await a.fetch(`/api/requests/${request.id}/vote`, { method: "POST" }))).toEqual({ id: request.id, voted: true, votes: 1 });
    expect(await json(await b.fetch(`/api/requests/${request.id}/vote`, { method: "POST" }))).toEqual({ id: request.id, voted: true, votes: 2 });
    expect(await json(await b.fetch(`/api/requests/${request.id}/vote`, { method: "POST" }))).toEqual({ id: request.id, voted: false, votes: 1 });
    expect(await json(await b.fetch(`/api/requests/${request.id}/vote`, { method: "POST" }))).toMatchObject({ votes: 2 });

    // A direct second insert is impossible.
    await expect(
      env.DB.prepare("INSERT INTO votes (request_id, user_id, created_at) SELECT request_id, user_id, 0 FROM votes WHERE request_id = ? LIMIT 1")
        .bind(request.id)
        .run(),
    ).rejects.toThrow(/UNIQUE|PRIMARY KEY/);

    // Public list, sorted by votes, with the viewer's vote state.
    await a.fetch("/api/requests", { method: "POST", json: { kind: "language", title: "Add Portuguese" } });
    const list = (await json(await anon("/api/requests"))).requests;
    const top = list.find((r: { id: string }) => r.id === request.id);
    expect(top).toMatchObject({ votes: 2, voted: false, mine: false });
    expect(list[0].votes).toBeGreaterThanOrEqual(list[list.length - 1].votes);
    const mine = (await json(await a.fetch("/api/requests?kind=provider"))).requests;
    expect(mine.every((r: { kind: string }) => r.kind === "provider")).toBe(true);
    expect(mine.find((r: { id: string }) => r.id === request.id)).toMatchObject({ voted: true, mine: true });

    expect((await a.fetch("/api/requests/00000000-0000-0000-0000-000000000000/vote", { method: "POST" })).status).toBe(404);
  });
});

describe("API keys", () => {
  it("shows the secret once and stores only its hash", async () => {
    const u = await signIn("keys@example.com");
    const res = await u.fetch("/api/keys", { method: "POST", json: { name: "ci" } });
    expect(res.status).toBe(201);
    const { key, secret } = await json(res);
    expect(secret).toMatch(/^cb_[A-Za-z0-9_-]{43}$/);
    expect(secret.startsWith(key.prefix)).toBe(true);
    expect(key.prefix.length).toBe(11);

    const listed = await json(await u.fetch("/api/keys"));
    expect(listed.keys).toHaveLength(1);
    expect(JSON.stringify(listed)).not.toContain(secret);
    expect(listed.keys[0]).toEqual({ id: key.id, name: "ci", prefix: key.prefix, created_at: key.created_at, last_used_at: null });

    const row = await env.DB.prepare("SELECT * FROM api_keys WHERE id = ?").bind(key.id).first<Record<string, unknown>>();
    expect(row?.hash).toBe(await sha256Hex(secret));
    expect(Object.values(row ?? {})).not.toContain(secret);

    // The key works on the data API, then stops working once deleted.
    expect((await anon("/api/v1/runs", { headers: { authorization: `Bearer ${secret}` } })).status).toBe(200);
    expect((await u.fetch(`/api/keys/${key.id}`, { method: "DELETE" })).status).toBe(200);
    const revoked = await anon("/api/v1/runs", { headers: { authorization: `Bearer ${secret}` } });
    expect(revoked.status).toBe(401);
    expect((await json(revoked)).error.code).toBe("invalid_api_key");
  });
});

describe("public data API", () => {
  it("serves the exported JSON from assets", async () => {
    const index = await anon("/api/v1/runs");
    expect(index.status).toBe(200);
    expect(index.headers.get("access-control-allow-origin")).toBe("*");
    expect((await json(index)).latest).toBe("r1");
    const run = await json(await anon("/api/v1/runs/r1"));
    expect(run.providers.map((p: { id: string }) => p.id)).toEqual(["model-a", "model-b", "model-c"]);
    // Missing run: a JSON 404, not the SPA index.html fallback.
    const missing = await anon("/api/v1/runs/nope");
    expect(missing.status).toBe(404);
    expect((await json(missing)).error.code).toBe("not_found");
    expect((await anon("/api/v1/runs/..%2Fsecret")).status).toBe(400);
    // Conditional GET with the ETag the docs promise.
    const tag = index.headers.get("etag");
    expect(tag).toBeTruthy();
    const again = await anon("/api/v1/runs", { headers: { "if-none-match": tag! } });
    expect(again.status).toBe(304);
    expect(again.headers.get("etag")).toBe(tag);
  });

  it("rate limit kicks in for anonymous callers, not for an API key", async () => {
    const ip = "203.0.113.7";
    const statuses: number[] = [];
    for (let i = 0; i < 32; i++) {
      statuses.push((await anon("/api/v1/runs", { headers: { "cf-connecting-ip": ip } })).status);
    }
    expect(statuses.slice(0, 30).every((s) => s === 200)).toBe(true);
    expect(statuses.at(-1)).toBe(429);
    const limited = await anon("/api/v1/runs", { headers: { "cf-connecting-ip": ip } });
    expect(limited.headers.get("retry-after")).toBe("60");
    expect((await json(limited)).error.code).toBe("rate_limited");

    const u = await signIn("ratelimit@example.com");
    const { secret } = await json(await u.fetch("/api/keys", { method: "POST", json: { name: "bulk" } }));
    const keyed = await anon("/api/v1/runs", { headers: { "cf-connecting-ip": ip, authorization: `Bearer ${secret}` } });
    expect(keyed.status).toBe(200);
  });

  it("D1 fixed-window fallback limiter counts per key", async () => {
    const results: boolean[] = [];
    for (let i = 0; i < 4; i++) results.push(await d1FixedWindow(env.DB, "test:key", 3));
    expect(results).toEqual([true, true, true, false]);
    expect(await d1FixedWindow(env.DB, "test:other", 3)).toBe(true);
  });
});

describe("saved comparisons", () => {
  it("public share read only when public", async () => {
    const u = await signIn("compare@example.com");
    const body = { name: "Cheap and good", providers: ["deepgram", "groq-whisper"], tier: "verified", metric: "cer", tags: ["background_music"] };
    const priv = await json(await u.fetch("/api/comparisons", { method: "POST", json: body }));
    const pub = await json(await u.fetch("/api/comparisons", { method: "POST", json: { ...body, public: true } }));
    expect(priv.comparison).toMatchObject({ ...body, public: false });

    expect((await anon(`/api/comparisons/${priv.comparison.id}/public`)).status).toBe(404);
    const shared = await anon(`/api/comparisons/${pub.comparison.id}/public`);
    expect(shared.status).toBe(200);
    const read = await json(shared);
    expect(read.comparison).toMatchObject({ ...body, public: true, id: pub.comparison.id });
    expect(JSON.stringify(read)).not.toContain("user_id");

    expect((await json(await u.fetch("/api/comparisons"))).comparisons).toHaveLength(2);
    const other = await signIn("compare-other@example.com");
    expect((await other.fetch(`/api/comparisons/${pub.comparison.id}`, { method: "DELETE" })).status).toBe(404);
    expect((await u.fetch(`/api/comparisons/${pub.comparison.id}`, { method: "DELETE" })).status).toBe(200);
    expect((await anon(`/api/comparisons/${pub.comparison.id}/public`)).status).toBe(404);
  });
});

describe("admin notify", () => {
  it("emails opted-in watchers once (logged, Resend unset)", async () => {
    const u = await signIn("notify@example.com");
    await u.fetch("/api/alerts", { method: "PUT", json: { email_on_new_run: true, email_on_rank_change: false } });
    await u.fetch("/api/watchlist/model-b", { method: "PUT" });
    const call = () =>
      anon("/api/admin/notify-new-run", { method: "POST", json: { run_id: "r1" }, headers: { authorization: "Bearer test-admin-token" } });
    const first = await json(await call());
    expect(first).toMatchObject({ run_id: "r1", sent: 0, failed: 0 });
    expect(first.recipients).toBeGreaterThanOrEqual(1);
    expect(first.logged).toBe(first.recipients);
    expect(await json(await call())).toMatchObject({ already_sent: true, logged: 0 });
    const missing = await anon("/api/admin/notify-new-run", {
      method: "POST",
      json: { run_id: "nope" },
      headers: { authorization: "Bearer test-admin-token" },
    });
    expect(missing.status).toBe(404);
  });

  it("never emails ranks for a run whose language is not published", async () => {
    const res = await anon("/api/admin/notify-new-run", {
      method: "POST",
      json: { run_id: "r2-unpublished" },
      headers: { authorization: "Bearer test-admin-token" },
    });
    expect(res.status).toBe(200);
    expect(await json(res)).toMatchObject({ unpublished: true, recipients: 0, sent: 0, logged: 0 });
  });
});

describe("accounts off (ACCOUNTS unset, no auth secrets)", () => {
  // The production default: only the public data API answers, and it must not need Better Auth or its secrets.
  const offEnv = { ...env, ACCOUNTS: "false", BETTER_AUTH_SECRET: undefined, BETTER_AUTH_URL: undefined } as unknown as Env;
  const call = async (path: string, init: RequestInit = {}) => {
    const ctx = createExecutionContext();
    const res = await app.request(`${ORIGIN}${path}`, { ...init, headers: { "cf-connecting-ip": "198.51.100.9", ...(init.headers ?? {}) } }, offEnv, ctx);
    await waitOnExecutionContext(ctx);
    return res;
  };

  it("serves /api/v1 with CORS and JSON 404s", async () => {
    const index = await call("/api/v1/runs");
    expect(index.status).toBe(200);
    expect(index.headers.get("access-control-allow-origin")).toBe("*");
    expect(index.headers.get("cache-control")).toBe("public, max-age=300");
    expect((await json(index)).latest).toBe("r1");
    expect((await call("/api/v1/runs/r1")).status).toBe(200);
    const missing = await call("/api/v1/runs/nope");
    expect(missing.status).toBe(404);
    expect((await json(missing)).error.code).toBe("not_found");
    const pre = await call("/api/v1/runs", { method: "OPTIONS", headers: { origin: "https://example.org", "access-control-request-method": "GET" } });
    expect(pre.status).toBeLessThan(300);
    expect(pre.headers.get("access-control-allow-origin")).toBe("*");
  });

  it("every account route is a JSON 404", async () => {
    for (const [method, path] of [
      ["GET", "/api/me"],
      ["GET", "/api/auth/get-session"],
      ["GET", "/api/auth-providers"],
      ["GET", "/api/requests"],
      ["GET", "/api/comparisons/x/public"],
      ["POST", "/api/admin/notify-new-run"],
    ] as const) {
      const res = await call(path, { method, headers: { origin: ORIGIN } });
      expect(res.status, `${method} ${path}`).toBe(404);
      expect((await json(res)).error.code).toBe("not_found");
      expect(res.headers.get("x-content-type-options")).toBe("nosniff");
    }
  });
});
