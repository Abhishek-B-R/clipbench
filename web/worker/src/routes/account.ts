import { Hono } from "hono";
import { z } from "zod";
import { ApiError, body, bool, jsonArray, now, notFound, parse, providerId, requireUser, withSession, type AppEnv } from "../http";

/** Signed-in features: watchlist, alerts, saved comparisons. Mounted at /api. */
export const account = new Hono<AppEnv>();

// ---------------------------------------------------------------- watchlist

account.get("/watchlist", withSession, async (c) => {
  const user = requireUser(c);
  const { results } = await c.env.DB.prepare(
    "SELECT provider_id, created_at FROM watchlist WHERE user_id = ? ORDER BY created_at, provider_id",
  )
    .bind(user.id)
    .all<{ provider_id: string; created_at: number }>();
  return c.json({ providers: results.map((r) => r.provider_id), items: results });
});

account.put("/watchlist/:providerId", withSession, async (c) => {
  const user = requireUser(c);
  const pid = parse(providerId, c.req.param("providerId"));
  const count = await c.env.DB.prepare("SELECT COUNT(*) AS n FROM watchlist WHERE user_id = ?")
    .bind(user.id)
    .first<number>("n");
  if ((count ?? 0) >= 100) throw new ApiError(409, "limit_reached", "Watchlist is full (100 providers).");
  const res = await c.env.DB.prepare(
    "INSERT INTO watchlist (user_id, provider_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
  )
    .bind(user.id, pid, now())
    .run();
  return c.json({ provider_id: pid, watching: true, added: res.meta.changes > 0 });
});

account.delete("/watchlist/:providerId", withSession, async (c) => {
  const user = requireUser(c);
  const pid = parse(providerId, c.req.param("providerId"));
  const res = await c.env.DB.prepare("DELETE FROM watchlist WHERE user_id = ? AND provider_id = ?").bind(user.id, pid).run();
  return c.json({ provider_id: pid, watching: false, removed: res.meta.changes > 0 });
});

// ---------------------------------------------------------------- alerts

const Alerts = z.strictObject({ email_on_new_run: z.boolean(), email_on_rank_change: z.boolean() });

account.get("/alerts", withSession, async (c) => {
  const user = requireUser(c);
  const row = await c.env.DB.prepare("SELECT email_on_new_run, email_on_rank_change FROM alert_prefs WHERE user_id = ?")
    .bind(user.id)
    .first<{ email_on_new_run: number; email_on_rank_change: number }>();
  return c.json({ email_on_new_run: bool(row?.email_on_new_run), email_on_rank_change: bool(row?.email_on_rank_change) });
});

account.put("/alerts", withSession, async (c) => {
  const user = requireUser(c);
  const prefs = await body(c, Alerts);
  await c.env.DB.prepare(
    `INSERT INTO alert_prefs (user_id, email_on_new_run, email_on_rank_change, updated_at) VALUES (?, ?, ?, ?)
     ON CONFLICT (user_id) DO UPDATE SET email_on_new_run = excluded.email_on_new_run,
       email_on_rank_change = excluded.email_on_rank_change, updated_at = excluded.updated_at`,
  )
    .bind(user.id, prefs.email_on_new_run ? 1 : 0, prefs.email_on_rank_change ? 1 : 0, now())
    .run();
  return c.json(prefs);
});

// ---------------------------------------------------------------- saved comparisons

const tag = z.string().regex(/^[a-z0-9_:-]{1,48}$/, "must be a clip tag like 'background_music'");
const Comparison = z.strictObject({
  name: z.string().trim().min(1).max(80),
  providers: z.array(providerId).min(1).max(12),
  tier: z.enum(["verified", "creator"]),
  metric: z.string().regex(/^[a-z0-9_]{1,32}$/, "must be a metric name like 'cer'"),
  tags: z.array(tag).max(20).default([]),
  public: z.boolean().default(false),
});

type ComparisonRow = {
  id: string;
  user_id: string;
  name: string;
  providers: string;
  tier: string;
  metric: string;
  tags: string;
  public: number;
  created_at: number;
};

function comparisonOut(r: ComparisonRow) {
  return {
    id: r.id,
    name: r.name,
    providers: jsonArray(r.providers),
    tier: r.tier,
    metric: r.metric,
    tags: jsonArray(r.tags),
    public: bool(r.public),
    created_at: r.created_at,
  };
}

account.get("/comparisons", withSession, async (c) => {
  const user = requireUser(c);
  const { results } = await c.env.DB.prepare("SELECT * FROM comparisons WHERE user_id = ? ORDER BY created_at DESC")
    .bind(user.id)
    .all<ComparisonRow>();
  return c.json({ comparisons: results.map(comparisonOut) });
});

account.post("/comparisons", withSession, async (c) => {
  const user = requireUser(c);
  const input = await body(c, Comparison);
  const row: ComparisonRow = {
    id: crypto.randomUUID(),
    user_id: user.id,
    name: input.name,
    providers: JSON.stringify([...new Set(input.providers)]),
    tier: input.tier,
    metric: input.metric,
    tags: JSON.stringify([...new Set(input.tags)]),
    public: input.public ? 1 : 0,
    created_at: now(),
  };
  await c.env.DB.prepare(
    "INSERT INTO comparisons (id, user_id, name, providers, tier, metric, tags, public, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
  )
    .bind(row.id, row.user_id, row.name, row.providers, row.tier, row.metric, row.tags, row.public, row.created_at)
    .run();
  return c.json({ comparison: comparisonOut(row) }, 201);
});

account.delete("/comparisons/:id", withSession, async (c) => {
  const user = requireUser(c);
  const id = c.req.param("id");
  const res = await c.env.DB.prepare("DELETE FROM comparisons WHERE id = ? AND user_id = ?").bind(id, user.id).run();
  if (res.meta.changes === 0) throw notFound("Comparison");
  return c.json({ deleted: true });
});

// Public share link: no session, only comparisons marked public. Owner id is never returned.
account.get("/comparisons/:id/public", async (c) => {
  const row = await c.env.DB.prepare("SELECT * FROM comparisons WHERE id = ? AND public = 1")
    .bind(c.req.param("id"))
    .first<ComparisonRow>();
  if (!row) throw notFound("Comparison");
  c.header("Cache-Control", "public, max-age=60");
  return c.json({ comparison: comparisonOut(row) });
});
