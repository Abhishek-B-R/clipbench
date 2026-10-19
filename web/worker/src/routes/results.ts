import { Hono, type Context } from "hono";
import { z } from "zod";
import { badgeSvg, percent } from "../badge";
import { sha256Hex } from "../crypto";
import { ApiError, parse, providerId, type AppEnv } from "../http";

/**
 * Derived read-only views over the exported run JSON (A12): leaderboards per language, metric and slice;
 * per-model metrics; canary history, drift and reliability; SVG badges. Every number comes from the files
 * `clipbench export` wrote under /data. A language that is not published (docs/V2.md) gets its status and
 * no numbers: no ranking, no history, no badge value.
 */
export const results = new Hono<AppEnv>();

type Ctx = Context<AppEnv>;
type Obj = Record<string, unknown>;

type Aggregate = {
  provider: string;
  model_id?: string | null;
  tier: string;
  metric: string;
  slice_kind: string;
  slice: string;
  n_clips?: number | null;
  error_rate?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  too_small?: boolean | null;
};
type Provider = { id: string; label?: string; model_id?: string | null };
type Status = { lang: string; published?: boolean; verified_clips?: number; providers_ok?: number };
type Run = {
  run_id: string;
  created_at?: string;
  language: string;
  metrics?: { name: string; primary?: boolean; label?: string }[];
  providers?: Provider[];
  aggregates?: Aggregate[];
  languages?: Status[];
  history?: { series?: Record<string, Obj[]> } & Obj;
  drift?: (Obj & { provider?: string })[] | null;
  reliability?: { run?: Obj[]; all_runs?: Obj[] } | null;
};
type IndexRun = { run_id: string; created_at?: string; language: string; tiers?: string[] };
type Index = { latest?: string; runs?: IndexRun[]; languages?: Status[] };

export const lang = z.string().regex(/^[a-z]{2,3}$/, "must be a language code like 'ja'");
const metricName = z.string().regex(/^[a-z0-9_]{1,40}$/, "must be a metric name like 'cer'");
const tierName = z.enum(["verified", "creator"]);
const sliceName = z.string().regex(/^[A-Za-z0-9_<>+.-]{1,60}$/, "must be a slice name");

async function assetJson<T>(c: Ctx, path: string): Promise<T | null> {
  const res = await c.env.ASSETS.fetch(new URL(path, c.req.url));
  if (!res.ok || !(res.headers.get("content-type") ?? "").includes("json")) return null;
  try {
    return (await res.json()) as T;
  } catch {
    return null;
  }
}

async function index(c: Ctx): Promise<Index> {
  const idx = await assetJson<Index>(c, "/data/index.json");
  if (!idx) throw new ApiError(404, "not_found", "No runs published yet.");
  return idx;
}

/** The run the site shows for a language: the newest with a verified tier, else the newest. */
export function pickRun(idx: Index, language: string): IndexRun | null {
  const runs = (idx.runs ?? []).filter((r) => r.language === language);
  runs.sort((a, b) => String(b.created_at ?? "").localeCompare(String(a.created_at ?? "")));
  return runs.find((r) => (r.tiers ?? []).includes("verified")) ?? runs[0] ?? null;
}

async function runFor(c: Ctx, language: string): Promise<Run | null> {
  const pick = pickRun(await index(c), language);
  if (!pick || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(pick.run_id)) return null;
  return assetJson<Run>(c, `/data/runs/${pick.run_id}.json`);
}

function status(run: Run | null, language: string): Status {
  const st = run?.languages?.find((s) => s.lang === language);
  return { lang: language, published: false, verified_clips: 0, providers_ok: 0, ...(st ?? {}) };
}

const isPublished = (run: Run | null, language: string) => status(run, language).published === true;

function primaryMetric(run: Run): string | undefined {
  return run.metrics?.find((m) => m.primary)?.name ?? run.metrics?.[0]?.name;
}

