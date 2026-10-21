// Types for the run export. Source of truth: docs/WEB.md ("Run data") and docs/INTERFACES.md ("Scores").

export type Tier = "verified" | "creator";
export type SliceKind = "all" | "tag" | "duration" | "category" | "audio_condition" | "condition";
export type Verdict = "a_better" | "b_better" | "no_difference";

export interface RunSummary {
  run_id: string;
  created_at: string;
  language: string;
  tiers: Tier[];
  n_clips: Partial<Record<Tier, number>>;
  providers: string[];
}

/** A provider seen in the run that is not eligible for ranking (docs/V3.md, "Publication gate fields"). */
export interface ProviderExclusion {
  provider: string;
  /** Share 0..1 of the verified evaluation clips with an ok row; null when the run has no verified clips. */
  coverage: number | null;
  n_scored: number | null;
  n_skipped: number | null;
  n_error: number | null;
  /** Display sentence written by the exporter. */
  reason: string | null;
}

/** Diversity of the verified evaluation clips (clipbench.data.check.check_verified). */
export interface VerifiedDiversity {
  channels: number;
  largest_channel_share: number | null;
  categories: string[];
  passes: boolean;
  /** Display sentences, empty when `passes`. */
  problems: string[];
}

/** The three publication conditions, each computed by the exporter. */
export interface GateFlags {
  verified_clips_ok: boolean;
  providers_ok: boolean;
  diversity_ok: boolean;
}

/**
 * Per-language publication status (docs/V2.md "Languages", docs/V3.md "Publication gate fields"). Written by the
 * exporter; the site only reads it. A status without `gate` (an older export) is treated as not published.
 */
export interface LanguageStatus {
  lang: string;
  verified_clips: number;
  /** Number of ELIGIBLE providers (= providers_eligible.length). */
  providers_ok: number;
  published: boolean;
  channels?: number;
  categories?: string[] | number;
  largest_channel_share?: number | null;
  providers_eligible?: string[];
  providers_excluded?: ProviderExclusion[];
  /** Coverage threshold the exporter used, 0..1. */
  min_coverage?: number;
  verified_diversity?: VerifiedDiversity;
  gate?: GateFlags;
}

/** One row of the exporter's ranking (`ranking[tier][metric]`, slice all, eligible providers only). */
export interface RankEntry {
  provider: string;
  rank: number;
  error_rate: number;
  ci_low: number;
  ci_high: number;
  n_clips: number;
}

export interface RunIndex {
  /** Set only on the synthetic dev fixture. */
  demo?: boolean;
  latest: string;
  runs: (RunSummary & { languages?: LanguageStatus[] })[];
  languages?: LanguageStatus[];
}

export interface Metric {
  name: string;
  primary: boolean;
  label: string;
}

export interface Provider {
  id: string;
  model_id: string;
  label: string;
  pricing_checked_on: string;
  usd_per_hour: number;
  paid: boolean;
  uploads_audio: boolean;
  /** Data policy, checked on pricing_checked_on (contract addition, see docs/WEB.md). Optional until the exporter writes them. */
  /** Days the provider keeps uploaded audio. 0 = not stored, null = unknown or not applicable (local). */
  retention_days?: number | null;
  /** Whether the provider trains on API audio unless told not to. null = not applicable (local). */
  trains_by_default?: boolean | null;
  /** What clipbench did about it: "applied" (opt-out set), "not_offered", "not_needed" (no training, or local). */
  opt_out?: "applied" | "not_offered" | "not_needed";
  /** Link to the provider's data policy page. */
  policy_url?: string;
}

export interface Aggregate {
  provider: string;
  model_id: string;
  tier: Tier;
  metric: string;
  slice_kind: SliceKind;
  slice: string;
  n_clips: number;
  error_rate: number;
  ci_low: number;
  ci_high: number;
  too_small: boolean;
  hallucination_rate: number;
  long_output_rate: number;
  cost_usd_total: number;
  audio_hours: number;
  cost_per_hour: number | null;
  latency_p50: number | null;
  latency_p95: number | null;
  rtf: number | null;
  // v2 caption metrics (docs/methodology_v2_metrics.md). Optional until the exporter writes them; null = n/a, never 0.
  /** Median across clips of the per-clip median word start offset, ms. */
  timing_median_ms?: number | null;
  timing_p90_ms?: number | null;
  /** Share 0..1 of matched words starting within 200 ms of the reference (word weighted). */
  timing_within_200ms?: number | null;
  timing_n_clips?: number | null;
  /** Words emitted over non-speech, per minute of non-speech audio. */
  halluc_per_nonspeech_min?: number | null;
  nonspeech_n_clips?: number | null;
  /** Punctuation F1 0..1 on clips marked punctuated. */
  punct_f1?: number | null;
  casing_acc?: number | null;
  punct_n_clips?: number | null;
  /** Error rate on tagged code-switch spans, 0..1. */
  codeswitch_error_rate?: number | null;
  codeswitch_n_clips?: number | null;
  // v3 (docs/V3.md, A7): score against the closest valid path through the reference's alternatives.
  error_rate_lattice?: number | null;
  ci_low_lattice?: number | null;
  ci_high_lattice?: number | null;
  // Publication gate (docs/V3.md). Missing in older exports: treated as not eligible.
  /** The provider is in providers_eligible. Rows with false are never ranked or compared. */
  eligible?: boolean;
  /** The provider's coverage of the verified evaluation clips, 0..1, or null. */
  coverage?: number | null;
  /** Channel-clustered 95% interval (sensitivity analysis) and the channels behind it. */
  ci_low_cluster?: number | null;
  ci_high_cluster?: number | null;
  n_channels?: number | null;
  mean_clip_error_rate?: number | null;
}

