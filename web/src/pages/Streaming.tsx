import { Link, useSearchParams } from "react-router";
import { ArrowRight } from "lucide-react";
import type { Run, SliceKind, Tier } from "@/lib/types";
import { useRunFor } from "@/lib/data";
import { LANGUAGE_NAME, TIER_LABEL, isPublished, pickTier, providerColor, providerLabel } from "@/lib/analysis";
import { SLICE_KIND_LABEL, sliceLabel } from "@/lib/metrics";
import { slicesIn, streamPair, v3, type StreamRow, type Streaming as StreamingData } from "@/lib/v3";
import { date, ms, pct, signedPts } from "@/lib/format";
import { maxOf } from "@/lib/scale";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/Table";
import { CIBar } from "@/components/ui/CIBar";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { TierControl, TierNote } from "@/components/ui/TierControl";
import { Gated } from "@/components/ui/Gate";
import { buttonClass } from "@/components/ui/Button";
import { SliceFilter, readSlice, sliceParams } from "@/components/ui/SliceFilter";
import { Help, ModelCell, ValueCell, bestBy } from "@/components/v3/shared";
import { inSlice } from "@/lib/v3";

const KIND_ORDER: SliceKind[] = ["all", "condition", "category", "audio_condition", "tag", "duration"];

/**
 * Streaming leaderboard (A1). Realtime APIs fed audio at real-time pace over a WebSocket. Its own board, never mixed
 * with batch numbers: time to first partial, time to final segment, how often partial words get rewritten, and the
 * error rate of the final transcript with 95% intervals.
 */
export default function Streaming() {
  const [params, setParams] = useSearchParams();
  const lang = params.get("lang");
  const state = useRunFor(lang);
  const run = state.data ? state.data.run : undefined;
  const st = run ? v3(run).streaming : null;
  const published = run ? isPublished(run) : run === undefined;
  const tier = pickTier(run ?? undefined, params.get("tier"));
  const present = st ? slicesIn(st.rows, tier) : [];
  const kinds = KIND_ORDER.filter((k) => present.some((p) => p.kind === k));
  const { kind, slice, values } = readSlice(params, kinds.length ? kinds : ["all"], (k) => present.filter((p) => p.kind === k).map((p) => p.slice));
  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v == null) p.delete(k);
    else p.set(k, v);
    setParams(p, { replace: true });
  };

  return (
    <>
      <PageHeader
        eyebrow={run ? `Streaming / ${LANGUAGE_NAME[run.language] ?? run.language}${st?.created_at ? ` / ${date(st.created_at)}` : ""}` : "Streaming"}
        title="Streaming leaderboard"
        description="Realtime APIs, fed audio at the pace it was spoken. Ranked by the error rate of the final transcript, with how fast words appear and how often the first guess gets rewritten. These numbers are never mixed with the batch leaderboard."
        actions={
          <Link to={`/leaderboard${lang ? `?lang=${lang}` : ""}`} className={buttonClass("secondary", "sm")}>
            Batch leaderboard <ArrowRight size={14} />
          </Link>
        }
      >
        {published && st && <TierControl run={run ?? undefined} value={tier} onChange={(v) => set("tier", v)} />}
        {published && st && <SliceFilter kinds={kinds} kind={kind} slice={slice} values={values} onChange={(k, v) => setParams(sliceParams(params, k, v), { replace: true })} />}
      </PageHeader>
      <div className="frame py-8">
        {state.status === "error" ? (
          <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />
        ) : state.status === "loading" ? (
          <Skeleton className="h-96 w-full" />
        ) : (
          <Gated run={run} lang={lang ?? undefined} what="streaming ranking">
            {run && !st ? (
              <EmptyState title="No streaming results in this run yet." body="The streaming track runs separately from batch runs. This board fills in once a streaming run is exported for this language." />
            ) : (
              run && st && (
                <div className="grid grid-cols-1 gap-8">
                  <TierNote tier={tier} />
                  <Board run={run} st={st} tier={tier} kind={kind} slice={slice} />
                  <Timeline run={run} rows={inSlice(st.rows, tier, kind, slice)} kind={kind} slice={slice} />
                </div>
              )
            )}
          </Gated>
        )}
      </div>
    </>
  );
}