/** JSON with a content ETag and a 304 for a matching If-None-Match. */
async function cached(c: Ctx, body: string, type: string, maxAge: number, status = 200): Promise<Response> {
  const etag = `"${(await sha256Hex(body)).slice(0, 32)}"`;
  const headers = new Headers({ "Cache-Control": `public, max-age=${maxAge}`, ETag: etag });
  const sent = c.req.header("if-none-match");
  if (sent && sent.split(",").some((t) => t.trim().replace(/^W\//, "") === etag)) {
    return new Response(null, { status: 304, headers });
  }
  headers.set("Content-Type", type);
  return new Response(body, { status, headers });
}

const json = (c: Ctx, value: unknown, status = 200) =>
  cached(c, JSON.stringify(value), "application/json; charset=utf-8", 300, status);

function notPublished(run: Run | null, language: string) {
  return {
    language,
    run_id: run?.run_id ?? null,
    published: false,
    status: status(run, language),
    message: `${language} is not published yet: rankings and numbers appear once it has enough verified clips and providers.`,
  };
}

// ---------------------------------------------------------------- leaderboard

export function leaderboard(run: Run, metric: string, tier: string, sliceKind: string, slice: string) {
  const labels = new Map((run.providers ?? []).map((p) => [p.id, p.label ?? p.id]));
  const rows = (run.aggregates ?? [])
    .filter((a) => a.tier === tier && a.metric === metric && a.slice_kind === sliceKind && a.slice === slice)
    .filter((a) => typeof a.error_rate === "number")
    .sort((a, b) => (a.error_rate as number) - (b.error_rate as number) || a.provider.localeCompare(b.provider));
  return rows.map((a, i) => ({
    rank: i + 1,
    provider: a.provider,
    label: labels.get(a.provider) ?? a.provider,
    model_id: a.model_id ?? null,
    error_rate: a.error_rate,
    ci_low: a.ci_low ?? null,
    ci_high: a.ci_high ?? null,
    n_clips: a.n_clips ?? null,
    too_small: a.too_small ?? null,
  }));
}

results.get("/leaderboard/:lang", async (c) => {
  const language = parse(lang, c.req.param("lang"));
  const run = await runFor(c, language);
  if (!run) throw new ApiError(404, "not_found", `No run for ${language}.`);
  if (!isPublished(run, language)) return json(c, notPublished(run, language));
  const metric = parse(metricName, c.req.query("metric") ?? primaryMetric(run) ?? "");
  const tier = parse(tierName, c.req.query("tier") ?? "verified");
  const sliceKind = parse(sliceName, c.req.query("slice_kind") ?? "all");
  const slice = parse(sliceName, c.req.query("slice") ?? "all");
  const metrics = (run.metrics ?? []).map((m) => m.name);
  if (!metrics.includes(metric)) throw new ApiError(400, "invalid_request", `metric: one of ${metrics.join(", ")}.`);
  const slices = [
    ...new Map(
      (run.aggregates ?? [])
        .filter((a) => a.tier === tier && a.metric === metric)
        .map((a) => [`${a.slice_kind}\u0000${a.slice}`, { slice_kind: a.slice_kind, slice: a.slice }]),
    ).values(),
  ];
  return json(c, {
    language,
    run_id: run.run_id,
    created_at: run.created_at ?? null,
    published: true,
    metric,
    tier,
    slice_kind: sliceKind,
    slice,
    metrics,
    slices,
    rows: leaderboard(run, metric, tier, sliceKind, slice),
  });
});

// ---------------------------------------------------------------- per model

results.get("/models/:provider", async (c) => {
  const id = parse(providerId, c.req.param("provider"));
  const idx = await index(c);
  const languages = [...new Set((idx.runs ?? []).map((r) => r.language))].sort();
  const out = [];
  for (const language of languages) {
    const run = await runFor(c, language);
    if (!run) continue;
    const provider = run.providers?.find((p) => p.id === id);
    const hasRows = (run.aggregates ?? []).some((a) => a.provider === id);
    if (!provider && !hasRows) continue;
    if (!isPublished(run, language)) {
      out.push({ language, run_id: run.run_id, published: false });
      continue;
    }
    out.push({
      language,
      run_id: run.run_id,
      created_at: run.created_at ?? null,
      published: true,
      provider: provider ?? { id },
      primary_metric: primaryMetric(run) ?? null,
      aggregates: (run.aggregates ?? []).filter((a) => a.provider === id),
      history: run.history?.series?.[id] ?? [],
      drift: (run.drift ?? []).filter((e) => e.provider === id),
      reliability: (run.reliability?.all_runs ?? []).find((r) => r.provider === id) ?? null,
    });
  }
  if (!out.length) throw new ApiError(404, "not_found", `No results for provider ${id}.`);
  return json(c, { provider: id, languages: out });
});

// ---------------------------------------------------------------- history, drift, reliability

results.get("/history/:lang", async (c) => {
  const language = parse(lang, c.req.param("lang"));
  const run = await runFor(c, language);
  if (!run) throw new ApiError(404, "not_found", `No run for ${language}.`);
  if (!isPublished(run, language)) return json(c, notPublished(run, language));
  const only = c.req.query("provider");
  const history = run.history ?? null;
  let series = history?.series ?? {};
  if (only !== undefined) {
    const id = parse(providerId, only);
    series = id in series ? { [id]: series[id]! } : {};
  }
  return json(c, { language, run_id: run.run_id, published: true, history: history ? { ...history, series } : null });
});

results.get("/drift/:lang", async (c) => {
  const language = parse(lang, c.req.param("lang"));
  const run = await runFor(c, language);
  if (!run) throw new ApiError(404, "not_found", `No run for ${language}.`);
  if (!isPublished(run, language)) return json(c, notPublished(run, language));
  return json(c, { language, run_id: run.run_id, published: true, drift: run.drift ?? [] });
});

results.get("/reliability/:lang", async (c) => {
  const language = parse(lang, c.req.param("lang"));
  const run = await runFor(c, language);
  if (!run) throw new ApiError(404, "not_found", `No run for ${language}.`);
  if (!isPublished(run, language)) return json(c, notPublished(run, language));
  return json(c, { language, run_id: run.run_id, published: true, reliability: run.reliability ?? null });
});

// ---------------------------------------------------------------- badges

const BADGE_MAX_AGE = 3600;

results.get("/badge/:provider/:file", async (c) => {
  const svg = (message: string, tone: "value" | "muted", code = 200) =>
    cached(c, badgeSvg("clipbench", message, tone), "image/svg+xml; charset=utf-8", BADGE_MAX_AGE, code);
  const file = /^([a-z]{2,3})\.svg$/.exec(c.req.param("file"));
  if (!file?.[1]) throw new ApiError(404, "not_found", "Badges are /api/v1/badge/<provider>/<lang>.svg.");
  const language = file[1];
  const id = parse(providerId, c.req.param("provider"));
  const metricQ = c.req.query("metric");
  const metric = metricQ === undefined ? undefined : parse(metricName, metricQ);
  let run: Run | null = null;
  try {
    run = await runFor(c, language);
  } catch (err) {
    if (!(err instanceof ApiError) || err.status !== 404) throw err;
  }
  if (!run || !isPublished(run, language)) return svg(`not published ${language}`, "muted");
  const m = metric ?? primaryMetric(run);
  const row = (run.aggregates ?? []).find(
    (a) => a.provider === id && a.metric === m && a.tier === "verified" && a.slice_kind === "all" && a.slice === "all",
  );
  if (!m || !row || typeof row.error_rate !== "number") return svg(`no data ${language}`, "muted", 404);
  return svg(`${percent(row.error_rate)} ${m.toUpperCase()} ${language}`, "value");
});
