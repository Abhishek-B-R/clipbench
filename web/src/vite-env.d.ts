/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" or "true" turns on sign-in, account, requests and alerts (docs/V2.md, "Web flags"). Default off. */
  readonly VITE_ACCOUNTS?: string;
}
