import { env, SELF } from "cloudflare:test";

export const ORIGIN = "http://localhost:8787";

let ipCounter = 0;
/** A fresh client IP per call so Better Auth's per-IP sign-in limit never trips across tests. */
export const freshIp = () => `10.0.${Math.floor(++ipCounter / 250)}.${ipCounter % 250}`;

export type Client = {
  cookie: string;
  ip: string;
  fetch: (path: string, init?: RequestInit & { json?: unknown }) => Promise<Response>;
};

function request(path: string, init: RequestInit & { json?: unknown } = {}, cookie = "", ip = freshIp()) {
  const headers = new Headers(init.headers);
  if (!headers.has("cf-connecting-ip")) headers.set("cf-connecting-ip", ip);
  if (cookie) headers.set("cookie", cookie);
  const method = (init.method ?? "GET").toUpperCase();
  if (method !== "GET" && method !== "HEAD" && !headers.has("origin")) headers.set("origin", ORIGIN);
  let body = init.body;
  if (init.json !== undefined) {
    headers.set("content-type", "application/json");
    body = JSON.stringify(init.json);
  }
  return SELF.fetch(`${ORIGIN}${path}`, { ...init, headers, body, redirect: "manual" });
}

export const anon = (path: string, init?: RequestInit & { json?: unknown }) => request(path, init);

/**
 * Sign in through Better Auth's real magic link flow: request a link (logged, since RESEND_API_KEY is
 * empty), read the token Better Auth stored in the verification table, then open the verify URL and keep
 * the session cookie it sets.
 */
export async function signIn(email: string): Promise<Client> {
  const ip = freshIp();
  const sent = await request("/api/auth/sign-in/magic-link", { method: "POST", json: { email, callbackURL: "/" } }, "", ip);
  if (sent.status !== 200) throw new Error(`magic link request failed: ${sent.status} ${await sent.text()}`);
  const row = await env.DB.prepare("SELECT identifier FROM verification WHERE value LIKE ? ORDER BY createdAt DESC LIMIT 1")
    .bind(`%${email}%`)
    .first<{ identifier: string }>();
  if (!row) throw new Error("no verification row for magic link");
  const token = row.identifier.replace(/^magic-link:/, "");
  const verify = await request(`/api/auth/magic-link/verify?token=${encodeURIComponent(token)}&callbackURL=%2F`, {}, "", ip);
  const setCookies = verify.headers.getSetCookie();
  const cookie = setCookies.map((c) => c.split(";")[0]).join("; ");
  if (!cookie.includes("session_token")) throw new Error(`no session cookie (status ${verify.status})`);
  return { cookie, ip, fetch: (path, init) => request(path, init, cookie, ip) };
}

export async function json<T = any>(res: Response): Promise<T> {
  return (await res.json()) as T;
}
