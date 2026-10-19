import { Link } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import type { LanguageStatus, Run } from "@/lib/types";
import { LANGUAGE_NAME, runLanguageStatus, type LanguageProgress } from "@/lib/analysis";
import { RULES } from "@/lib/config";
import { date, int } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Badge } from "./Badge";
import { Corners } from "./Card";
import { buttonClass } from "./Button";

/**
 * Publication gate UI (docs/V2.md, "Languages"). A language shows a ranking only with enough verified clips and
 * providers; until then every view shows progress toward the gate, computed from the export, never a ranking.
 */

/** One "n of target" bar. Ink fill, hairline track, numbers in mono. */
export function GateBar({ label, value, target, className }: { label: string; value: number; target: number; className?: string }) {
  const share = target > 0 ? Math.min(1, value / target) : 0;
  return (
    <div className={cn("grid gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
        <span className="text-ink-2">{label}</span>
        <span className="num text-ink">
          {int(value)} <span className="text-ink-3">of {int(target)}</span>
        </span>
      </div>
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={target}
        aria-valuenow={Math.min(value, target)}
        className="relative h-1.5 overflow-hidden rounded-full bg-sunken ring-1 ring-line ring-inset"
      >
        <span className={cn("absolute inset-y-0 left-0 rounded-full", share >= 1 ? "bg-good" : "bg-ink")} style={{ width: `${share * 100}%` }} />
      </div>
    </div>
  );
}

export function GateBars({ status, className }: { status: LanguageStatus; className?: string }) {
  return (
    <div className={cn("grid gap-4", className)}>
      <GateBar label="Verified clips" value={status.verified_clips} target={RULES.publishMinVerified} />
      <GateBar label="Providers with results" value={status.providers_ok} target={RULES.publishMinProviders} />
    </div>
  );
}

/** Calm "not published yet" state for ranking views (leaderboard, compare, picker, model pages). */
export function NotPublished({ status, scored, models, what = "ranking", className }: { status: LanguageStatus; scored?: number; models?: number; what?: string; className?: string }) {
  const name = LANGUAGE_NAME[status.lang] ?? status.lang;
  return (
    <section className={cn("relative grid gap-8 rounded-md border border-line bg-surface p-6 shadow-card sm:p-8 lg:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)] lg:gap-12", className)}>
      <Corners className="pointer-events-none absolute inset-0" />
      <div>
        <div className="label flex items-center gap-2">
          <span className="text-signal-ink">Not published yet</span> / {name}
        </div>
        <h2 className="mt-4 text-[24px] leading-tight font-semibold tracking-[-0.02em] text-ink sm:text-[28px]" style={{ fontStretch: "94%" }}>
          No {what} for {name} yet.
        </h2>
        <p className="mt-3 max-w-[60ch] text-[15px] text-ink-2">
          A language gets a ranking once it has {int(RULES.publishMinVerified)} clips whose captions a person corrected by ear and results from at least {int(RULES.publishMinProviders)} providers. Below that, the
          intervals are too wide to say which model is better, so we show progress instead of a table that would mislead.
        </p>
        {scored != null && scored > 0 && (
          <p className="mt-3 max-w-[60ch] text-[13.5px] text-ink-3">
            Scored so far: {int(scored)} {scored === 1 ? "clip" : "clips"}
            {models != null ? ` with ${int(models)} ${models === 1 ? "model" : "models"}` : ""}, as pipeline checks. Per-clip scores are in the clip browser.
          </p>
        )}
        <div className="mt-6 flex flex-wrap gap-2">
          <Link to={`/lang/${status.lang}`} className={buttonClass("primary", "sm")}>
            {name} progress <ArrowRight size={14} />
          </Link>
          <Link to="/methodology" className={buttonClass("secondary", "sm")}>
            How scoring works
          </Link>
          <Link to="/clips" className={buttonClass("ghost", "sm")}>
            Clip browser
          </Link>
        </div>
      </div>
      <div className="self-center rounded-sm border border-line bg-paper p-5">
        <div className="label mb-4">Publication gate</div>
        <GateBars status={status} />
      </div>
    </section>
  );
}

/** Every v2 language with its progress toward the gate. Rows link to the language page. */
export function LanguageBoard({ rows, className }: { rows: LanguageProgress[]; className?: string }) {
  return (
    <ol className={cn("overflow-hidden rounded-md border border-line bg-surface shadow-card", className)}>
      {rows.map((l, i) => (
        <li key={l.lang} className="border-b border-line last:border-b-0">
          <Link to={`/lang/${l.lang}`} className="group grid gap-x-8 gap-y-3 px-4 py-4 transition-colors hover:bg-sunken/50 sm:px-5 md:grid-cols-[minmax(0,190px)_minmax(0,1fr)_minmax(0,1fr)_150px] md:items-center">
            <span className="flex items-center gap-3">
              <span className="num text-[12px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[15px] font-medium text-ink">{LANGUAGE_NAME[l.lang] ?? l.lang}</span>
            </span>
            <GateBar label="Verified clips" value={l.verified_clips} target={RULES.publishMinVerified} />
            <GateBar label="Providers" value={l.providers_ok} target={RULES.publishMinProviders} />
            <span className="flex items-center justify-between gap-3 md:justify-end">
              {l.published ? <Badge tone="good">Published</Badge> : l.latestRun ? <Badge tone="outline">Collecting</Badge> : <Badge>Not started</Badge>}
              <ArrowUpRight size={14} className="text-ink-3 transition-colors group-hover:text-ink" aria-hidden />
            </span>
          </Link>
          {l.latestAt && (
            <span className="sr-only">
              Latest run {date(l.latestAt)}, {int(l.scored)} clips scored.
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

/**
 * Wrap a ranking view: renders `children` only when the run's language is published, otherwise the calm
 * not-published state with progress. Without a run (still loading) it renders children, which show skeletons.
 */
export function Gated({ run, what, children, lang }: { run: Run | null | undefined; what?: string; children: ReactNode; lang?: string }) {
  if (run === undefined) return <>{children}</>;
  if (run === null) return <NotPublished status={{ lang: lang ?? "en", verified_clips: 0, providers_ok: 0, published: false }} what={what} />;
  const st = runLanguageStatus(run);
  if (st.published) return <>{children}</>;
  return <NotPublished status={st} scored={run.clips.length} models={run.providers.length} what={what} />;
}
