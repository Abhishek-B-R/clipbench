import { Hono } from "hono";
import { z } from "zod";
import { ApiError, body, now, notFound, requireUser, withSession, type AppEnv } from "../http";

/** Requests board: ask for a provider or a language, one vote per user. Mounted at /api/requests. */
export const requests = new Hono<AppEnv>();

const NewRequest = z.strictObject({
  kind: z.enum(["provider", "language"]),
  title: z.string().trim().min(3).max(120),
  body: z.string().trim().max(2000).default(""),
});

type RequestRow = {
  id: string;
  kind: string;
  title: string;
  body: string;
  status: string;
  created_at: number;
  votes: number;
  voted: number;
  mine: number;
};

const out = (r: RequestRow) => ({
  id: r.id,
  kind: r.kind,
  title: r.title,
  body: r.body,
  status: r.status,
  created_at: r.created_at,
  votes: r.votes,
  voted: r.voted === 1,
  mine: r.mine === 1,
});

const kindFilter = z.enum(["provider", "language"]).optional();

requests.get("/", withSession, async (c) => {
  const uid = c.get("session")?.user.id ?? "";
  const kind = kindFilter.safeParse(c.req.query("kind") || undefined);
  if (!kind.success) throw new ApiError(400, "invalid_request", "kind: must be 'provider' or 'language'");
  const { results } = await c.env.DB.prepare(
    `SELECT r.id, r.kind, r.title, r.body, r.status, r.created_at,
       (SELECT COUNT(*) FROM votes v WHERE v.request_id = r.id) AS votes,
       EXISTS (SELECT 1 FROM votes v WHERE v.request_id = r.id AND v.user_id = ?1) AS voted,
       (r.user_id IS NOT NULL AND r.user_id = ?1) AS mine
     FROM requests r
     WHERE (?2 IS NULL OR r.kind = ?2)
     ORDER BY votes DESC, r.created_at DESC
     LIMIT 200`,
  )
    .bind(uid, kind.data ?? null)
    .all<RequestRow>();
  return c.json({ requests: results.map(out) });
});

requests.post("/", withSession, async (c) => {
  const user = requireUser(c);
  const input = await body(c, NewRequest);
  const recent = await c.env.DB.prepare("SELECT COUNT(*) AS n FROM requests WHERE user_id = ? AND created_at > ?")
    .bind(user.id, now() - 24 * 60 * 60 * 1000)
    .first<number>("n");
  if ((recent ?? 0) >= 10) throw new ApiError(429, "rate_limited", "At most 10 requests per day.");
  const id = crypto.randomUUID();
  const created = now();
  // The author votes for their own request.
  await c.env.DB.batch([
    c.env.DB.prepare("INSERT INTO requests (id, user_id, kind, title, body, created_at) VALUES (?, ?, ?, ?, ?, ?)").bind(
      id,
      user.id,
      input.kind,
      input.title,
      input.body,
      created,
    ),
    c.env.DB.prepare("INSERT INTO votes (request_id, user_id, created_at) VALUES (?, ?, ?)").bind(id, user.id, created),
  ]);
  return c.json(
    { request: out({ id, ...input, status: "open", created_at: created, votes: 1, voted: 1, mine: 1 }) },
    201,
  );
});

/** Toggle the signed-in user's vote. The (request_id, user_id) primary key makes a second vote impossible. */
requests.post("/:id/vote", withSession, async (c) => {
  const user = requireUser(c);
  const id = c.req.param("id");
  const exists = await c.env.DB.prepare("SELECT 1 AS x FROM requests WHERE id = ?").bind(id).first("x");
  if (!exists) throw notFound("Request");
  const removed = await c.env.DB.prepare("DELETE FROM votes WHERE request_id = ? AND user_id = ?").bind(id, user.id).run();
  let voted = false;
  if (removed.meta.changes === 0) {
    await c.env.DB.prepare("INSERT INTO votes (request_id, user_id, created_at) VALUES (?, ?, ?) ON CONFLICT DO NOTHING")
      .bind(id, user.id, now())
      .run();
    voted = true;
  }
  const votes = await c.env.DB.prepare("SELECT COUNT(*) AS n FROM votes WHERE request_id = ?").bind(id).first<number>("n");
  return c.json({ id, voted, votes: votes ?? 0 });
});
