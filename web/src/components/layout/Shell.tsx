import { Outlet, ScrollRestoration } from "react-router";
import { FlaskConical } from "lucide-react";
import { setDemo, useSource } from "@/lib/data";
import { Nav } from "./Nav";
import { Footer } from "./Footer";
import { CommandMenu } from "../ui/CommandMenu";
import { Toaster } from "../ui/Toast";

/** App shell: demo banner (only on synthetic data), nav, page, footer, command menu, toasts. */
export function Shell() {
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
      <CommandMenu />
      <Toaster />
      <ScrollRestoration />
    </div>
  );
}

/**
 * Data banner. Three states:
 * - development only: the synthetic fixture (?demo=1), with "Back to real results";
 * - a real but early run (no verified tier or under 30 clips): plain warning (plus "View demo data" in dev).
 */
export function DemoBanner() {
  const src = useSource();
  const s = src.data;
  if (!s) return null;
  const latest = s.index.runs.find((r) => r.run_id === s.index.latest) ?? s.index.runs[0];
  const n = latest ? Object.values(latest.n_clips).reduce((a, b) => a + (b ?? 0), 0) : 0;
  const creatorOnly = latest ? !latest.tiers.includes("verified") : false;
  const early = !s.demo && latest && (creatorOnly || n < 30);
  if (!s.demo && !early) return null;
  const btn = "ml-1 shrink-0 rounded-xs px-1.5 py-0.5 font-medium text-ink underline decoration-line-strong underline-offset-[3px] hover:decoration-ink";
  return (
    <div role="note" className="border-b border-line bg-sunken">
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
            <span className="font-medium text-ink">Early run.</span>
            <span className="hidden sm:inline">
              {" "}
              {n} clips{creatorOnly ? ", creator captions only (none verified yet)" : ""}. Intervals are wide; most slices are too small to conclude.
            </span>
            <span className="sm:hidden"> {n} clips, too few to conclude.</span>
          </span>
        )}
        {import.meta.env.DEV && s.forced ? (
          <button type="button" className={btn} onClick={() => setDemo(false)}>
            Back to real results
          </button>
        ) : !s.demo && import.meta.env.DEV ? (
          <button type="button" className={btn} onClick={() => setDemo(true)}>
            View demo data
          </button>
        ) : null}
      </div>
    </div>
  );
}
