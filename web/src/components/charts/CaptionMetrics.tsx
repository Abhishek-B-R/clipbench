import { Link } from "react-router";
import type { Aggregate, Run } from "@/lib/types";
import { providerColor, providerLabel } from "@/lib/analysis";
import { CAPTION_METRICS, bestFor, metricN, metricValue, type CaptionMetric } from "@/lib/metrics";
import { int } from "@/lib/format";
import { cn } from "@/lib/cn";
import { DataTable, type Column } from "@/components/ui/Table";
import { SeriesDot } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";

/** URL `sort=` values for the caption metrics (findings deep-link with these). */
export const SORT_KEYS: Record<string, { key: CaptionMetric["key"]; dir: "asc" | "desc" }> = {
  timing: { key: "timing_within_200ms", dir: "desc" },
  offset: { key: "timing_median_ms", dir: "asc" },
  halluc: { key: "halluc_per_nonspeech_min", dir: "asc" },
  punct: { key: "punct_f1", dir: "desc" },
  casing: { key: "casing_acc", dir: "desc" },
  codeswitch: { key: "codeswitch_error_rate", dir: "asc" },
};

/**
 * The v2 caption metrics for every model in one slice: word timing, invented words over non-speech,
 * punctuation and casing, code-switch errors. n/a when a model or the clips cannot be scored on a metric
 * (no word timings, no punctuated clips, no code-switch spans). Best value per column in bold.
 */
export function CaptionMetricsTable({ run, rows, sort, highlight, metrics = CAPTION_METRICS }: { run: Run; rows: Aggregate[]; sort?: string | null; highlight?: string | null; metrics?: CaptionMetric[] }) {
  const s = sort ? SORT_KEYS[sort] : undefined;
  const best = Object.fromEntries(metrics.map((m) => [m.key, bestFor(rows, m)]));
  const columns: Column<Aggregate>[] = [
    {
      key: "model",
      header: "Model",
      sticky: true,
      sort: (r) => providerLabel(run, r.provider),
      cell: (r) => (
        <Link to={`/providers/${r.provider}`} className="flex items-center gap-2.5 hover:underline">
          <SeriesDot color={providerColor(run, r.provider)} />
          <span className="font-medium text-ink">{providerLabel(run, r.provider)}</span>
        </Link>
      ),
    },
    ...metrics.map<Column<Aggregate>>((m) => ({
      key: m.key,
      align: "right",
      header: (
        <Tooltip content={m.help}>
          <span tabIndex={0} className="cursor-help">
            {m.short}
          </span>
        </Tooltip>
      ),
      description: `${m.label}. ${m.better === "higher" ? "Higher" : "Lower"} is better.`,
      // n/a sorts last in both directions (DataTable puts null last).
      sort: (r) => metricValue(r, m),
      cell: (r) => {
        const v = metricValue(r, m);
        const n = metricN(r, m);
        if (v == null) return <span className="text-[12.5px] text-ink-3">n/a</span>;
        return (
          <span className="inline-flex flex-col items-end leading-tight">
            <span className={cn("num text-[13px]", best[m.key] === r.provider ? "font-semibold text-ink" : "text-ink-2")}>{m.fmt(v)}</span>
            {n != null && <span className="num text-[10.5px] text-ink-3">n={int(n)}</span>}
          </span>
        );
      },
    })),
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Caption metrics" initialSort={s} highlight={highlight ? (r) => r.provider === highlight : undefined} />;
}
