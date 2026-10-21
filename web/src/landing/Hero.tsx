import { Link } from "react-router";
import { m, useReducedMotion } from "motion/react";
import { ArrowRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { LANGUAGE_NAME, overall, providerLabel, defaultTier, type LanguageProgress } from "@/lib/analysis";
import { date, int, ms, pct, usdPerHour } from "@/lib/format";
import { buttonClass } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Rule } from "@/components/layout/Section";
import { CopyCommand } from "./CopyCommand";
import { LANGUAGES, REPO_SLUG, RULES } from "@/lib/config";
import { Badge } from "@/components/ui/Badge";
import { Corners } from "@/components/ui/Card";
import { GateBar } from "@/components/ui/Gate";

const ease = [0.16, 1, 0.3, 1] as const;

/**
 * Homepage hero. Always leads with the positioning question (docs/V2.md). Below it:
 * when a language is published, the latest finding and a stat strip; otherwise "first results: in progress"
 * with live progress toward the publication gate. Every number comes from the export.
 */
export function Hero({ run, progress, published }: { run: Run | undefined; progress: LanguageProgress[] | undefined; published: boolean }) {
  const reduce = useReducedMotion();
  const anim = (d: number) =>
    reduce ? {} : { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.8, ease, delay: d } };
  const n = LANGUAGES.length;

  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden">
      <div className="frame frame-lines grid gap-12 pt-10 pb-16 sm:pt-16 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)] lg:gap-14 lg:pt-20 lg:pb-24">
        <div className="flex min-w-0 flex-col">
          <m.div {...anim(0)} className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-3 pl-1.5 text-[12px] text-ink-2">
              <span className="rounded-full bg-ink px-2 py-0.5 font-mono text-[10.5px] tracking-wide text-paper uppercase">{published ? "Latest run" : "First results"}</span>
              {published && run ? date(run.created_at) : "in progress"}
            </span>
            <span className="label">Independent / open source / no provider funding</span>
          </m.div>

          <m.h1 {...anim(0.06)} id="hero-title" className="display mt-7 max-w-[17ch] text-[44px] leading-[1.02] text-ink sm:text-[60px] xl:text-[68px]">
            Which speech-to-text should your <Underlined>video app</Underlined> use?
          </m.h1>
          <m.p {...anim(0.14)} className="mt-6 max-w-[54ch] text-[17px] leading-relaxed text-ink-2 sm:text-[18px]">
            The independent benchmark for captions on real short-form video: accuracy, word timing, hallucination over music, and cost, in {countWord(n).toLowerCase()} languages.
          </m.p>

          <m.div {...anim(0.22)} className="mt-9 flex flex-wrap items-center gap-3">
            {published ? (
              <Link to="/leaderboard" className={buttonClass("primary", "lg", "group")}>
                See the leaderboard
                <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" />
              </Link>
            ) : (
              <Link to="/methodology" className={buttonClass("primary", "lg", "group")}>
                How it is measured
                <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" />
              </Link>
            )}
            <CopyCommand command={`git clone https://github.com/${REPO_SLUG}`} display={`git clone github.com/${REPO_SLUG}`} />
          </m.div>
        </div>

        <m.div {...anim(0.3)} className="min-w-0 self-end">
          <ProgressCard progress={progress} published={published} />
        </m.div>
      </div>
      {published && run && <StatStrip run={run} />}
    </section>
  );
}

/** Live progress toward the first published results, one bar per language. */
function ProgressCard({ progress, published }: { progress: LanguageProgress[] | undefined; published: boolean }) {
  return (
    <div className="relative rounded-lg border border-line bg-surface shadow-card">
      <Corners />
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <span className="label">{published ? "Languages" : "Progress to first results"}</span>
        <Link to="#progress" className="label hover:text-ink">
          Details
        </Link>
      </div>
      {progress ? (
        <ul>
          {progress.map((l) => (
            <li key={l.lang} className="border-b border-line px-5 py-3.5 last:border-b-0">
              <div className="mb-2 flex items-center justify-between gap-3">
                <Link to={`/lang/${l.lang}`} className="text-[14px] font-medium text-ink hover:underline">
                  {LANGUAGE_NAME[l.lang] ?? l.lang}
                </Link>
                <span className="flex items-center gap-2.5">
                  {l.scored > 0 && !l.published && <span className="num text-[11.5px] text-ink-3">{int(l.scored)} scored</span>}
                  {l.published ? <Badge tone="good">Published</Badge> : l.latestRun ? <Badge tone="outline">Collecting</Badge> : <Badge>Not started</Badge>}
                </span>
              </div>
              <GateBar label="Verified clips" value={l.verified_clips} target={RULES.publishMinVerified} />
            </li>
          ))}
        </ul>
      ) : (
        <div className="grid gap-3 p-5">
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-9 w-full" />
          ))}
        </div>
      )}
      <p className="border-t border-line px-5 py-3 text-[12px] text-ink-3">
        A language is ranked at {int(RULES.publishMinVerified)} verified clips, {int(RULES.publishMinProviders)} providers with results on at least {pct(RULES.minProviderCoverage, 0)} of them, and a verified set spread across channels and categories.
      </p>
    </div>
  );
}

