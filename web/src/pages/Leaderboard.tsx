import { lazy, Suspense, useEffect, useMemo, useState } from "react";
import { maxOf } from "@/lib/scale";
import { Link, useSearchParams } from "react-router";
import { Download } from "lucide-react";
import type { Aggregate, Run, SliceKind, Tier } from "@/lib/types";
import { hasCaptionMetrics, sliceKindsIn, sliceLabel, slicesOf } from "@/lib/metrics";
import { hasLattice, inSlice, latticeFor, v3 } from "@/lib/v3";
import { SliceFilter, readSlice, sliceParams } from "@/components/ui/SliceFilter";
import { Tabs, tabPanelProps } from "@/components/ui/Tabs";
import { Tooltip } from "@/components/ui/Tooltip";
import { VIEW_LABEL, viewsIn, type LeaderView } from "@/components/v3/views";
import { CaptionMetricsTable } from "@/components/charts/CaptionMetrics";
import { loadRun, runUrl, useRunFor } from "@/lib/data";
import { Gated, NotRanked } from "@/components/ui/Gate";
import { LANGUAGE_NAME, TIER_LABEL, aggregatesFor, isPublished, pickMetric, pickTier, pair, providerColor, providerLabel } from "@/lib/analysis";
import { date, int, ms, pct, usdPerHour } from "@/lib/format";
import { RULES } from "@/lib/config";
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
import { EarlyLookView } from "@/components/early/EarlyLookView";

// Views beyond accuracy (sweeps, long-form, formatting, ...) carry their own chart code: one lazy chunk.
const LeaderViewPanel = lazy(() => import("@/components/v3/LeaderViews"));

/**
 * Full leaderboard, and the reference layout for other pages: PageHeader with filters synced to the URL,
 * one Figure holding a DataTable, a source line. Tier and metric live in ?tier= and ?metric=.
 */
