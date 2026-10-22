import { useEffect, useRef, useState, type RefObject } from "react";
import { useTheme } from "@/lib/theme";

/**
 * Cloudflare Turnstile, loaded only on /try (explicit render). Site key from VITE_TURNSTILE_SITE_KEY; without it,
 * Cloudflare's documented always-pass test key (pairs with the test secret in web/.dev.vars.example).
 * A token is single use: call `reset()` after each run for a fresh one.
 */
export const TEST_SITE_KEY = "1x00000000000000000000AA";
export const SITE_KEY: string = (import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined)?.trim() || TEST_SITE_KEY;
const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

type Turnstile = {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (id: string) => void;
  remove: (id: string) => void;
};
declare global {
  interface Window {
    turnstile?: Turnstile;
  }
}

let loading: Promise<Turnstile> | null = null;
function load(): Promise<Turnstile> {
  if (window.turnstile) return Promise.resolve(window.turnstile);
  loading ??= new Promise<Turnstile>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = SCRIPT;
    s.async = true;
    s.defer = true;
    s.onload = () => (window.turnstile ? resolve(window.turnstile) : reject(new Error("turnstile missing")));
    s.onerror = () => {
      loading = null;
      reject(new Error("turnstile blocked"));
    };
    document.head.appendChild(s);
  });
  return loading;
}

export type TurnstileState = { token: string | null; status: "loading" | "ready" | "error"; reset: () => void };

export function useTurnstile(container: RefObject<HTMLDivElement | null>): TurnstileState {
  const [token, setToken] = useState<string | null>(null);
  const [status, setStatus] = useState<TurnstileState["status"]>("loading");
  const id = useRef<string | null>(null);
  const api = useRef<Turnstile | null>(null);
  const { resolved } = useTheme();
  useEffect(() => {
    let alive = true;
    load().then(
      (t) => {
        if (!alive || !container.current) return;
        api.current = t;
        container.current.replaceChildren();
        id.current = t.render(container.current, {
          sitekey: SITE_KEY,
          action: "try",
          theme: resolved,
          size: "flexible",
          appearance: "always",
          callback: (tok: string) => {
            setToken(tok);
            setStatus("ready");
          },
          "expired-callback": () => setToken(null),
          "error-callback": () => {
            setToken(null);
            setStatus("error");
          },
        });
        setStatus("ready");
      },
      () => alive && setStatus("error"),
    );
    return () => {
      alive = false;
      if (id.current && api.current) api.current.remove(id.current);
      id.current = null;
    };
  }, [container, resolved]);
  return {
    token,
    status,
    reset: () => {
      setToken(null);
      if (id.current && api.current) api.current.reset(id.current);
    },
  };
}
