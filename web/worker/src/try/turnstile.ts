/**
 * Cloudflare Turnstile server-side check (Siteverify). https://developers.cloudflare.com/turnstile/get-started/server-side-validation/
 * Locally TURNSTILE_SECRET is Cloudflare's documented always-pass test secret (.dev.vars.example).
 */

export const SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify";
/** The token Cloudflare's test site keys hand out. */
export const TURNSTILE_DUMMY_TOKEN = "XXXX.DUMMY.TOKEN.XXXX";

export type TurnstileVerdict = { ok: true } | { ok: false; codes: string[] };

export async function verifyTurnstile(secret: string, token: string, ip: string): Promise<TurnstileVerdict> {
  const form = new FormData();
  form.set("secret", secret);
  form.set("response", token);
  if (ip && ip !== "unknown") form.set("remoteip", ip);
  form.set("idempotency_key", crypto.randomUUID());
  try {
    const res = await fetch(SITEVERIFY_URL, { method: "POST", body: form, signal: AbortSignal.timeout(10_000) });
    const body = (await res.json().catch(() => null)) as { success?: boolean; "error-codes"?: string[] } | null;
    if (body?.success === true) return { ok: true };
    return { ok: false, codes: body?.["error-codes"] ?? [`http-${res.status}`] };
  } catch {
    return { ok: false, codes: ["siteverify-unreachable"] };
  }
}
