import { lazy, Suspense, useMemo, useState, type ReactNode } from "react";
import { Link, useParams, useSearchParams } from "react-router";
import { ArrowRight, ArrowUpRight, GitCompareArrows } from "lucide-react";
import type { Aggregate, Run, SliceKind, Tier } from "@/lib/types";
import { useAllRuns, useRun } from "@/lib/data";
import {
  LANGUAGE_NAME,
  TAG_LABEL,
  TIER_LABEL,
  breaksOn,
  clipById,
  overall,
  isPublished,
  pairsFor,
  pickMetric,
  pickTier,
  providerById,
  providerColor,
  providerLabel,
  rankHistory,
  stabilityFor,
  tagProfile,
  youtubeUrl,
  type RankPoint,
} from "@/lib/analysis";
import { date, int, ms, pct, pValue, signedPts, usdPerHour } from "@/lib/format";
import { CAPTION_METRICS, SLICE_KIND_LABEL, hasCaptionMetrics, metricN, metricValue, sliceLabel } from "@/lib/metrics";
import { ACCOUNTS } from "@/lib/flags";
import { useWidth } from "@/lib/useWidth";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { DataTable, type Column } from "@/components/ui/Table";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { DiffBar, Verdict } from "@/components/ui/DiffBar";
import { Skeleton } from "@/components/ui/Skeleton";
import { buttonClass } from "@/components/ui/Button";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { TierControl, TierNote } from "@/components/ui/TierControl";
import { Gated } from "@/components/ui/Gate";
import { BadgeEmbed, CanaryHistory, ComputeSpec, EveryMeasure, FeatureRow, ReliabilitySpec, SettingsSpec, StrengthsFailures, priceSpeed, providerSections } from "@/components/v3/ProviderSections";

// Only with accounts on; it carries the Better Auth client, so the public build never loads it.
const WatchButton = ACCOUNTS ? lazy(() => import("@/components/accounts/WatchButton")) : null;

/**
 * One model in depth: headline numbers with intervals, rank across runs, clip types against the field,
 * paired results against every other model, what breaks it, cost, latency and data policy.
 */
export default function Provider() {
  const { id = "" } = useParams();
  const state = useRun();
  const runs = useAllRuns();
  const [params, setParams] = useSearchParams();
  const run = state.data?.run;
  const tier = pickTier(run, params.get("tier"));
  const metric = pickMetric(run ?? undefined, params.get("metric"));
  const p = run ? providerById(run, id) : undefined;
  const set = (k: string, v: string) => {
    const q = new URLSearchParams(params);
    q.set(k, v);
    setParams(q, { replace: true });
  };

  if (state.status === "error")
    return (
      <>
        <PageHeader eyebrow="Model" title={id} />
        <div className="frame py-8">
          <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />
        </div>
      </>
    );

  if (run && !p)
    return (
      <>
        <PageHeader eyebrow="Model" title="Model not found" />
        <div className="frame py-8">
          <EmptyState
            title={`No model with the id "${id}" in the latest run.`}
            body="It may have been removed or renamed. The leaderboard lists every model in this run."
            action={
              <Link to="/leaderboard" className={buttonClass("secondary", "sm")}>
                Open the leaderboard <ArrowRight size={14} />
              </Link>
            }
          />
        </div>
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow={
          run && p ? (
            <span className="inline-flex items-center gap-2">
              <SeriesDot color={providerColor(run, id)} /> Model / <span className="normal-case">{p.model_id}</span>
            </span>
          ) : (
            "Model"
          )
        }
        title={p?.label ?? "Model"}
        description={run && p ? (isPublished(run) ? <Summary run={run} id={id} tier={tier} metric={metric} /> : `Scored in the pipeline. ${LANGUAGE_NAME[run.language] ?? run.language} results are not published yet.`) : "Loading results."}
        actions={run && p && isPublished(run) && <Actions run={run} id={id} />}
      >
        {(!run || isPublished(run)) && <TierControl run={run} value={tier} onChange={(v) => set("tier", v)} />}
        {run && isPublished(run) && <Segmented label="Metric" value={metric} onChange={(v) => set("metric", v)} options={run.metrics.map((m) => ({ value: m.name, label: m.label }))} />}
      </PageHeader>
      {run && p && !isPublished(run) ? (
        <div className="frame py-8">
          <Gated run={run} what="model results">
            {null}
          </Gated>
        </div>
      ) : run && p ? (
        <Body run={run} id={id} tier={tier} metric={metric} history={runs.data?.runs} />
      ) : (
        <div className="frame grid gap-8 py-8">
          <Skeleton className="h-32 w-full" />
          <Skeleton className="h-80 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
      )}
    </>
  );
}

