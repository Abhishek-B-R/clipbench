import { useEffect } from "react";
import { Link, Outlet, ScrollRestoration, useLocation } from "react-router";
import { FlaskConical } from "lucide-react";
import { setDemo, useSource } from "@/lib/data";
import { languageProgress } from "@/lib/analysis";
import { Nav } from "./Nav";
import { Footer } from "./Footer";
import { CommandMenuHost } from "../ui/CommandMenuHost";
import { Toaster } from "../ui/Toast";

/** App shell: demo banner (only on synthetic data), nav, page, footer, command menu, toasts. */
export function Shell() {
  const { pathname } = useLocation();
  // index.html is also the fallback for every route without its own file (scripts/og-routes.ts), and its
  // canonical and og:url point at "/". Keep them on the current page after client-side navigation too.
  useEffect(() => {
    const url = `${window.location.origin}${pathname}`;
    document.querySelector('link[rel="canonical"]')?.setAttribute("href", url);
    document.querySelector('meta[property="og:url"]')?.setAttribute("content", url);
  }, [pathname]);
  return (
    <div className="flex min-h-dvh flex-col">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-ink focus:px-3 focus:py-2 focus:text-paper">
        Skip to content
      </a>
      <DemoBanner />
      <Nav />
      <main id="main" className="flex-1">
        <Outlet />
      </main>
      <Footer />
      <CommandMenuHost />
      <Toaster />
      <ScrollRestoration />
    </div>
  );
}

/**
 * Data banner:
 * - real data while no language is published: "First results in progress" with a link to the progress board;
 * - development only: the synthetic fixture (?demo=1), with "Back to real results" (or "View demo data").
 */
export function DemoBanner() {
  const src = useSource();
  const { pathname } = useLocation();
  const s = src.data;
  if (!s) return null;
  // Real data: say so plainly while no language is published (the homepage says it itself, so skip it there).
  const early = !s.demo && !languageProgress(s.index).some((l) => l.published) && pathname !== "/";
  if (!s.demo && !early) return null;
  const btn = "ml-1 shrink-0 rounded-xs px-1.5 py-0.5 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";
  return (
    <aside aria-label="Data status" className="border-b border-line bg-sunken">
      <div className="frame flex min-h-8 items-center justify-center gap-2 py-1 text-[12px] text-ink-2">
        <FlaskConical size={13} className="shrink-0 text-signal" aria-hidden />
        {import.meta.env.DEV && s.demo ? (
          <span className="min-w-0 truncate">
            <span className="font-medium text-ink">Demo data.</span>
            <span className="hidden sm:inline"> {s.forced ? "You are viewing the synthetic demo run" : "No real run has been published yet"}; models are named A to F and every number is synthetic.</span>
            <span className="sm:hidden"> Synthetic numbers, models A to F.</span>
          </span>
        ) : (
          <span className="min-w-0 truncate">
            <span className="font-medium text-ink">First results in progress.</span>
            <span className="hidden sm:inline"> No language has passed the publication gate yet, so no ranking is shown.</span>
          </span>
        )}
        {import.meta.env.DEV && s.forced ? (
          <button type="button" className={btn} onClick={() => setDemo(false)}>
            Back to real results
          </button>
        ) : !s.demo ? (
          <Link to="/#progress" className={btn}>
            See progress
          </Link>
        ) : null}
        {!import.meta.env.DEV || s.forced ? null : !s.demo ? (
          <button type="button" className={btn} onClick={() => setDemo(true)}>
            View demo data
          </button>
        ) : null}
      </div>
    </aside>
  );
}
