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
} as const;
