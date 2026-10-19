import { useEffect, useState } from "react";
import type { FindingsFile, Run, RunIndex } from "./types";

/**
 * Data layer. Reads only the static export written by `clipbench export` (docs/WEB.md, docs/V2.md):
 *   /data/index.json, then /data/runs/<run_id>.json
 * Every number on the site comes from these files or the Worker API. There is no synthetic fallback in a
 * production build: when nothing is published, pages show the method and progress, never invented numbers.
 *
 * Development only (`vite dev`, tests): `?demo=1` loads the synthetic fixture in web/test/fixtures/sample/
 * so every view can be seen filled in. That code and data are stripped from production builds.
 * Results are cached per URL for the session; every hook shares the same promise.
 */

export interface Source {
  index: RunIndex;
  /** "/data" for the real export; "dev:sample" for the dev-only fixture. */
  base: string;
  /** True only in development when the synthetic fixture is shown. */
  demo: boolean;
  /** Same as demo (kept for the dev banner). */
  forced: boolean;
}

const DEV = import.meta.env.DEV;
const SAMPLE_ROOT = DEV ? "../../test/fixtures/sample/" : "";
// Lazy, dev-only: in a production build DEV is false, the branch is dropped and no fixture is bundled.
const SAMPLE: Record<string, () => Promise<unknown>> = DEV ? import.meta.glob("../../test/fixtures/sample/**/*.json", { import: "default" }) : {};

const DEMO_KEY = "clipbench-demo";
/** Dev only: `?demo=1` turns the synthetic fixture on for this tab, `?demo=0` turns it off. Always false in production. */
export function demoForced(): boolean {
  if (!DEV || typeof window === "undefined") return false;
  try {
    const q = new URLSearchParams(window.location.search).get("demo");
    if (q === "1") sessionStorage.setItem(DEMO_KEY, "1");
    if (q === "0") sessionStorage.removeItem(DEMO_KEY);
    return sessionStorage.getItem(DEMO_KEY) === "1";
  } catch {
    return new URLSearchParams(window.location.search).get("demo") === "1";
  }
}

/** Dev only: switch the fixture on or off and reload. */
export function setDemo(on: boolean) {
  if (!DEV) return;
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
    if (url.startsWith("dev:sample/")) {
      const load = SAMPLE[SAMPLE_ROOT + url.slice("dev:sample/".length)];
      p = (load ? load() : Promise.reject(new Error(`missing fixture ${url}`))) as Promise<T>;
    } else {
      p = fetch(url, { headers: { accept: "application/json" } }).then(async (r) => {
        const type = r.headers.get("content-type") ?? "";
        // Vite and the Worker return index.html for unknown paths; treat that as missing.
        if (!r.ok || !type.includes("json")) throw new Error(`${r.status} ${url}`);
        return (await r.json()) as T;
      });
    }
    p.catch(() => cache.delete(url));
    cache.set(url, p);
  }
  return p;
}

let sourcePromise: Promise<Source> | null = null;

export function loadSource(): Promise<Source> {
  if (!sourcePromise) {
    sourcePromise = demoForced()
      ? getJSON<RunIndex>("dev:sample/index.json").then((index) => ({ index, base: "dev:sample", demo: true, forced: true }))
      : getJSON<RunIndex>("/data/index.json").then((index) => ({ index, base: "/data", demo: false, forced: false }));
    sourcePromise.catch(() => {
      sourcePromise = null;
    });
  }
  return sourcePromise;
}

/** Public URL of a run's JSON (null for the dev fixture, which has none). */
export function runUrl(source: Source, runId: string) {
  return source.base.startsWith("dev:") ? null : `${source.base}/runs/${encodeURIComponent(runId)}.json`;
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

/** Latest run for a language (null when that language has no run yet). Without a language, the latest run. */
export async function loadRunFor(lang?: string | null): Promise<{ run: Run | null; source: Source }> {
  const source = await loadSource();
  if (!lang) return loadRun();
  const r = [...source.index.runs].filter((x) => x.language === lang).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  if (!r) return { run: null, source };
  return loadRun(r.run_id);
}

export function useRunFor(lang?: string | null) {
  return useAsync(() => loadRunFor(lang), [lang]);
}

/** Findings for a language (/data/findings/<lang>.json). null when the file does not exist yet. */
export async function loadFindings(lang: string): Promise<FindingsFile | null> {
  const source = await loadSource();
  try {
    const raw = await getJSON<FindingsFile | FindingsFile["findings"]>(`${source.base}/findings/${encodeURIComponent(lang)}.json`);
    // Accept a bare array too, defensively.
    return Array.isArray(raw) ? { lang, findings: raw } : { ...raw, findings: raw.findings ?? [] };
  } catch {
    return null;
  }
}

export function useFindings(lang: string) {
  return useAsync(() => loadFindings(lang), [lang]);
}