function Board({ run, st, tier, kind, slice }: { run: Run; st: StreamingData; tier: Tier; kind: SliceKind; slice: string }) {
  const rows = [...inSlice(st.rows, tier, kind, slice)].sort((a, b) => (a.error_rate ?? 9) - (b.error_rate ?? 9));
  const leader = rows[0];
  const max = maxOf(rows.map((r) => r.ci_high ?? r.error_rate ?? 0), 0.1) * 1.08;
  const hasPairs = st.paired.length > 0;
  const columns: Column<StreamRow>[] = [
    { key: "rank", header: "#", cell: (_, i) => <span className="num text-ink-3">{String(i + 1).padStart(2, "0")}</span>, className: "w-10" },
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} sub={r.model_id} /> },
    {
      key: "err",
      header: <Help label="Final error rate" help="Error rate of the final transcript, after every partial has settled. Lower is better." />,
      sort: (r) => r.error_rate,
      cell: (r) =>
        r.error_rate == null ? (
          <span className="text-ink-3">n/a</span>
        ) : (
          <span className="flex items-center gap-3">
            <span className="num w-12 text-right text-ink">{pct(r.error_rate)}</span>
            {r.ci_low != null && r.ci_high != null && <CIBar value={r.error_rate} low={r.ci_low} high={r.ci_high} max={max} color={providerColor(run, r.provider)} className="hidden sm:inline-block" />}
          </span>
        ),
    },
    {
      key: "vs",
      header: "vs leader",
      cell: (r) => {
        if (!leader || r.provider === leader.provider) return <Badge tone="good">Lowest</Badge>;
        const t = hasPairs ? streamPair(st, tier, kind, slice, r.provider, leader.provider) : null;
        if (!t) return <span className="text-[12.5px] text-ink-3">not tested</span>;
        return t.tied ? <span className="text-[12.5px] text-ink-2">No detectable difference</span> : <span className="num text-[12.5px] text-ink-2">{signedPts(t.diff)}</span>;
      },
    },
    { key: "ttfp", header: <Help label="First partial" help="Time from the first audio sent to the first partial word back, median (p95 below). Lower is better." />, align: "right", sort: (r) => r.ttfp_p50_ms, cell: (r) => <Lat p50={r.ttfp_p50_ms} p95={r.ttfp_p95_ms} best={bestBy(rows, (q) => q.ttfp_p50_ms, "lower") === r.provider} /> },
    { key: "ttfs", header: <Help label="Final segment" help="Time from the end of a spoken segment to its final transcript (TTFS), median (p95 below). Lower is better." />, align: "right", sort: (r) => r.ttfs_p50_ms, cell: (r) => <Lat p50={r.ttfs_p50_ms} p95={r.ttfs_p95_ms} best={bestBy(rows, (q) => q.ttfs_p50_ms, "lower") === r.provider} /> },
    { key: "stab", header: <Help label="Partial stability" help="Share of partial words that reach the final transcript unchanged. Higher means captions flicker less." />, align: "right", sort: (r) => r.stability, cell: (r) => <ValueCell v={r.stability} fmt={(x) => pct(x)} lo={r.stability_ci_low} hi={r.stability_ci_high} best={bestBy(rows, (q) => q.stability, "higher") === r.provider} /> },
    {
      key: "n",
      header: "Clips",
      align: "right",
      sort: (r) => r.n_clips,
      cell: (r) => (r.too_small ? <Badge tone="warn">n={r.n_clips}</Badge> : <span className="num text-ink-3">{r.n_clips ?? "n/a"}</span>),
      headerClassName: "hidden sm:table-cell",
      className: "hidden sm:table-cell",
    },
  ];
  return (
    <Figure
      n={1}
      title={`Streaming, ${TIER_LABEL[tier].toLowerCase()}${kind === "all" ? "" : `, ${sliceLabel(kind, slice).toLowerCase()}`}`}
      caption={
        <>
          Lower error is better. Audio fed at {st.pace ?? "real-time"} pace. {hasPairs ? "The vs-leader column comes from the paired test on the final transcripts." : "No paired test was exported for streaming, so ties are not called."}
          {rows.some((r) => r.too_small) ? " Fewer than 15 clips: read as a first look." : ""}
        </>
      }
      source={`source: streaming${st.run_id ? ` (run ${st.run_id})` : ""}, tier=${tier}, slice_kind=${kind}, slice=${slice}`}
      bodyClassName="p-0 sm:p-0"
    >
      {rows.length ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Streaming leaderboard" /> : <div className="p-4 sm:p-5"><EmptyState title={`No streaming results for ${SLICE_KIND_LABEL[kind].toLowerCase()} ${sliceLabel(kind, slice).toLowerCase()}.`} /></div>}
    </Figure>
  );
}

