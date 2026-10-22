import { Link } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { ReactNode } from "react";
import type { LanguageStatus, ProviderExclusion, Run } from "@/lib/types";
import { LANGUAGE_NAME, excludedProviders, hasGate, providerLabel, runLanguageStatus, type LanguageProgress } from "@/lib/analysis";
import { RULES } from "@/lib/config";
import { date, int, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { earlyLook } from "@/lib/early";
import { EarlyBoardTable, earlySource } from "@/components/early/EarlyBoard";
import { Badge } from "./Badge";
import { Corners, Figure } from "./Card";
import { buttonClass } from "./Button";

/**
 * Publication gate UI (docs/V2.md "Languages", docs/V3.md "Publication gate fields"). A language shows a ranking only
 * when the exporter's three gate flags are true: enough verified clips, enough eligible providers (coverage at least
 * `min_coverage`), and a diverse verified set. Every value here is read from the export, never recomputed.
 */

/** The coverage threshold to state: the export's, else the configured default. */
export function minCoverage(status?: Pick<LanguageStatus, "min_coverage">) {
  return typeof status?.min_coverage === "number" ? status.min_coverage : RULES.minProviderCoverage;
}

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

/** One pass/fail condition with the exporter's problems listed under it. */
export function GateCheck({ label, ok, problems, className }: { label: string; ok: boolean | null; problems?: string[]; className?: string }) {
  return (
    <div className={cn("grid gap-1.5", className)}>
      <div className="flex items-baseline justify-between gap-3 text-[12.5px]">
        <span className="text-ink-2">{label}</span>
        <span className={cn("num", ok ? "text-good" : "text-ink")}>{ok == null ? "unknown" : ok ? "passes" : "not met"}</span>
      </div>
      {problems && problems.length > 0 && (
        <ul className="grid gap-1 text-[12.5px] text-ink-3" aria-label={`${label}: problems`}>
          {problems.map((p) => (
            <li key={p} className="flex gap-2">
              <span aria-hidden>-</span>
              <span>{p}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The three publication conditions: verified clips, eligible providers, diversity of the verified set. */
export function GateBars({ status, className }: { status: LanguageStatus; className?: string }) {
  const gated = hasGate(status);
  const div = status.verified_diversity;
  return (
    <div className={cn("grid gap-4", className)}>
      <GateBar label="Verified clips" value={status.verified_clips} target={RULES.publishMinVerified} />
      <GateBar label={`Eligible providers (at least ${pct(minCoverage(status), 0)} coverage)`} value={gated ? status.providers_ok : 0} target={RULES.publishMinProviders} />
      <GateCheck label="Verified set diversity" ok={gated ? status.gate.diversity_ok === true : null} problems={gated ? (div?.problems ?? []) : []} />
      {!gated && <p className="text-[12.5px] text-ink-3">This export has no publication gate fields, so it is treated as not published.</p>}
    </div>
  );
}

/**
 * Providers that are not ranked, with their coverage and the exporter's reason. Shown next to every ranked list so
 * an excluded provider is explained, never ranked.
 */
export function NotRankedList({ items, label, min, className, title = "Not ranked" }: { items: ProviderExclusion[]; label?: (id: string) => string; min?: number; className?: string; title?: string }) {
  if (!items.length) return null;
  return (
    <section className={cn("@container rounded-sm border border-dashed border-line-strong bg-paper px-4 py-3 sm:px-5", className)} aria-label={title} data-testid="not-ranked">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <Badge tone="outline">{title}</Badge>
        <span className="text-[12.5px] text-ink-3">
          {min != null ? `A provider is ranked only with results on at least ${pct(min, 0)} of the verified clips.` : "These providers have no eligibility in this export."}
        </span>
      </div>
      <ul className="mt-3 grid gap-2">
        {items.map((e) => (
          <li key={e.provider} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-4 gap-y-0.5 text-[13px] @2xl:grid-cols-[minmax(0,220px)_124px_minmax(0,1fr)]" data-provider={e.provider}>
            <Link to={`/providers/${encodeURIComponent(e.provider)}`} className="font-medium text-ink hover:underline">
              {label ? label(e.provider) : e.provider}
            </Link>
            <span className="num whitespace-nowrap text-ink-2">coverage {pct(e.coverage)}</span>
            <span className="col-span-2 text-ink-3 @2xl:col-span-1">{e.reason ?? "Not eligible in this run."}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The run's not-ranked group (excludedProviders), labelled with the run's provider names. */
export function NotRanked({ run, className, title }: { run: Run; className?: string; title?: string }) {
  const st = runLanguageStatus(run);
  return <NotRankedList items={excludedProviders(run)} label={(id) => providerLabel(run, id)} min={hasGate(st) ? minCoverage(st) : undefined} className={className} title={title} />;
}

/**
 * Stand-in for a ranking view (compare, picker, findings, streaming) while a language is below the gate. With a run it
 * shows what that run measured: every system on the same clips, unranked, with a link to the full early look. Without
 * one it says the language is set up and how its first run starts. No checklist of unmet conditions: the language
 * page carries the gate.
 */
export function NotPublished({ status, className, run }: { status: LanguageStatus; scored?: number; models?: number; what?: string; className?: string; run?: Run }) {
  const name = LANGUAGE_NAME[status.lang] ?? status.lang;
  const look = run ? earlyLook(run) : null;
  if (run && look && look.rows.length > 0)
    return (
      <section className={cn("grid min-w-0 gap-5", className)}>
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="max-w-[68ch]">
            <div className="label">
              <span className="text-signal-ink">Early look</span> / {name}
            </div>
            <h2 className="mt-3 text-[22px] leading-tight font-semibold tracking-[-0.02em] text-ink sm:text-[26px]" style={{ fontStretch: "94%" }}>
              Every system on the same {int(look.total)} {name} clips.
            </h2>
            <p className="mt-2 text-[14.5px] text-ink-2">
              Rankings and head-to-head verdicts open once {int(RULES.publishMinVerified)} clips have captions corrected by ear. Until then, here is what the first run measured against the creators' own captions, without ranks.
            </p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to={`/early?lang=${run.language}`} className={buttonClass("primary", "sm")}>
              Full early look <ArrowRight size={14} />
            </Link>
            <Link to="/methodology" className={buttonClass("secondary", "sm")}>
              How scoring works
            </Link>
          </div>
        </div>
        <Figure n={1} title={`${int(look.rows.length)} systems, sorted by ${look.metricLabel.toLowerCase()}`} caption="Lower is better. Overlapping intervals mean the order between neighbours is not settled." source={earlySource(look)} bodyClassName="p-0 sm:p-0">
          <EarlyBoardTable look={look} />
        </Figure>
      </section>
    );
  return <LanguageReady lang={status.lang} className={className} />;
}

/** A language with no run: what is already in place and the commands that start its first run. */
export function LanguageReady({ lang, className }: { lang: string; className?: string }) {
  const name = LANGUAGE_NAME[lang] ?? lang;
  return (
    <section className={cn("relative grid gap-8 rounded-md border border-line bg-surface p-6 shadow-card sm:p-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-12", className)}>
      <Corners className="pointer-events-none absolute inset-0" />
      <div>
        <div className="label">{name} / set up</div>
        <h2 className="mt-4 text-[22px] leading-tight font-semibold tracking-[-0.02em] text-ink sm:text-[26px]" style={{ fontStretch: "94%" }}>
          {name} runs on the same pipeline as every language.
        </h2>
        <p className="mt-3 max-w-[60ch] text-[14.5px] text-ink-2">
          Text normalisation, filler words, provider language codes and a starter channel list for {name} are in the repository. Results land here after its first run; the Japanese run shows what that looks like.
        </p>
        <div className="mt-6 flex flex-wrap gap-2">
          <Link to="/early" className={buttonClass("primary", "sm")}>
            See the Japanese run <ArrowRight size={14} />
          </Link>
          <Link to="/try" className={buttonClass("secondary", "sm")}>
            Try {name} on your own clip
          </Link>
        </div>
      </div>
      <pre className="self-center overflow-x-auto rounded-sm border border-line bg-paper p-4 font-mono text-[12px] leading-relaxed text-ink-2">
        <span className="text-ink-3"># start the first run</span>
        {"\n"}uv run clipbench data discover --lang {lang} \{"\n"}  --channels datasets/{lang}/channels.csv{"\n"}make dry-run run report
      </pre>
    </section>
  );
}

/** Every language: what its latest run scored, or that it is set up. Rows link to the language page. */
export function LanguageBoard({ rows, className }: { rows: LanguageProgress[]; className?: string }) {
  return (
    <ol className={cn("overflow-hidden rounded-md border border-line bg-surface shadow-card", className)}>
      {rows.map((l, i) => (
        <li key={l.lang} className="border-b border-line last:border-b-0">
          <Link to={`/lang/${l.lang}`} className="group grid gap-x-8 gap-y-2 px-4 py-4 transition-colors hover:bg-sunken/50 sm:px-5 md:grid-cols-[minmax(0,190px)_minmax(0,1fr)_150px] md:items-center">
            <span className="flex items-center gap-3">
              <span className="num text-[12px] text-ink-3">{String(i + 1).padStart(2, "0")}</span>
              <span className="text-[15px] font-medium text-ink">{LANGUAGE_NAME[l.lang] ?? l.lang}</span>
            </span>
            <span className="text-[13px] text-ink-2">
              {l.published ? (
                <>
                  <span className="num text-ink">{int(l.verified_clips)}</span> verified clips, <span className="num text-ink">{int(l.providers_ok)}</span> ranked systems
                </>
              ) : l.latestRun ? (
                <>
                  <span className="num text-ink">{int(l.scored)}</span> clips scored with <span className="num text-ink">{int(l.models)}</span> systems{l.latestAt ? `, ${date(l.latestAt)}` : ""}
                </>
              ) : (
                "Scoring rules and channel list in the repository"
              )}
            </span>
            <span className="flex items-center justify-between gap-3 md:justify-end">
              {l.published ? <Badge tone="good">Ranked</Badge> : l.latestRun ? <Badge tone="warn">Early look</Badge> : <Badge>Set up</Badge>}
              <ArrowUpRight size={14} className="text-ink-3 transition-colors group-hover:text-ink" aria-hidden />
            </span>
          </Link>
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
  return <NotPublished status={st} scored={run.clips.length} models={run.providers.length} what={what} run={run} />;
}
