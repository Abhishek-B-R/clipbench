import { sendEmail } from "./email";
import { newRunEmail, summarize } from "./emails/new-run";
import { now } from "./http";
import { isPublished, type Run } from "./gate";

export type NotifyResult = {
  run_id: string;
  recipients: number;
  sent: number;
  logged: number;
  failed: number;
  already_sent?: boolean;
  /** The run's language has not passed the publication gate, so nothing was sent. */
  unpublished?: boolean;
};

/** True when the exporter marked the run's own language published and every gate flag is true (src/gate.ts). */
export function isPublishedRun(run: unknown): boolean {
  const r = run as Run | null;
  return Boolean(r && typeof r.language === "string" && isPublished(r, r.language));
}

async function loadRun(env: Env, origin: string, runId: string): Promise<unknown | null> {
  const res = await env.ASSETS.fetch(new URL(`/data/runs/${runId}.json`, origin));
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return null;
  return res.json();
}

/**
 * Email every user with email_on_new_run about a run. Each user's watchlist is summarised in the email.
 * A run is notified at most once (run_notifications) unless `force` is set.
 */
export async function notifyNewRun(env: Env, origin: string, runId: string, force = false): Promise<NotifyResult | null> {
  const run = await loadRun(env, origin, runId);
  if (!run) return null;
  // The email carries ranks, so it only goes out for a language that passed the publication gate (docs/V2.md).
  if (!isPublishedRun(run)) return { run_id: runId, recipients: 0, sent: 0, logged: 0, failed: 0, unpublished: true };
  if (!force) {
    const done = await env.DB.prepare("SELECT recipients FROM run_notifications WHERE run_id = ?").bind(runId).first<number>("recipients");
    if (done !== null) return { run_id: runId, recipients: done, sent: 0, logged: 0, failed: 0, already_sent: true };
  }
  const summary = summarize(runId, run as Parameters<typeof summarize>[1]);
  const { results } = await env.DB.prepare(
    `SELECT u.id, u.email, (SELECT json_group_array(w.provider_id) FROM watchlist w WHERE w.user_id = u.id) AS watched
     FROM alert_prefs a JOIN "user" u ON u.id = a.user_id
     WHERE a.email_on_new_run = 1 AND u.email IS NOT NULL AND u.email != ''`,
  ).all<{ id: string; email: string; watched: string }>();

  const site = (env.BETTER_AUTH_URL || origin).replace(/\/$/, "");
  const result: NotifyResult = { run_id: runId, recipients: results.length, sent: 0, logged: 0, failed: 0 };
  for (const r of results) {
    let watched: string[] = [];
    try {
      const parsed: unknown = JSON.parse(r.watched ?? "[]");
      if (Array.isArray(parsed)) watched = parsed.filter((x): x is string => typeof x === "string");
    } catch {
      watched = [];
    }
    try {
      const outcome = await sendEmail(env, { to: r.email, ...newRunEmail(summary, watched, site) });
      result[outcome] += 1;
    } catch (err) {
      result.failed += 1;
      console.error(JSON.stringify({ event: "notify_failed", user_id: r.id, error: String(err) }));
    }
  }
  await env.DB.prepare(
    `INSERT INTO run_notifications (run_id, sent_at, recipients) VALUES (?, ?, ?)
     ON CONFLICT (run_id) DO UPDATE SET sent_at = excluded.sent_at, recipients = excluded.recipients`,
  )
    .bind(runId, now(), results.length)
    .run();
  return result;
}

/** Cron: when AUTO_NOTIFY is "true", notify about the latest run in index.json once. Otherwise only housekeeping. */
export async function scheduledNotify(env: Env): Promise<void> {
  if (env.AUTO_NOTIFY !== "true") return;
  const origin = env.BETTER_AUTH_URL || "https://clipbench.invalid";
  const res = await env.ASSETS.fetch(new URL("/data/index.json", origin));
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return;
  const index = (await res.json()) as { latest?: string; demo?: boolean };
  if (!index.latest || index.demo) return;
  const out = await notifyNewRun(env, origin, index.latest);
  console.log(JSON.stringify({ event: "scheduled_notify", ...out }));
}
