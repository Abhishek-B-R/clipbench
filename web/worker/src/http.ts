import type { Context, MiddlewareHandler } from "hono";
import type { ContentfulStatusCode } from "hono/utils/http-status";
import { z } from "zod";
import type { Auth, SessionData } from "./auth";

export type AppEnv = {
  Bindings: Env;
  Variables: {
    auth: Auth;
    session: SessionData | null;
  };
};

export type AppContext = Context<AppEnv>;

export class ApiError extends Error {
  constructor(
    readonly status: ContentfulStatusCode,
    readonly code: string,
    message: string,
    readonly headers: Record<string, string> = {},
  ) {
    super(message);
  }
}

export function errorBody(code: string, message: string) {
  return { error: { code, message } };
}

export const unauthorized = () => new ApiError(401, "unauthorized", "Sign in first.");
export const notFound = (what = "Resource") => new ApiError(404, "not_found", `${what} not found.`);

const MAX_BODY_BYTES = 16 * 1024;

/** Read a JSON body and validate it with zod. Every mutating route with a body goes through here. */
export async function body<T extends z.ZodType>(c: AppContext, schema: T): Promise<z.infer<T>> {
  const type = c.req.header("content-type") ?? "";
  if (!type.toLowerCase().startsWith("application/json")) {
    throw new ApiError(415, "unsupported_media_type", "Send the body as application/json.");
  }
  const declared = Number(c.req.header("content-length") ?? "0");
  if (declared > MAX_BODY_BYTES) throw new ApiError(413, "payload_too_large", "Body is too large.");
  const raw = await c.req.text();
  if (raw.length > MAX_BODY_BYTES) throw new ApiError(413, "payload_too_large", "Body is too large.");
  let json: unknown;
  try {
    json = JSON.parse(raw);
  } catch {
    throw new ApiError(400, "invalid_json", "Body is not valid JSON.");
  }
  return parse(schema, json);
}

export function parse<T extends z.ZodType>(schema: T, value: unknown): z.infer<T> {
  const res = schema.safeParse(value);
  if (!res.success) {
    const issue = res.error.issues[0];
    const where = issue && issue.path.length ? `${issue.path.join(".")}: ` : "";
    throw new ApiError(400, "invalid_request", `${where}${issue?.message ?? "Invalid request."}`);
  }
  return res.data;
}

/** Signed-in user or 401. */
export function requireUser(c: AppContext) {
  const s = c.get("session");
  if (!s) throw unauthorized();
  return s.user;
}

/** Load the session once per request (only on routes that need it). */
export const withSession: MiddlewareHandler<AppEnv> = async (c, next) => {
  const session = await c.get("auth").api.getSession({ headers: c.req.raw.headers });
  c.set("session", session ?? null);
  await next();
};

/** Same-origin check for cookie authenticated mutating routes (CSRF). */
export function sameOrigin(allowed: (env: Env) => string[]): MiddlewareHandler<AppEnv> {
  return async (c, next) => {
    const m = c.req.method;
    if (m === "GET" || m === "HEAD" || m === "OPTIONS") return next();
    const self = new URL(c.req.url).origin;
    const origin = c.req.header("origin");
    if (origin) {
      if (origin !== self && !allowed(c.env).includes(origin)) {
        throw new ApiError(403, "forbidden_origin", "Cross-origin request refused.");
      }
      return next();
    }
    // No Origin header: only accept browsers that say the request is same-origin.
    if (c.req.header("sec-fetch-site") === "same-origin") return next();
    throw new ApiError(403, "forbidden_origin", "Missing Origin header.");
  };
}

export const now = () => Date.now();

export function bool(v: unknown): boolean {
  return v === 1 || v === true || v === "1";
}

export function jsonArray(s: unknown): string[] {
  if (typeof s !== "string") return [];
  try {
    const v: unknown = JSON.parse(s);
    return Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

// Shared field schemas.
export const providerId = z
  .string()
  .regex(/^[a-z0-9][a-z0-9._-]{0,63}$/, "must be a provider id like 'deepgram'");
export const runId = z.string().regex(/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/, "must be a run id");
export const uuid = z.uuid();
