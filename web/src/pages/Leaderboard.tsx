import { useEffect, useMemo, useState } from "react";
import { maxOf } from "@/lib/scale";
import { Link, useSearchParams } from "react-router";
import { ChevronDown, Download } from "lucide-react";
import type { Aggregate, Run, SliceKind, Tier } from "@/lib/types";
import { SLICE_KIND_LABEL, hasCaptionMetrics, sliceKindsIn, sliceLabel, slicesOf } from "@/lib/metrics";
import { CaptionMetricsTable } from "@/components/charts/CaptionMetrics";
import { loadRun, runUrl, useRunFor } from "@/lib/data";
import { Gated } from "@/components/ui/Gate";
import { LANGUAGE_NAME, TIER_LABEL, aggregatesFor, isPublished, pickTier, pair, primaryMetric, providerColor, providerLabel } from "@/lib/analysis";
import { date, ms, pct, usdPerHour } from "@/lib/format";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { DataTable, type Column } from "@/components/ui/Table";
import { CIBar } from "@/components/ui/CIBar";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { Sparkline } from "@/components/ui/Sparkline";
import { Skeleton } from "@/components/ui/Skeleton";
import { TierControl } from "@/components/ui/TierControl";
import { ErrorState } from "@/components/ui/State";
import { buttonClass } from "@/components/ui/Button";

/**
 * Full leaderboard. Reference page for other pages: PageHeader with filters synced to the URL,
 * one Figure holding a DataTable, a source line. Tier and metric live in ?tier= and ?metric=.
 */
export default function Leaderboard() {
  const [params, setParams] = useSearchParams();
  const lang = params.get("lang");
  const state = useRunFor(lang);
  const run = state.data ? state.data.run : undefined;
  const published = run ? isPublished(run) : run === undefined;
  const tier = pickTier(run ?? undefined, params.get("tier"));
  const metric = params.get("metric") || (run ? primaryMetric(run).name : "cer");
  const kinds = run ? sliceKindsIn(run, tier) : (["all"] as SliceKind[]);
  const kind = (kinds.includes(params.get("slice_kind") as SliceKind) ? params.get("slice_kind") : "all") as SliceKind;
  const values = run ? slicesOf(run, tier, kind) : ["all"];
  const slice = kind === "all" ? "all" : values.includes(params.get("slice") ?? "") ? params.get("slice")! : (values[0] ?? "all");
  const history = useHistory(published ? (run ?? undefined) : undefined);
  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v == null || v === "") p.delete(k);
    else p.set(k, v);
    if (k === "slice_kind") p.delete("slice");
    setParams(p, { replace: true });
  };

  return (
    <>
      <PageHeader
        eyebrow={run ? `${LANGUAGE_NAME[run.language] ?? run.language} / run ${run.run_id} / ${date(run.created_at)}` : "Leaderboard"}
        title="Leaderboard"
        description={
          published
            ? "Every model on the same clips, ranked by error rate with 95% bootstrap intervals. The vs-leader column comes from the paired test, not from the gap between rows."
            : "Rankings appear here once a language passes the publication gate. Until then, this page shows progress."
        }
        actions={
          run &&
          published &&
          runUrl(state.data!.source, run.run_id) && (
            <a href={runUrl(state.data!.source, run.run_id)!} download className={buttonClass("secondary", "sm")}>
              <Download size={14} /> JSON
            </a>
          )
        }
      >
        {published && <TierControl run={run ?? undefined} value={tier} onChange={(v) => set("tier", v)} />}
        {run && published && (
          <Segmented
            label="Metric"
            value={metric}
            onChange={(v) => set("metric", v)}
            options={run.metrics.map((m) => ({ value: m.name, label: m.label }))}
          />
        )}
        {run && published && kinds.length > 1 && (
          <Segmented label="Slice" value={kind} onChange={(v) => set("slice_kind", v === "all" ? null : v)} options={kinds.map((k) => ({ value: k, label: SLICE_KIND_LABEL[k] }))} />
        )}
        {run && published && kind !== "all" && (
          <label className="relative inline-flex">
            <span className="sr-only">{SLICE_KIND_LABEL[kind]}</span>
            <select
              value={slice}
              onChange={(e) => set("slice", e.target.value)}
              className="h-8 appearance-none rounded-sm border border-line bg-surface pr-8 pl-2.5 text-[12.5px] text-ink hover:border-line-strong focus:border-ink focus:outline-none"
            >
              {values.map((v) => (
                <option key={v} value={v}>
                  {sliceLabel(kind, v)}
                </option>
              ))}
            </select>
            <ChevronDown size={14} className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-ink-3" aria-hidden />
          </label>
        )}
      </PageHeader>
      <div className="frame py-8">
        {state.status === "ready" ? (
          <Gated run={run} lang={lang ?? undefined}>
            {run && (
              <div className="grid gap-8">
                <Board run={run} tier={tier} metric={metric} kind={kind} slice={slice} history={history} highlight={params.get("provider")} />
                {hasCaptionMetrics(run) && (
                  <Figure
                    n={2}
                    title={`Caption metrics, ${sliceLabel(kind, slice).toLowerCase()}`}
                    caption="What a caption product ships besides the text: word timing, words invented over music and silence, punctuation, casing and code-switching. n/a means the model or these clips cannot be scored on it."
                    source={`source: aggregates, run=${run.run_id}, tier=${tier}, metric=${metric}, slice_kind=${kind}, slice=${slice}`}
                    bodyClassName="p-0 sm:p-0"
                  >
                    <CaptionMetricsTable run={run} rows={aggregatesFor(run, { tier, metric, kind, slice })} sort={params.get("sort")} highlight={params.get("provider")} />
                  </Figure>
                )}
              </div>
            )}
          </Gated>
        ) : state.status === "error" ? (
          <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />
        ) : (
          <Skeleton className="h-96 w-full" />
        )}
      </div>
    </>
  );
}

