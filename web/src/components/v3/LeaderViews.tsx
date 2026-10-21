import { useState, type ReactNode } from "react";
import type { Run, SliceKind, Tier } from "@/lib/types";
import { LANGUAGE_NAME, isEligible, overall, providerColor, providerLabel, rankedRun } from "@/lib/analysis";
import { NotRanked } from "@/components/ui/Gate";
import { ms, pct, signedPts, usdPerHour, int } from "@/lib/format";
import { maxOf } from "@/lib/scale";
import { cn } from "@/lib/cn";
import {
  ITN_KINDS,
  STYLE_PROPS,
  fallsApartAt,
  pricePer1000Min,
  speedFactor,
  sweepCurve,
  v3,
  type CaptionsRow,
  type DiarizationRow,
  type EntityRow,
  type FormattingRow,
  type LidRow,
  type LongformRow,
  type StyleRow,
} from "@/lib/v3";
import { Figure } from "@/components/ui/Card";
import { DataTable, type Column } from "@/components/ui/Table";
import { Segmented } from "@/components/ui/Segmented";
import { CIBar } from "@/components/ui/CIBar";
import { Badge } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/State";
import { LineChart, type LineSeries } from "@/components/charts/LineChart";
import { FellBack, Help, ModelCell, ShareBar, ValueCell, bestBy, sectionSlice } from "./shared";
import type { LeaderView } from "./views";

export interface ViewProps {
  run: Run;
  tier: Tier;
  metric: string;
  kind: SliceKind;
  slice: string;
  n: number;
}

/** One leaderboard view (everything but Accuracy). Lazy-loaded with its charts. */
export default function LeaderViewPanel({ view, run, ...rest }: ViewProps & { view: LeaderView }) {
  // Excluded providers (below the coverage minimum) are dropped from every view and listed as not ranked.
  return (
    <>
      <Panel view={view} run={rankedRun(run)} {...rest} />
      <NotRanked run={run} />
    </>
  );
}

function Panel({ view, ...p }: ViewProps & { view: LeaderView }) {
  switch (view) {
    case "formatting":
      return <FormattingView {...p} />;
    case "captions":
      return <CaptionFilesView {...p} />;
    case "speakers":
      return <SpeakersView {...p} />;
    case "lid":
      return <LidView {...p} />;
    case "entities":
      return <EntitiesView {...p} />;
    case "style":
      return <StyleView {...p} />;
    case "sweeps":
      return <SweepView {...p} />;
    case "longform":
      return <LongformView {...p} />;
    case "speed":
      return <SpeedView {...p} />;
    default:
      return null;
  }
}

const src = (run: Run, key: string, tier: Tier, kind: string, slice: string) => `source: ${key}, run=${run.run_id}, tier=${tier}, slice_kind=${kind}, slice=${slice}`;
const factor = (v: number) => `${v >= 10 ? v.toFixed(0) : v.toFixed(1)}x`;
const db = (v: number) => `${v > 0 ? "+" : ""}${v} dB`;

function Empty() {
  return <EmptyState title="Not measured in this run." body="This view fills in once the run carries these results." />;
}

// ---------------------------------------------------------------- A6 formatting

function FormattingView({ run, tier, kind, slice, n }: ViewProps) {
  const all = v3(run).formatting ?? [];
  const s = sectionSlice(all, tier, kind, slice);
  const rows = s.rows;
  const kinds = ITN_KINDS.filter((k) => rows.some((r) => r.itn_by_kind[k]?.acc != null));
  const best = {
    per: bestBy(rows, (r) => r.punct_error_rate, "lower"),
    cas: bestBy(rows, (r) => r.casing_acc, "higher"),
    itn: bestBy(rows, (r) => r.itn_acc, "higher"),
  };
  const columns: Column<FormattingRow>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    { key: "per", header: <Help label="Punctuation errors" help="Punctuation error rate: missing, extra or wrong marks per reference mark, on clips whose verified text is punctuated. Lower is better." />, align: "right", sort: (r) => r.punct_error_rate, cell: (r) => <ValueCell v={r.punct_error_rate} fmt={(x) => pct(x)} lo={r.punct_ci_low} hi={r.punct_ci_high} best={best.per === r.provider} /> },
    { key: "cas", header: <Help label="Casing" help="Share of Latin-letter words with the right capitalisation. Higher is better." />, align: "right", sort: (r) => r.casing_acc, cell: (r) => <ValueCell v={r.casing_acc} fmt={(x) => pct(x)} best={best.cas === r.provider} /> },
    { key: "itn", header: <Help label="Formatting" help="Inverse text normalisation: numbers, dates, times, currency, percentages and units written the way a caption should show them. Higher is better." />, align: "right", sort: (r) => r.itn_acc, cell: (r) => <ValueCell v={r.itn_acc} fmt={(x) => pct(x)} lo={r.itn_ci_low} hi={r.itn_ci_high} best={best.itn === r.provider} /> },
    ...kinds.map<Column<FormattingRow>>((k) => ({
      key: `itn_${k}`,
      header: k[0]!.toUpperCase() + k.slice(1),
      align: "right",
      sort: (r) => r.itn_by_kind[k]?.acc ?? null,
      cell: (r) => <ValueCell v={r.itn_by_kind[k]?.acc ?? null} fmt={(x) => pct(x, 0)} n={r.itn_by_kind[k]?.n} best={bestBy(rows, (q) => q.itn_by_kind[k]?.acc ?? null, "higher") === r.provider} />,
      headerClassName: "hidden lg:table-cell",
      className: "hidden lg:table-cell",
    })),
    { key: "n", header: "Spans", align: "right", sort: (r) => r.itn_n_spans, cell: (r) => <span className="num text-ink-3">{r.itn_n_spans == null ? "n/a" : int(r.itn_n_spans)}</span>, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
  ];
  return (
    <Figure n={n} title="Punctuation, casing and formatting" caption="What a caption shows besides the words: marks in the right places, capitals, and numbers, dates and money written the way a reader expects. Best value per column in bold." source={src(run, "formatting", tier, s.kind, s.slice)} bodyClassName="p-0 sm:p-0">
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {rows.length ? <DataTable columns={columns} rows={[...rows].sort((a, b) => (a.punct_error_rate ?? 9) - (b.punct_error_rate ?? 9))} rowKey={(r) => r.provider} caption="Formatting" /> : <div className="p-4 sm:p-5"><Empty /></div>}
    </Figure>
  );
}

