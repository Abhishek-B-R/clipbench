import { Hono } from "hono";
import { HTTPException } from "hono/http-exception";
import { secureHeaders } from "hono/secure-headers";
import { z } from "zod";
import { authProviders, createAuth, trustedOrigins, type Auth } from "./auth";
import { timingSafeEqualStr } from "./crypto";
import { ApiError, body, errorBody, runId, sameOrigin, withSession, type AppEnv } from "./http";
import { notifyNewRun, scheduledNotify } from "./notify";
import { pruneRateLimits } from "./ratelimit";
import { account } from "./routes/account";
import { data } from "./routes/data";
import { keys } from "./routes/keys";
import { requests } from "./routes/requests";

const app = new Hono<AppEnv>();

// Security headers on every Worker response (API only; static assets get theirs from dist/_headers).
app.use(
  "*",
  secureHeaders({
    contentSecurityPolicy: { defaultSrc: ["'none'"], frameAncestors: ["'none'"] },
    strictTransportSecurity: "max-age=31536000; includeSubDomains",
    referrerPolicy: "strict-origin-when-cross-origin",
    xFrameOptions: "DENY",
    crossOriginResourcePolicy: false, // the public data API is meant to be fetched cross-origin
    permissionsPolicy: { camera: [], microphone: [], geolocation: [] },
  }),
);

// One Better Auth instance per isolate and env. It holds no request state; building one per request
// makes later requests await promises created in an earlier request's context, which hangs in workerd.
const authByEnv = new WeakMap<Env, Auth>();
function getAuth(env: Env): Auth {
  let auth = authByEnv.get(env);
  if (!auth) {
    auth = createAuth(env);
    authByEnv.set(env, auth);
  }
  return auth;
}

/** Accounts (sign-in, watchlist, alerts, comparisons, requests, keys) are off unless ACCOUNTS is "true" (docs/V2.md, Web flags). */
export const accountsOn = (env: Env) => env.ACCOUNTS === "true" || env.ACCOUNTS === "1";

app.use("/api/*", async (c, next) => {
  // The public data API never touches Better Auth, so it works without auth secrets and with accounts off.
  if (c.req.path === "/api/v1" || c.req.path.startsWith("/api/v1/")) return next();
  // With accounts off every other API route is absent: no auth instance is built and no secret is needed.
  if (!accountsOn(c.env)) throw new ApiError(404, "not_found", "No such API route.");
  const auth = getAuth(c.env);
  // Finish Better Auth's async init inside this request, so no later request waits on it.
  await auth.$context;
  c.set("auth", auth);
  c.set("session", null);
  await next();
});

// Better Auth runs its own origin / CSRF checks (trustedOrigins).
app.on(["GET", "POST"], "/api/auth/*", (c) => c.get("auth").handler(c.req.raw));

app.get("/api/auth-providers", (c) => {
  c.header("Cache-Control", "public, max-age=300");
  return c.json(authProviders(c.env));
});

// Cookie authenticated mutating routes must come from our own origin. The admin route uses a bearer token.
const csrf = sameOrigin(trustedOrigins);
app.use("/api/watchlist/*", csrf);
app.use("/api/alerts", csrf);
app.use("/api/comparisons", csrf);
app.use("/api/comparisons/*", csrf);
app.use("/api/requests", csrf);
app.use("/api/requests/*", csrf);
app.use("/api/keys", csrf);
app.use("/api/keys/*", csrf);

app.get("/api/me", withSession, (c) => {
  const s = c.get("session");
  c.header("Cache-Control", "no-store");
  if (!s) return c.json({ user: null });
  const { id, name, email, image } = s.user;
  return c.json({ user: { id, name, email, image: image ?? null } });
});

app.route("/api", account);
app.route("/api/requests", requests);
app.route("/api/keys", keys);
app.route("/api/v1", data);

// ---------------------------------------------------------------- admin

const NotifyBody = z.strictObject({ run_id: runId, force: z.boolean().default(false) });

app.post("/api/admin/notify-new-run", async (c) => {
  const token = c.env.ADMIN_TOKEN;
  const given = /^Bearer\s+(.+)$/.exec(c.req.header("authorization") ?? "")?.[1] ?? "";
  if (!token || !given || !(await timingSafeEqualStr(given, token))) {
    throw new ApiError(401, "unauthorized", "Admin token required.");
  }
  const input = await body(c, NotifyBody);
  const result = await notifyNewRun(c.env, new URL(c.req.url).origin, input.run_id, input.force);
  if (!result) throw new ApiError(404, "not_found", `Run ${input.run_id} is not published in /data/runs/.`);
  return c.json(result);
});

// ---------------------------------------------------------------- errors

app.notFound((c) => c.json(errorBody("not_found", "No such API route."), 404));

app.onError((err, c) => {
  if (err instanceof ApiError) {
    for (const [k, v] of Object.entries(err.headers)) c.header(k, v);
    return c.json(errorBody(err.code, err.message), err.status);
  }
  if (err instanceof HTTPException) {
    return c.json(errorBody("http_error", err.message || "Request failed."), err.status);
  }
  console.error(JSON.stringify({ event: "unhandled_error", path: new URL(c.req.url).pathname, error: String(err), stack: err.stack }));
  return c.json(errorBody("internal", "Something went wrong."), 500);
});

export { app };

export default {
  fetch: app.fetch,
  async scheduled(_controller, env, ctx) {
    ctx.waitUntil(Promise.all([pruneRateLimits(env.DB), scheduledNotify(env)]));
  },
} satisfies ExportedHandler<Env>;
