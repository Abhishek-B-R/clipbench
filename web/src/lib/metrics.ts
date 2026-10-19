import type { Aggregate, FindingV2, Run, SliceKind, Tier } from "./types";
import { TAG_LABEL, TAG_ORDER } from "./analysis";
import { CATEGORIES, CONDITION_LABEL, CONDITION_ORDER } from "./config";
import { ms, pct } from "./format";

/**
 * v2 caption metrics (METHODOLOGY.md, "Caption metrics"). One descriptor per metric so every table and
 * figure labels, formats and orders them the same way. A null value means n/a (no word timings, no
 * punctuated clips, no code-switch spans), never zero.
 */
export interface CaptionMetric {
  key: "timing_within_200ms" | "timing_median_ms" | "timing_p90_ms" | "halluc_per_nonspeech_min" | "punct_f1" | "casing_acc" | "codeswitch_error_rate";
  label: string;
  short: string;
  help: string;
  better: "higher" | "lower";
  nKey: "timing_n_clips" | "nonspeech_n_clips" | "punct_n_clips" | "codeswitch_n_clips";
  fmt: (v: number) => string;
}

export const CAPTION_METRICS: CaptionMetric[] = [
  { key: "timing_within_200ms", label: "Words on time", short: "On time", help: "Share of correctly heard words that start within 200 ms of when they are said.", better: "higher", nKey: "timing_n_clips", fmt: (v) => pct(v) },
  { key: "timing_median_ms", label: "Timing offset, median", short: "Offset", help: "Median distance between when a word starts and when the model says it starts.", better: "lower", nKey: "timing_n_clips", fmt: (v) => ms(v) },
  { key: "halluc_per_nonspeech_min", label: "Invented words per non-speech minute", short: "Invented / min", help: "Words a model writes where nobody speaks (music, effects, silence), per minute of that audio.", better: "lower", nKey: "nonspeech_n_clips", fmt: (v) => v.toFixed(2) },
  { key: "punct_f1", label: "Punctuation F1", short: "Punct. F1", help: "Sentence ends and commas in the right places, on clips whose verified text is punctuated.", better: "higher", nKey: "punct_n_clips", fmt: (v) => v.toFixed(2) },
  { key: "casing_acc", label: "Casing accuracy", short: "Casing", help: "Correct capitalisation on words written in Latin letters.", better: "higher", nKey: "punct_n_clips", fmt: (v) => pct(v) },
  { key: "codeswitch_error_rate", label: "Code-switch error rate", short: "Code-switch", help: "Error rate on the spans where the speaker switches language, scored separately.", better: "lower", nKey: "codeswitch_n_clips", fmt: (v) => pct(v) },
];

export function metricValue(a: Aggregate | undefined, m: CaptionMetric): number | null {
  const v = a?.[m.key];
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

export function metricN(a: Aggregate | undefined, m: CaptionMetric): number | null {
  const v = a?.[m.nKey];
  return typeof v === "number" ? v : null;
}

/** Whether a run carries any v2 caption metric at all (older exports do not). */
export function hasCaptionMetrics(run: Run) {
  return run.aggregates.some((a) => CAPTION_METRICS.some((m) => metricValue(a, m) != null));
}

/** Best provider id for a metric among rows with a value. */
export function bestFor(rows: Aggregate[], m: CaptionMetric) {
  const xs = rows.filter((r) => metricValue(r, m) != null);
  xs.sort((a, b) => (m.better === "higher" ? metricValue(b, m)! - metricValue(a, m)! : metricValue(a, m)! - metricValue(b, m)!));
  return xs[0]?.provider;
}

// ---------------------------------------------------------------- slices

export const SLICE_KIND_LABEL: Record<SliceKind, string> = { all: "All clips", condition: "Condition", audio_condition: "Audio", category: "Category", tag: "Clip type", duration: "Length" };
export const AUDIO_LABEL: Record<string, string> = { music: "With music", no_music: "Without music" };

export { CONDITION_LABEL, CONDITION_ORDER };

export function sliceLabel(kind: string, slice: string) {
  if (kind === "all") return "All clips";
  if (kind === "tag") return TAG_LABEL[slice] ?? slice;
  if (kind === "category") return CATEGORIES[slice] ?? (slice === "other" ? "Other" : slice);
  if (kind === "audio_condition") return AUDIO_LABEL[slice] ?? slice;
  if (kind === "condition") return CONDITION_LABEL[slice] ?? slice.replace(/_/g, " ");
  return slice;
}

const DURATION_ORDER = ["<15s", "15-30s", "30-60s", "60-90s"];

/** Slice values of one kind that have data in this run, in a stable display order. */
export function slicesOf(run: Run, tier: Tier, kind: SliceKind): string[] {
  if (kind === "all") return ["all"];
  const have = [...new Set(run.aggregates.filter((a) => a.tier === tier && a.slice_kind === kind).map((a) => a.slice))];
  const order = kind === "tag" ? TAG_ORDER : kind === "duration" ? DURATION_ORDER : kind === "audio_condition" ? ["music", "no_music"] : kind === "condition" ? CONDITION_ORDER : Object.keys(CATEGORIES).concat("other");
  return have.sort((a, b) => (order.indexOf(a) === -1 ? 99 : order.indexOf(a)) - (order.indexOf(b) === -1 ? 99 : order.indexOf(b)));
}

/** Slice kinds that have data in this run. */
export function sliceKindsIn(run: Run, tier: Tier): SliceKind[] {
  const kinds = new Set(run.aggregates.filter((a) => a.tier === tier).map((a) => a.slice_kind));
  return (["all", "condition", "category", "audio_condition", "tag", "duration"] as SliceKind[]).filter((k) => kinds.has(k));
}

// ---------------------------------------------------------------- findings

/** Deep link to the exact table and filters a finding came from. */
export function findingHref(f: FindingV2, lang: string) {
  const fl = f.source?.filters ?? {};
  const q = new URLSearchParams();
  const put = (k: string, v: unknown) => v != null && v !== "" && q.set(k, String(v));
  put("tier", fl.tier);
  put("metric", fl.metric);
  if (fl.slice_kind && fl.slice_kind !== "all") {
    put("slice_kind", fl.slice_kind);
    put("slice", fl.slice);
  }
  if (f.source?.table === "paired" && fl.provider_a && fl.provider_b) {
    q.set("m", `${fl.provider_a},${fl.provider_b}`);
    return `/compare?${q}`;
  }
  put("sort", fl.sort);
  put("provider", fl.provider);
  q.set("lang", lang);
  return `/leaderboard?${q}`;
}

/** Plain description of a finding's source, for the mono source line. */
export function findingSource(f: FindingV2) {
  const fl = f.source?.filters ?? {};
  const parts = Object.entries(fl)
    .filter(([, v]) => v != null && v !== "")
    .map(([k, v]) => `${k}=${v}`);
  return `source: ${f.source?.table ?? "aggregates"}${parts.length ? `, ${parts.join(", ")}` : ""}`;
}
