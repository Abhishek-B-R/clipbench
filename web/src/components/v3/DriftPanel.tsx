import { Link } from "react-router";
import type { Run } from "@/lib/types";
import { primaryMetric, providerColor, providerLabel } from "@/lib/analysis";
import { historyFor, v3 } from "@/lib/v3";
import { date, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Figure } from "@/components/ui/Card";
import { SeriesDot } from "@/components/ui/Badge";
import { LineChart } from "@/components/charts/LineChart";

/**
 * A3 on the changelog: every provider's canary reruns over time, with drift events marked at their dates and
 * listed underneath. Call only for a published run (the history is a comparison between models).
 */
export function DriftPanel({ run }: { run: Run }) {
  const x = v3(run);
  const metric = primaryMetric(run).name;
  const ids = Object.keys(x.history ?? {}).sort();
  const events = (x.drift ?? []).filter((e) => !e.metric || e.metric === metric);
  if (!ids.length && !events.length) return null;
  const t = (d: string) => Date.parse(d);
  const series = ids.map((id) => ({ id, label: providerLabel(run, id), color: providerColor(run, id), points: historyFor(x.history, id, metric).map((p) => ({ x: t(p.date), y: p.error_rate })) })).filter((s) => s.points.length > 1);
  const xs = series.flatMap((s) => s.points.map((p) => p.x));
  const first = xs.length ? Math.min(...xs) : 0;
  const last = xs.length ? Math.max(...xs) : 1;
  const months: { value: number; label: string }[] = [];
  if (xs.length) {
    const d0 = new Date(first);
    for (let m = new Date(Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1)); m.getTime() <= last; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))) months.push({ value: m.getTime(), label: date(m.toISOString()).replace(/^1 /, "") });
  }
  return (
    <Figure
      n={1}
      title="Canary reruns and drift"
      caption="A fixed set of canary clips is rerun on a schedule for every model. A vermilion line marks a drift event: a model's score moved beyond its own bootstrap interval. Error rate on the canary clips; not comparable with the full-run numbers below."
      source={`source: history, drift; run=${run.run_id}, metric=${metric}`}
      className="mb-12"
    >
      {series.length > 0 && (
        <LineChart
          series={series}
          xDomain={[first, last]}
          xTicks={months}
          xFormat={(v) => date(v)}
          yFormat={(v) => pct(v, v < 0.1 ? 1 : 0)}
          yLabel="Canary error rate"
          vlines={events.map((e) => ({ x: t(e.date), label: `${providerLabel(run, e.provider)} ${date(e.date)}`, tone: "signal" as const }))}
          label={`Canary error rate over time for ${series.length} models, with ${events.length} drift events`}
          height={280}
        />
      )}
      <div className={cn(series.length > 0 && "mt-5 border-t border-line pt-4")}>
        <div className="label mb-2">Drift events</div>
        {events.length ? (
          <ul className="grid gap-2">
            {[...events].reverse().map((e) => (
              <li key={e.provider + e.date} className="grid gap-x-4 gap-y-0.5 text-[13px] sm:grid-cols-[110px_minmax(0,1fr)]">
                <time dateTime={e.date} className="num text-signal-ink">
                  {date(e.date)}
                </time>
                <span className="text-ink-2">
                  <Link to={`/providers/${e.provider}`} className="inline-flex items-center gap-1.5 text-ink hover:underline">
                    <SeriesDot color={providerColor(run, e.provider)} size={7} />
                    {providerLabel(run, e.provider)}
                  </Link>{" "}
                  {e.direction === "worse" ? "got worse" : e.direction === "better" ? "got better" : "moved"}: <span className="num text-ink">{pct(e.before)}</span> to <span className="num text-ink">{pct(e.after)}</span>
                  {e.ci_low != null && e.ci_high != null && (
                    <span className="text-ink-3">
                      , outside its interval of {pct(e.ci_low)} to {pct(e.ci_high)}
                    </span>
                  )}
                  {e.model_id && <span className="font-mono text-[11.5px] text-ink-3"> ({e.model_id})</span>}
                </span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[13px] text-ink-3">None so far: every canary rerun stayed inside each model's own interval.</p>
        )}
      </div>
    </Figure>
  );
}
