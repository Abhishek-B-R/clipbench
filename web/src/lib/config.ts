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
  /** ... and at least this many ELIGIBLE providers in its latest run (docs/V3.md, "Publication gate fields") ... */
  publishMinProviders: 6,
  /**
   * ... where a provider is eligible with ok rows on at least this share of the verified clips (clipbench.toml
   * min_provider_coverage, default). Display only: the export's `min_coverage` wins when present.
   */
  minProviderCoverage: 0.9,
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

/** The positioning line (docs/V2.md). */
export const POSITIONING = {
  question: "Which speech-to-text should your video app use?",
};

/**
 * Public data API paths (A12), exactly as web/worker/src/routes/results.ts serves them under /api/v1. The site only
 * documents them and builds the badge snippet. Keep this list in step with the Worker.
 */
export const API_V1 = {
  runs: "/api/v1/runs",
  run: (id: string) => `/api/v1/runs/${encodeURIComponent(id)}`,
  /** ?metric=&tier=&slice_kind=&slice= */
  leaderboard: (lang: string) => `/api/v1/leaderboard/${encodeURIComponent(lang)}`,
  model: (provider: string) => `/api/v1/models/${encodeURIComponent(provider)}`,
  /** ?provider= */
  history: (lang: string) => `/api/v1/history/${encodeURIComponent(lang)}`,
  drift: (lang: string) => `/api/v1/drift/${encodeURIComponent(lang)}`,
  reliability: (lang: string) => `/api/v1/reliability/${encodeURIComponent(lang)}`,
  /** ?metric= */
  badge: (provider: string, lang: string) => `/api/v1/badge/${encodeURIComponent(provider)}/${encodeURIComponent(lang)}.svg`,
};

/** Embed snippets for a provider's badge: Markdown and HTML, linking back to its model page. */
export function badgeSnippets(origin: string, id: string, lang: string, label: string) {
  const img = `${origin}${API_V1.badge(id, lang)}`;
  const page = `${origin}/providers/${encodeURIComponent(id)}`;
  const alt = `clipbench: ${label}, ${lang}`;
  return {
    markdown: `[![${alt}](${img})](${page})`,
    html: `<a href="${page}"><img src="${img}" alt="${alt}" height="20"></a>`,
    img,
    page,
  };
}