/** Signal underline drawn under the key phrase once on load. */
function Underlined({ children }: { children: React.ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <span className="relative whitespace-nowrap">
      {children}
      <svg aria-hidden viewBox="0 0 300 12" preserveAspectRatio="none" className="absolute -bottom-[0.12em] left-0 h-[0.2em] w-full overflow-visible">
        <m.path
          d="M2 8 C 60 3, 120 3, 170 6 S 260 10, 298 4"
          fill="none"
          stroke="var(--signal)"
          strokeWidth="3.5"
          strokeLinecap="round"
          vectorEffect="non-scaling-stroke"
          initial={reduce ? false : { pathLength: 0 }}
          animate={{ pathLength: 1 }}
          transition={{ duration: 1.1, ease, delay: 0.7 }}
        />
      </svg>
    </span>
  );
}

function StatStrip({ run }: { run: Run }) {
  const rows = overall(run);
  const best = rows[0];
  const paid = rows.filter((r) => (r.cost_per_hour ?? 0) > 0).sort((a, b) => (a.cost_per_hour ?? 0) - (b.cost_per_hour ?? 0))[0];
  const fastest = [...rows].filter((r) => r.latency_p50 != null).sort((a, b) => (a.latency_p50 ?? 0) - (b.latency_p50 ?? 0))[0];
  const clips = run.clips.filter((c) => c.tier === defaultTier(run));
  const channels = new Set(clips.map((c) => c.channel)).size;
  const stats = [
    best && { value: pct(best.error_rate), label: "Lowest error overall", note: `${providerLabel(run, best.provider)}, 95% interval ${pct(best.ci_low)} to ${pct(best.ci_high)}.` },
    paid && { value: usdPerHour(paid.cost_per_hour), label: "Cheapest paid model, per hour", note: `${providerLabel(run, paid.provider)}, at ${pct(paid.error_rate)} error.` },
    fastest && { value: ms(fastest.latency_p50), label: "Fastest median latency", note: `${providerLabel(run, fastest.provider)}, per clip, upload included.` },
    defaultTier(run) === "verified"
      ? { value: int(clips.length), label: "Clips checked by a person", note: `From ${channels} channels. References corrected by ear.` }
      : { value: int(clips.length), label: "Clips scored so far", note: `From ${channels} channels, against creator captions. None verified by ear yet.` },
  ].filter(Boolean) as { value: string; label: string; note: string }[];
  return (
    <>
      <Rule />
      <div className="frame frame-lines">
        <dl className="grid grid-cols-2 lg:grid-cols-4">
          {stats.map((s, i) => (
            <div key={s.label} className={`py-7 pr-4 sm:py-9 ${i % 2 ? "border-l border-line pl-4 sm:pl-6" : ""} ${i === 2 ? "lg:border-l lg:border-line lg:pl-6" : ""} ${i > 1 ? "border-t border-line lg:border-t-0" : ""}`}>
              <dt className="label">{s.label}</dt>
              <dd className="mt-3 text-[32px] leading-none font-medium tracking-[-0.03em] text-ink sm:text-[40px]" style={{ fontStretch: "95%" }}>
                {s.value}
              </dd>
              <dd className="mt-3 max-w-[30ch] text-[13px] text-ink-2">{s.note}</dd>
            </div>
          ))}
        </dl>
      </div>
    </>
  );
}

export const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
export function countWord(n: number) {
  return WORDS[n] ?? String(n);
}