export default function Leaderboard() {
  const [params, setParams] = useSearchParams();
  const lang = params.get("lang");
  const state = useRunFor(lang);
  const run = state.data ? state.data.run : undefined;
  const published = run ? isPublished(run) : run === undefined;
  const tier = pickTier(run ?? undefined, params.get("tier"));
  const metric = pickMetric(run ?? undefined, params.get("metric"));
  const kinds = run ? sliceKindsIn(run, tier) : (["all"] as SliceKind[]);
  const { kind, slice, values } = readSlice(params, kinds, (k) => (run ? slicesOf(run, tier, k) : []));
  const lattice = Boolean(run && hasLattice(run) && params.get("score") === "lattice");
  const views = run ? viewsIn(run) : (["accuracy"] as LeaderView[]);
  const view = (views.includes(params.get("view") as LeaderView) ? params.get("view") : "accuracy") as LeaderView;
  const history = useHistory(published ? (run ?? undefined) : undefined);
  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v == null || v === "") p.delete(k);
    else p.set(k, v);
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
            : `Every system on the same clips, scored against creator captions and sorted by error, with 95% intervals. Ranks and head-to-head verdicts switch on once ${int(RULES.publishMinVerified)} clips are corrected by ear.`
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
        {run && !published && <Badge tone="warn">Early look, not a ranking</Badge>}
        {run && (
          <Segmented
            label="Metric"
            value={metric}
            onChange={(v) => set("metric", v)}
            options={run.metrics.map((m) => ({ value: m.name, label: m.label }))}
          />
        )}
        {run && published && <SliceFilter kinds={kinds} kind={kind} slice={slice} values={values} onChange={(k, v) => setParams(sliceParams(params, k, v), { replace: true })} />}
        {run && published && hasLattice(run) && view === "accuracy" && (
          <Segmented
            label="Scoring"
            value={lattice ? "lattice" : "strict"}
            onChange={(v) => set("score", v === "lattice" ? v : null)}
            options={[
              { value: "strict", label: "Strict" },
              { value: "lattice", label: "Any valid form" },
            ]}
          />
        )}
      </PageHeader>
      <div className="frame py-8">
        {state.status === "ready" && run && !published ? (
          <EarlyLookView run={run} tier={tier} metric={metric} />
        ) : state.status === "ready" ? (
          <Gated run={run} lang={lang ?? undefined}>
            {run && (
              <div className="grid grid-cols-1 gap-8">
                {views.length > 1 && (
                  <div className="-mt-2 min-w-0 border-b border-line">
                    <Tabs label="Measure" value={view} onChange={(v) => set("view", v === "accuracy" ? null : v)} tabs={views.map((v) => ({ value: v, label: VIEW_LABEL[v] }))} />
                  </div>
                )}
                {view !== "accuracy" ? (
                  <div {...tabPanelProps(view)} className="grid min-w-0 grid-cols-1 gap-8 focus:outline-none">
                    <Suspense fallback={<Skeleton className="h-96 w-full" />}>
                      <LeaderViewPanel view={view} run={run} tier={tier} metric={metric} kind={kind} slice={slice} n={1} />
                    </Suspense>
                  </div>
                ) : (
                <div {...tabPanelProps("accuracy")} className="grid min-w-0 grid-cols-1 gap-8 focus:outline-none">
                <Board run={run} tier={tier} metric={metric} kind={kind} slice={slice} history={history} highlight={params.get("provider")} lattice={lattice} />
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

export function Board({ run, tier, metric, kind, slice, history, highlight, lattice }: { run: Run; tier: Tier; metric: string; kind: SliceKind; slice: string; history: Run[]; highlight: string | null; lattice: boolean }) {
  const strict = aggregatesFor(run, { tier, metric, kind, slice });
  // "Any valid form" (A7): the same rows scored against the closest valid path through the reference's alternatives.
  const lat = lattice ? latticeFor(run, tier, metric, kind, slice) : null;
  // The order and the rank always come from the strict ranking (the export's `ranking` for slice all); "any valid
  // form" only swaps the numbers shown.
  const rankOfRow = new Map(strict.map((a, i) => [a.provider, i + 1]));
  const rows = lat
    ? strict.flatMap((a) => {
        const l = lat.get(a.provider);
        return l ? [{ ...a, error_rate: l.error_rate, ci_low: l.ci_low ?? l.error_rate, ci_high: l.ci_high ?? l.error_rate }] : [];
      })
    : strict;
  const leader = strict[0];
  const clustered = rows.some((r) => r.ci_low_cluster != null && r.ci_high_cluster != null);
  const sem = v3(run).semantic;
  const semRows = sem?.published ? inSlice(sem.rows, tier, kind, slice) : [];
  const semOf = (id: string) => semRows.find((x) => x.provider === id);
  const max = maxOf(rows.map((r) => r.ci_high), 0.1) * 1.08 || 0.1;
  const trend = useMemo(() => {
    const out: Record<string, number[]> = {};
    for (const h of history)
      for (const a of h.aggregates)
        if (a.tier === tier && a.metric === metric && a.slice_kind === kind && a.slice === slice) (out[a.provider] ??= []).push(a.error_rate);
    return out;
  }, [history, tier, metric, kind, slice]);

  const columns: Column<Aggregate>[] = [
    { key: "rank", header: "#", cell: (r) => <span className="num text-ink-3">{String(rankOfRow.get(r.provider) ?? 0).padStart(2, "0")}</span>, className: "w-10" },
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
          {!lat && r.ci_low_cluster != null && r.ci_high_cluster != null && (
            <span className="block text-[11px]" title="Channel-clustered 95% interval: resamples whole channels instead of clips (sensitivity analysis).">
              by channel {pct(r.ci_low_cluster)} to {pct(r.ci_high_cluster)}
              {r.n_channels != null ? `, ${int(r.n_channels)} ch` : ""}
            </span>
          )}
        </span>
      ),
      headerClassName: "hidden xl:table-cell",
      className: "hidden xl:table-cell",
    },
    {
      key: "cov",
      header: (
        <Tooltip content="Share of the verified clips this model returned a result for. Only models at or above the minimum are ranked.">
          <span tabIndex={0} className="cursor-help">
            Coverage
          </span>
        </Tooltip>
      ),
      align: "right",
      sort: (r) => r.coverage ?? null,
      cell: (r) => <span className="num text-[12.5px] text-ink-3">{pct(r.coverage)}</span>,
      headerClassName: "hidden lg:table-cell",
      className: "hidden lg:table-cell",
    },
    ...(semRows.length
      ? [
          {
            key: "sem",
            header: (
              <Tooltip content="Share of meaning lost, as scored by a calibrated LLM judge against the verified reference. Lower is better.">
                <span tabIndex={0} className="cursor-help">
                  Semantic error
                </span>
              </Tooltip>
            ),
            align: "right" as const,
            sort: (r: Aggregate) => semOf(r.provider)?.error ?? null,
            cell: (r: Aggregate) => {
              const x = semOf(r.provider);
              return x?.error == null ? <span className="text-ink-3">n/a</span> : <span className="num text-ink-2">{pct(x.error)}</span>;
            },
            headerClassName: "hidden md:table-cell",
            className: "hidden md:table-cell",
          },
        ]
      : []),
    {
      key: "vs",
      header: "vs leader",
      cell: (r) => {
        if (!leader || r.provider === leader.provider) return <Badge tone="good">Lowest</Badge>;
        if (lat) return <span className="text-[12.5px] text-ink-3">not tested</span>;
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
      title={`${m?.label ?? metric}${lat ? ", any valid form" : ""}, ${TIER_LABEL[tier].toLowerCase()}${kind === "all" ? "" : `, ${sliceLabel(kind, slice).toLowerCase()}`}`}
      caption={
        <>
          {rows.some((r) => r.too_small) ? "Lower is better. Fewer than 15 clips per model: read the ranking as a first look, not a result." : "Lower is better. Click a header to sort; click a model for its page."}
          {lat && " Any valid form counts a span as right when it matches one of the reference's accepted spellings (kana or kanji, numerals or words). Order, rank and the paired test stay on strict scores."}
          {" "}The vs-leader column uses paired tests corrected for multiple comparisons (Benjamini-Hochberg).
          {clustered && " The by-channel interval resamples whole channels, a check on clips from one channel looking alike."}
        </>
      }
      source={`source: aggregates${lat ? " (error_rate_lattice)" : ""} + paired, run=${run.run_id}, tier=${tier}, metric=${metric}, slice_kind=${kind}, slice=${slice}`}
      bodyClassName="p-0 sm:p-0"
    >
      {tier === "creator" && (
        <p className="border-b border-line bg-warn-soft/60 px-4 py-2 text-[12.5px] text-ink-2 sm:px-5">
          Unverified reference: scored against the creators' own captions. Read as agreement with the creator, not accuracy.
        </p>
      )}
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Leaderboard" highlight={highlight ? (r) => r.provider === highlight : undefined} />
      <NotRanked run={run} className="m-4 sm:m-5" />
      {sem && !sem.published && (
        <p className="border-t border-line px-4 py-2.5 text-[12.5px] text-ink-3 sm:px-5">
          No semantic error column: {sem.reason ?? "the LLM judge has not passed its calibration against human labels, so its scores are not published."}{" "}
          <Link to="/methodology#semantic-judge" className="text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
            Judge calibration
          </Link>
        </p>
      )}
    </Figure>
  );
}
