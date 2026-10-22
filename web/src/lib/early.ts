import type { Aggregate, Clip, Run, Tier } from "./types";
import { defaultTier, median, perClipMatrix, primaryMetric, providerById, providerLabel, runLanguageStatus, stabilityFor } from "./analysis";
import { isObj } from "./v3";
import { RULES } from "./config";

/**
 * The early look: every system of the latest run on one tier (usually creator captions, unverified), sorted by error
 * rate, with no rank numbers. This is NOT the ranking: the publication gate (docs/V3.md) still decides when a
 * language gets one, and ranking views keep reading only `ranking` / `eligible` through analysis.ts. Everything here
 * is read from the export (aggregates, per_clip, stability, languages[].providers_excluded, run_meta, errors).
 */

/** The label every early-look view carries, word for word. */
export const EARLY_LABEL = "Early look: scored against creator captions, which are not verified. Not a ranking.";

/** Why creator captions favour YouTube auto captions (README, "First run"; also the export's limitations). */
export const YOUTUBE_NOTE =
  "Many creators start their captions from YouTube's own auto captions, so scoring against creator captions favours YouTube auto captions; only captions corrected by ear are fair ground truth.";

export interface LatencyMode {
  /** True when providers were timed one at a time; false when they ran at once (contended); null when unknown. */
  sequential: boolean | null;
  /** The exporter's sentence, e.g. "Latency was measured one provider at a time (...)". */
  text: string | null;
  short: string | null;
}

/** run_meta.latency_mode as written by the exporter. Without it, latency is treated as not comparable. */
export function latencyMode(run: Run): LatencyMode {
  const m = isObj(run.run_meta) ? run.run_meta : {};
  const lm = isObj(m.latency_mode) ? m.latency_mode : null;
  if (!lm) return { sequential: null, text: null, short: null };
  const seq = lm.sequential_providers === true && lm.contended !== true ? true : lm.sequential_providers === false || lm.contended === true ? false : null;
  return { sequential: seq, text: typeof lm.text === "string" ? lm.text : null, short: typeof lm.short === "string" ? lm.short : null };
}

/**
 * Why a provider covers fewer clips, from the exporter's exclusion reason ("...; on the clips it was given: not run
 * on 122 (account quota)"). Returns the part after "on the clips it was given:", or null when coverage is full or
 * the export says nothing about it.
 */
export function partialReason(run: Run, provider: string): string | null {
  const st = run.languages?.find((l) => l.lang === run.language);
  const reason = st?.providers_excluded?.find((e) => e.provider === provider)?.reason ?? "";
  const m = /on the clips it was given:\s*(.+)$/i.exec(reason);
  return m ? m[1]!.trim().replace(/\.$/, "") : null;
}

/** The short cause inside the reason's parentheses ("account quota", "no auto track"), or null. */
export function partialCause(reason: string | null): string | null {
  if (!reason) return null;
  const m = /\(([^)]+)\)/.exec(reason);
  return m ? m[1]!.trim() : null;
}

export interface EarlyRow {
  provider: string;
  label: string;
  model_id: string;
  agg: Aggregate;
  error_rate: number;
  ci_low: number;
  ci_high: number;
  /** Clips this system has a score on, and the clips of the tier. */
  n_clips: number;
  total: number;
  partial: string | null;
  cost_per_hour: number | null;
  /** List price per audio hour (providers[].usd_per_hour). */
  list_usd_per_hour: number | null;
  /** Median latency per clip, ms; null when latency was not measured sequentially or the system has none. */
  latency_p50: number | null;
  hallucination_rate: number;
  long_output_rate: number;
  stability: { n: number; repeats: number; disagreement: number } | null;
}

export interface EarlyLook {
  run: Run;
  tier: Tier;
  metric: string;
  metricLabel: string;
  rows: EarlyRow[];
  total: number;
  channels: number;
  latency: LatencyMode;
  published: boolean;
}

const cache = new WeakMap<Run, Map<string, EarlyLook>>();

/**
 * Every system on one tier and metric (slice all), sorted by error rate ascending, ties by provider id. Rows carry no
 * rank. Systems without a finite error rate on this tier are left out.
 */
export function earlyLook(run: Run, tier: Tier = defaultTier(run), metric = primaryMetric(run).name): EarlyLook {
  const key = `${tier}|${metric}`;
  let byKey = cache.get(run);
  const hit = byKey?.get(key);
  if (hit) return hit;
  const clips = run.clips.filter((c) => c.tier === tier);
  const latency = latencyMode(run);
  const rows: EarlyRow[] = run.aggregates
    .filter((a) => a.tier === tier && a.metric === metric && a.slice_kind === "all" && a.slice === "all" && Number.isFinite(a.error_rate))
    .map((a) => {
      const p = providerById(run, a.provider);
      const st = stabilityFor(run, a.provider);
      return {
        provider: a.provider,
        label: providerLabel(run, a.provider),
        model_id: a.model_id,
        agg: a,
        error_rate: a.error_rate,
        ci_low: a.ci_low,
        ci_high: a.ci_high,
        n_clips: a.n_clips,
        total: clips.length,
        partial: a.n_clips < clips.length ? partialReason(run, a.provider) : null,
        cost_per_hour: a.cost_per_hour,
        list_usd_per_hour: p?.usd_per_hour ?? null,
        latency_p50: latency.sequential ? a.latency_p50 : null,
        hallucination_rate: a.hallucination_rate,
        long_output_rate: a.long_output_rate,
        stability: st ? { n: st.n, repeats: st.repeats, disagreement: st.disagreement } : null,
      };
    })
    .sort((a, b) => a.error_rate - b.error_rate || (a.provider < b.provider ? -1 : a.provider > b.provider ? 1 : 0));
  const out: EarlyLook = {
    run,
    tier,
    metric,
    metricLabel: run.metrics.find((m) => m.name === metric)?.label ?? metric,
    rows,
    total: clips.length,
    channels: new Set(clips.map((c) => c.channel)).size,
    latency,
    published: runLanguageStatus(run).published,
  };
  if (!byKey) cache.set(run, (byKey = new Map()));
  byKey.set(key, out);
  return out;
}

