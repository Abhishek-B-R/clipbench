import { useMemo } from "react";
import { Link } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { YOUTUBE_NOTE, clipScores, earlyLook, flaggedClips, thumbUrl } from "@/lib/early";
import { LANGUAGE_NAME, clipById, runLanguageStatus, youtubeUrl } from "@/lib/analysis";
import { date, duration, int, pct } from "@/lib/format";
import { CATEGORIES } from "@/lib/config";
import { Figure } from "@/components/ui/Card";
import { GateBars } from "@/components/ui/Gate";
import { buttonClass } from "@/components/ui/Button";
import { EarlyBoardTable, EarlyLabel, LatencyNote, earlySource } from "./EarlyBoard";
import { ClipHeatmap, ErrorCostScatter, HallucinationBars } from "./EarlyCharts";

/**
 * The whole early look for one run: board, error vs cost, hallucination, flagged references, per-clip heatmap,
 * the run's own limitations and, last, the publication gate. Used by /early and by /leaderboard while unpublished.
 */
export function EarlyLookView({ run, tier, metric, showGate = true }: { run: Run; tier: Tier; metric: string; showGate?: boolean }) {
  const look = earlyLook(run, tier, metric);
  const clips = useMemo(() => clipScores(run, tier, metric), [run, tier, metric]);
  const flagged = flaggedClips(run, tier);
  const flaggedClipsList = clips.filter((c) => c.flagged);
  const st = runLanguageStatus(run);
  const lang = LANGUAGE_NAME[run.language] ?? run.language;
  let n = 0;
  const fig = () => ++n;

  if (!look.rows.length)
    return <p className="text-[14px] text-ink-2">This run has no scores on the {tier} tier yet.</p>;

  return (
    <div className="grid min-w-0 grid-cols-1 gap-8">
      <EarlyLabel />

      <Figure
        n={fig()}
        title={`Every system on ${int(look.total)} ${lang} clips, sorted by ${look.metricLabel.toLowerCase()}`}
        caption={
          <>
            Lower is better, but overlapping intervals mean the order between neighbours is not settled. Click a system for its page.{" "}
            <span className="text-ink-3">{YOUTUBE_NOTE}</span>
          </>
        }
        source={earlySource(look)}
        bodyClassName="p-0 sm:p-0"
      >
        <EarlyBoardTable look={look} />
        <div className="grid gap-1 border-t border-line px-4 py-3 sm:px-5">
          <LatencyNote look={look} />
          <p className="text-[12.5px] text-ink-3">Systems with fewer clips are scored on their own clips only, so their intervals are wider and their numbers are not on the same clips as the rest.</p>
        </div>
      </Figure>

      <Figure
        n={fig()}
        title="Error rate against cost per audio hour"
        caption="Each dot is a system with its 95% interval. Free systems ran locally or cost nothing per request. Hollow dots cover fewer clips."
        source={earlySource(look, "aggregates (error_rate, ci_low, ci_high, cost_per_hour, n_clips)")}
      >
        <ErrorCostScatter look={look} />
      </Figure>

      <div className="grid min-w-0 grid-cols-1 gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)] lg:items-start">
        <Figure
          n={fig()}
          title="Hallucination rate"
          caption="Output characters beyond 1.5 times the reference length, per reference character: text a system invented, mostly repetition loops."
          source={earlySource(look, "aggregates (hallucination_rate)")}
        >
          <HallucinationBars look={look} />
        </Figure>

        <Figure
          n={fig()}
          title={`${int(flaggedClipsList.length)} ${flaggedClipsList.length === 1 ? "reference" : "references"} flagged for re-review`}
          caption={`Every system scored above ${pct(flagged.threshold, 0)} error on these clips. That usually means the reference is wrong, not the systems, so they go back to the review queue.`}
          source={`source: ${flagged.source === "export" ? "errors.all_fail" : "per_clip (all systems above the threshold)"}, run=${run.run_id}, tier=${tier}`}
          bodyClassName="p-0 sm:p-0"
        >
          {flaggedClipsList.length ? (
            <ul className="divide-y divide-line">
              {flaggedClipsList.map((c) => (
                <li key={c.clip.video_id} className="flex items-center gap-3 px-4 py-2.5 sm:px-5">
                  <img src={thumbUrl(c.clip.video_id)} alt="" loading="lazy" decoding="async" className="h-9 w-16 shrink-0 rounded-xs bg-sunken object-cover" referrerPolicy="no-referrer" />
                  <span className="min-w-0 flex-1">
                    <Link to={`/clips?q=${encodeURIComponent(c.clip.video_id)}`} className="block truncate font-mono text-[12px] text-ink hover:underline">
                      {c.clip.video_id}
                    </Link>
                    <span className="block truncate text-[12px] text-ink-3">
                      {c.clip.channel}
                      {c.clip.category && CATEGORIES[c.clip.category] ? `, ${CATEGORIES[c.clip.category]!.toLowerCase()}` : ""}, {duration(c.clip.duration_s)}
                    </span>
                  </span>
                  <span className="num shrink-0 text-[12.5px] text-signal-ink">median {pct(c.med, 0)}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="px-4 py-4 text-[13px] text-ink-2 sm:px-5">No clip has every system above the threshold.</p>
          )}
        </Figure>
      </div>

      <Figure
        n={fig()}
        title="Error on every clip, hardest first"
        caption="One row per system, one column per clip. More colour means more errors. Columns where every row is dark are usually a bad reference."
        source={earlySource(look, "per_clip + clips")}
      >
        <ClipHeatmap look={look} clips={clips} />
      </Figure>

      <HardestClips run={run} clips={clips.filter((c) => !c.flagged).slice(0, 4)} />

      {run.limitations.length > 0 && (
        <section aria-labelledby="early-limits" className="rounded-md border border-line bg-surface px-4 py-4 shadow-card sm:px-5">
          <h2 id="early-limits" className="label mb-3">
            Limitations of this run, from the export
          </h2>
          <ul className="grid gap-2 text-[13.5px] text-ink-2">
            {run.limitations.map((l) => (
              <li key={l} className="flex gap-2.5">
                <span aria-hidden className="text-ink-3">
                  -
                </span>
                <span>{l}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {showGate && !st.published && (
        <section aria-labelledby="early-gate" className="grid gap-6 rounded-md border border-dashed border-line-strong bg-paper p-5 sm:p-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] lg:gap-10">
          <div>
            <h2 id="early-gate" className="label">
              <span className="text-signal-ink">Not ranked yet</span> / {lang}
            </h2>
            <p className="mt-3 max-w-[56ch] text-[14px] text-ink-2">
              A ranking needs references a person corrected by ear, enough systems covering them, and a set spread across channels and categories. Until {lang} passes, this page stays an early look.
            </p>
            <div className="mt-4 flex flex-wrap gap-2">
              <Link to={`/lang/${run.language}`} className={buttonClass("secondary", "sm")}>
                {lang} progress <ArrowRight size={14} />
              </Link>
              <Link to="/methodology" className={buttonClass("ghost", "sm")}>
                How scoring works
              </Link>
            </div>
          </div>
          <GateBars status={st} />
        </section>
      )}
      <p className="font-mono text-[11px] text-ink-3">
        run {run.run_id}, {date(run.created_at)}, commit {run.git_commit.slice(0, 7)}
      </p>
    </div>
  );
}

/** The hardest clips that are not flagged: real disagreement, with a thumbnail and the systems that struggled. */
function HardestClips({ run, clips }: { run: Run; clips: ReturnType<typeof clipScores> }) {
  if (!clips.length) return null;
  const label = (id: string) => run.providers.find((p) => p.id === id)?.label ?? id;
  return (
    <section aria-labelledby="early-hardest">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="early-hardest" className="label">
          Hardest clips that are not flagged
        </h2>
        <Link to="/clips" className="inline-flex items-center gap-1 text-[12.5px] text-ink-2 hover:text-ink">
          Clip browser <ArrowRight size={12} />
        </Link>
      </div>
      <ul className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {clips.map((c) => {
          const clip = clipById(run, c.clip.video_id) ?? c.clip;
          return (
            <li key={c.clip.video_id} className="overflow-hidden rounded-md border border-line bg-surface shadow-card">
              <a href={youtubeUrl(clip)} target="_blank" rel="noreferrer" className="group relative block aspect-video bg-sunken">
                <img src={thumbUrl(c.clip.video_id)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" className="size-full object-cover" />
                <span className="absolute right-2 bottom-2 inline-flex items-center gap-1 rounded-xs bg-ink/80 px-1.5 py-0.5 font-mono text-[10.5px] text-paper">
                  {duration(clip.duration_s)} <ArrowUpRight size={11} />
                </span>
              </a>
              <div className="grid gap-1.5 px-3.5 py-3">
                <span className="truncate text-[13px] font-medium text-ink">{clip.channel}</span>
                <span className="text-[12.5px] text-ink-2">
                  Median <span className="num text-signal-ink">{pct(c.med)}</span>. Hardest for {c.worst.slice(0, 2).map((w) => `${label(w.provider)} (${pct(w.err, 0)})`).join(" and ")}.
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
