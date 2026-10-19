/** Site-wide constants. */
export const GITHUB_URL = "https://github.com/Abhishek-B-R/clipbench";
export const REPO_SLUG = "Abhishek-B-R/clipbench";
export const SITE_NAME = "clipbench";

/** Methodology constants (rules of the benchmark, not results). Copy that states a rule reads them from here. */
export const RULES = {
  /** Default per-run budget cap in USD (clipbench.toml budget_usd). */
  budgetCapUsd: 10,
  /** Slices with fewer clips are marked too small to conclude. */
  minSliceClips: 15,
  /** A language is published only with at least this many verified clips (docs/V2.md). */
  publishMinVerified: 100,
  /** ... and at least this many providers with ok rows in its latest run. */
  publishMinProviders: 6,
  /** Bootstrap resamples for every interval and paired test. */
  bootstrapResamples: 1000,
  /** Dataset diversity (docs/V2.md): at least this many channels per language ... */
  minChannels: 30,
  /** ... no channel above this share of a language's set ... */
  maxChannelShare: 0.05,
  /** ... and at least this many content categories (not counting "other"). */
  minCategories: 6,
} as const;

/** v2 languages, in shipping order (docs/V2.md). */
export const LANGUAGES = ["en", "ja", "ko", "es", "hi"] as const;

/** Content categories, exact strings from docs/V2.md, with plain names. */
export const CATEGORIES: Record<string, string> = {
  talking_head: "Talking head",
  vlog_music: "Vlog with music",
  gaming: "Gaming",
  comedy_skit: "Comedy and skits",
  cooking_tutorial: "Cooking and tutorials",
  news_explainer: "News and explainers",
};

/** Audio conditions, docs/V3.md (A4), in display order. */
export const CONDITION_ORDER = ["music", "sfx", "multiple_speakers", "fast_speech", "whisper_shout", "outdoor_noise", "code_switching"];
export const CONDITION_LABEL: Record<string, string> = {
  music: "Music under speech",
  sfx: "Sound effects",
  multiple_speakers: "Several speakers",
  fast_speech: "Fast speech",
  whisper_shout: "Whisper or shout",
  outdoor_noise: "Outdoor noise",
  code_switching: "Code-switching",
};

/** The positioning line (docs/REPOSITION-PROMPT.md, section 2). */
export const POSITIONING = {
  question: "Which speech-to-text should your video app use?",
};
