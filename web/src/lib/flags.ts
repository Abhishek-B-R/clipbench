/**
 * Build-time feature flags (docs/V2.md, "Web flags").
 * ACCOUNTS: sign-in, account, requests, alerts, watch and save. Off by default: the public benchmark needs no
 * login, and with it off the site never calls /api/auth. The code stays; set VITE_ACCOUNTS=1 to bring it back.
 */
export const ACCOUNTS = import.meta.env.VITE_ACCOUNTS === "1" || import.meta.env.VITE_ACCOUNTS === "true";
