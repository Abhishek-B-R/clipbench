import { Link } from "react-router";
import { motion, useReducedMotion } from "motion/react";
import { ArrowRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { LANGUAGE_NAME, TAG_PHRASE, headline, overall, providerLabel, totalClips, defaultTier } from "@/lib/analysis";
import { date, int, ms, pct, pValue, usdPerHour } from "@/lib/format";
import { buttonClass } from "@/components/ui/Button";
import { Skeleton } from "@/components/ui/Skeleton";
import { Rule } from "@/components/layout/Section";
import { ClipDemo } from "./ClipDemo";
import { CopyCommand } from "./CopyCommand";
import { REPO_SLUG } from "@/lib/config";

const ease = [0.16, 1, 0.3, 1] as const;

export function Hero({ run }: { run: Run | undefined }) {
  const reduce = useReducedMotion();
  const anim = (d: number) =>
    reduce ? {} : { initial: { opacity: 0, y: 14 }, animate: { opacity: 1, y: 0 }, transition: { duration: 0.8, ease, delay: d } };

  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden">
      <div className="frame frame-lines grid gap-12 pt-10 pb-16 sm:pt-16 lg:grid-cols-[minmax(0,1fr)_minmax(0,500px)] lg:gap-12 lg:pt-20 lg:pb-24 xl:grid-cols-[minmax(0,1fr)_560px] xl:gap-16">
        <div className="flex min-w-0 flex-col">
          <motion.div {...anim(0)} className="flex flex-wrap items-center gap-x-3 gap-y-2">
            <span className="inline-flex items-center gap-2 rounded-full border border-line bg-surface py-1 pr-3 pl-1.5 text-[12px] text-ink-2">
              <span className="rounded-full bg-ink px-2 py-0.5 font-mono text-[10.5px] tracking-wide text-paper uppercase">Latest run</span>
              {run ? date(run.created_at) : "Loading"}
            </span>
            {run && (
              <span className="label">
                {LANGUAGE_NAME[run.language] ?? run.language} / {totalClips(run)} {defaultTier(run) === "verified" ? "verified" : "creator-captioned"} clips / {run.providers.length} models
              </span>
            )}
          </motion.div>

          <motion.div {...anim(0.08)}>{run ? <Claim run={run} /> : <ClaimSkeleton />}</motion.div>

          <motion.div {...anim(0.2)} className="mt-9 flex flex-wrap items-center gap-3">
            <Link to="/leaderboard" className={buttonClass("primary", "lg", "group")}>
              See the full leaderboard
              <ArrowRight size={16} className="transition-transform duration-200 group-hover:translate-x-0.5" />
            </Link>
            <CopyCommand command={`git clone https://github.com/${REPO_SLUG}`} display={`git clone github.com/${REPO_SLUG}`} />
          </motion.div>
        </div>

        <motion.div {...(reduce ? {} : { initial: { opacity: 0, y: 24 }, animate: { opacity: 1, y: 0 }, transition: { duration: 1, ease, delay: 0.25 } })} className="relative min-w-0">
          <div aria-hidden className="dot-grid absolute -inset-6 -z-10 [mask-image:radial-gradient(ellipse_at_center,black_40%,transparent_75%)]" />
          <ClipDemo />
        </motion.div>
      </div>
      {run && <StatStrip run={run} />}
    </section>
  );
}

function Claim({ run }: { run: Run }) {
  const h = headline(run);
  const lang = LANGUAGE_NAME[run.language] ?? run.language;
  if (!h) return <h1 id="hero-title" className="display mt-7 text-[44px] sm:text-[64px]">Speech-to-text on {lang} short-form video, measured.</h1>;
  const where = h.tag ? (
    <>
      {" "}with <span className="whitespace-nowrap">{TAG_PHRASE[h.tag] ?? h.tag}</span>
    </>
  ) : null;
  const winner = providerLabel(run, h.winner.provider);
  const loser = providerLabel(run, h.loser.provider);
  const significant = h.p < 0.05;
  const verified = defaultTier(run) === "verified";
  if (!significant)
    return (
      <>
        <h1 id="hero-title" className="display mt-7 text-[38px] leading-[1.05] text-ink sm:text-[52px] xl:text-[58px]">
          On {lang} Shorts{where}, {winner} and {loser} are <Underlined>too close to call</Underlined> so far.
        </h1>
        <p className="mt-6 max-w-[52ch] text-[16px] leading-relaxed text-ink-2 sm:text-[17px]">
          {winner} is ahead at <span className="num text-ink">{pct(h.winner.error_rate)}</span> against <span className="num text-ink">{pct(h.loser.error_rate)}</span>, but the paired bootstrap test finds no detectable difference ({pValue(h.p)}) on <span className="num text-ink">{int(h.n)}</span>{" "}
          {verified ? "hand-checked clips" : "clips scored against creator captions"}. More clips and more models come with the next run.
        </p>
      </>
    );
  return (
    <>
      <h1 id="hero-title" className="display mt-7 text-[38px] leading-[1.05] text-ink sm:text-[52px] xl:text-[58px]">
        On {lang} Shorts{where}, {winner} makes <Underlined>{Math.round(h.fewer * 100)}% fewer errors</Underlined> than {loser}.
      </h1>
      <p className="mt-6 max-w-[52ch] text-[16px] leading-relaxed text-ink-2 sm:text-[17px]">
        {winner} costs <span className="num text-ink">{usdPerHour(h.winner.cost_per_hour)}</span> per hour of audio. Measured on{" "}
        <span className="num text-ink">{int(h.n)}</span> {verified ? "hand-checked clips" : "clips scored against creator captions"} with a paired bootstrap test ({pValue(h.p)}). {countWord(run.providers.length)} models, one clip set, and code anyone can rerun.
      </p>
    </>
  );
}

/** Signal underline drawn under the key phrase once on load. */
function Underlined({ children }: { children: React.ReactNode }) {
  const reduce = useReducedMotion();
  return (
    <span className="relative whitespace-nowrap">
      {children}
      <svg aria-hidden viewBox="0 0 300 12" preserveAspectRatio="none" className="absolute -bottom-[0.12em] left-0 h-[0.2em] w-full overflow-visible">
        <motion.path
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

function ClaimSkeleton() {
  return (
    <div className="mt-7 grid gap-3" aria-busy>
      <Skeleton className="h-14 w-[92%]" />
      <Skeleton className="h-14 w-[80%]" />
      <Skeleton className="h-14 w-[55%]" />
      <Skeleton className="mt-4 h-5 w-[70%]" />
    </div>
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

const WORDS = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"];
function countWord(n: number) {
  return WORDS[n] ?? String(n);
}
