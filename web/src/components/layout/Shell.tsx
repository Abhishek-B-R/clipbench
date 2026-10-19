import { Outlet, ScrollRestoration } from "react-router";
import { FlaskConical } from "lucide-react";
import { useSource } from "@/lib/data";
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

/** Shown when the data is the synthetic sample (index.json has "demo": true, or the real export is missing). */
export function DemoBanner() {
  const src = useSource();
  if (!src.data?.demo) return null;
  return (
    <div role="note" className="border-b border-line bg-sunken">
      <div className="frame flex h-8 items-center justify-center gap-2 text-[12px] text-ink-2">
        <FlaskConical size={13} className="text-signal" aria-hidden />
        <span className="truncate">
          <span className="font-medium text-ink">Demo data.</span>
          <span className="hidden sm:inline"> No real run has been published yet; models are named A to F and every number is synthetic.</span>
          <span className="sm:hidden"> Synthetic numbers, models A to F.</span>
        </span>
      </div>
    </div>
  );
}
