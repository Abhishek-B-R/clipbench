import { Hono } from "hono";
import { z } from "zod";
import { newApiKeySecret, sha256Hex, SHOWN_PREFIX_LEN } from "../crypto";
import { ApiError, body, now, notFound, requireUser, withSession, type AppEnv } from "../http";

/** API keys for the public data API. The secret is returned once; only its SHA-256 is stored. */
export const keys = new Hono<AppEnv>();

const MAX_KEYS = 10;
const NewKey = z.strictObject({ name: z.string().trim().min(1).max(60) });

type KeyRow = { id: string; name: string; prefix: string; created_at: number; last_used_at: number | null };

keys.get("/", withSession, async (c) => {
  const user = requireUser(c);
  const { results } = await c.env.DB.prepare(
    "SELECT id, name, prefix, created_at, last_used_at FROM api_keys WHERE user_id = ? ORDER BY created_at DESC",
  )
    .bind(user.id)
    .all<KeyRow>();
  return c.json({ keys: results });
});

keys.post("/", withSession, async (c) => {
  const user = requireUser(c);
  const { name } = await body(c, NewKey);
  const n = await c.env.DB.prepare("SELECT COUNT(*) AS n FROM api_keys WHERE user_id = ?").bind(user.id).first<number>("n");
  if ((n ?? 0) >= MAX_KEYS) throw new ApiError(409, "limit_reached", `At most ${MAX_KEYS} API keys per account.`);
  const secret = newApiKeySecret();
  const key: KeyRow = {
    id: crypto.randomUUID(),
    name,
    prefix: secret.slice(0, SHOWN_PREFIX_LEN),
    created_at: now(),
    last_used_at: null,
  };
  await c.env.DB.prepare("INSERT INTO api_keys (id, user_id, name, prefix, hash, created_at) VALUES (?, ?, ?, ?, ?, ?)")
    .bind(key.id, user.id, key.name, key.prefix, await sha256Hex(secret), key.created_at)
    .run();
  c.header("Cache-Control", "no-store");
  return c.json({ key, secret, note: "Copy the secret now. It is not shown again." }, 201);
});

keys.delete("/:id", withSession, async (c) => {
  const user = requireUser(c);
  const res = await c.env.DB.prepare("DELETE FROM api_keys WHERE id = ? AND user_id = ?").bind(c.req.param("id"), user.id).run();
  if (res.meta.changes === 0) throw notFound("API key");
  return c.json({ deleted: true });
});
