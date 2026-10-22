import { Link } from "react-router";
import { FlaskConical } from "lucide-react";
import type { EarlyLook, EarlyRow } from "@/lib/early";
import { EARLY_LABEL, partialCause } from "@/lib/early";
import { date, int, ms, pct, usdPerHour } from "@/lib/format";
import { maxOf } from "@/lib/scale";
import { cn } from "@/lib/cn";
import { DataTable, type Column } from "@/components/ui/Table";
import { CIBar } from "@/components/ui/CIBar";
import { Badge } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";

/** The persistent early-look label. Every early-look view carries it, word for word (EARLY_LABEL). */
export function EarlyLabel({ className, compact = false }: { className?: string; compact?: boolean }) {
  return (
    <p
      className={cn(
        "flex items-start gap-2.5 border border-line bg-warn-soft/60 text-ink-2",
        compact ? "rounded-sm px-3 py-2 text-[12.5px]" : "rounded-md px-4 py-2.5 text-[13px]",
        className,
      )}
      data-testid="early-label"
    >
      <FlaskConical size={14} strokeWidth={1.75} className="mt-[3px] shrink-0 text-ink-3" aria-hidden />
      <span>
        <span className="font-medium text-ink">{EARLY_LABEL.split(":")[0]}:</span>
        {EARLY_LABEL.slice(EARLY_LABEL.indexOf(":") + 1)}
      </span>
    </p>
  );
}

/** "150 of 150" with the exporter's reason under it when a system covers fewer clips. */
export function Coverage({ row, className }: { row: Pick<EarlyRow, "n_clips" | "total" | "partial">; className?: string }) {
  const cause = partialCause(row.partial);
  return (
    <span className={cn("block text-right", className)}>
      <span className={cn("num", row.n_clips < row.total ? "text-ink" : "text-ink-3")}>
        {int(row.n_clips)} <span className="text-ink-3">of {int(row.total)}</span>
      </span>
      {row.n_clips < row.total && (
        <span className="mt-0.5 block text-[11.5px] whitespace-nowrap text-ink-3" title={row.partial ?? undefined}>
          {cause ? `partial: ${cause === "no auto track" ? "no YouTube track" : cause}` : "partial"}
        </span>
      )}
    </span>
  );
}

/**
 * Every system on one tier, sorted by error rate, without rank numbers. Latency shows only when the run timed one
 * provider at a time; otherwise the column is dropped and the note says why.
 */
