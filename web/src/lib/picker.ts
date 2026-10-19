import type { Aggregate, Run, SliceKind } from "./types";
import { aggregatesFor, defaultTier, overall, pair, primaryMetric, providerById } from "./analysis";
import { metricValue, CAPTION_METRICS } from "./metrics";

/**
 * "Pick a provider" for builders (docs/REPOSITION-PROMPT.md, section 7). Everything comes from the run's
 * aggregates and paired tables: constraints exclude a model, they never reweight it, and the pick is then
 * paired-tested against every eligible model on the same slice.
 */
export type LatencyNeed = "any" | "3000" | "1500";

export interface PickInput {
  /** "music": music-heavy clips, "no_music": mostly speech, "any": all clips. */
  audio: "any" | "music" | "no_music";
  budget: number | null;
  timing: boolean;
  latency: LatencyNeed;
  local: boolean;
}

export interface PickRow {
  provider: string;
  agg: Aggregate;
  price: number;
  p50: number | null;
  uploads: boolean;
  timing: number | null;
  halluc: number | null;
  excluded: string[];
  vsPick?: { diff: number; ci_low: number; ci_high: number; p: number; n: number; tied: boolean } | null;
}

const AUDIO = ["any", "music", "no_music"] as const;
const LATENCY = ["any", "3000", "1500"] as const;

/**
 * Read the picker inputs from the URL. Hand-edited or stale links fall back to the defaults instead of
 * leaking into the page ("Up to n/a", no option selected): unknown audio or latency values become "any",
 * and a budget that is not a finite number of dollars at or above zero means no limit.
 */
export function parsePickParams(params: URLSearchParams): Pick<PickInput, "audio" | "budget" | "latency"> {
  const a = params.get("audio");
  const l = params.get("lat");
  const b = params.get("budget");
  const n = b == null || b.trim() === "" ? NaN : Number(b);
  return {
    audio: (AUDIO as readonly string[]).includes(a ?? "") ? (a as PickInput["audio"]) : "any",
    latency: (LATENCY as readonly string[]).includes(l ?? "") ? (l as LatencyNeed) : "any",
    budget: Number.isFinite(n) && n >= 0 ? n : null,
  };
}

const TIMING = CAPTION_METRICS.find((m) => m.key === "timing_within_200ms")!;
const HALLUC = CAPTION_METRICS.find((m) => m.key === "halluc_per_nonspeech_min")!;

export function pick(run: Run, input: PickInput) {
  const tier = defaultTier(run);
  const metric = primaryMetric(run).name;
  let kind: SliceKind = input.audio === "any" ? "all" : "audio_condition";
  let slice = input.audio === "any" ? "all" : input.audio;
  let rows = aggregatesFor(run, { tier, metric, kind, slice });
  // Older exports have no audio-condition slices: fall back to all clips and say so.
  const fellBack = rows.length === 0 && kind !== "all";
  if (fellBack) {
    kind = "all";
    slice = "all";
    rows = aggregatesFor(run, { tier, metric, kind, slice });
  }
  const all = overall(run, tier, metric);
  const out: PickRow[] = rows.map((agg) => {
    const p = providerById(run, agg.provider);
    const allRow = all.find((a) => a.provider === agg.provider);
    const p50 = allRow?.latency_p50 ?? null;
    const timing = metricValue(agg, TIMING) ?? metricValue(allRow, TIMING);
    const halluc = metricValue(agg, HALLUC);
    const excluded: string[] = [];
    if (input.local && p?.uploads_audio) excluded.push("Sends audio to a server");
    if (input.budget != null && (p?.usd_per_hour ?? 0) > input.budget + 1e-9) excluded.push("Over budget");
    if (input.latency !== "any" && (p50 == null || p50 > Number(input.latency))) excluded.push("Too slow");
    if (input.timing && timing == null) excluded.push("No word timings");
    return { provider: agg.provider, agg, price: p?.usd_per_hour ?? 0, p50, uploads: Boolean(p?.uploads_audio), timing, halluc, excluded };
  });
  out.sort((a, b) => a.agg.error_rate - b.agg.error_rate);
  const eligible = out.filter((r) => !r.excluded.length);
  const best = eligible[0];
  if (best) {
    for (const r of out) {
      if (r === best) continue;
      const t = pair(run, { tier, metric, kind, slice }, r.provider, best.provider);
      r.vsPick = t ? { diff: t.diff, ci_low: t.ci_low, ci_high: t.ci_high, p: t.p, n: t.n, tied: t.better == null } : null;
    }
  }
  const tied = best ? eligible.filter((r) => r !== best && r.vsPick?.tied) : [];
  const cheaperTie = tied.filter((r) => r.price < (best?.price ?? 0)).sort((a, b) => a.price - b.price)[0];
  const betterExcluded = best ? out.filter((r) => r.excluded.length && r.agg.error_rate < best.agg.error_rate && r.vsPick && !r.vsPick.tied) : [];
  return { rows: out, eligible, best, tied, cheaperTie, betterExcluded, kind, slice, tier, metric, fellBack, nClips: rows[0]?.n_clips ?? 0 };
}
