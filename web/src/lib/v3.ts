import type { Run, Tier } from "./types";

/**
 * v3 export sections (docs/V3.md, "Export additions"): streaming, semantic, drift, history, sweeps, formatting,
 * lattice, longform, compute, diarization, lid, entities, style, reliability, captions, features, and run_meta.
 *
 * Every key is optional and nullable, and the Python side is still settling the exact shapes, so nothing here
 * trusts the input: each section accepts a bare array of rows or an object holding them (`aggregates`, `rows`,
 * `points`, ...), reads each field under a few likely names, and turns anything that is not a finite number into
 * null (n/a), never 0. A section that cannot be read becomes null and its view is hidden. Expected shapes, with
 * the aliases accepted, are listed in docs/design/system.md ("v3 data").
 */

// ---------------------------------------------------------------- primitives

type Obj = Record<string, unknown>;

export function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

/** Finite number, or a numeric string; anything else is null. */
export function num(v: unknown): number | null {
  if (typeof v === "number") return Number.isFinite(v) ? v : null;
  if (typeof v === "string" && v.trim() !== "") {
    const n = Number(v);
    return Number.isFinite(n) ? n : null;
  }
  return null;
}

export function str(v: unknown): string | null {
  if (typeof v === "string") return v.trim() === "" ? null : v;
  if (typeof v === "number" && Number.isFinite(v)) return String(v);
  return null;
}

export function bool(v: unknown): boolean | null {
  if (typeof v === "boolean") return v;
  if (v === 1 || v === "true" || v === "yes") return true;
  if (v === 0 || v === "false" || v === "no") return false;
  return null;
}

/** First number found under any of the names. */
function n(o: Obj, ...names: string[]): number | null {
  for (const k of names) {
    const v = num(o[k]);
    if (v != null) return v;
  }
  return null;
}

function s(o: Obj, ...names: string[]): string | null {
  for (const k of names) {
    const v = str(o[k]);
    if (v != null) return v;
  }
  return null;
}

function b(o: Obj, ...names: string[]): boolean | null {
  for (const k of names) {
    const v = bool(o[k]);
    if (v != null) return v;
  }
  return null;
}

/** Share 0..1. Values above 1 and at most 100 are read as percentages (an exporter writing 82 for 82%). */
function share(v: number | null): number | null {
  if (v == null) return null;
  if (v > 1 && v <= 100) return v / 100;
  return v < 0 ? null : v;
}

/** Rows of a section: a bare array, or the first array found under one of the keys. */
export function rowsOf(section: unknown, keys: string[] = ["aggregates", "rows", "data", "items"]): Obj[] {
  if (Array.isArray(section)) return section.filter(isObj);
  if (!isObj(section)) return [];
  for (const k of keys) {
    const v = section[k];
    if (Array.isArray(v)) return v.filter(isObj);
  }
  return [];
}

export interface Interval {
  low: number | null;
  high: number | null;
}

/** 95% interval under `<prefix>ci_low` / `<prefix>ci_high`, `ci_low_<suffix>`, or `ci: [lo, hi]` / `{low, high}`. */
function interval(o: Obj, prefix = "", suffix = ""): Interval {
  const lo = n(o, `${prefix}ci_low${suffix}`, `${prefix}low${suffix}`);
  const hi = n(o, `${prefix}ci_high${suffix}`, `${prefix}high${suffix}`);
  if (lo != null || hi != null) return { low: lo, high: hi };
  const ci = o[`${prefix}ci${suffix}`];
  if (Array.isArray(ci)) return { low: num(ci[0]), high: num(ci[1]) };
  if (isObj(ci)) return { low: n(ci, "low", "ci_low", "lo"), high: n(ci, "high", "ci_high", "hi") };
  return { low: null, high: null };
}

// ---------------------------------------------------------------- base row

/** Columns every feature row carries (docs/V3.md): provider, model_id, tier, language, slice_kind, slice, n_clips. */
export interface BaseRow {
  provider: string;
  model_id: string | null;
  /** null when the row does not say (then it matches any tier). */
  tier: Tier | null;
  language: string | null;
  slice_kind: string;
  slice: string;
  n_clips: number | null;
  too_small: boolean;
}

function base(o: Obj, minClips: number): BaseRow | null {
  const provider = s(o, "provider", "provider_id", "id");
  if (!provider) return null;
  const tier = s(o, "tier");
  const nClips = n(o, "n_clips", "n", "clips");
  return {
    provider,
    model_id: s(o, "model_id", "model"),
    tier: tier === "verified" || tier === "creator" ? tier : null,
    language: s(o, "language", "lang"),
    slice_kind: s(o, "slice_kind") ?? "all",
    slice: s(o, "slice") ?? "all",
    n_clips: nClips,
    too_small: b(o, "too_small") ?? (nClips != null && nClips < minClips),
  };
}

function baseRows<T>(section: unknown, read: (o: Obj, base: BaseRow) => T, keys?: string[], minClips = 15): (BaseRow & T)[] {
  const out: (BaseRow & T)[] = [];
  for (const o of rowsOf(section, keys)) {
    const bse = base(o, minClips);
    if (bse) out.push({ ...bse, ...read(o, bse) });
  }
  return out;
}

/** Rows for one tier and slice. Rows that name no tier match every tier. */
export function inSlice<T extends BaseRow>(rows: T[], tier: Tier, kind = "all", slice = "all"): T[] {
  const matching = rows.filter((r) => r.slice_kind === kind && r.slice === slice);
  const exact = matching.filter((r) => r.tier === tier);
  return exact.length ? exact : matching.filter((r) => r.tier == null);
}

/** Slice kinds and values present in a section, for its filter. */
export function slicesIn(rows: BaseRow[], tier: Tier): { kind: string; slice: string }[] {
  const seen = new Set<string>();
  const out: { kind: string; slice: string }[] = [];
  for (const r of rows) {
    if (r.tier != null && r.tier !== tier) continue;
    const k = `${r.slice_kind}\u0000${r.slice}`;
    if (!seen.has(k)) {
      seen.add(k);
      out.push({ kind: r.slice_kind, slice: r.slice });
    }
  }
  return out;
}

function meta(section: unknown): Obj {
  return isObj(section) ? section : {};
}

// ---------------------------------------------------------------- A1 streaming

export interface StreamRow extends BaseRow {
  metric: string | null;
  error_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  /** Time to first partial, ms after the first audio byte was sent. */
  ttfp_p50_ms: number | null;
  ttfp_p95_ms: number | null;
  /** Time to final segment: ms from the end of a spoken segment to its final transcript. */
  ttfs_p50_ms: number | null;
  ttfs_p95_ms: number | null;
  /** Share 0..1 of partial words that survive unchanged into the final transcript. */
  stability: number | null;
  stability_ci_low: number | null;
  stability_ci_high: number | null;
}

