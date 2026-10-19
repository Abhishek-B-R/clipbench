// Types for the run export. Source of truth: docs/WEB.md ("Run data") and docs/INTERFACES.md ("Scores").

export type Tier = "verified" | "creator";
export type SliceKind = "all" | "tag" | "duration";
export type Verdict = "a_better" | "b_better" | "no_difference";

export interface RunSummary {
  run_id: string;
  created_at: string;
  language: string;
  tiers: Tier[];
  n_clips: Partial<Record<Tier, number>>;
  providers: string[];
}

/** Per-language publication status (docs/V2.md, "Languages"). Written by the exporter; computed defensively when missing. */
export interface LanguageStatus {
  lang: string;
  verified_clips: number;
  providers_ok: number;
  published: boolean;
  channels?: number;
  categories?: string[] | number;
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
  verdict: Verdict;
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
  stability: Stability[];
  findings: Finding[];
  errors: { providers: Record<string, ProviderErrors>; examples: ErrorExample[]; all_fail?: string[] };
  clips: Clip[];
  per_clip: PerClip[];
  limitations: string[];
  /** docs/V2.md. Optional until the exporter writes it. */
  languages?: LanguageStatus[];
}
