import { useCallback, useEffect, useState } from "react";

/**
 * Client for the Worker API (docs/WEB.md, "API"). Errors come back as `{error: {code, message}}`.
 * When the Worker is not running, the dev proxy or the SPA fallback answers with HTML or a 5xx; that becomes
 * an ApiError with code "unavailable" so pages can show one plain sentence.
 */
export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
  get unavailable() {
    return this.code === "unavailable";
  }
  get unauthorized() {
    return this.status === 401;
  }
}

export async function api<T>(path: string, init: { method?: string; body?: unknown } = {}): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, {
      method: init.method ?? "GET",
      credentials: "same-origin",
      headers: { accept: "application/json", ...(init.body !== undefined ? { "content-type": "application/json" } : {}) },
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
    });
  } catch {
    throw new ApiError("unavailable", "The account service did not answer.", 0);
  }
  const json = (res.headers.get("content-type") ?? "").includes("json");
  if (!json) throw new ApiError("unavailable", "The account service did not answer.", res.status);
  const body = (await res.json().catch(() => null)) as ({ error?: { code: string; message: string } } & T) | null;
  if (!res.ok) {
    const e = body?.error;
    throw new ApiError(e?.code ?? "http_error", e?.message ?? `Request failed (${res.status}).`, res.status);
  }
  return body as T;
}

type State<T> = { status: "idle" | "loading" } | { status: "ready"; data: T } | { status: "error"; error: ApiError };

/**
 * GET a Worker route. Pass null to skip (for example while signed out).
 * `mutate` updates the local copy for optimistic UI; `reload` refetches.
 */
export function useApi<T>(path: string | null) {
  const [state, setState] = useState<State<T>>({ status: path ? "loading" : "idle" });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!path) {
      setState({ status: "idle" });
      return;
    }
    let alive = true;
    setState((s) => (s.status === "ready" ? s : { status: "loading" }));
    api<T>(path).then(
      (data) => alive && setState({ status: "ready", data }),
      (error: unknown) =>
        alive && setState({ status: "error", error: error instanceof ApiError ? error : new ApiError("unavailable", String(error), 0) }),
    );
    return () => {
      alive = false;
    };
  }, [path, tick]);
  const reload = useCallback(() => setTick((t) => t + 1), []);
  const mutate = useCallback((fn: (d: T) => T) => setState((s) => (s.status === "ready" ? { status: "ready", data: fn(s.data) } : s)), []);
  return {
    status: state.status,
    data: state.status === "ready" ? state.data : undefined,
    error: state.status === "error" ? state.error : undefined,
    reload,
    mutate,
  };
}

export type AuthProviders = { github: boolean; google: boolean; email: boolean };

/** Which sign-in methods the Worker has credentials for. `down` when the API does not answer. */
export function useAuthProviders() {
  const r = useApi<AuthProviders>("/api/auth-providers");
  if (r.status === "ready" && r.data) return { status: "ready" as const, providers: r.data };
  if (r.status === "error") return { status: "down" as const, providers: { github: true, google: true, email: true } };
  return { status: "loading" as const, providers: { github: true, google: true, email: true } };
}

// ---- shapes returned by the Worker (web/worker/src/routes/*)

export interface RequestItem {
  id: string;
  kind: "provider" | "language";
  title: string;
  body: string;
  status: string;
  created_at: number;
  votes: number;
  voted: boolean;
  mine: boolean;
}

export interface ApiKey {
  id: string;
  name: string;
  prefix: string;
  created_at: number;
  last_used_at: number | null;
}

export interface SavedComparison {
  id: string;
  name: string;
  providers: string[];
  tier: "verified" | "creator";
  metric: string;
  tags: string[];
  public: boolean;
  created_at: number;
}

export interface AlertPrefs {
  email_on_new_run: boolean;
  email_on_rank_change: boolean;
}