export function EarlyBoardTable({ look, limit, compact = false }: { look: EarlyLook; limit?: number; compact?: boolean }) {
  const rows = limit ? look.rows.slice(0, limit) : look.rows;
  const max = maxOf(look.rows.map((r) => r.ci_high), 0.1) * 1.05 || 0.1;
  const hasStability = look.rows.some((r) => r.stability);
  const showLatency = look.latency.sequential === true;
  const columns: Column<EarlyRow>[] = [
    {
      key: "model",
      header: "System",
      sticky: true,
      sort: (r) => r.label,
      cell: (r) => (
        <Link to={`/providers/${encodeURIComponent(r.provider)}`} className="block min-w-0 hover:underline">
          <span className="block font-medium text-ink">{r.label}</span>
          <span className="block max-w-[200px] truncate font-mono text-[11px] text-ink-3">{r.model_id}</span>
        </Link>
      ),
    },
    {
      key: "err",
      header: look.metricLabel === "Character error rate" ? (compact ? "CER" : "CER, 95% interval") : look.metricLabel,
      sort: (r) => r.error_rate,
      cell: (r) => (
        <span className="flex items-center gap-3">
          <span className="grid">
            <span className="num font-medium text-ink">{pct(r.error_rate)}</span>
            {!compact && (
              <span className="num text-[11px] whitespace-nowrap text-ink-3">
                {pct(r.ci_low)} to {pct(r.ci_high)}
              </span>
            )}
          </span>
          {!compact && <CIBar value={r.error_rate} low={r.ci_low} high={r.ci_high} max={max} color="var(--ink-2)" className="hidden sm:inline-block" width={84} />}
        </span>
      ),
    },
    ...(compact ? [{
      key: "ci",
      header: "95% interval",
      align: "right",
      cell: (r) => (
        <span className="num text-[12.5px] whitespace-nowrap text-ink-3">
          {pct(r.ci_low)} to {pct(r.ci_high)}
        </span>
      ),
      headerClassName: "hidden md:table-cell",
      className: "hidden md:table-cell",
    } as Column<EarlyRow>] : []),
    { key: "n", header: "Clips", align: "right", sort: (r) => r.n_clips, cell: (r) => <Coverage row={r} /> },
    {
      key: "cost",
      header: "$ / hour",
      align: "right",
      sort: (r) => r.cost_per_hour,
      cell: (r) => <span className="num text-ink-2">{r.cost_per_hour === 0 ? "free" : usdPerHour(r.cost_per_hour)}</span>,
      headerClassName: compact ? "hidden sm:table-cell" : undefined,
      className: compact ? "hidden sm:table-cell" : undefined,
    },
    ...(showLatency && !compact
      ? [
          {
            key: "p50",
            header: "p50 latency",
            align: "right" as const,
            sort: (r: EarlyRow) => r.latency_p50,
            cell: (r: EarlyRow) => <span className="num text-ink-2">{ms(r.latency_p50)}</span>,
            headerClassName: "hidden md:table-cell",
            className: "hidden md:table-cell",
          },
        ]
      : []),
    ...(!compact
      ? [
          {
            key: "hall",
            header: (
              <Tooltip content="Output characters beyond 1.5 times the reference length, per reference character. Mostly repetition loops.">
                <span tabIndex={0} className="cursor-help">
                  Hallucination
                </span>
              </Tooltip>
            ),
            align: "right" as const,
            sort: (r: EarlyRow) => r.hallucination_rate,
            cell: (r: EarlyRow) => <span className={cn("num", r.hallucination_rate > 0 ? "font-medium text-signal-ink" : "text-ink-3")}>{pct(r.hallucination_rate)}</span>,
            headerClassName: "hidden md:table-cell",
            className: "hidden md:table-cell",
          },
        ]
      : []),
    ...(hasStability && !compact
      ? [
          {
            key: "stab",
            header: (
              <Tooltip content="Rerun drift: mean text disagreement between repeated transcriptions of the same clip. 0 means every repeat returned the same text.">
                <span tabIndex={0} className="cursor-help">
                  Drift
                </span>
              </Tooltip>
            ),
            align: "right" as const,
            sort: (r: EarlyRow) => r.stability?.disagreement ?? null,
            cell: (r: EarlyRow) =>
              r.stability ? (
                <span className="block text-right">
                  <span className="num text-ink-2">{pct(r.stability.disagreement)}</span>
                  <span className="block text-[11px] text-ink-3">
                    {int(r.stability.n)} clips x{r.stability.repeats}
                  </span>
                </span>
              ) : (
                <span className="text-[12px] text-ink-3">not rerun</span>
              ),
            headerClassName: "hidden xl:table-cell",
            className: "hidden xl:table-cell",
          },
        ]
      : []),
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Early look, every system" dense={compact} />;
}

/** One line under the board: how latency was measured, from run_meta.latency_mode. */
export function LatencyNote({ look, className }: { look: EarlyLook; className?: string }) {
  const lm = look.latency;
  return (
    <p className={cn("text-[12.5px] text-ink-3", className)}>
      {lm.sequential === true
        ? `${lm.text ?? "Latency was measured one provider at a time."} Wall-clock from one machine and network, upload included: relative speed only.`
        : lm.sequential === false
          ? "Latency is hidden: this run timed several providers at once, which inflates it."
          : "Latency is hidden: this export does not say how it was measured."}
    </p>
  );
}

/** Run facts in mono for a source line. */
export function earlySource(look: EarlyLook, tables = "aggregates + stability + languages") {
  return `source: ${tables}, run=${look.run.run_id} (${date(look.run.created_at)}), tier=${look.tier}, metric=${look.metric}, slice=all`;
}

export function PartialBadge({ row }: { row: EarlyRow }) {
  if (row.n_clips >= row.total) return null;
  return <Badge tone="warn">{`${int(row.n_clips)} of ${int(row.total)} clips`}</Badge>;
}