export function earlyRowOf(run: Run, provider: string, tier?: Tier, metric?: string): EarlyRow | null {
  return earlyLook(run, tier, metric).rows.find((r) => r.provider === provider) ?? null;
}

// ---------------------------------------------------------------- flagged references

export interface Flagged {
  ids: Set<string>;
  /** Error threshold every system was above. */
  threshold: number;
  /** "export" when errors.all_fail was exported, "per_clip" when derived here with the same rule. */
  source: "export" | "per_clip";
}

/**
 * Clips flagged for re-review: every system with a result is above the all-fail threshold and at least two have a
 * result (clipbench/analysis/errors.py, all_fail). Read from `errors.all_fail` when the export carries it; else
 * derived from per_clip with the same rule.
 */
export function flaggedClips(run: Run, tier: Tier = defaultTier(run)): Flagged {
  const ex = run.errors as unknown as { all_fail?: unknown; all_fail_threshold?: unknown };
  const threshold = typeof ex?.all_fail_threshold === "number" ? ex.all_fail_threshold : RULES.allFailThreshold;
  if (Array.isArray(ex?.all_fail)) return { ids: new Set(ex.all_fail.filter((x): x is string => typeof x === "string")), threshold, source: "export" };
  const m = perClipMatrix(run, tier, primaryMetric(run).name);
  const ids = new Set<string>();
  for (const [id, row] of m) {
    const xs = [...row.values()].filter((v) => Number.isFinite(v));
    if (xs.length >= 2 && xs.every((v) => v > threshold)) ids.add(id);
  }
  return { ids, threshold, source: "per_clip" };
}

// ---------------------------------------------------------------- per clip

/** Top of the per-clip colour scale (error rate); everything above it is the darkest step. A display choice. */
export const CLIP_HEAT_MAX = 0.4;

export interface ClipScores {
  clip: Clip;
  errs: Map<string, number>;
  med: number;
  /** Systems on this clip, worst first. */
  worst: { provider: string; err: number }[];
  flagged: boolean;
}

/** Every clip of a tier with its per-system errors, hardest (highest median) first. */
export function clipScores(run: Run, tier: Tier = defaultTier(run), metric = primaryMetric(run).name): ClipScores[] {
  const m = perClipMatrix(run, tier, metric);
  const fl = flaggedClips(run, tier).ids;
  return run.clips
    .filter((c) => c.tier === tier)
    .map((c) => {
      const errs = m.get(c.video_id) ?? new Map<string, number>();
      const worst = [...errs].map(([provider, err]) => ({ provider, err })).sort((a, b) => b.err - a.err);
      return { clip: c, errs, med: errs.size ? median([...errs.values()]) : NaN, worst, flagged: fl.has(c.video_id) };
    })
    .sort((a, b) => (Number.isNaN(b.med) ? -1 : b.med) - (Number.isNaN(a.med) ? -1 : a.med));
}

/** The platform id of a clip (windows are `<id>@<start>-<end>`). */
export function videoIdOf(clipId: string) {
  return clipId.split("@")[0]!;
}

/** YouTube's public thumbnail for a clip's source video (i.ytimg.com, allowed by the CSP in public/_headers). */
export function thumbUrl(clipId: string, size: "mqdefault" | "hqdefault" = "mqdefault") {
  return `https://i.ytimg.com/vi/${encodeURIComponent(videoIdOf(clipId))}/${size}.jpg`;
}

// ---------------------------------------------------------------- run totals

/** Sum of the run's metered cost over every system's scored pass (primary metric, slice all, one tier). */
export function runSpend(run: Run, tier: Tier = defaultTier(run)) {
  const metric = primaryMetric(run).name;
  return run.aggregates.filter((a) => a.tier === tier && a.metric === metric && a.slice_kind === "all" && a.slice === "all").reduce((t, a) => t + (a.cost_usd_total ?? 0), 0);
}

/** Scored transcriptions in the main pass (per_clip rows on the primary metric). */
export function scoredTranscriptions(run: Run, tier: Tier = defaultTier(run)) {
  const metric = primaryMetric(run).name;
  return run.per_clip.filter((r) => r.tier === tier && r.metric === metric && Number.isFinite(r.error_rate)).length;
}
