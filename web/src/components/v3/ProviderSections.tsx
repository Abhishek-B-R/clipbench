import { useMemo, type ReactNode } from "react";
import { Link } from "react-router";
import { ArrowRight, ArrowUpRight, Check, Minus } from "lucide-react";
import type { Run, Tier } from "@/lib/types";
import { providerColor, providerLabel, tagProfile } from "@/lib/analysis";
import { badgeSnippets } from "@/lib/config";
import { SLICE_KIND_LABEL, sliceLabel } from "@/lib/metrics";
import {
  FEATURE_KEYS,
  FEATURE_LABEL,
  STYLE_PROPS,
  historyFor,
  inSlice,
  latticeFor,
  pricePer1000Min,
  reliabilityFor,
  runMetaFacts,
  settingsFor,
  speedFactor,
  v3,
} from "@/lib/v3";
import { date, int, ms, pct, signedPts } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Figure } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { CodeBlock } from "@/components/ui/CodeBlock";
import { Tooltip } from "@/components/ui/Tooltip";
import { LineChart } from "@/components/charts/LineChart";

type P = { run: Run; id: string; tier: Tier; metric: string; n: number };

function rankOf<T extends { provider: string }>(rows: T[], id: string, get: (r: T) => number | null | undefined, better: "lower" | "higher") {
  const xs = rows.filter((r) => get(r) != null).sort((a, b) => (better === "lower" ? get(a)! - get(b)! : get(b)! - get(a)!));
  const i = xs.findIndex((r) => r.provider === id);
  return i < 0 ? null : { rank: i + 1, of: xs.length };
}

// ---------------------------------------------------------------- A3 canary history with drift events

