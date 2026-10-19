import { useCallback, useEffect, useSyncExternalStore } from "react";

/**
 * Theme preference: "system" | "light" | "dark". The resolved theme is written to <html data-theme>,
 * which the CSS tokens read. index.html runs the same logic inline before first paint (no flash).
 */
export type ThemePref = "system" | "light" | "dark";
const KEY = "clipbench-theme";
const listeners = new Set<() => void>();

function read(): ThemePref {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : "system";
  } catch {
    return "system";
  }
}

function systemDark() {
  return typeof window !== "undefined" && window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function resolve(pref: ThemePref): "light" | "dark" {
  return pref === "system" ? (systemDark() ? "dark" : "light") : pref;
}

function apply(pref: ThemePref) {
  document.documentElement.dataset.theme = resolve(pref);
}

let current: ThemePref = typeof window === "undefined" ? "system" : read();

export function setTheme(pref: ThemePref) {
  current = pref;
  try {
    if (pref === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, pref);
  } catch {
    /* storage can be blocked; the choice still applies for this page view */
  }
  apply(pref);
  listeners.forEach((l) => l());
}

export function useTheme() {
  const pref = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => current,
    () => "system" as ThemePref,
  );
  useEffect(() => {
    if (pref !== "system") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const on = () => {
      apply("system");
      listeners.forEach((l) => l());
    };
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [pref]);
  const cycle = useCallback(() => setTheme(resolve(pref) === "dark" ? "light" : "dark"), [pref]);
  return { pref, resolved: resolve(pref), setTheme, cycle };
}
