// Secrets and vars the Worker reads. wrangler.jsonc has no "secrets.required" block (see the comment there), so
// `wrangler types` may not list them; they are declared here. Any of them can be absent at runtime: code checks
// with set()/truthiness before use, and with ACCOUNTS off none of them is read.
interface Env {
  ACCOUNTS: string;
  BETTER_AUTH_SECRET: string;
  BETTER_AUTH_URL: string;
  GITHUB_CLIENT_ID: string;
  GITHUB_CLIENT_SECRET: string;
  GOOGLE_CLIENT_ID: string;
  GOOGLE_CLIENT_SECRET: string;
  RESEND_API_KEY: string;
  EMAIL_FROM: string;
  ADMIN_TOKEN: string;
  /** Shared with the clipbench.abhishekbr.com forwarding Worker (web/proxy/); see clientIp() in http.ts. */
  PROXY_KEY: string;
  /** /api/try (routes/try.ts). Turnstile secret; provider keys; daily budget in USD; local fake providers. */
  TURNSTILE_SECRET: string;
  GROQ_API_KEY: string;
  DEEPGRAM_API_KEY: string;
  ELEVENLABS_API_KEY: string;
  TRY_DAILY_BUDGET_USD: string;
  TRY_FAKE_PROVIDERS: string;
}
declare namespace Cloudflare {
  interface Env {
    ACCOUNTS: string;
    BETTER_AUTH_SECRET: string;
    BETTER_AUTH_URL: string;
    GITHUB_CLIENT_ID: string;
    GITHUB_CLIENT_SECRET: string;
    GOOGLE_CLIENT_ID: string;
    GOOGLE_CLIENT_SECRET: string;
    RESEND_API_KEY: string;
    EMAIL_FROM: string;
    ADMIN_TOKEN: string;
    PROXY_KEY: string;
    TURNSTILE_SECRET: string;
    GROQ_API_KEY: string;
    DEEPGRAM_API_KEY: string;
    ELEVENLABS_API_KEY: string;
    TRY_DAILY_BUDGET_USD: string;
    TRY_FAKE_PROVIDERS: string;
  }
}