export interface Paired {
  tier: Tier;
  metric: string;
  slice_kind: SliceKind;
  slice: string;
  provider_a: string;
  provider_b: string;
  n_clips: number;
  /** a minus b, error rate */
  diff: number;
  ci_low: number;
  ci_high: number;
  p_value: number;
  /** Benjamini-Hochberg adjusted verdict. Use it for any "beats" wording. */
  verdict: Verdict;
  /** Unadjusted verdict and the BH-adjusted p value (docs/V3.md). Missing in older exports. */
  verdict_raw?: Verdict;
  p_adjusted?: number | null;
  ci_low_cluster?: number | null;
  ci_high_cluster?: number | null;
  p_value_cluster?: number | null;
  n_channels?: number | null;
}

export interface Stability {
  provider: string;
  video_id: string;
  n_repeats: number;
  text_disagreement: number;
  latency_mean: number | null;
  latency_std: number | null;
}

export interface Finding {
  tier: Tier;
  text: string;
  source: string;
}

/**
 * v2 findings file: /data/findings/<lang>.json (written by the exporter). Every sentence is generated from
 * numbers that survived the paired test, with a structured pointer to the table it came from.
 */
export interface FindingV2 {
  id: string;
  slug: string;
  text: string;
  status: "draft" | "verified" | string;
  source: { table: "aggregates" | "paired" | string; filters: Record<string, string | number | boolean | null> };
  numbers?: Record<string, number | null>;
  /** True when the comparison's channel-clustered interval crosses zero (docs/V3.md). */
  cluster_sensitive?: boolean;
}

export interface FindingsFile {
  lang: string;
  run_id?: string;
  created_at?: string;
  published?: boolean;
  findings: FindingV2[];
}

export interface ProviderErrors {
  top_substitutions: [string, string, number][];
  worst_clips: { video_id: string; error_rate: number; others_median: number }[];
}

export interface ErrorExample {
  provider: string;
  video_id: string;
  ref_snippet: string;
  hyp_snippet: string;
}

export interface Clip {
  video_id: string;
  tier: Tier;
  duration_s: number | null;
  channel: string;
  tags: string[];
  /** "short" (a whole Short) or "window" (a cut from a longer video). Contract addition. */
  kind?: "short" | "window";
  /** Window start in the source video, seconds (windows only). Contract addition. */
  start_s?: number | null;
  /** docs/V2.md categories: talking_head, vlog_music, gaming, comedy_skit, cooking_tutorial, news_explainer, other. */
  category?: string;
  audio_condition?: "music" | "no_music";
  /** docs/V3.md audio conditions (A4): music, sfx, multiple_speakers, fast_speech, whisper_shout, outdoor_noise, code_switching. */
  conditions?: string[];
}

export interface PerClip {
  provider: string;
  video_id: string;
  tier: Tier;
  metric: string;
  error_rate: number;
  hallucination_rate: number;
  latency_ms: number | null;
}

export interface Run {
  run_id: string;
  created_at: string;
  language: string;
  git_commit: string;
  sample_seed: number;
  metrics: Metric[];
  providers: Provider[];
  aggregates: Aggregate[];
  paired: Paired[];
  /** {tier: {metric: [...]}}: the one ranking order, eligible providers only, {} when unpublished (docs/V3.md). */
  ranking?: Partial<Record<Tier, Record<string, RankEntry[]>>>;
  stability: Stability[];
  findings: Finding[];
  errors: { providers: Record<string, ProviderErrors>; examples: ErrorExample[]; all_fail?: string[] };
  clips: Clip[];
  per_clip: PerClip[];
  limitations: string[];
  /** docs/V2.md. Optional until the exporter writes it. */
  languages?: LanguageStatus[];
  // v3 export keys (docs/V3.md, "Export additions"). Each is null when the run has no such data and missing in
  // older exports. Their shapes are still settling, so they stay `unknown` here and are read only through the
  // defensive parsers in src/lib/v3.ts.
  streaming?: unknown;
  semantic?: unknown;
  drift?: unknown;
  history?: unknown;
  sweeps?: unknown;
  formatting?: unknown;
  lattice?: unknown;
  longform?: unknown;
  compute?: unknown;
  diarization?: unknown;
  lid?: unknown;
  entities?: unknown;
  style?: unknown;
  reliability?: unknown;
  captions?: unknown;
  features?: unknown;
  /** The run's settings (run_meta.json): options and per-provider parameters. */
  run_meta?: unknown;
}
