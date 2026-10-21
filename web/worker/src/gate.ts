/**
 * Publication gate, read from the export (docs/V3.md, "Publication gate fields"). The Worker never re-derives
 * publication, eligibility or ranking order: `languages[].published` and `gate`, `providers_eligible`,
 * `providers_excluded`, aggregates `eligible`/`coverage` and the top-level `ranking` are written by
 * `clipbench export`. Missing fields (older exports) fail closed: not published, nothing ranked.
 * Pure functions, no Hono, so the web tests can import them too.
 */
import { percent } from "./badge";

export type Obj = Record<string, unknown>;

export type Aggregate = {
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
  eligible?: boolean | null;
  coverage?: number | null;
  ci_low_cluster?: number | null;
  ci_high_cluster?: number | null;
  n_channels?: number | null;
};
export type Provider = { id: string; label?: string; model_id?: string | null };
export type Exclusion = { provider: string; coverage?: number | null; n_scored?: number | null; n_skipped?: number | null; n_error?: number | null; reason?: string | null };
export type Status = {
  lang: string;
  published?: boolean;
  verified_clips?: number;
  providers_ok?: number;
  providers_eligible?: string[];
  providers_excluded?: Exclusion[];
  min_coverage?: number;
  verified_diversity?: { channels?: number; largest_channel_share?: number | null; categories?: string[]; passes?: boolean; problems?: string[] } & Obj;
  gate?: { verified_clips_ok?: boolean; providers_ok?: boolean; diversity_ok?: boolean };
} & Obj;
export type RankEntry = { provider: string; rank: number; error_rate: number; ci_low: number; ci_high: number; n_clips: number };
export type Run = {
  run_id: string;
  created_at?: string;
  language: string;
  metrics?: { name: string; primary?: boolean; label?: string }[];
  providers?: Provider[];
  aggregates?: Aggregate[];
  languages?: Status[];
  ranking?: Record<string, Record<string, RankEntry[]>> | null;
  history?: { series?: Record<string, Obj[]> } & Obj;
  drift?: (Obj & { provider?: string })[] | null;
  reliability?: { run?: Obj[]; all_runs?: Obj[] } | null;
};

/** The exporter's status for a language, with `published` true only when every gate flag is true. */
export function status(run: Run | null, language: string): Status {
  const st = run?.languages?.find((s) => s.lang === language);
  const g = st?.gate;
  const flags = Boolean(g && g.verified_clips_ok === true && g.providers_ok === true && g.diversity_ok === true);
  return { lang: language, verified_clips: 0, providers_ok: 0, ...(st ?? {}), published: st?.published === true && flags };
}

export const isPublished = (run: Run | null, language: string) => status(run, language).published === true;

/** Providers listed in `providers_eligible` whose aggregates rows all say `eligible: true`. */
export function eligible(run: Run, language: string): Set<string> {
  const listed = status(run, language).providers_eligible;
  const rows = run.aggregates ?? [];
  const flagged = new Set(rows.filter((a) => a.eligible === true).map((a) => a.provider));
  const denied = new Set(rows.filter((a) => a.eligible !== true).map((a) => a.provider));
  return new Set((Array.isArray(listed) ? listed : []).filter((p) => flagged.has(p) && !denied.has(p)));
}

export function primaryMetric(run: Run): string | undefined {
  return run.metrics?.find((m) => m.primary)?.name ?? run.metrics?.[0]?.name;
}

/** The exporter's ranking (slice all), eligible providers of a published language only. */
export function ranking(run: Run, language: string, tier: string, metric: string): RankEntry[] {
  if (!isPublished(run, language)) return [];
  const rows = run.ranking?.[tier]?.[metric];
  if (!Array.isArray(rows)) return [];
  const ok = eligible(run, language);
  return rows.filter((r) => ok.has(r.provider)).sort((a, b) => a.rank - b.rank);
}

const coverageOf = (run: Run, language: string, id: string): number | null => {
  const ex = status(run, language).providers_excluded?.find((e) => e.provider === id);
  if (typeof ex?.coverage === "number") return ex.coverage;
  const row = (run.aggregates ?? []).find((a) => a.provider === id && typeof a.coverage === "number");
  return row ? (row.coverage as number) : null;
};

const NO_GATE = "This export has no eligibility data, so the provider is not ranked.";