export function CanaryHistory({ run, id, metric, n }: P) {
  const x = v3(run);
  const pts = historyFor(x.history, id, metric);
  const events = (x.drift ?? []).filter((e) => e.provider === id && (!e.metric || e.metric === metric));
  if (pts.length < 2) return null;
  const t = (d: string) => Date.parse(d);
  const xs = pts.map((p) => t(p.date));
  const first = Math.min(...xs);
  const last = Math.max(...xs);
  const months: { value: number; label: string }[] = [];
  const d0 = new Date(first);
  for (let m = new Date(Date.UTC(d0.getUTCFullYear(), d0.getUTCMonth() + 1, 1)); m.getTime() <= last; m = new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth() + 1, 1))) months.push({ value: m.getTime(), label: date(m.toISOString()).replace(/^1 /, "") });
  return (
    <Figure
      n={n}
      title="History and drift"
      caption={
        events.length
          ? "Each point is a scheduled rerun on the fixed canary clips, with its 95% interval. A vermilion line marks a drift event: a score that moved beyond the model's own interval."
          : "Each point is a scheduled rerun on the fixed canary clips, with its 95% interval. No drift event so far: every move stayed inside the model's own interval."
      }
      source={`source: history (canary reruns), drift; metric=${metric}`}
    >
      <LineChart
        series={[{ id, label: providerLabel(run, id), color: providerColor(run, id), points: pts.map((p) => ({ x: t(p.date), y: p.error_rate, lo: p.ci_low, hi: p.ci_high })) }]}
        xDomain={[first, last]}
        xTicks={months}
        xFormat={(v) => date(v)}
        yFormat={(v) => pct(v, v < 0.1 ? 1 : 0)}
        yLabel="Error rate"
        vlines={events.map((e) => ({ x: t(e.date), label: `drift ${date(e.date)}`, tone: "signal" as const }))}
        label={`${providerLabel(run, id)} canary error rate over time, ${pts.length} reruns`}
        height={240}
      />
      {events.length > 0 && (
        <ul className="mt-4 grid gap-2 border-t border-line pt-3">
          {events.map((e) => (
            <li key={e.date} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[13px] text-ink-2">
              <time className="num text-signal-ink">{date(e.date)}</time>
              <span>
                {e.direction === "worse" ? "Got worse" : e.direction === "better" ? "Got better" : "Moved"}: <span className="num text-ink">{pct(e.before)}</span> to <span className="num text-ink">{pct(e.after)}</span>
                {e.ci_low != null && e.ci_high != null && (
                  <span className="text-ink-3">
                    {" "}
                    (its interval was {pct(e.ci_low)} to {pct(e.ci_high)})
                  </span>
                )}
                {e.model_id && <span className="font-mono text-[11.5px] text-ink-3"> {e.model_id}</span>}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Figure>
  );
}

// ---------------------------------------------------------------- strengths and failures

export function StrengthsFailures({ run, id, tier, metric, n }: P) {
  const rows = (["condition", "category"] as const).flatMap((kind) =>
    tagProfile(run, id, tier, metric, kind)
      .filter((r) => r.mine && !r.mine.too_small && Number.isFinite(r.fieldMedian))
      .map((r) => ({ kind, slice: r.slice, gap: r.mine!.error_rate - r.fieldMedian, rank: r.rank, of: r.rows.length, err: r.mine!.error_rate, nClips: r.mine!.n_clips })),
  );
  if (!rows.length) return null;
  const strengths = rows.filter((r) => r.gap < 0).sort((a, b) => a.gap - b.gap).slice(0, 4);
  const failures = rows.filter((r) => r.gap > 0).sort((a, b) => b.gap - a.gap).slice(0, 4);
  const list = (xs: typeof rows, good: boolean) =>
    xs.length ? (
      <ul className="grid">
        {xs.map((r) => (
          <li key={r.kind + r.slice} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-baseline gap-3 border-t border-line py-2.5 first:border-t-0">
            <Link to={`/leaderboard?lang=${run.language}&slice_kind=${r.kind}&slice=${encodeURIComponent(r.slice)}&provider=${id}`} className="min-w-0 text-[13.5px] text-ink hover:underline">
              {sliceLabel(r.kind, r.slice)} <span className="text-[11.5px] text-ink-3">{SLICE_KIND_LABEL[r.kind].toLowerCase()}</span>
            </Link>
            <span className="num text-[12.5px] text-ink-2">
              {pct(r.err)} <span className="text-ink-3">#{r.rank}/{r.of}</span>
            </span>
            <span className={cn("num w-20 text-right text-[12.5px]", good ? "text-good" : "text-signal-ink")}>{signedPts(r.gap)}</span>
          </li>
        ))}
      </ul>
    ) : (
      <p className="text-[13px] text-ink-3">{good ? "Not clearly better than the field on any condition or category." : "Not clearly worse than the field on any condition or category."}</p>
    );
  return (
    <Figure n={n} title="Strengths and failures by condition and category" caption="Gap to the median of the other models on the same slice. Slices under 15 clips are left out. Click one to see the full ranking there." source={`source: aggregates, tier=${tier}, metric=${metric}, slice_kind=condition and category`} bodyClassName="p-0 sm:p-0">
      <div className="grid md:grid-cols-2">
        <div className="border-b border-line p-4 sm:p-5 md:border-r md:border-b-0">
          <div className="label mb-2">Does best on</div>
          {list(strengths, true)}
        </div>
        <div className="p-4 sm:p-5">
          <div className="label mb-2">Struggles with</div>
          {list(failures, false)}
        </div>
      </div>
    </Figure>
  );
}

// ---------------------------------------------------------------- every measure

interface Measure {
  group: string;
  label: string;
  value: string | null;
  rank?: { rank: number; of: number } | null;
  note?: string;
  view?: string;
}

export function EveryMeasure({ run, id, tier, metric, n }: P) {
  const x = v3(run);
  const items = useMemo(() => {
    const out: Measure[] = [];
    const lat = latticeFor(run, tier, metric, "all", "all");
    if (lat.size) {
      const rows = [...lat.entries()].map(([provider, v]) => ({ provider, v: v.error_rate }));
      const mine = lat.get(id);
      out.push({ group: "Accuracy", label: "Error rate, any valid form", value: mine ? pct(mine.error_rate) : null, rank: rankOf(rows, id, (r) => r.v, "lower"), view: "accuracy&score=lattice" });
    }
    if (x.semantic?.published) {
      const rows = inSlice(x.semantic.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push({ group: "Accuracy", label: "Semantic error", value: me?.error == null ? null : pct(me.error), rank: rankOf(rows, id, (r) => r.error, "lower") });
    }
    if (x.streaming) {
      const rows = inSlice(x.streaming.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push(
        { group: "Streaming", label: "Final error rate", value: me?.error_rate == null ? null : pct(me.error_rate), rank: rankOf(rows, id, (r) => r.error_rate, "lower"), note: me ? undefined : "No realtime API in this run", view: "streaming" },
        { group: "Streaming", label: "First partial / final segment", value: me ? `${ms(me.ttfp_p50_ms)} / ${ms(me.ttfs_p50_ms)}` : null, rank: rankOf(rows, id, (r) => r.ttfs_p50_ms, "lower"), view: "streaming" },
        { group: "Streaming", label: "Partial stability", value: me?.stability == null ? null : pct(me.stability), rank: rankOf(rows, id, (r) => r.stability, "higher"), view: "streaming" },
      );
    }
    if (x.formatting) {
      const rows = inSlice(x.formatting, tier);
      const me = rows.find((r) => r.provider === id);
      out.push(
        { group: "Formatting", label: "Punctuation error rate", value: me?.punct_error_rate == null ? null : pct(me.punct_error_rate), rank: rankOf(rows, id, (r) => r.punct_error_rate, "lower"), view: "formatting" },
        { group: "Formatting", label: "Numbers, dates and money", value: me?.itn_acc == null ? null : pct(me.itn_acc), rank: rankOf(rows, id, (r) => r.itn_acc, "higher"), view: "formatting" },
      );
    }
    if (x.captions) {
      const rows = inSlice(x.captions.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push({ group: "Caption files", label: "Readable cues", value: me?.readable == null ? null : pct(me.readable), rank: rankOf(rows, id, (r) => r.readable, "higher"), view: "captions" }, { group: "Caption files", label: "Cues on time", value: me?.cue_within_200ms == null ? null : pct(me.cue_within_200ms), rank: rankOf(rows, id, (r) => r.cue_within_200ms, "higher"), view: "captions" });
    }
    if (x.diarization) {
      const rows = inSlice(x.diarization.rows, tier);
      const me = rows.find((r) => r.provider === id);
      const off = x.diarization.unsupported.includes(id);
      out.push({ group: "Speakers", label: "Diarization error rate", value: me?.der == null ? null : pct(me.der), rank: rankOf(rows, id, (r) => r.der, "lower"), note: off ? "Not offered" : undefined, view: "speakers" }, { group: "Speakers", label: "Speaker count right", value: me?.speaker_count_acc == null ? null : pct(me.speaker_count_acc), rank: rankOf(rows, id, (r) => r.speaker_count_acc, "higher"), note: off ? "Not offered" : undefined, view: "speakers" });
    }
    if (x.lid) {
      const rows = inSlice(x.lid.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push({ group: "Language", label: "Right language, auto-detect", value: me?.accuracy == null ? null : pct(me.accuracy), rank: rankOf(rows, id, (r) => r.accuracy, "higher"), view: "lid" }, { group: "Language", label: "Error rate without a hint", value: me?.error_rate_auto == null ? null : pct(me.error_rate_auto), rank: rankOf(rows, id, (r) => r.error_rate_auto, "lower"), view: "lid" });
    }
    if (x.entities) {
      const rows = inSlice(x.entities.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push(
        { group: "Names and brands", label: "Without custom vocabulary", value: me?.plain.acc == null ? null : pct(me.plain.acc), rank: rankOf(rows, id, (r) => r.plain.acc, "higher"), view: "entities" },
        { group: "Names and brands", label: "With the term list", value: me?.keyterms?.acc == null ? null : pct(me.keyterms.acc), rank: rankOf(rows, id, (r) => r.keyterms?.acc, "higher"), note: me && !me.keyterms ? "Not offered" : undefined, view: "entities" },
      );
    }
    if (x.longform) {
      const rows = inSlice(x.longform.rows, tier);
      const me = rows.find((r) => r.provider === id);
      out.push(
        { group: "Long-form", label: "Error rate on full videos", value: me?.error_rate == null ? null : pct(me.error_rate), rank: rankOf(rows, id, (r) => r.error_rate, "lower"), view: "longform" },
        { group: "Long-form", label: "Timing drift", value: me?.drift_ms_per_min == null ? null : `${me.drift_ms_per_min.toFixed(1)} ms per minute`, rank: rankOf(rows, id, (r) => r.drift_ms_per_min, "lower"), view: "longform" },
      );
    }
    if (x.style) {
      const me = inSlice(x.style, tier).find((r) => r.provider === id);
      for (const p of STYLE_PROPS) if (me?.[p.key] != null) out.push({ group: "Style (a choice, not an error)", label: `${p.label} kept`, value: pct(me[p.key], 0), view: "style" });
    }
    return out;
  }, [run, id, tier, metric, x]);
  if (!items.length) return null;
  const groups = [...new Set(items.map((i) => i.group))];
  return (
    <Figure n={n} title="Every measure" caption="Everything this run measured for the model, with its rank among the models scored on each. n/a means it was not measured or the feature is not offered." source={`source: streaming, semantic, aggregates, formatting, captions, diarization, lid, entities, longform, style; tier=${tier}, slice=all`} bodyClassName="p-0 sm:p-0">
      <div className="grid md:grid-cols-2">
        {groups.map((g, gi) => (
          <section key={g} className={cn("border-line px-4 py-3 sm:px-5", gi > 0 && "border-t", gi % 2 === 1 && "md:border-l", gi === 1 && "md:border-t-0")}>
            <h3 className="label mb-1">{g}</h3>
            <dl>
              {items
                .filter((i) => i.group === g)
                .map((i) => (
                  <div key={i.label} className="grid grid-cols-[minmax(0,1fr)_auto_44px] items-baseline gap-3 py-1.5">
                    <dt className="text-[13px] text-ink-2">
                      {i.view ? (
                        <Link to={i.view === "streaming" ? `/streaming?lang=${run.language}` : `/leaderboard?lang=${run.language}&view=${i.view}&provider=${id}`} className="hover:text-ink hover:underline">
                          {i.label}
                        </Link>
                      ) : (
                        i.label
                      )}
                    </dt>
                    <dd className="num text-right text-[13px] text-ink">{i.value ?? <span className="text-[12px] text-ink-3">{i.note ?? "n/a"}</span>}</dd>
                    <dd className="num text-right text-[11.5px] text-ink-3">{i.value != null && i.rank ? `#${i.rank.rank}/${i.rank.of}` : ""}</dd>
                  </div>
                ))}
            </dl>
          </section>
        ))}
      </div>
    </Figure>
  );
}

// ---------------------------------------------------------------- spec sheets: cost/speed, compute, reliability, settings

export function SpecSheet({ n, title, rows, source, footer }: { n: number; title: string; rows: [string, ReactNode][]; source: string; footer?: ReactNode }) {
  return (
    <Figure n={n} title={title} source={source} bodyClassName="p-0 sm:p-0" className="self-start">
      <dl>
        {rows.map(([k, v]) => (
          <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
            <dt className="text-[13px] text-ink-3">{k}</dt>
            <dd className="min-w-0 text-[13.5px] break-words text-ink">{v}</dd>
          </div>
        ))}
      </dl>
      {footer && <div className="border-t border-line px-4 py-3 sm:px-5">{footer}</div>}
    </Figure>
  );
}

/** Price per 1,000 minutes and speed factor (A9), for the headline stats. */
export function priceSpeed(run: Run, id: string, tier: Tier, metric: string) {
  const p = run.providers.find((x) => x.id === id);
  return { per1k: pricePer1000Min(p?.usd_per_hour), speed: speedFactor(run, id, tier, metric) };
}

export function ComputeSpec({ run, id, n }: Omit<P, "tier" | "metric">) {
  const c = v3(run).compute;
  const row = c?.rows.find((r) => r.provider === id);
  if (!row) return null;
  return (
    <SpecSheet
      n={n}
      title="Compute"
      source="source: compute (local runs)"
      rows={[
        ["Machine", row.machine ?? c?.machine ?? "Not stated"],
        ["Device", row.device ?? "Not stated"],
        ["Speed factor (RTFx)", row.rtfx == null ? "n/a" : `${row.rtfx.toFixed(1)}x real time`],
        ["Model size", row.model_size_mb == null ? "n/a" : row.model_size_mb >= 1024 ? `${(row.model_size_mb / 1024).toFixed(1)} GB` : `${int(Math.round(row.model_size_mb))} MB`],
        ["Parameters", row.params_m == null ? "n/a" : `${int(row.params_m)} million`],
        ["Peak memory", row.peak_memory_mb == null ? "n/a" : row.peak_memory_mb >= 1024 ? `${(row.peak_memory_mb / 1024).toFixed(1)} GB` : `${int(Math.round(row.peak_memory_mb))} MB`],
      ]}
    />
  );
}

export function ReliabilitySpec({ run, id, n }: Omit<P, "tier" | "metric">) {
  const { run: r, all } = reliabilityFor(v3(run).reliability, id);
  if (!r && !all) return null;
  const both = (f: (x: NonNullable<typeof r>) => string | null): ReactNode => (
    <span className="num">
      {r ? (f(r) ?? "n/a") : "n/a"}
      {all && <span className="text-ink-3"> / {f(all) ?? "n/a"}</span>}
    </span>
  );
  return (
    <SpecSheet
      n={n}
      title="Reliability"
      source={`source: reliability (this run${all ? " / all runs" : ""})`}
      rows={[
        ["Requests", both((x) => (x.n_requests == null ? null : int(x.n_requests)))],
        ["Failure rate", both((x) => (x.failure_rate == null ? null : pct(x.failure_rate, 2)))],
        ["Timeout rate", both((x) => (x.timeout_rate == null ? null : pct(x.timeout_rate, 2)))],
        ["Retries per request", both((x) => (x.retries_per_request == null ? null : x.retries_per_request.toFixed(2)))],
        ["Latency p50 / p99", both((x) => (x.p99_ms == null ? null : `${ms(x.p50_ms)} / ${ms(x.p99_ms)}`))],
        ...(all?.since ? ([["On record since", date(all.since)]] as [string, ReactNode][]) : []),
      ]}
      footer={all ? <span className="text-[12px] text-ink-3">First value: this run. After the slash: every run on record{all.n_runs != null ? `, ${int(all.n_runs)} in all` : ""}.</span> : undefined}
    />
  );
}

export function SettingsSpec({ run, id, n }: Omit<P, "tier" | "metric">) {
  const s = settingsFor(run, id);
  const facts = runMetaFacts(run);
  const p = run.providers.find((x) => x.id === id);
  const rows: [string, ReactNode][] = [["model_id", <code key="m" className="font-mono text-[12.5px]">{p?.model_id}</code>], ...s.map(([k, v]) => [k, <code key={k} className="font-mono text-[12.5px]">{v}</code>] as [string, ReactNode]), ...facts.map(([k, v]) => [`run ${k}`, <code key={k} className="font-mono text-[12.5px]">{v}</code>] as [string, ReactNode])];
  return <SpecSheet n={n} title="Exact settings used" source={`source: run_meta, run=${run.run_id}`} rows={rows} footer={s.length ? undefined : <span className="text-[12px] text-ink-3">This export does not record per-provider settings; defaults were used.</span>} />;
}

// ---------------------------------------------------------------- B20 feature support row

export function FeatureRow({ run, id, n }: Omit<P, "tier" | "metric">) {
  const fp = v3(run).features?.providers.find((p) => p.id === id);
  if (!fp) return null;
  const keys = FEATURE_KEYS.filter((k) => fp.cells[k]);
  return (
    <Figure
      n={n}
      title="Feature support"
      caption="From the provider's documentation, with the date we checked it. Each item links to its source."
      source="source: features (pricing.toml)"
      controls={
        <Link to="/features" className="inline-flex items-center gap-1 text-[12.5px] text-ink-2 hover:text-ink">
          Feature matrix <ArrowRight size={12} />
        </Link>
      }
    >
      <ul className="flex flex-wrap gap-1.5">
        {keys.map((k) => {
          const c = fp.cells[k]!;
          const val = c.support === "yes" ? null : c.support === "no" ? null : Array.isArray(c.value) ? `${int(c.value.length)}` : c.value == null ? "not stated" : String(c.value);
          const tip = `${FEATURE_LABEL[k]}${c.checked_on ? `, checked ${date(c.checked_on)}` : ""}`;
          const inner = (
            <span className={cn("inline-flex h-8 items-center gap-1.5 rounded-sm border px-2.5 text-[12.5px]", c.support === "no" ? "border-line text-ink-3" : "border-line-strong text-ink")}>
              {c.support === "yes" ? <Check size={13} className="text-good" aria-hidden /> : c.support === "no" ? <Minus size={13} aria-hidden /> : null}
              {FEATURE_LABEL[k]}
              {val && <span className="font-mono text-[11px] text-ink-3">{k === "max_file_minutes" && typeof c.value === "number" ? `${int(c.value)} min` : val}</span>}
              {c.url && <ArrowUpRight size={11} className="text-ink-3" aria-hidden />}
              <span className="sr-only">{c.support === "yes" ? "offered" : c.support === "no" ? "not offered" : ""}</span>
            </span>
          );
          return (
            <li key={k}>
              <Tooltip content={tip}>
                {c.url ? (
                  <a href={c.url} target="_blank" rel="noreferrer" aria-label={`${tip}, documentation`}>
                    {inner}
                  </a>
                ) : (
                  <span tabIndex={0}>{inner}</span>
                )}
              </Tooltip>
            </li>
          );
        })}
      </ul>
    </Figure>
  );
}

// ---------------------------------------------------------------- A12 badge

export function BadgeEmbed({ run, id, n }: Omit<P, "tier" | "metric">) {
  const origin = typeof window === "undefined" ? "" : window.location.origin;
  const label = providerLabel(run, id);
  const s = badgeSnippets(origin, id, run.language, label);
  return (
    <Figure n={n} title="Embed a badge" caption="A small SVG badge with this model's current error rate and language, served by the data API. It updates with every published run and links back here." source={`source: ${s.img.replace(origin, "")}`}>
      <CodeBlock
        samples={[
          { label: "Markdown", code: s.markdown },
          { label: "HTML", code: s.html },
          { label: "URL", code: s.img },
        ]}
      />
      <p className="mt-3 text-[12.5px] text-ink-3">
        Shown only for published languages. <Badge tone="outline">SVG</Badge>
      </p>
    </Figure>
  );
}


/** Which sections have something to show, so figure numbers stay consecutive. */
export function providerSections(run: Run, id: string, tier: Tier, metric: string) {
  const x = v3(run);
  return {
    canary: historyFor(x.history, id, metric).length >= 2,
    features: Boolean(x.features?.providers.some((p) => p.id === id)),
    strengths: (["condition", "category"] as const).some((k) => tagProfile(run, id, tier, metric, k).some((r) => r.mine && !r.mine.too_small)),
    measures: Boolean(latticeFor(run, tier, metric, "all", "all").size || x.semantic?.published || x.streaming || x.formatting || x.captions || x.diarization || x.lid || x.entities || x.longform || x.style),
    reliability: Boolean(reliabilityFor(x.reliability, id).run || reliabilityFor(x.reliability, id).all),
    compute: Boolean(x.compute?.rows.some((r) => r.provider === id)),
  };
}
