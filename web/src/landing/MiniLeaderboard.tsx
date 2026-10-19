import { useState } from "react";
import { maxOf } from "@/lib/scale";
import { Link } from "react-router";
import { ArrowUpRight } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { TIER_LABEL, defaultTier, tiersInRun, overall, pair, primaryMetric, providerColor, providerLabel } from "@/lib/analysis";
import { ms, pct, usdPerHour } from "@/lib/format";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { DataTable, type Column } from "@/components/ui/Table";
import { CIBar } from "@/components/ui/CIBar";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";
import type { Aggregate } from "@/lib/types";

export function MiniLeaderboard({ run }: { run: Run }) {
  const [tier, setTier] = useState<Tier>(defaultTier(run));
  const metric = primaryMetric(run);
  const rows = overall(run, tier, metric.name);
  const leader = rows[0];
  const max = maxOf(rows.map((r) => r.ci_high), 0.1) * 1.08 || 0.1;

  const columns: Column<Aggregate>[] = [
    { key: "rank", header: "#", cell: (_, i) => <span className="num text-ink-3">{String(i + 1).padStart(2, "0")}</span>, className: "w-10" },
    {
      key: "model",
      header: "Model",
      sticky: true,
      sort: (r) => providerLabel(run, r.provider),
      cell: (r) => (
        <Link to={`/providers/${r.provider}`} className="group/m flex items-center gap-2.5">
          <SeriesDot color={providerColor(run, r.provider)} />
          <span className="min-w-0">
            <span className="block font-medium text-ink group-hover/m:underline">{providerLabel(run, r.provider)}</span>
            <span className="block truncate font-mono text-[11.5px] text-ink-3">{r.model_id}</span>
          </span>
        </Link>
      ),
    },
    {
      key: "err",
      header: metric.name.toUpperCase(),
      sort: (r) => r.error_rate,
      align: "left",
      cell: (r) => (
        <span className="flex items-center gap-3">
          <span className="num w-12 text-right text-ink">{pct(r.error_rate)}</span>
          <CIBar value={r.error_rate} low={r.ci_low} high={r.ci_high} max={max} color={providerColor(run, r.provider)} className="hidden sm:inline-block" />
        </span>
      ),
    },
    {
      key: "vs",
      header: "vs leader",
      cell: (r) => {
        if (!leader || r.provider === leader.provider) return <Badge tone="good">Lowest</Badge>;
        const t = pair(run, { tier, metric: metric.name, kind: "all", slice: "all" }, r.provider, leader.provider);
        if (!t) return <span className="text-[12.5px] text-ink-3">not tested</span>;
        if (t.better === leader.provider)
          return (
            <Tooltip content={`Paired test on ${t.n} clips: ${pct(Math.abs(t.diff))} more errors, 95% interval ${pct(Math.abs(t.ci_high))} to ${pct(Math.abs(t.ci_low))}.`}>
              <button type="button" className="text-[12.5px] text-ink-2 underline decoration-line-strong decoration-dotted underline-offset-4">
                +{(t.diff * 100).toFixed(1)} pts
              </button>
            </Tooltip>
          );
        return <span className="text-[12.5px] text-ink-2">No detectable difference</span>;
      },
    },
    { key: "cost", header: "$ / hour", align: "right", sort: (r) => r.cost_per_hour, cell: (r) => <span className="num text-ink-2">{usdPerHour(r.cost_per_hour)}</span> },
    { key: "lat", header: "p50 latency", align: "right", sort: (r) => r.latency_p50, cell: (r) => <span className="num text-ink-2">{ms(r.latency_p50)}</span>, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" },
  ];

  return (
    <Figure
      n={1}
      title={`Overall ${metric.label.toLowerCase()}, ${TIER_LABEL[tier].toLowerCase()}`}
      caption="Lower is better. The dot is the error rate; the band is its 95% bootstrap interval."
      controls={
        tiersInRun(run).length > 1 ? (
          <Segmented
            label="Reference tier"
            value={tier}
            onChange={setTier}
            options={[
              { value: "verified", label: "Verified" },
              { value: "creator", label: "Creator captions" },
            ]}
          />
        ) : undefined
      }
      source={
        <span className="flex flex-wrap items-center justify-between gap-2">
          <span>
            source: aggregates, tier={tier}, metric={metric.name}, slice=all
          </span>
          <Link to="/leaderboard" className="inline-flex items-center gap-1 text-ink-2 hover:text-ink">
            Full leaderboard <ArrowUpRight size={12} />
          </Link>
        </span>
      }
      bodyClassName="p-0 sm:p-0"
    >
      {tier === "creator" && (
        <p className="border-b border-line bg-warn-soft/60 px-4 py-2 text-[12.5px] text-ink-2 sm:px-5">
          Unverified reference: scored against the creators' own captions, which are often not verbatim. Read as agreement, not accuracy.
        </p>
      )}
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Overall leaderboard" />
    </Figure>
  );
}
