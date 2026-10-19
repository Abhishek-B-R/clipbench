import type { Aggregate, Run, SliceKind, Tier } from "./types";
import { aggregatesFor, defaultTier, overall, pair, primaryMetric, providerById } from "./analysis";
import { metricValue, CAPTION_METRICS } from "./metrics";
import { inSlice, streamPair, supports, v3 } from "./v3";

/**
 * "Pick a provider" for builders (docs/V2.md). Everything comes from the run's
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
  /** v2 (B22): batch files, or live captions over a realtime API (ranked on the streaming board). */
  mode?: "batch" | "streaming";
  /** v2 (B22): needs speaker labels. */
  diarization?: boolean;
}

export interface PickRow {
  provider: string;
  agg: Aggregate;
  price: number;
  p50: number | null;
  uploads: boolean;
  timing: number | null;
  halluc: number | null;
  /** Diarization error rate, when measured. */
  der: number | null;
  /** Streaming only: time to final segment, median ms. */
  ttfs: number | null;
  excluded: string[];
  vsPick?: { diff: number; ci_low: number; ci_high: number; p: number; n: number; tied: boolean } | null;
}

const AUDIO = ["any", "music", "no_music"] as const;
const LATENCY = ["any", "3000", "1500"] as const;
const MODES = ["batch", "streaming"] as const;

/**
 * Read the picker inputs from the URL. Hand-edited or stale links fall back to the defaults instead of
 * leaking into the page ("Up to n/a", no option selected): unknown audio or latency values become "any",
 * and a budget that is not a finite number of dollars at or above zero means no limit.
 */
export function parsePickParams(params: URLSearchParams): Pick<PickInput, "audio" | "budget" | "latency"> & { mode: "batch" | "streaming"; diarization: boolean } {
  const a = params.get("audio");
  const l = params.get("lat");
  const b = params.get("budget");
  const n = b == null || b.trim() === "" ? NaN : Number(b);
  return {
    audio: (AUDIO as readonly string[]).includes(a ?? "") ? (a as PickInput["audio"]) : "any",
    latency: (LATENCY as readonly string[]).includes(l ?? "") ? (l as LatencyNeed) : "any",
    budget: Number.isFinite(n) && n >= 0 ? n : null,
    mode: (MODES as readonly string[]).includes(params.get("mode") ?? "") ? (params.get("mode") as "batch" | "streaming") : "batch",
    diarization: params.get("diar") === "1",
  };
}

/** Whether a provider can label speakers: measured in the diarization section, else documented in the feature matrix. */
function canDiarize(run: Run, id: string): boolean | null {
  const d = v3(run).diarization;
  if (d?.unsupported.includes(id)) return false;
  if (d?.rows.some((r) => r.provider === id && r.der != null)) return true;
  return supports(v3(run).features, id, "diarization");
}

function derOf(run: Run, id: string, tier: Tier) {
  return inSlice(v3(run).diarization?.rows ?? [], tier).find((r) => r.provider === id)?.der ?? null;
}

const TIMING = CAPTION_METRICS.find((m) => m.key === "timing_within_200ms")!;
const HALLUC = CAPTION_METRICS.find((m) => m.key === "halluc_per_nonspeech_min")!;

export function pick(run: Run, input: PickInput) {
  if (input.mode === "streaming") return pickStreaming(run, input);
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
    if (input.diarization && canDiarize(run, agg.provider) !== true) excluded.push("No speaker labels");
    return { provider: agg.provider, agg, price: p?.usd_per_hour ?? 0, p50, uploads: Boolean(p?.uploads_audio), timing, halluc, der: derOf(run, agg.provider, tier), ttfs: null, excluded };
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
  return { rows: out, eligible, best, tied, cheaperTie, betterExcluded, kind, slice, tier, metric, fellBack, nClips: rows[0]?.n_clips ?? 0, mode: "batch" as const, paired: true };
}

/**
 * Streaming picks rank on the streaming board (A1), never on batch numbers: final error rate on the matching audio
 * slice, then the same exclusions. Ties come from the streaming paired table when the export has one; without it
 * nothing is called a tie (`paired: false`) and the page says so.
 */
function pickStreaming(run: Run, input: PickInput) {
  const tier = defaultTier(run);
  const metric = primaryMetric(run).name;
  const st = v3(run).streaming;
  let kind: SliceKind = input.audio === "any" ? "all" : "audio_condition";
  let slice = input.audio === "any" ? "all" : input.audio;
  let src = st ? inSlice(st.rows, tier, kind, slice) : [];
  const fellBack = src.length === 0 && kind !== "all" && Boolean(st);
  if (fellBack) {
    kind = "all";
    slice = "all";
    src = inSlice(st!.rows, tier);
  }
  const all = overall(run, tier, metric);
  const out: PickRow[] = src
    .filter((r) => r.error_rate != null)
    .map((r) => {
      const p = providerById(run, r.provider);
      const allRow = all.find((a) => a.provider === r.provider);
      // A stand-in aggregate so the page reads streaming rows the same way as batch ones.
      const agg = { ...(allRow ?? {}), provider: r.provider, model_id: r.model_id ?? p?.model_id ?? "", tier, metric, slice_kind: kind, slice, n_clips: r.n_clips ?? 0, error_rate: r.error_rate!, ci_low: r.ci_low ?? r.error_rate!, ci_high: r.ci_high ?? r.error_rate!, too_small: r.too_small } as Aggregate;
      const timing = metricValue(allRow, TIMING);
      const excluded: string[] = [];
      if (input.local) excluded.push("Sends audio to a server");
      if (input.budget != null && (p?.usd_per_hour ?? 0) > input.budget + 1e-9) excluded.push("Over budget");
      if (input.timing && timing == null && supports(v3(run).features, r.provider, "word_timestamps") !== true) excluded.push("No word timings");
      if (input.diarization && canDiarize(run, r.provider) !== true) excluded.push("No speaker labels");
      return { provider: r.provider, agg, price: p?.usd_per_hour ?? 0, p50: r.ttfs_p50_ms, uploads: true, timing, halluc: null, der: derOf(run, r.provider, tier), ttfs: r.ttfs_p50_ms, excluded };
    });
  out.sort((a, b) => a.agg.error_rate - b.agg.error_rate);
  const eligible = out.filter((r) => !r.excluded.length);
  const best = eligible[0];
  const paired = Boolean(st?.paired.length);
  if (best && st) {
    for (const r of out) {
      if (r === best) continue;
      const t = streamPair(st, tier, kind, slice, r.provider, best.provider);
      r.vsPick = t ? { diff: t.diff, ci_low: t.ci_low ?? t.diff, ci_high: t.ci_high ?? t.diff, p: t.p ?? 1, n: t.n ?? 0, tied: t.tied } : null;
    }
  }
  const tied = best ? eligible.filter((r) => r !== best && r.vsPick?.tied) : [];
  const cheaperTie = tied.filter((r) => r.price < (best?.price ?? 0)).sort((a, b) => a.price - b.price)[0];
  const betterExcluded = best ? out.filter((r) => r.excluded.length && r.agg.error_rate < best.agg.error_rate && r.vsPick && !r.vsPick.tied) : [];
  return { rows: out, eligible, best, tied, cheaperTie, betterExcluded, kind, slice, tier, metric, fellBack, nClips: src[0]?.n_clips ?? 0, mode: "streaming" as const, paired };
}