function Lat({ p50, p95, best }: { p50: number | null; p95: number | null; best: boolean }) {
  if (p50 == null) return <span className="text-[12.5px] text-ink-3">n/a</span>;
  return (
    <span className="inline-flex flex-col items-end leading-tight">
      <span className={best ? "num text-[13px] font-semibold text-ink" : "num text-[13px] text-ink-2"}>{ms(p50)}</span>
      {p95 != null && <span className="num text-[10.5px] text-ink-3">p95 {ms(p95)}</span>}
    </span>
  );
}

/** First partial to final segment, per model, on one millisecond axis (median). */
function Timeline({ run, rows, kind, slice }: { run: Run; rows: StreamRow[]; kind: SliceKind; slice: string }) {
  const ok = rows.filter((r) => r.ttfp_p50_ms != null || r.ttfs_p50_ms != null).sort((a, b) => (a.ttfs_p50_ms ?? a.ttfp_p50_ms ?? 0) - (b.ttfs_p50_ms ?? b.ttfp_p50_ms ?? 0));
  if (!ok.length) return null;
  const max = maxOf(ok.flatMap((r) => [r.ttfp_p95_ms ?? 0, r.ttfs_p50_ms ?? 0, r.ttfp_p50_ms ?? 0]), 1000) * 1.08;
  const x = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => Math.round((max * f) / 50) * 50);
  return (
    <Figure n={2} title="How fast words appear" caption="Hollow dot: first partial word. Filled dot: final text for a segment. Both medians; shorter lines mean captions settle sooner." source={`source: streaming, slice_kind=${kind}, slice=${slice}`}>
      <div className="grid gap-2.5">
        {ok.map((r) => (
          <div key={r.provider} className="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-3 sm:grid-cols-[140px_minmax(0,1fr)_150px]">
            <span className="truncate text-[13px] text-ink">{providerLabel(run, r.provider)}</span>
            <span className="relative h-5" role="img" aria-label={`${providerLabel(run, r.provider)}: first partial ${ms(r.ttfp_p50_ms)}, final segment ${ms(r.ttfs_p50_ms)}`}>
              <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line" />
              {r.ttfp_p50_ms != null && r.ttfs_p50_ms != null && <span className="absolute top-1/2 h-[2px] -translate-y-1/2" style={{ left: x(Math.min(r.ttfp_p50_ms, r.ttfs_p50_ms)), width: `calc(${x(Math.max(r.ttfp_p50_ms, r.ttfs_p50_ms))} - ${x(Math.min(r.ttfp_p50_ms, r.ttfs_p50_ms))})`, background: providerColor(run, r.provider) }} />}
              {r.ttfp_p50_ms != null && <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 bg-surface" style={{ left: x(r.ttfp_p50_ms), borderColor: providerColor(run, r.provider) }} />}
              {r.ttfs_p50_ms != null && <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface" style={{ left: x(r.ttfs_p50_ms), background: providerColor(run, r.provider) }} />}
            </span>
            <span className="num hidden text-right text-[12px] text-ink-3 sm:block">
              {ms(r.ttfp_p50_ms)} / {ms(r.ttfs_p50_ms)}
            </span>
          </div>
        ))}
        <div className="grid grid-cols-[96px_minmax(0,1fr)] gap-3 sm:grid-cols-[140px_minmax(0,1fr)_150px]">
          <span />
          <span className="relative h-4 font-mono text-[10.5px] text-ink-3">
            {ticks.map((t, i) => (
              <span key={i} className="absolute -translate-x-1/2" style={{ left: x(t) }}>
                {ms(t)}
              </span>
            ))}
          </span>
        </div>
      </div>
    </Figure>
  );
}
