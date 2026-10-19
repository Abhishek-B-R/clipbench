import { Link } from "react-router";
import { ArrowDown, ArrowRight, ArrowUp, Download, Plus, Minus, RefreshCw } from "lucide-react";
import type { Run } from "@/lib/types";
import { useAllRuns } from "@/lib/data";
import { LANGUAGE_NAME, TIER_LABEL, defaultTier, primaryMetric, providerColor, providerLabel, runChanges, type RunChange } from "@/lib/analysis";
import { date, pct, signedPts } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { buttonClass } from "@/components/ui/Button";
import { EmptyState, ErrorState } from "@/components/ui/State";

/** Every published run, newest first: what changed in the line-up, who leads, and how each model moved. */
export default function Changelog() {
  const state = useAllRuns();
  const runs = state.data?.runs ?? [];
  const changes = runChanges(runs).reverse();
  const base = state.data?.source.base ?? "/data";
  return (
    <>
      <PageHeader
        eyebrow={state.data ? `${runs.length} published ${runs.length === 1 ? "run" : "runs"}` : "Changelog"}
        title="Changelog"
        description="Every published run and what changed: models added or re-versioned, who leads, and how each model's error rate moved. Each run is a fresh draw of clips, so small moves are within noise."
      />
      <div className="frame py-10">
        {state.status === "error" && <ErrorState title="Could not load the runs." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {state.status === "loading" && (
          <div className="grid gap-10">
            {[0, 1].map((i) => (
              <div key={i} className="grid gap-4 md:grid-cols-[180px_minmax(0,1fr)]">
                <Skeleton className="h-5 w-32" />
                <Skeleton className="h-56 w-full" />
              </div>
            ))}
          </div>
        )}
        {state.status === "ready" && changes.length === 0 && <EmptyState title="No runs published yet." body="The first run shows up here as soon as it is exported." />}
        <ol className="relative">
          {changes.map((c, i) => (
            <Entry key={c.run.run_id} c={c} latest={i === 0} base={base} last={i === changes.length - 1} />
          ))}
        </ol>
      </div>
    </>
  );
}

function Entry({ c, latest, base, last }: { c: RunChange; latest: boolean; base: string; last: boolean }) {
  const run = c.run;
  const tier = defaultTier(run);
  const n = run.clips.filter((x) => x.tier === tier).length;
  const sameLeader = c.prevLeader && c.leader && c.prevLeader.provider === c.leader.provider;
  return (
    <li className="relative grid gap-4 pb-12 md:grid-cols-[180px_minmax(0,1fr)] md:gap-10">
      {!last && <span aria-hidden className="absolute top-3 bottom-0 left-[3px] hidden w-px bg-line md:block" />}
      <div className="md:pt-0.5">
        <div className="flex items-center gap-3">
          <span aria-hidden className={cn("relative z-[1] hidden size-[7px] rounded-full md:block", latest ? "bg-signal" : "bg-line-strong")} />
          <time dateTime={run.created_at} className="num text-[13px] text-signal-ink">
            {date(run.created_at)}
          </time>
        </div>
        <div className="mt-1 font-mono text-[11.5px] text-ink-3 md:pl-[19px]">{run.run_id}</div>
        {latest && <Badge tone="outline" className="mt-2 md:ml-[19px]">Latest</Badge>}
      </div>
      <article className="min-w-0 rounded-md border border-line bg-surface shadow-card">
        <header className="border-b border-line px-4 py-4 sm:px-5">
          <h2 className="text-[17px] font-semibold tracking-[-0.015em] text-ink">
            {!c.prev ? "First published run." : sameLeader ? `${providerLabel(run, c.leader!.provider)} still leads.` : c.leader ? `New leader: ${providerLabel(run, c.leader.provider)}.` : "Run published."}
          </h2>
          <p className="mt-1 text-[13.5px] text-ink-2">
            {run.providers.length} models on {n} {TIER_LABEL[tier].toLowerCase()} clips in {LANGUAGE_NAME[run.language] ?? run.language}
            {c.leader ? `. Lowest ${primaryMetric(run).name.toUpperCase()}: ${pct(c.leader.error_rate)} (${pct(c.leader.ci_low)} to ${pct(c.leader.ci_high)}).` : "."}
          </p>
          {(c.prev ? c.added.length + c.removed.length + c.changed.length > 0 : true) && (
            <ul className="mt-3 flex flex-wrap gap-1.5">
              {(c.prev ? c.added : []).map((p) => (
                <Change key={`a${p}`} icon={<Plus size={12} />} run={run} id={p}>
                  Added {providerLabel(run, p)} <span className="font-mono text-[11.5px] text-ink-3">{run.providers.find((x) => x.id === p)?.model_id}</span>
                </Change>
              ))}
              {c.removed.map((p) => (
                <Change key={`r${p}`} icon={<Minus size={12} />} run={c.prev!} id={p}>
                  Removed {providerLabel(c.prev!, p)}
                </Change>
              ))}
              {c.changed.map((x) => (
                <Change key={`c${x.provider}`} icon={<RefreshCw size={12} />} run={run} id={x.provider}>
                  {providerLabel(run, x.provider)} <span className="font-mono text-[11.5px] text-ink-3">{x.from}</span>
                  <ArrowRight size={11} className="text-ink-3" aria-label="to" />
                  <span className="font-mono text-[11.5px] text-ink">{x.to}</span>
                </Change>
              ))}
              {!c.prev && <li className="text-[12.5px] text-ink-3">Baseline for every later comparison.</li>}
            </ul>
          )}
        </header>
        <Deltas run={run} c={c} />
        <footer className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 sm:px-5">
          <span className="font-mono text-[11px] text-ink-3">
            commit {run.git_commit.slice(0, 7)} / seed {run.sample_seed}
          </span>
          <span className="flex gap-2">
            {latest && (
              <Link to="/leaderboard" className={buttonClass("ghost", "sm")}>
                Leaderboard <ArrowRight size={13} />
              </Link>
            )}
            <a href={`${base}/runs/${run.run_id}.json`} download className={buttonClass("secondary", "sm")}>
              <Download size={13} /> JSON
            </a>
          </span>
        </footer>
      </article>
    </li>
  );
}

function Change({ icon, children, run, id }: { icon: React.ReactNode; children: React.ReactNode; run: Run; id: string }) {
  return (
    <li className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line bg-sunken/60 px-2 text-[12.5px] text-ink">
      <span className="text-ink-3">{icon}</span>
      <SeriesDot color={providerColor(run, id)} size={6} />
      {children}
    </li>
  );
}

function Deltas({ run, c }: { run: Run; c: RunChange }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[320px] border-separate border-spacing-0 text-[13px]">
        <caption className="sr-only">Error rate and rank by model</caption>
        <thead>
          <tr>
            {["Model", "Error rate", c.prev ? "Change" : "", "Rank"].map((h, i) => (
              <th key={i} scope="col" className={cn("border-b border-line px-3 py-2 font-normal first:pl-4 last:pr-4 sm:first:pl-5 sm:last:pr-5", i ? "text-right" : "text-left")}>
                <span className="label">{h}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {c.deltas.map((d) => {
            const delta = d.before ? d.now.error_rate - d.before.error_rate : null;
            const moved = d.prevRank != null ? d.prevRank - d.rank : null;
            return (
              <tr key={d.provider}>
                <td className="border-b border-line py-2 pr-3 pl-4 sm:pl-5">
                  <Link to={`/providers/${d.provider}`} className="inline-flex items-center gap-2 text-ink hover:underline">
                    <SeriesDot color={providerColor(run, d.provider)} />
                    {providerLabel(run, d.provider)}
                  </Link>
                </td>
                <td className="num border-b border-line px-3 py-2 text-right text-ink">{pct(d.now.error_rate)}</td>
                <td className={cn("num border-b border-line px-3 py-2 text-right", delta == null ? "text-ink-3" : delta > 0.002 ? "text-signal-ink" : delta < -0.002 ? "text-good" : "text-ink-3")}>
                  {c.prev ? (delta == null ? "new" : signedPts(delta)) : ""}
                </td>
                <td className="border-b border-line py-2 pr-4 pl-3 text-right sm:pr-5">
                  <span className="inline-flex items-center gap-1.5">
                    {moved != null && moved !== 0 && (
                      <span className={cn("inline-flex items-center text-[11.5px]", moved > 0 ? "text-good" : "text-signal-ink")} aria-label={moved > 0 ? `up ${moved}` : `down ${-moved}`}>
                        {moved > 0 ? <ArrowUp size={12} /> : <ArrowDown size={12} />}
                        <span className="num">{Math.abs(moved)}</span>
                      </span>
                    )}
                    <span className="num text-ink-2">#{d.rank}</span>
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
