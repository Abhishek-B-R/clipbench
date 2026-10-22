/**
 * The five models /api/try runs, with list prices and data terms copied from clipbench/providers/pricing.toml
 * (checked_on 2026-10-19). Pure data, no Worker types, so the web tests can check web/src/try/models.ts against it.
 */

export type TryModel = {
  /** clipbench provider id (pricing.toml section). */
  provider: string;
  label: string;
  model_id: string;
  usd_per_minute: number;
  /** The provider bills at least this many seconds per request. */
  min_billed_seconds: number;
  /** Data use and training policy (pricing.toml policy_url, or retention / training_opt_out url). */
  policy_url: string;
  /** One plain sentence on retention and training, from pricing.toml. */
  policy: string;
};

export const TRY_MODELS: readonly TryModel[] = [
  {
    provider: "cf-whisper-large-v3-turbo",
    label: "Workers AI Whisper large-v3 turbo",
    model_id: "@cf/openai/whisper-large-v3-turbo",
    usd_per_minute: 0.00051293,
    min_billed_seconds: 0,
    policy_url: "https://developers.cloudflare.com/workers-ai/platform/data-usage/",
    policy: "Cloudflare does not train on Workers AI inputs and does not keep them after the request.",
  },
  {
    provider: "cf-deepgram-nova-3",
    label: "Workers AI Deepgram Nova-3",
    model_id: "@cf/deepgram/nova-3",
    usd_per_minute: 0.0052,
    min_billed_seconds: 0,
    policy_url: "https://developers.cloudflare.com/workers-ai/platform/data-usage/",
    policy: "Hosted by Cloudflare, not kept after the request; sent with Deepgram's training opt-out.",
  },
  {
    provider: "groq-whisper",
    label: "Groq Whisper large-v3",
    model_id: "whisper-large-v3",
    usd_per_minute: 0.00185,
    min_billed_seconds: 10,
    policy_url: "https://console.groq.com/docs/your-data",
    policy: "Not kept by default, but Groq may log requests for up to 30 days for abuse and reliability.",
  },
  {
    provider: "deepgram",
    label: "Deepgram Nova-3",
    model_id: "nova-3",
    usd_per_minute: 0.0043,
    min_billed_seconds: 0,
    policy_url: "https://developers.deepgram.com/docs/the-deepgram-model-improvement-partnership-program",
    policy: "Sent with mip_opt_out=true, so it stays out of Deepgram's Model Improvement Program.",
  },
  {
    provider: "elevenlabs-scribe",
    label: "ElevenLabs Scribe v2",
    model_id: "scribe_v2",
    usd_per_minute: 0.003667,
    min_billed_seconds: 0,
    policy_url: "https://elevenlabs.io/docs/help-center/legal/is-my-data-used-to-improve-eleven-labs-ai-models",
    policy: "ElevenLabs keeps logs and transcripts by default and may train on non-enterprise audio.",
  },
];

export const TRY_LANGUAGES = ["auto", "en", "fr", "es", "ja", "ko", "hi"] as const;
export type TryLanguage = (typeof TRY_LANGUAGES)[number];

/** List price of one model for a clip of `seconds`. */
export function modelCost(m: TryModel, seconds: number): number {
  return (m.usd_per_minute * Math.max(seconds, m.min_billed_seconds)) / 60;
}

/** Estimated cost of running every model on one clip. */
export function clipCost(seconds: number): number {
  return TRY_MODELS.reduce((s, m) => s + modelCost(m, seconds), 0);
}
