import type { Run } from "@/lib/types";
import { calibrationPasses, v3 } from "@/lib/v3";
import { date, int, pct } from "@/lib/format";
import { Figure } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/State";

/**
 * Semantic judge calibration (A2): how often the LLM judge agrees with human labels, against the publication
 * threshold. Semantic scores appear on the site only when the export says the judge passed.
 */
export function JudgeCalibration({ run, n }: { run: Run | undefined; n: number }) {
  const sem = run ? v3(run).semantic : null;
  const c = sem?.calibration;
  if (!sem || !c)
    return (
      <Figure n={n} title="Semantic judge calibration" source="source: semantic.calibration (judge_calibration.json)">
        <EmptyState title="No calibration in the latest export." body="Semantic scores stay unpublished until the judge has been checked against pairs a person labelled, and agrees with them often enough." />
      </Figure>
    );
  const x = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;
  // The gauge shows agreement when the export has it, else the Spearman correlation against its minimum.
  const g = c.agreement != null ? { v: c.agreement, lo: c.ci_low, hi: c.ci_high, min: c.threshold, name: "Agreement", fmt: (v: number) => pct(v, 0) } : c.spearman != null ? { v: c.spearman, lo: null, hi: null, min: c.min_spearman, name: "Spearman", fmt: (v: number) => v.toFixed(2) } : null;
  const rows: [string, string][] = [
    ["Judge", [sem.judge.model, sem.judge.provider && `via ${sem.judge.provider}`].filter(Boolean).join(" ") || "Not stated"],
    ["Prompt", sem.judge.prompt ?? "Not stated"],
    ["Human-labelled pairs", c.n_pairs == null ? "n/a" : int(c.n_pairs)],
    ...(c.spearman != null ? ([["Spearman rank correlation", `${c.spearman.toFixed(2)}${c.min_spearman != null ? `, needs at least ${c.min_spearman.toFixed(2)}` : ""}`]] as [string, string][]) : []),
    ...(c.mae != null ? ([["Mean absolute error", `${c.mae.toFixed(2)}${c.max_mae != null ? `, needs at most ${c.max_mae.toFixed(2)}` : ""}`]] as [string, string][]) : []),
    ...(c.min_pairs != null ? ([["Pairs needed", int(c.min_pairs)]] as [string, string][]) : []),
    ...(c.agreement != null ? ([["Agreement", `${pct(c.agreement)}${c.ci_low != null && c.ci_high != null ? ` (95% interval ${pct(c.ci_low)} to ${pct(c.ci_high)})` : ""}`]] as [string, string][]) : []),
    ...(c.agreement_rule ? ([["Counts as agreeing", c.agreement_rule]] as [string, string][]) : []),
    ...(c.kappa != null ? ([["Cohen's kappa", c.kappa.toFixed(2)]] as [string, string][]) : []),
    ...(c.threshold != null ? ([["Agreement needed", pct(c.threshold)]] as [string, string][]) : []),
    ...(c.labelled_on ? ([["Labelled", date(c.labelled_on)]] as [string, string][]) : []),
  ];
  return (
    <Figure
      n={n}
      title="Semantic judge calibration"
      caption={sem.published ? "The judge agrees with people often enough, so semantic error is published next to the strict error rate." : (sem.reason ?? "The judge does not agree with people often enough yet, so semantic scores are not published.")}
      controls={
        <>
          {calibrationPasses(c) != null && <Badge tone={calibrationPasses(c) ? "good" : "outline"}>{calibrationPasses(c) ? "Passes the gate" : "Below the gate"}</Badge>}
          {sem.published ? <Badge tone="good">Published</Badge> : <Badge tone="outline">Not published</Badge>}
        </>
      }
      source={`source: semantic.calibration, run=${run!.run_id}`}
      bodyClassName="p-0 sm:p-0"
    >
      {g && (
        <div className="border-b border-line px-4 py-5 sm:px-5">
          <div className="relative h-8" role="img" aria-label={`${g.name} ${g.fmt(g.v)}${g.min != null ? `, minimum ${g.fmt(g.min)}` : ""}`}>
            <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line-strong" />
            {g.lo != null && g.hi != null && <span className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-ink opacity-20" style={{ left: x(g.lo), width: `calc(${x(g.hi)} - ${x(g.lo)})` }} />}
            {g.min != null && (
              <span className="absolute top-0 bottom-0 w-px bg-signal" style={{ left: x(g.min) }}>
                <span className="absolute -top-4 left-1 font-mono text-[10px] whitespace-nowrap text-signal-ink">minimum {g.fmt(g.min)}</span>
              </span>
            )}
            <span className="absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink ring-2 ring-surface" style={{ left: x(g.v) }} />
          </div>
          <div className="mt-1 flex justify-between font-mono text-[10.5px] text-ink-3">
            <span>{g.fmt(0)}</span>
            <span>
              {g.name} {g.fmt(g.v)}
            </span>
            <span>{g.fmt(1)}</span>
          </div>
        </div>
      )}
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.6fr)] gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
            <dt className="text-[13px] text-ink-3">{k}</dt>
            <dd className="text-[13.5px] break-words text-ink">{v}</dd>
          </div>
        ))}
      </dl>
    </Figure>
  );
}