/** Error rate per provider across all published runs (oldest first), for the trend column. */
function useHistory(run: Run | undefined) {
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    if (!run) return;
    let alive = true;
    loadRun(run.run_id).then(({ source }) =>
      Promise.all(source.index.runs.map((r) => loadRun(r.run_id).then((x) => x.run).catch(() => null))).then((xs) => {
        if (alive) setRuns(xs.filter(Boolean).sort((a, b) => a!.created_at.localeCompare(b!.created_at)) as Run[]);
      }),
    );
    return () => {
      alive = false;
    };
  }, [run]);
  return runs;
}

function Board({ run, tier, metric, kind, slice, history, highlight }: { run: Run; tier: Tier; metric: string; kind: SliceKind; slice: string; history: Run[]; highlight: string | null }) {
  const rows = aggregatesFor(run, { tier, metric, kind, slice });
  const leader = rows[0];
  const max = maxOf(rows.map((r) => r.ci_high), 0.1) * 1.08 || 0.1;
  const trend = useMemo(() => {
    const out: Record<string, number[]> = {};
    for (const h of history)
      for (const a of h.aggregates)
        if (a.tier === tier && a.metric === metric && a.slice_kind === kind && a.slice === slice) (out[a.provider] ??= []).push(a.error_rate);
    return out;
  }, [history, tier, metric, kind, slice]);

  const columns: Column<Aggregate>[] = [
    { key: "rank", header: "#", cell: (_, i) => <span className="num text-ink-3">{String(i + 1).padStart(2, "0")}</span>, className: "w-10" },
    {
      key: "model",
      header: "Model",
      sticky: true,
      sort: (r) => providerLabel(run, r.provider),
      cell: (r) => (
        <Link to={`/providers/${r.provider}`} className="flex items-center gap-2.5 hover:underline">
          <SeriesDot color={providerColor(run, r.provider)} />
          <span>
            <span className="block font-medium text-ink">{providerLabel(run, r.provider)}</span>
            <span className="block font-mono text-[11.5px] text-ink-3">{r.model_id}</span>
          </span>
        </Link>
      ),
    },
    {
      key: "err",
      header: "Error rate",
      sort: (r) => r.error_rate,
      cell: (r) => (
        <span className="flex items-center gap-3">
          <span className="num w-12 text-right text-ink">{pct(r.error_rate)}</span>
          <CIBar value={r.error_rate} low={r.ci_low} high={r.ci_high} max={max} color={providerColor(run, r.provider)} className="hidden sm:inline-block" />
        </span>
      ),
    },
    {
      key: "ci",
      header: "95% interval",
      align: "right",
      cell: (r) => (
        <span className="num text-[12.5px] text-ink-3">
          {pct(r.ci_low)} to {pct(r.ci_high)}
        </span>
      ),
      headerClassName: "hidden xl:table-cell",
      className: "hidden xl:table-cell",
    },
    {
      key: "vs",
      header: "vs leader",
      cell: (r) => {
        if (!leader || r.provider === leader.provider) return <Badge tone="good">Lowest</Badge>;
        const t = pair(run, { tier, metric, kind, slice }, r.provider, leader.provider);
        if (!t) return <span className="text-[12.5px] text-ink-3">not tested</span>;
        return t.better === leader.provider ? (
          <span className="num text-[12.5px] text-ink-2">+{(t.diff * 100).toFixed(1)} pts</span>
        ) : (
          <span className="text-[12.5px] text-ink-2">No detectable difference</span>
        );
      },
    },
    {
      key: "trend",
      header: "Trend",
      cell: (r) => (trend[r.provider]?.length ?? 0) > 1 ? <Sparkline values={trend[r.provider]!} color={providerColor(run, r.provider)} label={`${providerLabel(run, r.provider)} error rate over ${trend[r.provider]!.length} runs`} /> : <span className="text-ink-3">n/a</span>,
      headerClassName: "hidden lg:table-cell",
      className: "hidden lg:table-cell",
    },
    { key: "hall", header: "Hallucination", align: "right", sort: (r) => r.hallucination_rate, cell: (r) => <span className="num text-ink-2">{pct(r.hallucination_rate, 2)}</span>, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" },
    { key: "cost", header: "$ / hour", align: "right", sort: (r) => r.cost_per_hour, cell: (r) => <span className="num text-ink-2">{usdPerHour(r.cost_per_hour)}</span> },
    { key: "p50", header: "p50", align: "right", sort: (r) => r.latency_p50, cell: (r) => <span className="num text-ink-2">{ms(r.latency_p50)}</span>, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" },
    { key: "p95", header: "p95", align: "right", sort: (r) => r.latency_p95, cell: (r) => <span className="num text-ink-3">{ms(r.latency_p95)}</span>, headerClassName: "hidden 2xl:table-cell", className: "hidden 2xl:table-cell" },
    {
      key: "n",
      header: "Clips",
      align: "right",
      sort: (r) => r.n_clips,
      cell: (r) =>
        r.too_small ? (
          <span className="inline-flex items-center gap-1.5">
            <Badge tone="warn">Too small</Badge>
            <span className="num text-ink-3">{r.n_clips}</span>
          </span>
        ) : (
          <span className="num text-ink-3">{r.n_clips}</span>
        ),
      headerClassName: "hidden sm:table-cell",
      className: "hidden sm:table-cell",
    },
  ];

  const m = run.metrics.find((x) => x.name === metric);
  return (
    <Figure
      n={1}
      title={`${m?.label ?? metric}, ${TIER_LABEL[tier].toLowerCase()}${kind === "all" ? "" : `, ${sliceLabel(kind, slice).toLowerCase()}`}`}
      caption={rows.some((r) => r.too_small) ? "Lower is better. Fewer than 15 clips per model: read the ranking as a first look, not a result." : "Lower is better. Click a header to sort; click a model for its page."}
      source={`source: aggregates + paired, run=${run.run_id}, tier=${tier}, metric=${metric}, slice_kind=${kind}, slice=${slice}`}
      bodyClassName="p-0 sm:p-0"
    >
      {tier === "creator" && (
        <p className="border-b border-line bg-warn-soft/60 px-4 py-2 text-[12.5px] text-ink-2 sm:px-5">
          Unverified reference: scored against the creators' own captions. Read as agreement with the creator, not accuracy.
        </p>
      )}
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Leaderboard" highlight={highlight ? (r) => r.provider === highlight : undefined} />
    </Figure>
  );
}
