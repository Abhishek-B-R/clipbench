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

export interface RunIndex {
  /** Set only on the synthetic sample. The UI shows a "demo data" banner when true. */
  demo?: boolean;
  latest: string;
  runs: RunSummary[];
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
}