export interface StreamPaired {
  tier: Tier | null;
  slice_kind: string;
  slice: string;
  provider_a: string;
  provider_b: string;
  diff: number;
  ci_low: number | null;
  ci_high: number | null;
  p_value: number | null;
  n_clips: number | null;
  verdict: string | null;
}

export interface Streaming {
  rows: StreamRow[];
  paired: StreamPaired[];
  run_id: string | null;
  created_at: string | null;
  /** How audio was fed, for example "real-time". */
  pace: string | null;
  note: string | null;
}

function parseStreaming(section: unknown): Streaming | null {
  const rows = baseRows(section, (o) => {
    const ci = interval(o);
    const st = interval(o, "stability_");
    const rewrite = share(n(o, "rewrite_rate", "partial_rewrite_rate", "rewrites"));
    return {
      metric: s(o, "metric"),
      error_rate: n(o, "error_rate", "final_error_rate", "cer", "wer"),
      ci_low: ci.low,
      ci_high: ci.high,
      ttfp_p50_ms: n(o, "ttfp_p50_ms", "ttfp_ms_p50", "ttf_partial_ms_p50", "time_to_first_partial_ms_p50", "ttfp_ms", "ttf_partial_ms"),
      ttfp_p95_ms: n(o, "ttfp_p95_ms", "ttfp_ms_p95", "ttf_partial_ms_p95", "time_to_first_partial_ms_p95"),
      ttfs_p50_ms: n(o, "ttfs_p50_ms", "ttfs_ms_p50", "time_to_final_ms_p50", "ttfs_ms", "ttfs"),
      ttfs_p95_ms: n(o, "ttfs_p95_ms", "ttfs_ms_p95", "time_to_final_ms_p95"),
      stability: share(n(o, "stability", "partial_stability", "stability_rate")) ?? (rewrite != null ? 1 - rewrite : null),
      stability_ci_low: share(st.low),
      stability_ci_high: share(st.high),
    };
  });
  if (!rows.length) return null;
  const m = meta(section);
  const paired: StreamPaired[] = [];
  for (const o of rowsOf(m.paired)) {
    const a = s(o, "provider_a");
    const bb = s(o, "provider_b");
    const d = n(o, "diff");
    if (!a || !bb || d == null) continue;
    const ci = interval(o);
    const tier = s(o, "tier");
    paired.push({ tier: tier === "verified" || tier === "creator" ? tier : null, slice_kind: s(o, "slice_kind") ?? "all", slice: s(o, "slice") ?? "all", provider_a: a, provider_b: bb, diff: d, ci_low: ci.low, ci_high: ci.high, p_value: n(o, "p_value", "p"), n_clips: n(o, "n_clips"), verdict: s(o, "verdict") });
  }
  return { rows, paired, run_id: s(m, "run_id"), created_at: s(m, "created_at"), pace: s(m, "pace", "feed"), note: s(m, "note") };
}

/** Paired streaming result oriented as x minus y, or null when the export has no paired streaming table. */
export function streamPair(st: Streaming, tier: Tier, kind: string, slice: string, x: string, y: string) {
  const row = st.paired.find((p) => (p.tier == null || p.tier === tier) && p.slice_kind === kind && p.slice === slice && ((p.provider_a === x && p.provider_b === y) || (p.provider_a === y && p.provider_b === x)));
  if (!row) return null;
  const flip = row.provider_a !== x;
  const lo = flip ? (row.ci_high == null ? null : -row.ci_high) : row.ci_low;
  const hi = flip ? (row.ci_low == null ? null : -row.ci_low) : row.ci_high;
  const tied = row.verdict ? row.verdict === "no_difference" : lo != null && hi != null ? lo <= 0 && hi >= 0 : true;
  return { diff: flip ? -row.diff : row.diff, ci_low: lo, ci_high: hi, p: row.p_value, n: row.n_clips, tied };
}

// ---------------------------------------------------------------- A2 semantic