function Summary({ run, id, tier, metric }: { run: Run; id: string; tier: Tier; metric: string }) {
  const rows = overall(run, tier, metric);
  const i = rows.findIndex((r) => r.provider === id);
  const b = breaksOn(run, id, tier);
  if (i < 0) return <>No results on this tier.</>;
  return (
    <>
      Ranked {i + 1} of {rows.length} on {TIER_LABEL[tier].toLowerCase()} clips in the run of {date(run.created_at)}.
      {b ? ` Struggles most with ${TAG_LABEL[b.tag]?.toLowerCase()}.` : ""}
    </>
  );
}

function Actions({ run, id }: { run: Run; id: string }) {
  const leader = overall(run)[0]?.provider;
  const other = leader && leader !== id ? leader : overall(run)[1]?.provider;
  return (
    <>
      <Link to={`/compare?m=${[id, other].filter(Boolean).join(",")}`} className={buttonClass("secondary", "sm")}>
        <GitCompareArrows size={14} /> Compare
      </Link>
      {WatchButton && (
        <Suspense fallback={null}>
          <WatchButton id={id} />
        </Suspense>
      )}
    </>
  );
}

function Body({ run, id, tier, metric, history }: { run: Run; id: string; tier: Tier; metric: string; history?: Run[] }) {
  const rows = overall(run, tier, metric);
  const me = rows.find((r) => r.provider === id);
  const hist = useMemo(() => (history ? rankHistory(history, id, tier, metric) : []), [history, id, tier, metric]);
  if (!me)
    return (
      <div className="frame py-8">
        <EmptyState title="No results on this tier." body="This model was not scored on these clips. Switch the reference tier above." />
      </div>
    );
  const p = providerById(run, id)!;
  const color = providerColor(run, id);
  const rank = rows.indexOf(me) + 1;
  const ps = priceSpeed(run, id, tier, metric);
  const has = providerSections(run, id, tier, metric);
  let n = 0;
  const fig = () => ++n;
  return (
    <div className="frame grid gap-8 py-8">
      <TierNote tier={tier} />
      <Stats
        items={[
          { label: "Error rate", value: pct(me.error_rate), note: `95% interval ${pct(me.ci_low)} to ${pct(me.ci_high)}, ${me.n_clips} clips.` },
          {
            label: "Rank",
            value: (
              <span className="inline-flex items-baseline gap-1">
                {rank}
                <span className="text-[18px] text-ink-3">/ {rows.length}</span>
              </span>
            ),
            note: hist.length > 1 ? `Across ${hist.length} runs: ${hist.map((h) => h.rank).join(", ")}.` : "First run with this model.",
          },
          { label: "Per 1,000 min", value: p.paid ? usdPerHour(ps.per1k) : "$0", note: p.paid ? `${usdPerHour(p.usd_per_hour)} per hour at list price, checked ${date(p.pricing_checked_on)}.` : "Runs locally. No per-minute charge." },
          {
            label: "Speed factor",
            value: ps.speed.value == null ? "n/a" : `${ps.speed.value >= 10 ? ps.speed.value.toFixed(0) : ps.speed.value.toFixed(1)}x`,
            note: `${ps.speed.source === "compute" ? "Measured on the declared machine. " : "Audio seconds per second of waiting. "}Median latency ${ms(me.latency_p50)}, p95 ${ms(me.latency_p95)}.`,
          },
        ]}
      />

      <RankHistory n={fig()} run={run} id={id} points={hist} color={color} tier={tier} metric={metric} loading={!history} />
      {has.canary && <CanaryHistory n={fig()} run={run} id={id} tier={tier} metric={metric} />}
      {has.features && <FeatureRow n={fig()} run={run} id={id} />}
      {has.strengths && <StrengthsFailures n={fig()} run={run} id={id} tier={tier} metric={metric} />}
      <ByType n={fig()} run={run} id={id} tier={tier} metric={metric} />
      {has.measures && <EveryMeasure n={fig()} run={run} id={id} tier={tier} metric={metric} />}
      {hasCaptionMetrics(run) && <CaptionQuality n={fig()} id={id} rows={rows} tier={tier} metric={metric} />}
      <Paired n={fig()} run={run} id={id} tier={tier} metric={metric} />
      <Breaks n={fig()} run={run} id={id} tier={tier} />
      <div className="grid gap-8 lg:grid-cols-2">
        {has.reliability && <ReliabilitySpec n={fig()} run={run} id={id} />}
        {has.compute && <ComputeSpec n={fig()} run={run} id={id} />}
        <Spec
          n={fig()}
          title="Cost and speed"
          source={`source: aggregates, tier=${tier}, slice=all; pricing.toml`}
          rows={[
            ["List price", p.paid ? `${usdPerHour(p.usd_per_hour)} per hour of audio` : "Free, runs on your machine"],
            ["Cost of this run", `$${me.cost_usd_total.toFixed(2)} for ${me.audio_hours.toFixed(2)} h of audio`],
            ["1,000 hours at list price", p.paid ? `$${Math.round(p.usd_per_hour * 1000).toLocaleString("en-US")}` : "$0"],
            ["Per 1,000 minutes", p.paid ? usdPerHour(ps.per1k) : "$0"],
            ["Latency p50 / p95", `${ms(me.latency_p50)} / ${ms(me.latency_p95)}`],
            ["Real-time factor", me.rtf != null ? `${me.rtf.toFixed(2)} (lower is faster)` : "n/a"],
            ...stabilityRows(run, id),
            ["Pricing checked", date(p.pricing_checked_on)],
          ]}
        />
        <Spec
          n={fig()}
          title="Data policy"
          source={`source: providers, checked ${p.pricing_checked_on}`}
          rows={[
            ["Audio leaves your machine", p.uploads_audio ? "Yes, uploaded to the provider" : "No, runs locally"],
            ["Retention", retention(p.retention_days, p.uploads_audio)],
            ["Trains on API audio by default", p.trains_by_default == null ? (p.uploads_audio ? "Not stated" : "Not applicable") : p.trains_by_default ? "Yes" : "No"],
            ["Opt-out", p.uploads_audio ? optOut(p.opt_out) : "Not needed"],
          ]}
          footer={
            p.policy_url ? (
              <a href={p.policy_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[13px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
                Provider's policy <ArrowUpRight size={13} />
              </a>
            ) : (
              <span className="text-[12.5px] text-ink-3">Policies change; check the provider's terms before sending sensitive audio.</span>
            )
          }
        />
        <SettingsSpec n={fig()} run={run} id={id} />
        <BadgeEmbed n={fig()} run={run} id={id} />
      </div>
    </div>
  );
}

function stabilityRows(run: Run, id: string): [string, string][] {
  const s = stabilityFor(run, id);
  if (!s) return [["Run-to-run stability", "Not measured in this run"]];
  return [["Run-to-run disagreement", `${pct(s.disagreement, 2)} of text, ${s.n} clips x ${s.repeats} repeats`]];
}

function retention(d: number | null | undefined, uploads: boolean) {
  if (!uploads) return "Nothing is sent";
  if (d == null) return "Not stated";
  return d === 0 ? "Not stored after the request" : `${d} days`;
}

function optOut(o: string | undefined) {
  if (o === "applied") return "Offered, and clipbench sets it";
  if (o === "not_offered") return "Not offered";
  if (o === "not_needed") return "Not needed";
  return "Not stated";
}

// ---------------------------------------------------------------- stats

function Stats({ items }: { items: { label: string; value: ReactNode; note: string }[] }) {
  return (
    <dl className="grid grid-cols-2 overflow-hidden rounded-md border border-line bg-surface shadow-card lg:grid-cols-4">
      {items.map((s, i) => (
        <div key={s.label} className={cn("px-4 py-5 sm:px-5 sm:py-6", i % 2 && "border-l border-line", i > 1 && "border-t border-line lg:border-t-0", i === 2 && "lg:border-l")}>
          <dt className="label">{s.label}</dt>
          <dd className="mt-3 text-[30px] leading-none font-medium tracking-[-0.03em] text-ink sm:text-[36px]" style={{ fontStretch: "95%" }}>
            {s.value}
          </dd>
          <dd className="mt-2.5 text-[12.5px] text-ink-2">{s.note}</dd>
        </div>
      ))}
    </dl>
  );
}

// ---------------------------------------------------------------- rank history

function RankHistory({ n, run, id, points, color, tier, metric, loading }: { n: number; run: Run; id: string; points: RankPoint[]; color: string; tier: Tier; metric: string; loading: boolean }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const changed = points.some((p, i) => i > 0 && points[i - 1]!.model_id !== p.model_id);
  const H = changed ? 206 : 190;
  const pad = { l: 44, r: 24, t: 28, b: changed ? 50 : 34 };
  const step = niceStep(Math.max(0.01, ...points.map((p) => p.agg.ci_high)) * 1.15);
  const hi = Math.ceil((Math.max(0.01, ...points.map((p) => p.agg.ci_high)) * 1.15) / step) * step;
  const inset = 28; // keep the first and last points (and their labels) clear of the axis labels
  const x = (i: number) => (points.length <= 1 ? (w - pad.l - pad.r) / 2 + pad.l : pad.l + inset + (i / (points.length - 1)) * (w - pad.l - pad.r - inset * 2));
  const y = (v: number) => pad.t + (1 - v / hi) * (H - pad.t - pad.b);
  const ticks = Array.from({ length: Math.round(hi / step) + 1 }, (_, i) => i * step);
  return (
    <Figure
      n={n}
      title="Error rate and rank across runs"
      caption={points.length > 1 ? "Each point is one published run, with its 95% interval. The label above is the rank in that run." : "Only one run so far. The trend fills in as runs are published."}
      source={`source: aggregates of every run in index.json, tier=${tier}, metric=${metric}, slice=all`}
    >
      <div ref={ref} className="w-full">
        {loading ? (
          <Skeleton className="h-[200px] w-full" />
        ) : (
          <svg width={w} height={H} role="img" aria-label={`${providerLabel(run, id)} by run: ${points.map((p) => `${date(p.created_at)} rank ${p.rank} of ${p.of}, ${pct(p.agg.error_rate)}`).join("; ")}`}>
            {ticks.map((t) => (
              <g key={t}>
                <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" />
                <text x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
                  {pct(t, Math.abs(Math.round(step * 100) - step * 100) > 1e-6 ? 1 : 0)}
                </text>
              </g>
            ))}
            {points.length > 1 && (
              <path d={points.map((p, i) => `${i ? "L" : "M"}${x(i)},${y(p.agg.error_rate)}`).join(" ")} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" />
            )}
            {points.map((p, i) => (
              <g key={p.run_id}>
                <line x1={x(i)} x2={x(i)} y1={y(p.agg.ci_low)} y2={y(p.agg.ci_high)} stroke={color} strokeWidth={6} strokeLinecap="round" opacity={0.3} />
                <circle cx={x(i)} cy={y(p.agg.error_rate)} r={5} fill={color} stroke="var(--surface)" strokeWidth={2} />
                <text x={x(i)} y={y(p.agg.ci_high) - 10} textAnchor="middle" className="fill-[var(--ink)] font-mono text-[11px]">
                  #{p.rank}
                  <tspan className="fill-[var(--ink-3)]">/{p.of}</tspan>
                </text>
                <text x={x(i)} y={H - pad.b + 20} textAnchor="middle" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
                  {date(p.created_at)}
                </text>
                {i > 0 && points[i - 1]!.model_id !== p.model_id && (
                  <text x={x(i)} y={H - pad.b + 36} textAnchor="middle" className="fill-[var(--ink-2)] font-mono text-[10.5px]">
                    new: {p.model_id}
                  </text>
                )}
              </g>
            ))}
          </svg>
        )}
      </div>
    </Figure>
  );
}

/** A round tick step (in rate units) giving three to five gridlines. */
function niceStep(max: number) {
  for (const s of [0.01, 0.02, 0.025, 0.05, 0.1, 0.2, 0.25]) if (max / s <= 5) return s;
  return 0.5;
}

// ---------------------------------------------------------------- by clip type

function ByType({ n, run, id, tier, metric }: { n: number; run: Run; id: string; tier: Tier; metric: string }) {
  type K = Exclude<SliceKind, "all">;
  const kinds = (["condition", "category", "audio_condition", "tag", "duration"] as K[]).filter((k) => tagProfile(run, id, tier, metric, k).length > 0);
  const [kind, setKind] = useState<K>(() => kinds[0] ?? "duration");
  const rows = tagProfile(run, id, tier, metric, kind);
  const max = Math.max(0.01, ...rows.flatMap((r) => r.rows.map((a) => a.ci_high))) * 1.04;
  const color = providerColor(run, id);
  const w = (v: number) => `${Math.min(100, (v / max) * 100)}%`;
  return (
    <Figure
      n={n}
      title={`Error rate by ${SLICE_KIND_LABEL[kind].toLowerCase()}, against the field`}
      caption="Bars are this model with its 95% interval. The grey tick is the median of the other models; the right column is the gap to it."
      controls={
        <Segmented
          label="Slice by"
          value={kind}
          onChange={setKind}
          options={kinds.map((k) => ({ value: k, label: SLICE_KIND_LABEL[k] }))}
        />
      }
      source={`source: aggregates, tier=${tier}, metric=${metric}, slice_kind=${kind}`}
    >
      {rows.length === 0 ? (
        <EmptyState
          title={kind === "tag" ? "No clip types tagged in this run yet." : "No length slices in this run."}
          body={kind === "tag" ? "Clip types are tagged when a person verifies a clip. Switch to Length to see how clip length affects this model." : undefined}
        />
      ) : (
        <div className="grid gap-1">
          <div className="hidden grid-cols-[150px_minmax(0,1fr)_60px_76px_44px] gap-3 pb-1 sm:grid">
            <span className="label">{SLICE_KIND_LABEL[kind]}</span>
            <span />
            <span className="label text-right">Rate</span>
            <span className="label text-right">vs median</span>
            <span className="label text-right">Rank</span>
          </div>
          {rows.map((r) => {
            const m = r.mine!;
            const gap = m.error_rate - r.fieldMedian;
            return (
              <div key={r.slice} className="grid grid-cols-[minmax(0,1fr)_56px_64px] items-center gap-x-3 gap-y-1.5 border-t border-line py-2.5 sm:grid-cols-[150px_minmax(0,1fr)_60px_76px_44px]">
                <span className="col-span-3 flex items-center gap-2 text-[13.5px] text-ink sm:col-span-1">
                  {sliceLabel(kind, r.slice)}
                  {m.too_small && <Badge tone="warn">n={m.n_clips}</Badge>}
                </span>
                <span className="relative h-6" role="img" aria-label={`${pct(m.error_rate)}, interval ${pct(m.ci_low)} to ${pct(m.ci_high)}; others' median ${pct(r.fieldMedian)}`}>
                  <span className="absolute inset-y-0 left-0 w-px bg-line-strong" />
                  <span className={cn("absolute top-1 bottom-1 left-0 rounded-r-[4px]", m.too_small && "hatch border border-line")} style={{ width: w(m.error_rate), background: m.too_small ? undefined : color, opacity: 0.85 }} />
                  <span className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" style={{ left: w(m.ci_low), width: `calc(${w(m.ci_high)} - ${w(m.ci_low)})` }}>
                    <span className="absolute -top-[4px] left-0 h-[9px] w-px bg-ink" />
                    <span className="absolute -top-[4px] right-0 h-[9px] w-px bg-ink" />
                  </span>
                  <span className="absolute -top-0.5 -bottom-0.5 w-[2px] -translate-x-1/2 rounded-full bg-ink-3" style={{ left: w(r.fieldMedian) }} />
                </span>
                <span className="num text-right text-[13px] text-ink">{pct(m.error_rate)}</span>
                <span className={cn("num text-right text-[12.5px]", m.too_small ? "text-ink-3" : gap > 0.002 ? "text-signal-ink" : gap < -0.002 ? "text-good" : "text-ink-3")}>{signedPts(gap)}</span>
                <span className="num hidden text-right text-[12.5px] text-ink-3 sm:block">
                  {r.rank}/{r.rows.length}
                </span>
              </div>
            );
          })}
          <div className="mt-2 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-line pt-3 text-[11.5px] text-ink-3">
            <span className="inline-flex items-center gap-2">
              <span className="h-2.5 w-5 rounded-r-[3px]" style={{ background: color, opacity: 0.85 }} /> {providerLabel(run, id)}
            </span>
            <span className="inline-flex items-center gap-2">
              <span className="h-3 w-[2px] rounded-full bg-ink-3" /> Median of the other models
            </span>
            <span className="inline-flex items-center gap-2">
              <span className="hatch inline-block h-2.5 w-5 rounded-[2px] border border-line" /> Fewer than 15 clips
            </span>
          </div>
        </div>
      )}
    </Figure>
  );
}

// ---------------------------------------------------------------- paired

type PairRow = ReturnType<typeof pairsFor>[number];

function Paired({ n, run, id, tier, metric }: { n: number; run: Run; id: string; tier: Tier; metric: string }) {
  const rows = pairsFor(run, { tier, metric, kind: "all", slice: "all" }, id);
  const span = Math.max(0.01, ...rows.flatMap((r) => [Math.abs(r.t.ci_low), Math.abs(r.t.ci_high)])) * 1.1;
  const label = providerLabel(run, id);
  const beats = rows.filter((r) => r.t.better === id).length;
  const loses = rows.filter((r) => r.t.better && r.t.better !== id).length;
  const columns: Column<PairRow>[] = [
    {
      key: "other",
      header: "Against",
      sticky: true,
      cell: (r) => (
        <Link to={`/providers/${r.other}`} className="flex items-center gap-2 hover:underline">
          <SeriesDot color={providerColor(run, r.other)} />
          <span className="text-ink">{providerLabel(run, r.other)}</span>
        </Link>
      ),
    },
    { key: "verdict", header: "Paired test", cell: (r) => <Verdict better={r.t.better} id={id} /> },
    { key: "diff", header: "Difference", align: "right", sort: (r) => r.t.diff, cell: (r) => <span className="num text-ink">{signedPts(r.t.diff)}</span> },
    {
      key: "bar",
      header: "95% interval",
      cell: (r) => (
        <span className="flex items-center gap-3">
          <DiffBar diff={r.t.diff} low={r.t.ci_low} high={r.t.ci_high} span={span} width={150} className="hidden sm:inline-block" />
          <span className="num text-[12px] text-ink-3">
            {signedPts(r.t.ci_low)} to {signedPts(r.t.ci_high)}
          </span>
        </span>
      ),
    },
    { key: "p", header: "p", align: "right", sort: (r) => r.t.p, cell: (r) => <span className="num text-[12.5px] text-ink-2">{pValue(r.t.p).replace("p = ", "").replace("p ", "")}</span> },
    { key: "n", header: "Clips", align: "right", cell: (r) => <span className="num text-[12.5px] text-ink-3">{r.t.n}</span>, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" },
  ];
  return (
    <Figure
      n={n}
      title="Paired results against every other model"
      caption={`${pairSummary(label, beats, loses, rows.length - beats - loses)} Difference is ${label} minus the other model on the same clips; negative means fewer errors.`}
      source={`source: paired, tier=${tier}, metric=${metric}, slice=all, paired bootstrap 1000 resamples`}
      bodyClassName="p-0 sm:p-0"
    >
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.other} caption={`Paired results for ${label}`} />
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 text-[11.5px] text-ink-3 sm:px-5">
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-good" /> Interval below zero: fewer errors
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-signal" /> Above zero: more errors
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-full border-[1.5px] border-ink-3" /> Crosses zero: no detectable difference
        </span>
      </div>
    </Figure>
  );
}

function pairSummary(label: string, beats: number, loses: number, ties: number) {
  const n = (k: number) => `${k} ${k === 1 ? "model" : "models"}`;
  if (!beats && !loses) return `No detectable difference between ${label} and ${ties === 1 ? "the other model" : `any of the ${n(ties)}`} yet.`;
  const parts = [beats && `fewer errors than ${n(beats)}`, loses && `more errors than ${n(loses)}`].filter(Boolean).join(", ");
  return `${label} makes ${parts}${ties ? `, with no detectable difference against ${n(ties)}` : ""}.`;
}

// ---------------------------------------------------------------- what breaks it

function Breaks({ n, run, id, tier }: { n: number; run: Run; id: string; tier: Tier }) {
  const b = breaksOn(run, id, tier);
  const e = run.errors.providers[id];
  const allSubs = e?.top_substitutions ?? [];
  const subs = allSubs.slice(0, 8);
  const worst = (e?.worst_clips ?? []).slice(0, 6);
  const max = Math.max(0.01, ...worst.map((w) => w.error_rate));
  const color = providerColor(run, id);
  return (
    <Figure n={n} title="What breaks it" caption="The clip type that raises its error rate most, the slips it makes most often, and the clips where it does worst against the others." source="source: errors (top_substitutions, worst_clips), aggregates; snippets are a few characters at most" bodyClassName="p-0 sm:p-0">
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
        <div className="border-b border-line p-4 sm:p-5 lg:border-r lg:border-b-0">
          {b ? (
            <>
              <p className="text-[13px] text-ink-3">Breaks most on</p>
              <p className="mt-1 text-[22px] font-medium tracking-[-0.02em] text-ink">{TAG_LABEL[b.tag]}</p>
              <p className="mt-2 text-[13.5px] text-ink-2">
                <span className="num text-ink">{pct(b.agg.error_rate)}</span> against <span className="num">{pct(b.base.error_rate)}</span> on all clips: errors rise <span className="num text-ink">{b.ratio.toFixed(1)}x</span> ({b.agg.n_clips} clips).
              </p>
            </>
          ) : (
            <p className="text-[13.5px] text-ink-3">No clip type has enough clips to say.</p>
          )}
          <div className="label mt-6 mb-2.5">Most common slips</div>
          {subs.length ? (
            <ul className="flex flex-wrap gap-1.5">
              {subs.map(([ref, hyp, k]) => (
                <li key={ref + hyp} lang="ja" className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line bg-sunken/60 px-2.5 text-[13px]">
                  <span className="text-ink-2">{ref}</span>
                  <ArrowRight size={11} className="text-ink-3" aria-label="heard as" />
                  <span className="text-signal-ink">{hyp || <span className="font-mono text-[11px] text-ink-3">(dropped)</span>}</span>
                  <span className="num ml-0.5 text-[11px] text-ink-3">x{k}</span>
                </li>
              ))}
              {allSubs.length > subs.length && <li className="inline-flex h-8 items-center px-1 text-[12px] text-ink-3">and {allSubs.length - subs.length} more in the run JSON</li>}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-3">No substitution data in this run.</p>
          )}
        </div>
        <div className="p-4 sm:p-5">
          <div className="mb-2 grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_52px] gap-3">
            <span className="label">Worst clips</span>
            <span className="label">This model vs others' median</span>
            <span />
          </div>
          {worst.length ? (
            <ul>
              {worst.map((w) => {
                const clip = clipById(run, w.video_id);
                return (
                  <li key={w.video_id} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)_52px] items-center gap-3 border-t border-line py-2.5">
                    <a
                      href={youtubeUrl(clip ?? { video_id: w.video_id })}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex min-w-0 items-center gap-1 font-mono text-[12.5px] text-ink hover:underline"
                      aria-label={`Open clip ${w.video_id} on YouTube`}
                    >
                      <span className="truncate">{w.video_id}</span>
                      <ArrowUpRight size={12} className="shrink-0 text-ink-3" />
                    </a>
                    <span className="grid gap-1" role="img" aria-label={`${pct(w.error_rate)} against a median of ${pct(w.others_median)} for the other models`}>
                      <span className="block h-1.5 rounded-r-[3px]" style={{ width: `${(w.error_rate / max) * 100}%`, background: color }} />
                      <span className="block h-1.5 rounded-r-[3px] bg-line-strong" style={{ width: `${(w.others_median / max) * 100}%` }} />
                    </span>
                    <span className="num text-right text-[12.5px] text-ink">{pct(w.error_rate, 0)}</span>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="text-[13px] text-ink-3">No per-clip data in this run.</p>
          )}
        </div>
      </div>
    </Figure>
  );
}

// ---------------------------------------------------------------- caption quality

function CaptionQuality({ n, id, rows, tier, metric }: { n: number; id: string; rows: Aggregate[]; tier: Tier; metric: string }) {
  const me = rows.find((r) => r.provider === id);
  return (
    <Figure
      n={n}
      title="Caption quality"
      caption="What a caption product ships besides the text. Each row ranks this model among those that can be scored on it; n/a means it cannot (for example, no word timings returned)."
      source={`source: aggregates, tier=${tier}, metric=${metric}, slice=all`}
      bodyClassName="p-0 sm:p-0"
    >
      <dl>
        {CAPTION_METRICS.map((m) => {
          const v = metricValue(me, m);
          const scored = rows.filter((r) => metricValue(r, m) != null).sort((a, b) => (m.better === "higher" ? metricValue(b, m)! - metricValue(a, m)! : metricValue(a, m)! - metricValue(b, m)!));
          const rank = scored.findIndex((r) => r.provider === id) + 1;
          const nn = metricN(me, m);
          return (
            <div key={m.key} className="grid grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_auto] items-baseline gap-4 border-b border-line px-4 py-3 last:border-b-0 sm:px-5">
              <dt>
                <span className="block text-[13.5px] text-ink">{m.label}</span>
                <span className="block text-[12px] text-ink-3">{m.help}</span>
              </dt>
              <dd className="num text-right text-[14px] text-ink">
                {v == null ? <span className="text-[12.5px] text-ink-3">n/a</span> : m.fmt(v)}
                {v != null && nn != null && <span className="block text-[10.5px] text-ink-3">n={int(nn)}</span>}
              </dd>
              <dd className="num w-14 text-right text-[12.5px] text-ink-3">{v == null || !rank ? "" : `#${rank}/${scored.length}`}</dd>
            </div>
          );
        })}
      </dl>
    </Figure>
  );
}

// ---------------------------------------------------------------- spec sheet

function Spec({ n, title, rows, source, footer }: { n: number; title: string; rows: [string, string][]; source: string; footer?: ReactNode }) {
  return (
    <Figure n={n} title={title} source={source} bodyClassName="p-0 sm:p-0" className="self-start">
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
            <dt className="text-[13px] text-ink-3">{k}</dt>
            <dd className="text-[13.5px] text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {footer && <div className="border-t border-line px-4 py-3 sm:px-5">{footer}</div>}
    </Figure>
  );
}
