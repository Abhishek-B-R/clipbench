/**
 * /api/try guards backed by D1 (migrations/0003_try.sql): a per-visitor limit and a global daily budget.
 * Only counts and costs are stored; the visitor key is a SHA-256 of the IP, never the IP.
 */
import { sha256Hex } from "../crypto";

export const TRY_LIMITS = {
  /** Runs per visitor per 10 minute window. */
  burst: { limit: 5, windowS: 600 },
  /** Runs per visitor per UTC day. */
  daily: { limit: 20, windowS: 86_400 },
} as const;

export const DEFAULT_DAILY_BUDGET_USD = 1;

export const utcDay = (t = Date.now()) => new Date(t).toISOString().slice(0, 10);

/** Seconds until the next 00:00 UTC. */
export const secondsToUtcMidnight = (t = Date.now()) => Math.max(1, Math.ceil((86_400_000 - (t % 86_400_000)) / 1000));

export type LimitVerdict = { ok: true } | { ok: false; retryAfterS: number; which: "burst" | "daily" };

/** Count one run for this visitor in both windows; refuse when either is over its limit. */
export async function takeVisitorSlot(db: D1Database, ip: string, t = Date.now()): Promise<LimitVerdict> {
  const who = await sha256Hex(`clipbench-try:${ip}`);
  const nowS = Math.floor(t / 1000);
  const rows = await db.batch(
    (["burst", "daily"] as const).map((k) => {
      const { windowS } = TRY_LIMITS[k];
      return db
        .prepare(
          `INSERT INTO try_limits (key, window_start, count) VALUES (?, ?, 1)
           ON CONFLICT (key, window_start) DO UPDATE SET count = count + 1
           RETURNING count`,
        )
        .bind(`${k}:${who}`, Math.floor(nowS / windowS) * windowS);
    }),
  );
  for (const [i, k] of (["burst", "daily"] as const).entries()) {
    const count = (rows[i]?.results?.[0] as { count?: number } | undefined)?.count ?? 0;
    const { limit, windowS } = TRY_LIMITS[k];
    if (count > limit) return { ok: false, which: k, retryAfterS: windowS - (nowS % windowS) };
  }
  return { ok: true };
}

export function dailyBudget(env: Env): number {
  const v = Number.parseFloat(env.TRY_DAILY_BUDGET_USD ?? "");
  return Number.isFinite(v) && v >= 0 ? v : DEFAULT_DAILY_BUDGET_USD;
}

/** Reserve `usd` against today's budget. False (and a refusal counted) when it would pass the budget. */
export async function reserveBudget(db: D1Database, budget: number, usd: number, day = utcDay()): Promise<boolean> {
  const [, res] = await db.batch([
    db.prepare("INSERT OR IGNORE INTO try_budget (day) VALUES (?)").bind(day),
    db
      .prepare("UPDATE try_budget SET committed_usd = committed_usd + ?1 WHERE day = ?2 AND committed_usd + ?1 <= ?3 RETURNING committed_usd")
      .bind(usd, day, budget),
  ]);
  const ok = (res?.results?.length ?? 0) > 0;
  if (!ok) await db.prepare("UPDATE try_budget SET refused = refused + 1 WHERE day = ?").bind(day).run();
  return ok;
}

export type Settled = { provider: string; cost_usd: number; error: string | null };

/** Replace the reservation with the actual cost, and add per provider counts. */
export async function settleBudget(db: D1Database, day: string, reserved: number, seconds: number, results: Settled[]): Promise<void> {
  const spent = results.reduce((s, r) => s + r.cost_usd, 0);
  await db.batch([
    db.prepare("UPDATE try_budget SET committed_usd = MAX(committed_usd - ? + ?, 0), spent_usd = spent_usd + ?, runs = runs + 1 WHERE day = ?").bind(reserved, spent, spent, day),
    ...results.map((r) =>
      db
        .prepare(
          `INSERT INTO try_provider_day (day, provider, calls, errors, audio_seconds, cost_usd) VALUES (?1, ?2, 1, ?3, ?4, ?5)
           ON CONFLICT (day, provider) DO UPDATE SET calls = calls + 1, errors = errors + ?3, audio_seconds = audio_seconds + ?4, cost_usd = cost_usd + ?5`,
        )
        .bind(day, r.provider, r.error ? 1 : 0, seconds, r.cost_usd),
    ),
  ]);
}

/** Old visitor windows go; budget and provider rows (counts only) stay as the history. */
export async function pruneTryLimits(db: D1Database, t = Date.now()): Promise<void> {
  await db.prepare("DELETE FROM try_limits WHERE window_start < ?").bind(Math.floor(t / 1000) - 2 * 86_400).run();
}