export interface SemanticRow extends BaseRow {
  /** Mean judge score 0..1: share of meaning kept. */
  score: number | null;
  /** Semantic error 0..1 (the exporter's semantic_wer, else 1 minus the score). Lower is better. */
  error: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

export interface Calibration {
  n_pairs: number | null;
  /** Agreement with human labels, 0..1. */
  agreement: number | null;
  ci_low: number | null;
  ci_high: number | null;
  /** Minimum agreement to publish semantic scores, 0..1. */
  threshold: number | null;
  kappa: number | null;
  /** What "agree" means, as written by the exporter (for example "within 0.2 of the human score"). */
  agreement_rule: string | null;
  labelled_on: string | null;
  /** METHODOLOGY.md gate: Spearman rank correlation and mean absolute error between judge and human scores. */
  spearman: number | null;
  mae: number | null;
  min_pairs: number | null;
  min_spearman: number | null;
  max_mae: number | null;
}

export interface Semantic {
  published: boolean;
  /** Why semantic scores are not published (from the export), when they are not. */
  reason: string | null;
  judge: { model: string | null; prompt: string | null; provider: string | null };
  calibration: Calibration | null;
  rows: SemanticRow[];
}

function parseCalibration(raw: unknown, lang?: string): Calibration | null {
  if (!isObj(raw)) return null;
  // Per-language files ({ja: {...}}) or a list of rows with lang: take this run's language.
  let v: Obj = raw;
  if (lang && isObj(raw[lang])) v = raw[lang] as Obj;
  else if (lang && isObj(raw.languages) && isObj((raw.languages as Obj)[lang])) v = (raw.languages as Obj)[lang] as Obj;
  else if (lang && Array.isArray(raw.languages)) v = rowsOf(raw.languages).find((o) => s(o, "lang", "language") === lang) ?? raw;
  const th = isObj(v.thresholds) ? v.thresholds : isObj(raw.thresholds) ? raw.thresholds : v;
  const ci = interval(v);
  const c: Calibration = {
    n_pairs: n(v, "n_pairs", "n", "pairs", "n_labelled"),
    agreement: share(n(v, "agreement", "accuracy", "agreement_rate")),
    ci_low: share(ci.low),
    ci_high: share(ci.high),
    threshold: share(n(v, "threshold", "min_agreement", "publish_threshold")),
    kappa: n(v, "kappa", "cohen_kappa"),
    agreement_rule: s(v, "agreement_rule", "rule", "definition"),
    labelled_on: s(v, "labelled_on", "checked_on", "date", "calibrated_on"),
    spearman: n(v, "spearman", "spearman_rho", "rho"),
    mae: n(v, "mae", "mean_absolute_error"),
    min_pairs: n(th, "min_pairs", "min_n_pairs"),
    min_spearman: n(th, "min_spearman"),
    max_mae: n(th, "max_mae"),
  };
  return c.agreement == null && c.n_pairs == null && c.spearman == null ? null : c;
}

/** The METHODOLOGY.md gate, when the thresholds are known: enough pairs, Spearman high enough, error low enough. */
export function calibrationPasses(c: Calibration | null): boolean | null {
  if (!c) return null;
  if (c.spearman != null && c.min_spearman != null && c.mae != null && c.max_mae != null) {
    return c.spearman >= c.min_spearman && c.mae <= c.max_mae && (c.min_pairs == null || (c.n_pairs ?? 0) >= c.min_pairs);
  }
  if (c.agreement != null && c.threshold != null) return c.agreement >= c.threshold;
  return null;
}

function parseSemantic(section: unknown, lang?: string): Semantic | null {
  if (section == null) return null;
  const m = meta(section);
  const rows = baseRows(section, (o) => {
    const score = share(n(o, "score", "semantic_score", "meaning_kept", "mean_score"));
    const err = share(n(o, "semantic_wer", "semantic_error", "semantic_error_rate"));
    const ci = interval(o);
    // An interval given on the score flips into an interval on the error.
    const onScore = err == null && score != null;
    const lo = share(ci.low);
    const hi = share(ci.high);
    return {
      score,
      error: err ?? (score != null ? 1 - score : null),
      ci_low: onScore ? (hi == null ? null : 1 - hi) : lo,
      ci_high: onScore ? (lo == null ? null : 1 - lo) : hi,
    };
  });
  const calibration = parseCalibration(m.calibration ?? m.judge_calibration, lang);
  const judge = isObj(m.judge) ? m.judge : {};
  const gate = calibrationPasses(calibration);
  const published = (b(m, "published", "publish") ?? gate ?? false) && rows.length > 0;
  if (!rows.length && !calibration && !s(m, "reason", "note")) return null;
  return {
    published,
    reason: published ? null : s(m, "reason", "note", "not_published_reason"),
    judge: { model: s(judge, "model", "model_id") ?? s(m, "judge_model", "model"), prompt: s(judge, "prompt", "prompt_id", "prompt_sha", "prompt_version") ?? s(m, "prompt_sha", "prompt_version"), provider: s(judge, "provider") },
    calibration,
    rows,
  };
}

// ---------------------------------------------------------------- A3 drift and history

export interface DriftEvent {
  provider: string;
  model_id: string | null;
  date: string;
  metric: string | null;
  before: number | null;
  after: number | null;
  /** The provider's own bootstrap interval the move went beyond. */
  ci_low: number | null;
  ci_high: number | null;
  direction: "worse" | "better" | null;
  tier: Tier | null;
  language: string | null;
}

function parseDrift(section: unknown): DriftEvent[] | null {
  const rows = rowsOf(section, ["events", "rows", "items"]);
  const out: DriftEvent[] = [];
  for (const o of rows) {
    const provider = s(o, "provider");
    const date = s(o, "date", "detected_on", "created_at", "at");
    if (!provider || !date) continue;
    const before = n(o, "before", "from", "baseline");
    const after = n(o, "after", "to", "value");
    const ci = interval(o);
    const dir = s(o, "direction");
    const tier = s(o, "tier");
    out.push({
      provider,
      model_id: s(o, "model_id"),
      date,
      metric: s(o, "metric"),
      before,
      after,
      ci_low: ci.low,
      ci_high: ci.high,
      direction: dir === "worse" || dir === "better" ? dir : before != null && after != null ? (after > before ? "worse" : "better") : null,
      tier: tier === "verified" || tier === "creator" ? tier : null,
      language: s(o, "language", "lang"),
    });
  }
  out.sort((a, c) => a.date.localeCompare(c.date));
  return out.length ? out : null;
}

export interface HistoryPoint {
  date: string;
  run_id: string | null;
  error_rate: number;
  ci_low: number | null;
  ci_high: number | null;
  n_clips: number | null;
  model_id: string | null;
  metric: string | null;
  /** "canary" for scheduled reruns on the fixed canary set, "full" for full runs, null when unknown. */
  kind: string | null;
}

export type History = Record<string, HistoryPoint[]>;

function point(o: Obj): HistoryPoint | null {
  const date = s(o, "date", "created_at", "at");
  const er = n(o, "error_rate", "value", "cer", "wer");
  if (!date || er == null) return null;
  const ci = interval(o);
  return { date, run_id: s(o, "run_id"), error_rate: er, ci_low: ci.low, ci_high: ci.high, n_clips: n(o, "n_clips", "n"), model_id: s(o, "model_id"), metric: s(o, "metric"), kind: s(o, "kind", "run_kind", "source") };
}

function parseHistory(section: unknown): History | null {
  const out: History = {};
  const add = (provider: string, pts: unknown) => {
    for (const o of rowsOf(pts, ["points", "series", "history", "rows"])) {
      const p = point(o);
      if (p) (out[provider] ??= []).push(p);
    }
  };
  if (Array.isArray(section)) {
    for (const o of section.filter(isObj)) {
      const provider = s(o, "provider");
      if (!provider) continue;
      if (Array.isArray(o.points) || Array.isArray(o.series) || Array.isArray(o.history)) add(provider, o);
      else add(provider, [o]);
    }
  } else if (isObj(section)) {
    const inner = section.series ?? section.providers ?? section.history;
    if (inner !== undefined && inner !== section) return parseHistory(inner);
    for (const [k, v] of Object.entries(section)) if (Array.isArray(v) || isObj(v)) add(k, v);
  }
  for (const k of Object.keys(out)) out[k]!.sort((a, c) => a.date.localeCompare(c.date));
  return Object.keys(out).length ? out : null;
}

/** One provider's series for a metric (points that name no metric are kept). */
export function historyFor(h: History | null, provider: string, metric?: string): HistoryPoint[] {
  const pts = h?.[provider] ?? [];
  const named = metric ? pts.filter((p) => p.metric === metric) : [];
  return named.length ? named : pts.filter((p) => p.metric == null || !metric || p.metric === metric);
}

// ---------------------------------------------------------------- A5 sweeps

export interface SweepPoint extends BaseRow {
  /** Background bed: "music", "noise", "reverb", or whatever the recipe names. "clean" for the unmixed baseline. */
  bed: string;
  /** Speech-to-bed ratio in dB; null for the clean baseline and for reverb levels. */
  snr_db: number | null;
  /** Reverb level label or RT60 (seconds) when bed is reverb. */
  level: string | null;
  error_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  halluc_per_nonspeech_min: number | null;
}

export interface Sweeps {
  points: SweepPoint[];
  /** Exporter-computed "falls apart at" SNRs, when given. */
  falls_apart: { provider: string; bed: string; snr_db: number | null }[];
  sweep_id: string | null;
  beds: string[];
}

function parseSweeps(section: unknown): Sweeps | null {
  const points = baseRows(section, (o) => {
    const ci = interval(o);
    const snr = n(o, "snr_db", "snr");
    const level = s(o, "level", "rt60_s", "reverb");
    const rawBed = s(o, "bed", "kind", "noise", "mix", "condition") ?? (snr == null && level == null ? "clean" : "noise");
    return { bed: rawBed, snr_db: snr, level, error_rate: n(o, "error_rate", "cer", "wer"), ci_low: ci.low, ci_high: ci.high, halluc_per_nonspeech_min: n(o, "halluc_per_nonspeech_min", "hallucination_per_min") };
  }, ["points", "rows", "aggregates"]);
  if (!points.length) return null;
  const m = meta(section);
  const falls: Sweeps["falls_apart"] = [];
  for (const o of rowsOf(m.falls_apart ?? m.breakpoints)) {
    const provider = s(o, "provider");
    if (provider) falls.push({ provider, bed: s(o, "bed") ?? "music", snr_db: n(o, "snr_db", "snr") });
  }
  const beds = [...new Set(points.map((p) => p.bed).filter((x) => x !== "clean"))];
  return { points, falls_apart: falls, sweep_id: s(m, "sweep_id"), beds };
}

/** A provider's curve on one bed, loudest speech first (high SNR to low), with its clean baseline if any. */
export function sweepCurve(sw: Sweeps, provider: string, bed: string, tier: Tier) {
  const mine = sw.points.filter((p) => p.provider === provider && (p.tier == null || p.tier === tier));
  const clean = mine.find((p) => p.bed === "clean") ?? null;
  const pts = mine.filter((p) => p.bed === bed && p.snr_db != null && p.error_rate != null).sort((a, c) => c.snr_db! - a.snr_db!);
  return { clean, pts };
}

/**
 * "Falls apart at": the exporter's value when it gives one; otherwise the highest SNR whose whole 95% interval sits
 * above the clean-speech interval (or above the first point's, without a clean baseline). Null when it never does.
 */
export function fallsApartAt(sw: Sweeps, provider: string, bed: string, tier: Tier): number | null {
  const given = sw.falls_apart.find((f) => f.provider === provider && f.bed === bed);
  if (given) return given.snr_db;
  const { clean, pts } = sweepCurve(sw, provider, bed, tier);
  const ref = clean ?? pts[0];
  if (!ref) return null;
  const top = ref.ci_high ?? ref.error_rate;
  if (top == null) return null;
  for (const p of pts) {
    if (p === ref) continue;
    const lo = p.ci_low ?? p.error_rate!;
    if (lo > top) return p.snr_db;
  }
  return null;
}

// ---------------------------------------------------------------- A6 formatting

export interface FormattingRow extends BaseRow {
  /** Punctuation error rate (Picovoice style), lower is better. */
  punct_error_rate: number | null;
  punct_ci_low: number | null;
  punct_ci_high: number | null;
  casing_acc: number | null;
  /** Inverse text normalisation: share of tagged spans written correctly. */
  itn_acc: number | null;
  itn_ci_low: number | null;
  itn_ci_high: number | null;
  itn_n_spans: number | null;
  itn_by_kind: Record<string, { acc: number | null; n: number | null }>;
}

export const ITN_KINDS = ["number", "date", "time", "currency", "percent", "unit"] as const;

function parseFormatting(section: unknown): FormattingRow[] | null {
  const rows = baseRows(section, (o) => {
    const p = interval(o, "punct_");
    const per = interval(o, "per_");
    const itn = interval(o, "itn_");
    const byKind: FormattingRow["itn_by_kind"] = {};
    const given = isObj(o.itn_by_kind) ? o.itn_by_kind : null;
    for (const k of ITN_KINDS) {
      const g = given?.[k];
      const acc = isObj(g) ? share(n(g, "acc", "accuracy", "itn_acc")) : share(num(g)) ?? share(n(o, `itn_${k}_acc`, `itn_acc_${k}`));
      const cnt = isObj(g) ? n(g, "n", "n_spans") : n(o, `itn_${k}_n`, `itn_n_${k}`);
      if (acc != null || cnt != null) byKind[k] = { acc, n: cnt };
    }
    return {
      punct_error_rate: share(n(o, "punct_error_rate", "per", "punctuation_error_rate")),
      punct_ci_low: share(p.low ?? per.low),
      punct_ci_high: share(p.high ?? per.high),
      casing_acc: share(n(o, "casing_acc", "casing_accuracy")),
      itn_acc: share(n(o, "itn_acc", "itn_accuracy", "formatting_acc")),
      itn_ci_low: share(itn.low),
      itn_ci_high: share(itn.high),
      itn_n_spans: n(o, "itn_n_spans", "itn_n", "n_spans"),
      itn_by_kind: byKind,
    };
  });
  return rows.length ? rows : null;
}

// ---------------------------------------------------------------- A7 lattice

export interface LatticeRow extends BaseRow {
  metric: string | null;
  error_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
}

function parseLattice(section: unknown): LatticeRow[] | null {
  const rows = baseRows(section, (o) => {
    const ci = interval(o, "", "_lattice");
    const plain = interval(o);
    return { metric: s(o, "metric"), error_rate: n(o, "error_rate_lattice", "lattice_error_rate", "error_rate"), ci_low: ci.low ?? plain.low, ci_high: ci.high ?? plain.high };
  });
  return rows.length ? rows : null;
}

/** Lattice scores per provider for a slice: from the aggregates' `error_rate_lattice` columns, else the lattice section. */
export function latticeFor(run: Run, tier: Tier, metric: string, kind: string, slice: string): Map<string, { error_rate: number; ci_low: number | null; ci_high: number | null }> {
  const out = new Map<string, { error_rate: number; ci_low: number | null; ci_high: number | null }>();
  for (const a of run.aggregates) {
    if (a.tier !== tier || a.metric !== metric || a.slice_kind !== kind || a.slice !== slice) continue;
    const v = num(a.error_rate_lattice);
    if (v != null) out.set(a.provider, { error_rate: v, ci_low: num(a.ci_low_lattice), ci_high: num(a.ci_high_lattice) });
  }
  if (out.size) return out;
  for (const r of inSlice(v3(run).lattice ?? [], tier, kind, slice)) {
    if (r.error_rate == null || (r.metric && r.metric !== metric)) continue;
    out.set(r.provider, { error_rate: r.error_rate, ci_low: r.ci_low, ci_high: r.ci_high });
  }
  return out;
}

// ---------------------------------------------------------------- A8 long-form

export interface LongformRow extends BaseRow {
  n_videos: number | null;
  minutes: number | null;
  error_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  /** Share 0..1 of videos the provider handled end to end (no failure, no truncation). */
  completed: number | null;
  /** Chunking errors: dropped or duplicated text at chunk joins, per video. */
  chunk_errors_per_video: number | null;
  /** How much the median word timing offset grows per minute of audio, ms per minute. */
  drift_ms_per_min: number | null;
}

export interface MinutePoint {
  provider: string;
  minute: number;
  offset_median_ms: number | null;
  offset_p90_ms: number | null;
  n_words: number | null;
}

export interface Longform {
  rows: LongformRow[];
  by_minute: MinutePoint[];
}

function parseLongform(section: unknown): Longform | null {
  const rows = baseRows(section, (o) => {
    const ci = interval(o);
    return {
      n_videos: n(o, "n_videos", "videos", "n_clips"),
      minutes: n(o, "minutes", "audio_minutes"),
      error_rate: n(o, "error_rate", "cer", "wer"),
      ci_low: ci.low,
      ci_high: ci.high,
      completed: share(n(o, "completed", "completed_rate", "completion_rate", "handled_rate")),
      chunk_errors_per_video: n(o, "chunk_errors_per_video", "chunking_errors", "chunk_errors"),
      drift_ms_per_min: n(o, "drift_ms_per_min", "timing_drift_ms_per_min", "drift_slope_ms_per_min"),
    };
  }, ["aggregates", "rows", "videos"], 5);
  const m = meta(section);
  const by_minute: MinutePoint[] = [];
  for (const o of rowsOf(m.by_minute ?? m.drift_by_minute ?? m.timing_by_minute ?? m.minutes)) {
    const provider = s(o, "provider");
    const minute = n(o, "minute", "min", "t_min");
    if (!provider || minute == null) continue;
    by_minute.push({ provider, minute, offset_median_ms: n(o, "offset_median_ms", "median_offset_ms", "timing_median_ms", "offset_ms"), offset_p90_ms: n(o, "offset_p90_ms", "p90_offset_ms", "timing_p90_ms"), n_words: n(o, "n_words", "words") });
  }
  by_minute.sort((a, c) => a.minute - c.minute);
  if (!rows.length && !by_minute.length) return null;
  return { rows, by_minute };
}

// ---------------------------------------------------------------- A9 compute

export interface ComputeRow {
  provider: string;
  model_id: string | null;
  machine: string | null;
  device: string | null;
  /** Inverse real-time factor: seconds of audio per second of processing. */
  rtfx: number | null;
  model_size_mb: number | null;
  params_m: number | null;
  peak_memory_mb: number | null;
}

export interface Compute {
  rows: ComputeRow[];
  machine: string | null;
}

function machineName(v: unknown): string | null {
  if (isObj(v)) return [s(v, "name", "label"), s(v, "chip", "cpu", "gpu"), num(v.memory_gb) != null ? `${num(v.memory_gb)} GB` : null].filter(Boolean).join(", ") || null;
  return str(v);
}

function parseCompute(section: unknown): Compute | null {
  const out: ComputeRow[] = [];
  for (const o of rowsOf(section, ["rows", "models", "aggregates"])) {
    const provider = s(o, "provider");
    if (!provider) continue;
    const rtf = n(o, "rtf");
    out.push({
      provider,
      model_id: s(o, "model_id"),
      machine: machineName(o.machine) ?? s(o, "host"),
      device: s(o, "device", "backend"),
      rtfx: n(o, "rtfx", "speed_factor", "inverse_rtf") ?? (rtf && rtf > 0 ? 1 / rtf : null),
      model_size_mb: n(o, "model_size_mb", "size_mb", "disk_mb"),
      params_m: n(o, "params_m", "parameters_m"),
      peak_memory_mb: n(o, "peak_memory_mb", "memory_mb", "peak_rss_mb"),
    });
  }
  if (!out.length) return null;
  return { rows: out, machine: machineName(meta(section).machine) };
}

/** Speed factor for any provider: compute's RTFx when measured, else 1 / real-time factor from the aggregates. */
export function speedFactor(run: Run, provider: string, tier: Tier, metric: string): { value: number | null; source: "compute" | "aggregates" | null } {
  const c = v3(run).compute?.rows.find((r) => r.provider === provider);
  if (c?.rtfx != null) return { value: c.rtfx, source: "compute" };
  const a = run.aggregates.find((x) => x.provider === provider && x.tier === tier && x.metric === metric && x.slice_kind === "all");
  const rtf = num(a?.rtf);
  return rtf && rtf > 0 ? { value: 1 / rtf, source: "aggregates" } : { value: null, source: null };
}

/** List price per 1,000 minutes of audio, from the per-hour price. */
export function pricePer1000Min(usdPerHour: number | null | undefined): number | null {
  const v = num(usdPerHour);
  return v == null ? null : (v / 60) * 1000;
}

// ---------------------------------------------------------------- B15 diarization

export interface DiarizationRow extends BaseRow {
  /** Diarization error rate 0..1+ (missed speech + false alarm + speaker confusion). */
  der: number | null;
  der_ci_low: number | null;
  der_ci_high: number | null;
  /** Share 0..1 of clips where the provider found the right number of speakers. */
  speaker_count_acc: number | null;
  count_ci_low: number | null;
  count_ci_high: number | null;
  missed: number | null;
  false_alarm: number | null;
  confusion: number | null;
}

export interface Diarization {
  rows: DiarizationRow[];
  /** Providers that offer no diarization (recorded in raw_response.clipbench_unsupported). */
  unsupported: string[];
}

function ids(v: unknown): string[] {
  return Array.isArray(v) ? v.map(str).filter((x): x is string => Boolean(x)) : [];
}

function parseDiarization(section: unknown): Diarization | null {
  const rows = baseRows(section, (o) => {
    const d = interval(o, "der_");
    const c = interval(o, "count_");
    const c2 = interval(o, "speaker_count_");
    return {
      der: n(o, "der", "diarization_error_rate"),
      der_ci_low: d.low ?? n(o, "ci_low"),
      der_ci_high: d.high ?? n(o, "ci_high"),
      speaker_count_acc: share(n(o, "speaker_count_acc", "count_acc", "speaker_count_accuracy")),
      count_ci_low: share(c.low ?? c2.low),
      count_ci_high: share(c.high ?? c2.high),
      missed: n(o, "missed", "missed_speech"),
      false_alarm: n(o, "false_alarm"),
      confusion: n(o, "confusion", "speaker_confusion"),
    };
  });
  const unsupported = ids(meta(section).unsupported);
  if (!rows.length && !unsupported.length) return null;
  return { rows, unsupported };
}

// ---------------------------------------------------------------- B16 language ID

export interface LidRow extends BaseRow {
  /** Share 0..1 of clips where auto-detect picked the right language. */
  accuracy: number | null;
  ci_low: number | null;
  ci_high: number | null;
  /** Error rate with the language hint, and with auto-detect (no hint). */
  error_rate_hint: number | null;
  error_rate_auto: number | null;
  auto_ci_low: number | null;
  auto_ci_high: number | null;
  /** Most common wrong pick, when given. */
  top_confusion: string | null;
}

export interface Lid {
  rows: LidRow[];
  unsupported: string[];
}

function parseLid(section: unknown): Lid | null {
  const rows = baseRows(section, (o) => {
    const ci = interval(o);
    const auto = interval(o, "auto_");
    return {
      accuracy: share(n(o, "accuracy", "lid_acc", "lid_accuracy", "correct_rate")),
      ci_low: share(ci.low),
      ci_high: share(ci.high),
      error_rate_hint: n(o, "error_rate_hint", "error_rate_with_hint", "error_rate_hinted"),
      error_rate_auto: n(o, "error_rate_auto", "error_rate_no_hint", "error_rate_without_hint"),
      auto_ci_low: auto.low,
      auto_ci_high: auto.high,
      top_confusion: s(o, "top_confusion", "most_confused_with"),
    };
  });
  const unsupported = ids(meta(section).unsupported);
  if (!rows.length && !unsupported.length) return null;
  return { rows, unsupported };
}

// ---------------------------------------------------------------- B17 entities

export interface EntityScore {
  acc: number | null;
  ci_low: number | null;
  ci_high: number | null;
  n_spans: number | null;
}

export interface EntityRow extends BaseRow {
  /** Without custom vocabulary. */
  plain: EntityScore;
  /** With the provider's keyterm / custom vocabulary feature and the same term list; null when not run. */
  keyterms: EntityScore | null;
  keyterms_supported: boolean | null;
}

export interface Entities {
  rows: EntityRow[];
  /** Size of the shared term list, when given. */
  n_terms: number | null;
}

function parseEntities(section: unknown): Entities | null {
  const raw = rowsOf(section);
  const byKey = new Map<string, EntityRow>();
  for (const o of raw) {
    const bse = base(o, 15);
    if (!bse) continue;
    const key = `${bse.provider}\u0000${bse.tier}\u0000${bse.slice_kind}\u0000${bse.slice}`;
    const row = byKey.get(key) ?? { ...bse, plain: { acc: null, ci_low: null, ci_high: null, n_spans: null }, keyterms: null, keyterms_supported: b(o, "keyterms_supported", "custom_vocabulary") };
    const mode = s(o, "mode", "variant", "with");
    const read = (suffix: string): EntityScore => {
      const ci = interval(o, "", suffix);
      return { acc: share(n(o, `entity_acc${suffix}`, `accuracy${suffix}`, `acc${suffix}`)), ci_low: share(ci.low), ci_high: share(ci.high), n_spans: n(o, `n_spans${suffix}`, "n_spans", "n_entities") };
    };
    if (mode) {
      const kt = /key|vocab|with|boost/i.test(mode) && !/without|no_|plain|base/i.test(mode);
      if (kt) row.keyterms = read("");
      else row.plain = read("");
    } else {
      row.plain = read("");
      const k = read("_keyterms");
      if (k.acc != null) row.keyterms = k;
    }
    if (row.keyterms_supported == null) row.keyterms_supported = b(o, "keyterms_supported");
    byKey.set(key, row);
  }
  const rows = [...byKey.values()];
  if (!rows.length) return null;
  return { rows, n_terms: n(meta(section), "n_terms", "terms") };
}

// ---------------------------------------------------------------- B18 style

export interface StyleRow extends BaseRow {
  /** Shares 0..1 of reference items kept in the output. Style choices, not errors. */
  fillers_kept: number | null;
  stutters_kept: number | null;
  profanity_kept: number | null;
  profanity_masked: number | null;
  bleeps_marked: number | null;
}

export const STYLE_PROPS: { key: keyof Pick<StyleRow, "fillers_kept" | "stutters_kept" | "profanity_kept" | "profanity_masked" | "bleeps_marked">; label: string; help: string; low: string; high: string }[] = [
  { key: "fillers_kept", label: "Fillers", help: "Um, uh and similar sounds the speaker made, written out.", low: "Drops them", high: "Keeps them" },
  { key: "stutters_kept", label: "Stutters and repeats", help: "Repeated starts (\"I, I think\") written as said.", low: "Cleans up", high: "Verbatim" },
  { key: "profanity_kept", label: "Swear words, as said", help: "Swear words written out in full.", low: "Never", high: "Always" },
  { key: "profanity_masked", label: "Swear words, masked", help: "Swear words written with asterisks or a placeholder.", low: "Never", high: "Always" },
  { key: "bleeps_marked", label: "Bleeps marked", help: "A bleeped word in the audio shown with a marker instead of a guess.", low: "Never", high: "Always" },
];

function parseStyle(section: unknown): StyleRow[] | null {
  const rows = baseRows(section, (o) => ({
    fillers_kept: share(n(o, "fillers_kept", "filler_kept_rate", "fillers_kept_rate", "fillers")),
    stutters_kept: share(n(o, "stutters_kept", "stutter_kept_rate", "stutters_kept_rate", "stutters")),
    profanity_kept: share(n(o, "profanity_kept", "profanity_kept_rate")),
    profanity_masked: share(n(o, "profanity_masked", "profanity_masked_rate")),
    bleeps_marked: share(n(o, "bleeps_marked", "bleep_marked_rate", "bleeped_marked")),
  }));
  return rows.length ? rows : null;
}

// ---------------------------------------------------------------- B19 reliability

export interface ReliabilityRow {
  provider: string;
  /** "run" for this run, "all" for every run on record. */
  scope: "run" | "all";
  n_requests: number | null;
  n_runs: number | null;
  failure_rate: number | null;
  timeout_rate: number | null;
  retries_per_request: number | null;
  p50_ms: number | null;
  p99_ms: number | null;
  since: string | null;
}

function parseReliability(section: unknown): ReliabilityRow[] | null {
  const out: ReliabilityRow[] = [];
  const read = (rows: Obj[], scope: "run" | "all" | null) => {
    for (const o of rows) {
      const provider = s(o, "provider");
      if (!provider) continue;
      const sc = s(o, "scope");
      out.push({
        provider,
        scope: scope ?? (sc === "all" || sc === "all_runs" || sc === "history" ? "all" : "run"),
        n_requests: n(o, "n_requests", "requests", "n"),
        n_runs: n(o, "n_runs", "runs"),
        failure_rate: share(n(o, "failure_rate", "error_rate", "fail_rate")),
        timeout_rate: share(n(o, "timeout_rate", "timeouts_rate")),
        retries_per_request: n(o, "retries_per_request", "retry_rate", "retries_mean", "mean_retries"),
        p50_ms: n(o, "p50_ms", "latency_p50", "p50_latency_ms"),
        p99_ms: n(o, "p99_ms", "latency_p99", "p99_latency_ms"),
        since: s(o, "since", "first_run"),
      });
    }
  };
  if (isObj(section) && (section.run !== undefined || section.all_runs !== undefined || section.all !== undefined)) {
    read(rowsOf(section.run), "run");
    read(rowsOf(section.all_runs ?? section.all), "all");
  } else read(rowsOf(section), null);
  return out.length ? out : null;
}

/** This run's reliability row and the all-runs row for a provider. */
export function reliabilityFor(rows: ReliabilityRow[] | null, provider: string) {
  const mine = (rows ?? []).filter((r) => r.provider === provider);
  return { run: mine.find((r) => r.scope === "run") ?? null, all: mine.find((r) => r.scope === "all") ?? null };
}

// ---------------------------------------------------------------- B21 caption export

export interface CaptionsRow extends BaseRow {
  /** Median distance between a cue's start and the reference cue's start, ms. */
  cue_start_median_ms: number | null;
  cue_end_median_ms: number | null;
  /** Share 0..1 of cues starting within 200 ms of the reference. */
  cue_within_200ms: number | null;
  /** Median reading speed, characters per second. */
  cps_median: number | null;
  /** Share 0..1 of cues faster than the reading-speed limit. */
  cps_over: number | null;
  /** Share 0..1 of lines longer than the line limit. */
  line_over: number | null;
  /** Share 0..1 of cues that meet every readability rule. */
  readable: number | null;
  n_cues: number | null;
}

export interface CaptionRule {
  max_chars_per_line: number | null;
  max_lines: number | null;
  max_cps: number | null;
  min_duration_ms: number | null;
  max_duration_ms: number | null;
}

export interface Captions {
  rows: CaptionsRow[];
  rule: CaptionRule | null;
  formats: string[];
}

function parseCaptions(section: unknown): Captions | null {
  const rows = baseRows(section, (o) => ({
    cue_start_median_ms: n(o, "cue_start_median_ms", "start_offset_median_ms", "cue_timing_median_ms"),
    cue_end_median_ms: n(o, "cue_end_median_ms", "end_offset_median_ms"),
    cue_within_200ms: share(n(o, "cue_within_200ms", "cues_within_200ms", "cue_on_time")),
    cps_median: n(o, "cps_median", "reading_speed_cps"),
    cps_over: share(n(o, "cps_over", "cps_over_rate", "too_fast_rate")),
    line_over: share(n(o, "line_over", "line_over_rate", "line_too_long_rate")),
    readable: share(n(o, "readable", "readable_rate", "cue_ok_rate", "readability")),
    n_cues: n(o, "n_cues", "cues"),
  }));
  if (!rows.length) return null;
  const m = meta(section);
  const r = isObj(m.rule) ? m.rule : isObj(m.line_rule) ? m.line_rule : null;
  const rule = r
    ? { max_chars_per_line: n(r, "max_chars_per_line", "max_chars", "chars_per_line"), max_lines: n(r, "max_lines", "lines"), max_cps: n(r, "max_cps", "cps"), min_duration_ms: n(r, "min_duration_ms"), max_duration_ms: n(r, "max_duration_ms") }
    : null;
  const formats = Array.isArray(m.formats) ? m.formats.map(str).filter((x): x is string => Boolean(x)) : [];
  return { rows, rule, formats };
}

// ---------------------------------------------------------------- B20 features

export const FEATURE_KEYS = ["languages", "batch", "streaming", "word_timestamps", "diarization", "language_id", "custom_vocabulary", "max_file_minutes", "retention", "training_opt_out"] as const;
export type FeatureKey = (typeof FEATURE_KEYS)[number];

export const FEATURE_LABEL: Record<FeatureKey | "price", string> = {
  languages: "Languages",
  batch: "Batch",
  streaming: "Streaming",
  word_timestamps: "Word timestamps",
  diarization: "Diarization",
  language_id: "Language ID",
  custom_vocabulary: "Custom vocabulary",
  max_file_minutes: "Max file length",
  retention: "Data retention",
  training_opt_out: "Training opt-out",
  price: "Price",
};

export interface FeatureCell {
  /** Raw value from pricing.toml (boolean, number, string or list). */
  value: unknown;
  /** yes / no for boolean-like values, null otherwise. */
  support: "yes" | "no" | "partial" | null;
  url: string | null;
  checked_on: string | null;
}

export interface FeatureProvider {
  id: string;
  label: string | null;
  cells: Partial<Record<FeatureKey, FeatureCell>>;
  /** Price per hour and its doc link, when the matrix carries them. */
  price: FeatureCell | null;
}

export interface Features {
  providers: FeatureProvider[];
  checked_on: string | null;
}

function support(v: unknown): FeatureCell["support"] {
  const bv = bool(v);
  if (bv != null) return bv ? "yes" : "no";
  if (typeof v === "string" && /^(partial|beta|limited|preview)/i.test(v)) return "partial";
  if (typeof v === "string" && /^(not offered|none|unsupported|n\/a)$/i.test(v)) return "no";
  return null;
}

function cell(o: Obj, key: string): FeatureCell | null {
  const raw = o[key];
  if (raw === undefined && o[`${key}_url`] === undefined) return null;
  if (isObj(raw) && ("value" in raw || "url" in raw || "checked_on" in raw)) {
    return { value: raw.value ?? null, support: support(raw.value), url: safeUrl(s(raw, "url", "doc_url")), checked_on: s(raw, "checked_on", "checked") };
  }
  return { value: raw ?? null, support: support(raw), url: safeUrl(s(o, `${key}_url`)), checked_on: s(o, `${key}_checked_on`, `${key}_checked`, "checked_on") };
}

/** Only http(s) links leave the site; anything else (javascript:, data:) is dropped. */
export function safeUrl(u: string | null): string | null {
  if (!u) return null;
  try {
    const url = new URL(u);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function featureProvider(id: string, o: Obj): FeatureProvider {
  const src = isObj(o.features) ? { ...o, ...o.features } : o;
  const cells: FeatureProvider["cells"] = {};
  for (const k of FEATURE_KEYS) {
    const c = cell(src, k) ?? (k === "max_file_minutes" ? cell(src, "max_file_length") : k === "training_opt_out" ? cell(src, "opt_out") : null);
    if (c) cells[k] = c;
  }
  const price = cell(src, "price") ?? cell(src, "usd_per_hour");
  return { id, label: s(o, "label", "name"), cells, price };
}

function parseFeatures(section: unknown): Features | null {
  const out = new Map<string, FeatureProvider>();
  const m = meta(section);
  if (Array.isArray(section) || Array.isArray(m.rows) || Array.isArray(m.cells)) {
    const rows = rowsOf(section, ["rows", "cells", "providers"]);
    const long = rows.some((o) => "key" in o || "feature" in o);
    for (const o of rows) {
      const id = s(o, "provider", "id");
      if (!id) continue;
      if (long) {
        const key = s(o, "key", "feature");
        if (!key) continue;
        const p = out.get(id) ?? { id, label: null, cells: {}, price: null };
        const c: FeatureCell = { value: o.value ?? null, support: support(o.value), url: safeUrl(s(o, "url", "doc_url")), checked_on: s(o, "checked_on") };
        if (key === "price" || key === "usd_per_hour") p.price = c;
        else if ((FEATURE_KEYS as readonly string[]).includes(key)) p.cells[key as FeatureKey] = c;
        out.set(id, p);
      } else out.set(id, featureProvider(id, o));
    }
  } else if (isObj(section)) {
    const inner = isObj(m.providers) ? m.providers : Array.isArray(m.providers) ? null : section;
    if (Array.isArray(m.providers)) return parseFeatures(m.providers.map((x) => x));
    for (const [id, v] of Object.entries(inner ?? {})) if (isObj(v) && id !== "checked_on") out.set(id, featureProvider(id, v));
  }
  const providers = [...out.values()].filter((p) => Object.keys(p.cells).length > 0 || p.price);
  if (!providers.length) return null;
  return { providers, checked_on: s(m, "checked_on") };
}

/** Whether a provider offers a feature, from the matrix (null when the matrix does not say). */
export function supports(f: Features | null, provider: string, key: FeatureKey): boolean | null {
  const c = f?.providers.find((p) => p.id === provider)?.cells[key];
  if (!c) return null;
  return c.support === "yes" || c.support === "partial" ? true : c.support === "no" ? false : c.value != null && c.value !== "" ? true : null;
}

// ---------------------------------------------------------------- run settings

const SECRET = /key|token|secret|password|auth|credential/i;

function show(v: unknown): string {
  if (v == null) return "null";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try {
    return JSON.stringify(v);
  } catch {
    return String(v);
  }
}

/**
 * The exact settings a provider ran with: the run's global options (diarize, detect_language, keyterms), then the
 * provider's own entry under run_meta.providers / run_meta.settings, then provider.settings. Anything whose name
 * looks like a credential is dropped, whatever the exporter wrote.
 */
export function settingsFor(run: Run, provider: string): [string, string][] {
  const m = isObj(run.run_meta) ? run.run_meta : {};
  const out = new Map<string, string>();
  const put = (o: unknown, prefix = "") => {
    if (!isObj(o)) return;
    for (const [k, v] of Object.entries(o)) {
      if (SECRET.test(k) || v === undefined) continue;
      out.set(prefix + k, show(v));
    }
  };
  put(m.options);
  const per = (isObj(m.providers) && m.providers[provider]) || (isObj(m.settings) && m.settings[provider]) || (isObj(m.provider_settings) && m.provider_settings[provider]);
  put(per);
  const p = run.providers.find((x) => x.id === provider) as unknown as Obj | undefined;
  if (p) put(p.settings ?? p.options ?? p.params);
  return [...out.entries()];
}

/** Run-level facts from run_meta worth printing (kind, machine, cadence). */
export function runMetaFacts(run: Run): [string, string][] {
  const m = isObj(run.run_meta) ? run.run_meta : {};
  const out: [string, string][] = [];
  for (const k of ["kind", "machine", "budget_usd", "repeats", "clipbench_version"]) {
    const v = m[k];
    if (v != null && !SECRET.test(k)) out.push([k, k === "machine" ? (machineName(v) ?? show(v)) : show(v)]);
  }
  return out;
}

// ---------------------------------------------------------------- all sections

export interface V3 {
  streaming: Streaming | null;
  semantic: Semantic | null;
  drift: DriftEvent[] | null;
  history: History | null;
  sweeps: Sweeps | null;
  formatting: FormattingRow[] | null;
  lattice: LatticeRow[] | null;
  longform: Longform | null;
  compute: Compute | null;
  diarization: Diarization | null;
  lid: Lid | null;
  entities: Entities | null;
  style: StyleRow[] | null;
  reliability: ReliabilityRow[] | null;
  captions: Captions | null;
  features: Features | null;
}

/** A parser that throws on some odd input must not take the page down: the section becomes null instead. */
function safe<T>(f: (x: unknown) => T | null, x: unknown): T | null {
  if (x == null) return null;
  try {
    return f(x);
  } catch {
    return null;
  }
}

const memo = new WeakMap<object, V3>();

/** Parsed v3 sections of a run (memoised per run object). */
export function v3(run: Run): V3 {
  const hit = memo.get(run);
  if (hit) return hit;
  const out: V3 = {
    streaming: safe(parseStreaming, run.streaming),
    semantic: safe((x) => parseSemantic(x, run.language), run.semantic),
    drift: safe(parseDrift, run.drift),
    history: safe(parseHistory, run.history),
    sweeps: safe(parseSweeps, run.sweeps),
    formatting: safe(parseFormatting, run.formatting),
    lattice: safe(parseLattice, run.lattice),
    longform: safe(parseLongform, run.longform),
    compute: safe(parseCompute, run.compute),
    diarization: safe(parseDiarization, run.diarization),
    lid: safe(parseLid, run.lid),
    entities: safe(parseEntities, run.entities),
    style: safe(parseStyle, run.style),
    reliability: safe(parseReliability, run.reliability),
    captions: safe(parseCaptions, run.captions),
    features: safe(parseFeatures, run.features),
  };
  memo.set(run, out);
  return out;
}

/** Whether a run has lattice scores anywhere. */
export function hasLattice(run: Run) {
  return run.aggregates.some((a) => num(a.error_rate_lattice) != null) || Boolean(v3(run).lattice?.length);
}
