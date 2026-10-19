import { useEffect, useState } from "react";
import type { Run, RunIndex } from "./types";

/**
 * Data layer. Reads the static export written by `clipbench export` (docs/WEB.md):
 *   /data/index.json, then /data/runs/<run_id>.json
 * Before the first real run exists it falls back to the synthetic sample:
 *   /data/sample/index.json, /data/sample/runs/<run_id>.json   (index has "demo": true)
 * Results are cached per URL for the session; every hook shares the same promise.
 */

export interface Source {
  index: RunIndex;
  /** "/data" or "/data/sample" */
  base: string;
  demo: boolean;
  /** True when the demo was chosen on purpose (?demo=1) while real data exists. */
  forced: boolean;
}

/**
 * Demo mode: `?demo=1` shows the synthetic sample even when a real export exists (for design review and for
 * seeing every view filled in); `?demo=0` goes back. The choice is sticky for the tab (sessionStorage).
 */
const DEMO_KEY = "clipbench-demo";
export function demoForced(): boolean {
  if (typeof window === "undefined") return false;
  try {
    const q = new URLSearchParams(window.location.search).get("demo");
    if (q === "1") sessionStorage.setItem(DEMO_KEY, "1");
    if (q === "0") sessionStorage.removeItem(DEMO_KEY);
    return sessionStorage.getItem(DEMO_KEY) === "1";
  } catch {
    return new URLSearchParams(window.location.search).get("demo") === "1";
  }
}

/** Switch between the real export and the demo, then reload so every cached view refetches. */
export function setDemo(on: boolean) {
  try {
    if (on) sessionStorage.setItem(DEMO_KEY, "1");
    else sessionStorage.removeItem(DEMO_KEY);
  } catch {
    /* falls back to the URL flag */
  }
  const url = new URL(window.location.href);
  url.searchParams.set("demo", on ? "1" : "0");
  window.location.assign(url.toString());
}

const cache = new Map<string, Promise<unknown>>();

function getJSON<T>(url: string): Promise<T> {
  let p = cache.get(url) as Promise<T> | undefined;
  if (!p) {
    p = fetch(url, { headers: { accept: "application/json" } }).then(async (r) => {
      const type = r.headers.get("content-type") ?? "";
      // Vite and the Worker return index.html for unknown paths; treat that as missing.
      if (!r.ok || !type.includes("json")) throw new Error(`${r.status} ${url}`);
      return (await r.json()) as T;
    });
    p.catch(() => cache.delete(url));
    cache.set(url, p);
  }
  return p;
}

let sourcePromise: Promise<Source> | null = null;

export function loadSource(): Promise<Source> {
  if (!sourcePromise) {
    const sample = (forced: boolean) => getJSON<RunIndex>("/data/sample/index.json").then((index) => ({ index, base: "/data/sample", demo: true, forced }));
    sourcePromise = demoForced()
      ? sample(true)
      : getJSON<RunIndex>("/data/index.json")
          .then((index) => ({ index, base: "/data", demo: Boolean(index.demo), forced: false }))
          .catch(() => sample(false));
    sourcePromise.catch(() => {
      sourcePromise = null;
    });
  }
  return sourcePromise;
}

export async function loadRun(runId?: string): Promise<{ run: Run; source: Source }> {
  const source = await loadSource();
  const id = runId ?? source.index.latest;
  const run = await getJSON<Run>(`${source.base}/runs/${encodeURIComponent(id)}.json`);
  return { run, source };
}

type State<T> = { status: "loading"; data?: undefined; error?: undefined } | { status: "ready"; data: T; error?: undefined } | { status: "error"; data?: undefined; error: Error };

function useAsync<T>(fn: () => Promise<T>, deps: unknown[]): State<T> {
  const [state, setState] = useState<State<T>>({ status: "loading" });
  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    fn().then(
      (data) => alive && setState({ status: "ready", data }),
      (error: unknown) => alive && setState({ status: "error", error: error instanceof Error ? error : new Error(String(error)) }),
    );
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export function useSource() {
  return useAsync(loadSource, []);
}

/** Latest run when runId is undefined. */
export function useRun(runId?: string) {
  return useAsync(() => loadRun(runId), [runId]);
}

/** Every published run, oldest first (for trends, rank history and the changelog). Failed runs are skipped. */
export async function loadAllRuns(): Promise<{ runs: Run[]; source: Source }> {
  const source = await loadSource();
  const runs = await Promise.all(source.index.runs.map((r) => getJSON<Run>(`${source.base}/runs/${encodeURIComponent(r.run_id)}.json`).catch(() => null)));
  return { runs: (runs.filter(Boolean) as Run[]).sort((a, b) => a.created_at.localeCompare(b.created_at)), source };
}

export function useAllRuns() {
  return useAsync(loadAllRuns, []);
}