/** Every provider seen in the run that is not eligible, with coverage and the exporter's reason. */
export function notRanked(run: Run, language: string): { provider: string; label: string; coverage: number | null; reason: string }[] {
  const ok = eligible(run, language);
  const labels = new Map((run.providers ?? []).map((p) => [p.id, p.label ?? p.id]));
  const listed = (status(run, language).providers_excluded ?? []).filter((e) => typeof e?.provider === "string" && !ok.has(e.provider));
  const seen = new Set(listed.map((e) => e.provider));
  const others = [...new Set([...(run.providers ?? []).map((p) => p.id), ...(run.aggregates ?? []).map((a) => a.provider)])].filter((p) => !ok.has(p) && !seen.has(p));
  return [
    ...listed.map((e) => ({ provider: e.provider, label: labels.get(e.provider) ?? e.provider, coverage: typeof e.coverage === "number" ? e.coverage : null, reason: e.reason ?? NO_GATE })),
    ...others.map((p) => ({ provider: p, label: labels.get(p) ?? p, coverage: coverageOf(run, language, p), reason: NO_GATE })),
  ].sort((a, b) => (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0));
}

/**
 * Leaderboard rows for a tier, metric and slice. Slice all follows `ranking`; other slices use the exporter's rule
 * (error rate ascending, ties by provider id) over eligible providers. Empty when unpublished.
 */
export function leaderboard(run: Run, metric: string, tier: string, sliceKind: string, slice: string) {
  const language = run.language;
  if (!isPublished(run, language)) return [];
  const labels = new Map((run.providers ?? []).map((p) => [p.id, p.label ?? p.id]));
  const ok = eligible(run, language);
  const inSlice = (run.aggregates ?? []).filter((a) => a.tier === tier && a.metric === metric && a.slice_kind === sliceKind && a.slice === slice && ok.has(a.provider));
  let ordered: Aggregate[];
  if (sliceKind === "all" && slice === "all") {
    ordered = ranking(run, language, tier, metric).flatMap((r) => inSlice.filter((a) => a.provider === r.provider).slice(0, 1));
  } else {
    ordered = inSlice
      .filter((a) => typeof a.error_rate === "number")
      .sort((a, b) => (a.error_rate as number) - (b.error_rate as number) || (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0));
  }
  return ordered.map((a, i) => ({
    rank: i + 1,
    provider: a.provider,
    label: labels.get(a.provider) ?? a.provider,
    model_id: a.model_id ?? null,
    error_rate: a.error_rate,
    ci_low: a.ci_low ?? null,
    ci_high: a.ci_high ?? null,
    n_clips: a.n_clips ?? null,
    too_small: a.too_small ?? null,
    coverage: a.coverage ?? null,
    ci_low_cluster: a.ci_low_cluster ?? null,
    ci_high_cluster: a.ci_high_cluster ?? null,
    n_channels: a.n_channels ?? null,
  }));
}

/** Eligibility, coverage and rank (verified tier, primary metric) of one provider in a published run. */
export function standing(run: Run, language: string, id: string) {
  const ok = eligible(run, language).has(id);
  const metric = primaryMetric(run);
  const rank = ok && metric ? (ranking(run, language, "verified", metric).find((r) => r.provider === id)?.rank ?? null) : null;
  const ex = ok ? null : notRanked(run, language).find((e) => e.provider === id);
  return { eligible: ok, coverage: coverageOf(run, language, id), rank, reason: ex?.reason ?? null };
}

export type BadgeAnswer = { message: string; tone: "value" | "muted"; code: number };

/**
 * Badge text for a provider and language: the error rate only for an eligible, ranked provider of a published
 * language; otherwise its status, never a number.
 */
export function badgeMessage(run: Run | null, id: string, language: string, metric?: string): BadgeAnswer {
  if (!run || !isPublished(run, language)) return { message: `not published ${language}`, tone: "muted", code: 200 };
  const m = metric ?? primaryMetric(run);
  const known = (run.providers ?? []).some((p) => p.id === id) || (run.aggregates ?? []).some((a) => a.provider === id);
  if (!m || !known) return { message: `no data ${language}`, tone: "muted", code: 404 };
  if (!eligible(run, language).has(id)) return { message: `not ranked ${language}`, tone: "muted", code: 200 };
  const row = ranking(run, language, "verified", m).find((r) => r.provider === id);
  if (!row || typeof row.error_rate !== "number") return { message: `no data ${language}`, tone: "muted", code: 404 };
  return { message: `${percent(row.error_rate)} ${m.toUpperCase()} ${language}`, tone: "value", code: 200 };
}