// ---------------------------------------------------------------- B21 caption files

function CaptionFilesView({ run, tier, kind, slice, n }: ViewProps) {
  const cap = v3(run).captions;
  const s = sectionSlice(cap?.rows ?? [], tier, kind, slice);
  const rows = s.rows;
  const rule = cap?.rule;
  const ruleText = rule
    ? [
        rule.max_chars_per_line != null && `at most ${int(rule.max_chars_per_line)} characters a line`,
        rule.max_lines != null && `${int(rule.max_lines)} lines a cue`,
        rule.max_cps != null && `${int(rule.max_cps)} characters a second`,
        rule.pause_ms != null && `a new cue after a ${ms(rule.pause_ms)} pause`,
        rule.max_duration_ms != null && `no cue longer than ${ms(rule.max_duration_ms)}`,
      ]
        .filter(Boolean)
        .join(", ")
    : null;
  const has = (key: keyof CaptionsRow) => rows.some((r) => r[key] != null);
  const col = (key: keyof CaptionsRow, label: string, help: string, fmt: (x: number) => string, better: "lower" | "higher" | null, hide?: string): Column<CaptionsRow>[] =>
    has(key)
      ? [
          {
            key,
            header: <Help label={label} help={help} />,
            align: "right",
            sort: (r) => r[key] as number | null,
            cell: (r) => <ValueCell v={r[key] as number | null} fmt={fmt} best={better ? bestBy(rows, (q) => q[key] as number | null, better) === r.provider : false} lo={key === "overlap" ? r.overlap_ci_low : undefined} hi={key === "overlap" ? r.overlap_ci_high : undefined} />,
            headerClassName: hide,
            className: hide,
          },
        ]
      : [];
  const columns: Column<CaptionsRow>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    ...col("overlap", "Time overlap", "How much of the time the provider's cues and the reference cues are on screen together (intersection over union). Higher is better.", (x) => pct(x), "higher"),
    ...col("readable", "Readable cues", "Share of cues that meet every rule of the line-break rule. Higher is better.", (x) => pct(x), "higher"),
    ...col("cue_within_200ms", "Cues on time", "Share of cues that appear within 200 ms of the speech they show. Higher is better.", (x) => pct(x), "higher"),
    ...col("cue_start_median_ms", "Start error", "Median distance between when a cue appears and when the matching reference cue appears.", (x) => ms(x), "lower"),
    ...col("cue_end_median_ms", "End error", "Median distance between when a cue leaves the screen and when the reference cue does.", (x) => ms(x), "lower", "hidden md:table-cell"),
    ...col("cps_median", "Reading speed", "Median characters per second a viewer has to read.", (x) => `${x.toFixed(1)} cps`, null, "hidden md:table-cell"),
    ...col("cps_over", "Too fast", "Share of cues faster than the reading-speed limit. Lower is better.", (x) => pct(x), "lower", "hidden md:table-cell"),
    ...col("line_over", "Lines too long", "Share of lines over the character limit. Lower is better.", (x) => pct(x), "lower", "hidden lg:table-cell"),
    ...col("cue_count_ratio", "Cues per reference cue", "How many cues the provider's text makes for each reference cue. Near 1 means the breaks land in the same places.", (x) => x.toFixed(2), null, "hidden lg:table-cell"),
    { key: "n", header: "Clips", align: "right", sort: (r) => r.n_clips, cell: (r) => <span className="num text-ink-3">{r.n_clips == null ? "n/a" : int(r.n_clips)}</span>, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
  ];
  const sortKey = has("overlap") ? "overlap" : "readable";
  return (
    <Figure
      n={n}
      title={`Caption files${cap?.formats.length ? `, ${cap.formats.map((f) => f.toUpperCase()).join(" and ")}` : ", SRT and VTT"}`}
      caption={<>Each model's timed words turned into caption cues with one fixed line-break rule{ruleText ? `: ${ruleText}` : ""}, the same rule applied to the reference, then compared as a viewer sees them. Best value per column in bold.</>}
      source={src(run, "captions", tier, s.kind, s.slice)}
      bodyClassName="p-0 sm:p-0"
    >
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {rows.length ? <DataTable columns={columns} rows={[...rows].sort((a, b) => ((b[sortKey] as number | null) ?? -1) - ((a[sortKey] as number | null) ?? -1))} rowKey={(r) => r.provider} caption="Caption files" /> : <div className="p-4 sm:p-5"><Empty /></div>}
    </Figure>
  );
}

// ---------------------------------------------------------------- B15 speakers

function NotOffered({ run, ids, what }: { run: Run; ids: string[]; what: string }) {
  if (!ids.length) return null;
  return (
    <p className="border-t border-line px-4 py-3 text-[12.5px] text-ink-3 sm:px-5">
      No speaker labels returned, so not scored: {ids.map((id) => providerLabel(run, id)).join(", ")}. {what}
    </p>
  );
}

function SpeakersView({ run, tier, kind, slice, n }: ViewProps) {
  const d = v3(run).diarization;
  const s = sectionSlice(d?.rows ?? [], tier, kind, slice);
  const rows = [...s.rows].sort((a, b) => (a.der ?? 9) - (b.der ?? 9));
  const max = maxOf(rows.map((r) => r.der_ci_high ?? r.der ?? 0), 0.1) * 1.08;
  const columns: Column<DiarizationRow>[] = [
    { key: "rank", header: "#", cell: (_, i) => <span className="num text-ink-3">{String(i + 1).padStart(2, "0")}</span>, className: "w-10" },
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    {
      key: "der",
      header: <Help label="Diarization error" help="Diarization error rate: speech missed, speech invented and words given to the wrong speaker, as a share of reference speech. Lower is better." />,
      sort: (r) => r.der,
      cell: (r) =>
        r.der == null ? (
          <span className="text-ink-3">n/a</span>
        ) : (
          <span className="flex items-center gap-3">
            <span className="num w-12 text-right text-ink">{pct(r.der)}</span>
            {r.der_ci_low != null && r.der_ci_high != null && <CIBar value={r.der} low={r.der_ci_low} high={r.der_ci_high} max={max} color={providerColor(run, r.provider)} className="hidden sm:inline-block" />}
          </span>
        ),
    },
    { key: "count", header: <Help label="Speaker count right" help="Share of clips where the model found the right number of speakers. Higher is better." />, align: "right", sort: (r) => r.speaker_count_acc, cell: (r) => <ValueCell v={r.speaker_count_acc} fmt={(x) => pct(x)} lo={r.count_ci_low} hi={r.count_ci_high} best={bestBy(rows, (q) => q.speaker_count_acc, "higher") === r.provider} /> },
    {
      key: "split",
      header: "Missed / invented / confused",
      align: "right",
      cell: (r) => (r.missed == null && r.false_alarm == null && r.confusion == null ? <span className="text-ink-3">n/a</span> : <span className="num text-[12.5px] text-ink-2">{[r.missed, r.false_alarm, r.confusion].map((x) => (x == null ? "n/a" : pct(x))).join(" / ")}</span>),
      headerClassName: "hidden lg:table-cell",
      className: "hidden lg:table-cell",
    },
    { key: "n", header: "Clips", align: "right", sort: (r) => r.n_clips, cell: (r) => <span className="num text-ink-3">{r.too_small ? <Badge tone="warn">n={r.n_clips}</Badge> : (r.n_clips ?? "n/a")}</span>, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
  ];
  return (
    <Figure n={n} title="Who spoke when" caption="Speaker labels on clips with more than one speaker, against turns a person tagged by ear. Lower diarization error is better." source={src(run, "diarization", tier, s.kind, s.slice)} bodyClassName="p-0 sm:p-0">
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {rows.length ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Diarization" /> : <div className="p-4 sm:p-5"><Empty /></div>}
      <NotOffered run={run} ids={(d?.unsupported.length ? d.unsupported : run.providers.map((p) => p.id).filter((id) => !rows.some((r) => r.provider === id))).filter((id) => isEligible(run, id))} what="The run asked every provider for speaker labels." />
    </Figure>
  );
}

// ---------------------------------------------------------------- B16 language ID

function LidView({ run, tier, kind, slice, n }: ViewProps) {
  const l = v3(run).lid;
  const s = sectionSlice(l?.rows ?? [], tier, kind, slice);
  const rows = [...s.rows].sort((a, b) => (b.accuracy ?? -1) - (a.accuracy ?? -1));
  const columns: Column<LidRow>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    { key: "acc", header: <Help label="Right language" help="Share of clips where auto-detect picked the right language. Higher is better." />, align: "right", sort: (r) => r.accuracy, cell: (r) => <ValueCell v={r.accuracy} fmt={(x) => pct(x)} lo={r.ci_low} hi={r.ci_high} best={bestBy(rows, (q) => q.accuracy, "higher") === r.provider} /> },
    { key: "hint", header: <Help label="Error, with hint" help="Error rate when the run tells the model the language." />, align: "right", sort: (r) => r.error_rate_hint, cell: (r) => <ValueCell v={r.error_rate_hint} fmt={(x) => pct(x)} /> },
    { key: "auto", header: <Help label="Error, auto-detect" help="Error rate when the model has to work the language out itself." />, align: "right", sort: (r) => r.error_rate_auto, cell: (r) => <ValueCell v={r.error_rate_auto} fmt={(x) => pct(x)} lo={r.auto_ci_low} hi={r.auto_ci_high} best={bestBy(rows, (q) => q.error_rate_auto, "lower") === r.provider} /> },
    {
      key: "cost",
      header: <Help label="Cost of no hint" help="Error rate with auto-detect minus error rate with the hint." />,
      align: "right",
      sort: (r) => r.diff ?? (r.error_rate_auto != null && r.error_rate_hint != null ? r.error_rate_auto - r.error_rate_hint : null),
      cell: (r) => {
        const d = r.diff ?? (r.error_rate_auto != null && r.error_rate_hint != null ? r.error_rate_auto - r.error_rate_hint : null);
        if (d == null) return <span className="text-ink-3">n/a</span>;
        if (r.verdict === "no_difference") return <span className="text-[12.5px] text-ink-2">No detectable difference</span>;
        return (
          <span className="inline-flex flex-col items-end leading-tight">
            <span className="num text-[12.5px] text-ink-2">{signedPts(d)}</span>
            {r.diff_ci_low != null && r.diff_ci_high != null && <span className="num text-[10.5px] text-ink-3">{signedPts(r.diff_ci_low)} to {signedPts(r.diff_ci_high)}</span>}
          </span>
        );
      },
      headerClassName: "hidden md:table-cell",
      className: "hidden md:table-cell",
    },
    ...(rows.some((r) => r.top_confusion)
      ? [{ key: "conf", header: "Often picks", align: "right" as const, cell: (r: LidRow) => <span className="text-[12.5px] text-ink-2">{r.top_confusion ? (LANGUAGE_NAME[r.top_confusion] ?? r.top_confusion) : "n/a"}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" }]
      : []),
    ...(rows.some((r) => r.wrong_language_error_rate != null)
      ? [{ key: "wrong", header: <Help label="Error when wrong" help="Error rate on the clips where it picked the wrong language." />, align: "right" as const, sort: (r: LidRow) => r.wrong_language_error_rate, cell: (r: LidRow) => <ValueCell v={r.wrong_language_error_rate} fmt={(x) => pct(x)} />, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" }]
      : []),
  ];
  return (
    <Figure n={n} title="Language identification" caption={`With language auto-detect on and no hint: how often each model recognises ${LANGUAGE_NAME[run.language] ?? run.language}, and how much accuracy that costs.`} source={src(run, "lid", tier, s.kind, s.slice)} bodyClassName="p-0 sm:p-0">
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {rows.length ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Language identification" /> : <div className="p-4 sm:p-5"><Empty /></div>}
      <NotOffered run={run} ids={(l?.unsupported ?? []).filter((id) => isEligible(run, id))} what="" />
    </Figure>
  );
}

// ---------------------------------------------------------------- B17 entities

function EntitiesView({ run, tier, kind, slice, n }: ViewProps) {
  const e = v3(run).entities;
  const s = sectionSlice(e?.rows ?? [], tier, kind, slice);
  const rows = [...s.rows].sort((a, b) => (b.keyterms?.acc ?? b.plain.acc ?? -1) - (a.keyterms?.acc ?? a.plain.acc ?? -1));
  const columns: Column<EntityRow>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    {
      key: "dumbbell",
      header: "Without and with the term list",
      cell: (r) => <Dumbbell a={r.plain.acc} b={r.keyterms?.acc ?? null} color={providerColor(run, r.provider)} />,
      headerClassName: "hidden md:table-cell",
      className: "hidden md:table-cell",
    },
    { key: "plain", header: <Help label="Without" help="Names and brands written correctly with no custom vocabulary. Higher is better." />, align: "right", sort: (r) => r.plain.acc, cell: (r) => <ValueCell v={r.plain.acc} fmt={(x) => pct(x)} lo={r.plain.ci_low} hi={r.plain.ci_high} best={bestBy(rows, (q) => q.plain.acc, "higher") === r.provider} /> },
    {
      key: "kt",
      header: <Help label="With keyterms" help="The same, with the provider's custom vocabulary or keyterm feature fed the same term list." />,
      align: "right",
      sort: (r) => r.keyterms?.acc ?? null,
      cell: (r) => (r.keyterms ? <ValueCell v={r.keyterms.acc} fmt={(x) => pct(x)} lo={r.keyterms.ci_low} hi={r.keyterms.ci_high} best={bestBy(rows, (q) => q.keyterms?.acc ?? null, "higher") === r.provider} /> : <span className="text-[12.5px] text-ink-3">{r.keyterms_supported === false ? "Not offered" : "n/a"}</span>),
    },
    {
      key: "gain",
      header: "Gain",
      align: "right",
      sort: (r) => (r.keyterms?.acc != null && r.plain.acc != null ? r.keyterms.acc - r.plain.acc : null),
      cell: (r) =>
        r.paired?.verdict === "no_difference" ? (
          <span className="text-[12.5px] text-ink-2">No detectable difference</span>
        ) : r.keyterms?.acc != null && r.plain.acc != null ? (
          <span className={cn("num text-[12.5px]", r.keyterms.acc > r.plain.acc && r.paired?.verdict !== "b_better" ? "text-good" : "text-ink-2")}>{signedPts(r.keyterms.acc - r.plain.acc)}</span>
        ) : (
          <span className="text-ink-3">n/a</span>
        ),
    },
    { key: "n", header: "Spans", align: "right", cell: (r) => <span className="num text-ink-3">{r.plain.n_spans == null ? "n/a" : int(r.plain.n_spans)}</span>, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
  ];
  return (
    <Figure
      n={n}
      title="Names and brands"
      caption={<>Proper nouns and brand names tagged in the references{e?.n_entities != null ? ` (${int(e.n_entities)} in all)` : ""}, written exactly right. Each provider gets the same term list{e?.n_terms != null ? ` (${int(e.n_terms)} terms)` : ""} through its own custom vocabulary feature, where it has one. Gain is paired on the same names; a provider that ignores the list shows no difference.</>}
      source={src(run, "entities", tier, s.kind, s.slice)}
      bodyClassName="p-0 sm:p-0"
    >
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {rows.length ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Entity accuracy" /> : <div className="p-4 sm:p-5"><Empty /></div>}
    </Figure>
  );
}

function Dumbbell({ a, b, color }: { a: number | null; b: number | null; color: string }) {
  const W = 150;
  const x = (v: number) => `${Math.min(100, Math.max(0, v * 100))}%`;
  return (
    <span aria-hidden className="relative inline-block h-3 align-middle" style={{ width: W }}>
      <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line-strong" />
      {a != null && b != null && <span className="absolute top-1/2 h-[2px] -translate-y-1/2" style={{ left: x(Math.min(a, b)), width: `calc(${x(Math.max(a, b))} - ${x(Math.min(a, b))})`, background: color, opacity: 0.6 }} />}
      {a != null && <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 bg-surface" style={{ left: x(a), borderColor: color }} />}
      {b != null && <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface" style={{ left: x(b), background: color }} />}
    </span>
  );
}

// ---------------------------------------------------------------- B18 style

function StyleView({ run, tier, kind, slice, n }: ViewProps) {
  const s = sectionSlice(v3(run).style ?? [], tier, kind, slice);
  const rows = [...s.rows].sort((a, b) => providerLabel(run, a.provider).localeCompare(providerLabel(run, b.provider)));
  const props = STYLE_PROPS.filter((p) => rows.some((r) => r[p.key] != null));
  return (
    <Figure
      n={n}
      title="Style: what each model keeps"
      caption="Not errors. Caption apps choose differently: some want every um and stutter, others want clean text. Each track runs from never to always; pick the behaviour your product wants."
      source={src(run, "style", tier, s.kind, s.slice)}
      bodyClassName="p-0 sm:p-0"
    >
      {s.fellBack && <FellBack kind={kind} slice={slice} />}
      {!rows.length ? (
        <div className="p-4 sm:p-5">
          <Empty />
        </div>
      ) : (
        <div className="relative overflow-x-auto">
          <table className="w-full min-w-[640px] border-separate border-spacing-0 text-[13px]">
            <caption className="sr-only">Style properties per model, share of items kept</caption>
            <thead>
              <tr>
                <th scope="col" className="sticky left-0 z-10 border-b border-line bg-surface px-4 py-2.5 text-left font-normal sm:px-5">
                  <span className="label">Model</span>
                </th>
                {props.map((p) => (
                  <th key={p.key} scope="col" className="border-b border-line bg-surface px-3 py-2.5 text-left font-normal last:pr-4 sm:last:pr-5">
                    <span className="label">
                      <Help label={p.label} help={p.help} />
                    </span>
                    <span className="mt-1 flex justify-between gap-2 font-mono text-[10px] text-ink-3">
                      <span>{p.low}</span>
                      <span>{p.high}</span>
                    </span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.provider} className="group">
                  <th scope="row" className="sticky left-0 z-[1] border-b border-line bg-surface px-4 py-3 text-left font-normal group-last:border-b-0 sm:px-5">
                    <ModelCell run={run} id={r.provider} />
                  </th>
                  {props.map((p) => (
                    <td key={p.key} className="border-b border-line px-3 py-3 group-last:border-b-0 last:pr-4 sm:last:pr-5">
                      <StyleTrack v={r[p.key]} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Figure>
  );
}

function StyleTrack({ v }: { v: StyleRow["fillers_kept"] }) {
  if (v == null) return <span className="text-[12.5px] text-ink-3">n/a</span>;
  return (
    <span className="flex items-center gap-2.5">
      <span className="relative inline-block h-3 w-[110px]" role="img" aria-label={`${pct(v, 0)} kept`}>
        <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line-strong" />
        <span className="absolute top-1/2 left-0 h-1.5 w-px -translate-y-1/2 bg-line-strong" />
        <span className="absolute top-1/2 right-0 h-1.5 w-px -translate-y-1/2 bg-line-strong" />
        <span className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full bg-ink ring-2 ring-surface" style={{ left: `${v * 100}%` }} />
      </span>
      <span className="num w-9 text-right text-[12.5px] text-ink-2">{pct(v, 0)}</span>
    </span>
  );
}

// ---------------------------------------------------------------- A5 sweeps

/**
 * A bootstrap interval on an error rate near the cap can run past 100% when the point itself does not. Show it
 * capped at 100% (or at the value, when a rate really is above 100% from insertions).
 */
function capHi(hi: number | null | undefined, v: number | null | undefined) {
  if (hi == null) return hi;
  return Math.min(hi, Math.max(1, v ?? 0));
}

function SweepView({ run, tier, n }: ViewProps) {
  const sw = v3(run).sweeps!;
  const snrBeds = sw.beds.filter((b) => sw.points.some((p) => p.bed === b && p.snr_db != null));
  const [bed, setBed] = useState(snrBeds.includes("music") ? "music" : (snrBeds[0] ?? "music"));
  const ids = [...new Set(sw.points.map((p) => p.provider))].sort((a, b) => providerLabel(run, a).localeCompare(providerLabel(run, b)));
  const snrs = [...new Set(sw.points.filter((p) => p.bed === bed && p.snr_db != null).map((p) => p.snr_db!))].sort((a, b) => b - a);
  const top = snrs[0] ?? 20;
  const step = snrs.length > 1 ? Math.abs(snrs[0]! - snrs[1]!) : 10;
  const cleanX = top + step;
  const series: LineSeries[] = ids.map((id) => {
    const { clean, pts } = sweepCurve(sw, id, bed, tier);
    const all = [...(clean?.error_rate != null ? [{ x: cleanX, y: clean.error_rate, lo: clean.ci_low, hi: capHi(clean.ci_high, clean.error_rate) }] : []), ...pts.map((p) => ({ x: p.snr_db!, y: p.error_rate!, lo: p.ci_low, hi: capHi(p.ci_high, p.error_rate) }))];
    return { id, label: providerLabel(run, id), color: providerColor(run, id), points: all };
  });
  const falls = ids.map((id) => ({ id, at: fallsApartAt(sw, id, bed, tier), censored: sw.falls_apart.find((f) => f.provider === id && f.bed === bed)?.censored ?? null, ...sweepCurve(sw, id, bed, tier) }));
  const doubling = sw.falls_apart.some((f) => f.rule === "doubling");
  const lowest = snrs[snrs.length - 1] ?? 0;
  const reverb = sw.points.filter((p) => p.bed === "reverb" && p.error_rate != null);
  const levels = [...new Set(reverb.map((p) => p.level ?? ""))];
  return (
    <div className="grid grid-cols-1 gap-8">
      <Figure
        n={n}
        title={`Error rate as the ${bed} gets louder`}
        caption={`Verified clean-speech clips mixed with ${bed} at fixed speech-to-${bed} ratios, louder ${bed} to the right. Bands are 95% intervals. A ring marks where a model falls apart: ${doubling ? "where its error rate reaches double its clean-speech rate (interpolated between the levels tested)" : "the first level whose interval sits wholly above its clean-speech interval"}.`}
        controls={snrBeds.length > 1 ? <Segmented label="Background" value={bed} onChange={setBed} options={snrBeds.map((b) => ({ value: b, label: b[0]!.toUpperCase() + b.slice(1) }))} /> : undefined}
        source={`source: sweeps${sw.sweep_id ? ` ${sw.sweep_id}` : ""}, bed=${bed}${sw.licences.length ? `; beds: ${sw.licences.join(", ")}` : ""}; mix recipes are public, mixed audio is not`}
      >
        {snrs.length === 0 ? (
          <Empty />
        ) : (
          <LineChart
            series={series}
            xDomain={[cleanX, snrs[snrs.length - 1]!]}
            xTicks={[{ value: cleanX, label: "clean" }, ...snrs.map((v) => ({ value: v, label: db(v) }))]}
            xFormat={(v) => (v === cleanX ? "clean" : db(v))}
            yFormat={(v) => pct(v, v < 0.1 ? 1 : 0)}
            yLabel="Error rate"
            markers={falls.filter((f) => f.at != null && !f.censored).map((f) => ({ series: f.id, x: f.at!, label: "" }))}
            label={`Error rate against speech-to-${bed} ratio for ${ids.length} models`}
            height={300}
          />
        )}
      </Figure>
      <Figure n={n + 1} title="Where each model falls apart" caption={doubling ? "The ratio at which the error rate doubles from clean speech, and the error rate at the loudest level tested." : "The ratio at which errors first rise clear of clean speech, and the error rate at the loudest level tested."} source={`source: sweeps, bed=${bed}`} bodyClassName="p-0 sm:p-0">
        <DataTable
          rows={[...falls].sort((a, b) => (a.censored === "below" ? -99 : a.censored === "above" ? 99 : (a.at ?? -99)) - (b.censored === "below" ? -99 : b.censored === "above" ? 99 : (b.at ?? -99)))}
          rowKey={(r) => r.id}
          caption="Falls apart at"
          columns={[
            { key: "model", header: "Model", sticky: true, cell: (r) => <ModelCell run={run} id={r.id} /> },
            {
              key: "at",
              header: doubling ? "Errors double at" : "Falls apart at",
              align: "right",
              cell: (r) =>
                r.censored === "above" ? (
                  <span className="text-[12.5px] text-signal-ink">Already at {db(snrs[0] ?? 0)}</span>
                ) : r.censored === "below" || r.at == null ? (
                  <span className="text-[12.5px] text-ink-2">Holds to {db(lowest)}</span>
                ) : (
                  <span className="num text-signal-ink">{db(Math.round(r.at * 10) / 10)}</span>
                ),
            },
            { key: "clean", header: "Clean", align: "right", cell: (r) => <ValueCell v={r.clean?.error_rate ?? null} fmt={(x) => pct(x)} /> },
            { key: "worst", header: `At ${db(snrs[snrs.length - 1] ?? 0)}`, align: "right", cell: (r) => <ValueCell v={r.pts[r.pts.length - 1]?.error_rate ?? null} fmt={(x) => pct(x)} lo={r.pts[r.pts.length - 1]?.ci_low} hi={capHi(r.pts[r.pts.length - 1]?.ci_high, r.pts[r.pts.length - 1]?.error_rate)} /> },
          ]}
        />
      </Figure>
      {reverb.length > 0 && (
        <Figure n={n + 2} title="Reverb" caption="The same clips with room reverb at three decay times (RT60, seconds). Error rate per level." source="source: sweeps, bed=reverb" bodyClassName="p-0 sm:p-0">
          <DataTable
            rows={ids}
            rowKey={(r) => r}
            caption="Reverb"
            columns={[
              { key: "model", header: "Model", sticky: true, cell: (id) => <ModelCell run={run} id={id} /> },
              ...levels.map<Column<string>>((lv) => ({
                key: lv,
                header: `RT60 ${lv} s`,
                align: "right",
                cell: (id) => {
                  const p = reverb.find((q) => q.provider === id && (q.level ?? "") === lv);
                  return <ValueCell v={p?.error_rate ?? null} fmt={(x) => pct(x)} lo={p?.ci_low} hi={capHi(p?.ci_high, p?.error_rate)} />;
                },
              })),
            ]}
          />
        </Figure>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- A8 long-form

function LongformView({ run, n }: ViewProps) {
  const lf = v3(run).longform!;
  // Long-form has its own reference (creator captions on full videos), so the page's tier does not filter it.
  const rows = [...lf.rows].sort((a, b) => (a.error_rate ?? 9) - (b.error_rate ?? 9));
  const hasDup = rows.some((r) => r.dup_per_hour != null);
  const ids = [...new Set(lf.by_minute.map((p) => p.provider))].sort();
  const minutes = [...new Set(lf.by_minute.map((p) => p.minute))].sort((a, b) => a - b);
  const series: LineSeries[] = ids.map((id) => ({ id, label: providerLabel(run, id), color: providerColor(run, id), points: lf.by_minute.filter((p) => p.provider === id && p.offset_median_ms != null).map((p) => ({ x: p.minute, y: p.offset_median_ms! })) }));
  const columns: Column<LongformRow>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => providerLabel(run, r.provider), cell: (r) => <ModelCell run={run} id={r.provider} /> },
    { key: "err", header: "Error rate", align: "right", sort: (r) => r.error_rate, cell: (r) => <ValueCell v={r.error_rate} fmt={(x) => pct(x)} lo={r.ci_low} hi={r.ci_high} best={bestBy(rows, (q) => q.error_rate, "lower") === r.provider} /> },
    { key: "done", header: <Help label="Handled" help="Share of videos transcribed end to end, with no failure or cut-off." />, align: "right", sort: (r) => r.completed, cell: (r) => <ValueCell v={r.completed} fmt={(x) => pct(x, 0)} best={bestBy(rows, (q) => q.completed, "higher") === r.provider} /> },
    hasDup
      ? { key: "dup", header: <Help label="Repeats / h" help="Stretches of text written twice where the provider stitches chunks of a long file together, per hour of audio. Lower is better." />, align: "right", sort: (r) => r.dup_per_hour, cell: (r) => <ValueCell v={r.dup_per_hour} fmt={(x) => x.toFixed(1)} best={bestBy(rows, (q) => q.dup_per_hour, "lower") === r.provider} /> }
      : { key: "chunk", header: <Help label="Join errors" help="Text dropped or repeated where the provider splits long audio into chunks, per video. Lower is better." />, align: "right", sort: (r) => r.chunk_errors_per_video, cell: (r) => <ValueCell v={r.chunk_errors_per_video} fmt={(x) => x.toFixed(1)} best={bestBy(rows, (q) => q.chunk_errors_per_video, "lower") === r.provider} /> },
    ...(rows.some((r) => r.gaps_per_hour != null)
      ? [{ key: "gaps", header: <Help label="Gaps / h" help="Stretches of speech with no text at all, per hour of audio. Lower is better." />, align: "right" as const, sort: (r: LongformRow) => r.gaps_per_hour, cell: (r: LongformRow) => <ValueCell v={r.gaps_per_hour} fmt={(x) => x.toFixed(1)} />, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" }]
      : []),
    ...(rows.some((r) => r.timing_after5_ms != null)
      ? [{ key: "t5", header: <Help label="Offset, then after min 5" help="Median word timing offset in the first five minutes, then after minute five." />, align: "right" as const, cell: (r: LongformRow) => <span className="num text-[12.5px] text-ink-2">{ms(r.timing_first5_ms)} / {ms(r.timing_after5_ms)}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" }]
      : []),
    { key: "drift", header: <Help label="Timing drift" help="How much the median word timing offset grows per minute of audio. Lower is better." />, align: "right", sort: (r) => r.drift_ms_per_min, cell: (r) => <ValueCell v={r.drift_ms_per_min} fmt={(x) => `${x.toFixed(1)} ms/min`} best={bestBy(rows, (q) => q.drift_ms_per_min, "lower") === r.provider} /> },
    { key: "n", header: "Videos", align: "right", cell: (r) => <span className="num text-ink-3">{r.n_videos ?? "n/a"}</span>, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
  ];
  const totalMin = maxOf(rows.map((r) => r.minutes ?? 0), 0);
  return (
    <div className="grid grid-cols-1 gap-8">
      <Figure n={n} title="Long-form: full videos" caption={`Whole videos instead of short clips${totalMin ? `, ${int(totalMin)} minutes of audio per model` : ""}. Accuracy, whether the provider copes with the length, and errors where it stitches chunks together.${lf.reference ? ` Reference: ${lf.reference}.` : ""}`} source={`source: longform, run=${run.run_id}`} bodyClassName="p-0 sm:p-0">
        {rows.length ? <DataTable columns={columns} rows={rows} rowKey={(r) => r.provider} caption="Long-form" /> : <div className="p-4 sm:p-5"><Empty /></div>}
      </Figure>
      {series.length > 0 && minutes.length > 1 && (
        <Figure n={n + 1} title="Word timing by minute" caption="Median word start offset in each minute of the video. A flat line keeps captions in sync to the end; a rising one drifts. The dashed line marks minute five." source="source: longform.by_minute">
          <LineChart
            series={series}
            xDomain={[minutes[0]!, minutes[minutes.length - 1]!]}
            xTicks={minutes.filter((m) => m % 5 === 0).map((m) => ({ value: m, label: `${m} min` }))}
            xFormat={(v) => `minute ${v}`}
            yFormat={(v) => ms(v)}
            yLabel="Median offset"
            vlines={minutes.includes(5) ? [{ x: 5, label: "minute 5" }] : []}
            label="Median word timing offset by minute of video, per model"
            height={280}
          />
        </Figure>
      )}
    </div>
  );
}

// ---------------------------------------------------------------- A9 speed and cost

function SpeedView({ run, tier, metric, n }: ViewProps) {
  const comp = v3(run).compute;
  const all = overall(run, tier, metric);
  const rows = run.providers.filter((p) => isEligible(run, p.id)).map((p) => {
    const sf = speedFactor(run, p.id, tier, metric);
    const c = comp?.rows.find((r) => r.provider === p.id);
    return { p, sf, c, agg: all.find((a) => a.provider === p.id), per1k: pricePer1000Min(p.usd_per_hour) };
  });
  rows.sort((a, b) => (a.per1k ?? 0) - (b.per1k ?? 0) || (b.sf.value ?? 0) - (a.sf.value ?? 0));
  const maxSf = maxOf(rows.map((r) => r.sf.value ?? 0), 1);
  type R = (typeof rows)[number];
  const columns: Column<R>[] = [
    { key: "model", header: "Model", sticky: true, sort: (r) => r.p.label, cell: (r) => <ModelCell run={run} id={r.p.id} sub={r.p.model_id} /> },
    { key: "price", header: <Help label="Per 1,000 min" help="List price for 1,000 minutes of audio, from the provider's price per hour." />, align: "right", sort: (r) => r.per1k, cell: (r) => <span className="num text-ink">{r.p.paid ? usdPerHour(r.per1k) : "$0"}</span> },
    {
      key: "speed",
      header: <Help label="Speed factor" help="Seconds of audio transcribed per second of waiting. Higher is faster. For local models, measured on the machine named below; for APIs, from this run's request times." />,
      sort: (r) => r.sf.value,
      cell: (r) => (
        <span className="flex items-center gap-2.5">
          <span className="num w-12 text-right text-ink">{r.sf.value == null ? "n/a" : factor(r.sf.value)}</span>
          <ShareBar v={r.sf.value == null ? null : r.sf.value / maxSf} color={providerColor(run, r.p.id)} />
        </span>
      ),
    },
    { key: "err", header: "Error rate", align: "right", sort: (r) => r.agg?.error_rate ?? null, cell: (r) => <ValueCell v={r.agg?.error_rate ?? null} fmt={(x) => pct(x)} />, headerClassName: "hidden sm:table-cell", className: "hidden sm:table-cell" },
    { key: "where", header: "Runs on", cell: (r) => <span className="text-[12.5px] text-ink-2">{r.p.uploads_audio ? "Provider API" : (r.c?.machine ?? comp?.machine ?? "Your machine")}</span>, headerClassName: "hidden md:table-cell", className: "hidden md:table-cell" },
    { key: "size", header: "Model size", align: "right", cell: (r) => <span className="num text-[12.5px] text-ink-2">{r.c?.model_size_mb != null ? sizeMb(r.c.model_size_mb) : r.c?.params_m != null ? `${int(r.c.params_m)}M params` : "n/a"}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" },
    { key: "mem", header: "Peak memory", align: "right", cell: (r) => <span className="num text-[12.5px] text-ink-2">{r.c?.peak_memory_mb != null ? sizeMb(r.c.peak_memory_mb) : "n/a"}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" },
  ];
  return (
    <Figure
      n={n}
      title="Price per 1,000 minutes and speed"
      caption={<>Cheapest first. Speed factor is how many seconds of audio come back per second of waiting{comp?.machine ? `; local models measured on ${comp.machine}` : ""}.</>}
      source={`source: providers (list prices), compute, aggregates (rtf), run=${run.run_id}, tier=${tier}`}
      bodyClassName="p-0 sm:p-0"
    >
      <DataTable columns={columns} rows={rows} rowKey={(r) => r.p.id} caption="Price and speed" />
    </Figure>
  );
}

function sizeMb(v: number): ReactNode {
  return v >= 1024 ? `${(v / 1024).toFixed(1)} GB` : `${int(Math.round(v))} MB`;
}
