import type { Run } from "@/lib/types";
import { overall, pair, primaryMetric, providerColor, providerLabel, defaultTier } from "@/lib/analysis";
import { pct, usdPerHour } from "@/lib/format";
import { Figure } from "@/components/ui/Card";
import { SeriesDot } from "@/components/ui/Badge";
import { CostAccuracy } from "@/components/charts/CostAccuracy";

/** FIG. 04 plus three computed sentences about the price of accuracy. */
export function CostSection({ run }: { run: Run }) {
  const metric = primaryMetric(run).name;
  const rows = overall(run, defaultTier(run), metric);
  const leader = rows[0];
  const notes: { id: string; text: React.ReactNode }[] = [];
  if (leader) {
    notes.push({
      id: leader.provider,
      text: (
        <>
          <b className="font-medium text-ink">{providerLabel(run, leader.provider)}</b> makes the fewest errors, at {leader.cost_per_hour ? `${usdPerHour(leader.cost_per_hour)} per hour` : "no cost per hour (it runs locally)"}.
        </>
      ),
    });
    const cheaper = rows
      .filter((r) => r.provider !== leader.provider && (r.cost_per_hour ?? 0) > 0 && (r.cost_per_hour ?? 0) < (leader.cost_per_hour ?? 0))
      .sort((a, b) => a.error_rate - b.error_rate)[0];
    if (cheaper) {
      const t = pair(run, { tier: defaultTier(run), metric, kind: "all", slice: "all" }, cheaper.provider, leader.provider);
      const save = 1 - (cheaper.cost_per_hour ?? 0) / (leader.cost_per_hour ?? 1);
      notes.push({
        id: cheaper.provider,
        text: (
          <>
            <b className="font-medium text-ink">{providerLabel(run, cheaper.provider)}</b> costs {Math.round(save * 100)}% less and{" "}
            {t?.better === leader.provider ? `makes ${((cheaper.error_rate - leader.error_rate) * 100).toFixed(1)} more errors per 100 characters.` : "the paired test cannot tell it apart from the leader."}
          </>
        ),
      });
    }
    const free = rows.find((r) => (r.cost_per_hour ?? 0) === 0 && r.provider !== leader.provider);
    if (free)
      notes.push({
        id: free.provider,
        text: (
          <>
            <b className="font-medium text-ink">{providerLabel(run, free.provider)}</b> is free and keeps audio on your machine, at {pct(free.error_rate)} error, {(free.error_rate / leader.error_rate).toFixed(1)} times the leader.
          </>
        ),
      });
  }
  return (
    <div className="grid gap-8 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)] lg:gap-10">
      <ul className="grid min-w-0 content-start gap-0 self-start border-t border-line">
        {notes.map((n) => (
          <li key={n.id} className="flex gap-3 border-b border-line py-4 text-[15px] leading-relaxed text-ink-2">
            <SeriesDot color={providerColor(run, n.id)} className="mt-[9px]" />
            <span>{n.text}</span>
          </li>
        ))}
        <li className="pt-4 text-[13px] text-ink-3">List prices as checked on {run.providers[0]?.pricing_checked_on}. Cost is per hour of audio sent, not per hour of compute.</li>
      </ul>
      <Figure className="min-w-0" n={4} title="Cost against accuracy" caption="Each dot is a model; the whisker is its 95% interval. The dotted line joins models nothing else beats on both price and errors." source={`source: aggregates, tier=${defaultTier(run)}, metric=${metric}, slice=all; providers.usd_per_hour`}>
        <CostAccuracy run={run} rows={rows} />
      </Figure>
    </div>
  );
}
