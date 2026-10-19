/**
 * Rate limiting for the public data API.
 *
 * Primary: the Workers Rate Limiting binding (ANON_LIMITER, KEY_LIMITER in wrangler.jsonc). It is per
 * Cloudflare location and eventually consistent, which is fine for abuse protection.
 * Fallback: a D1 fixed-window counter (table rate_limits), used when a binding is not configured.
 */

export const LIMITS = { anon: 30, key: 600 } as const; // per 60 s; keep in sync with wrangler.jsonc

export type Tier = keyof typeof LIMITS;

export async function d1FixedWindow(db: D1Database, key: string, limit: number, windowS = 60): Promise<boolean> {
  const windowStart = Math.floor(Date.now() / 1000 / windowS) * windowS;
  const row = await db
    .prepare(
      `INSERT INTO rate_limits (key, window_start, count) VALUES (?, ?, 1)
       ON CONFLICT (key, window_start) DO UPDATE SET count = count + 1
       RETURNING count`,
    )
    .bind(key, windowStart)
    .first<number>("count");
  return (row ?? 0) <= limit;
}

/** Old windows are deleted by the scheduled handler. */
export async function pruneRateLimits(db: D1Database): Promise<void> {
  await db.prepare("DELETE FROM rate_limits WHERE window_start < ?").bind(Math.floor(Date.now() / 1000) - 3600).run();
}

export async function allow(env: Env, tier: Tier, key: string): Promise<boolean> {
  const binding = tier === "anon" ? env.ANON_LIMITER : env.KEY_LIMITER;
  if (binding) {
    const { success } = await binding.limit({ key: `${tier}:${key}` });
    return success;
  }
  return d1FixedWindow(env.DB, `${tier}:${key}`, LIMITS[tier]);
}
