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
  }
}
