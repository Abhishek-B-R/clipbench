import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link, useLocation, useSearchParams } from "react-router";
import { Bookmark, Link2, Loader2 } from "lucide-react";
import type { Run, SliceKind, Tier } from "@/lib/types";
import { CAPTION_METRICS, metricValue, sliceLabel } from "@/lib/metrics";
import { useRun } from "@/lib/data";
import { TAG_LABEL, TAG_PHRASE, isPublished, pickTier, aggregatesFor, overall, pair, primaryMetric, providerColor, providerLabel, providerOrder, tagsInRun, type SliceQuery } from "@/lib/analysis";
import { ms, pct, pValue, pts, usdPerHour, words } from "@/lib/format";
import { api, ApiError, type SavedComparison } from "@/lib/api";
import { useSession } from "@/lib/auth";
import { ACCOUNTS } from "@/lib/flags";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Chip } from "@/components/ui/Chip";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { CIBar } from "@/components/ui/CIBar";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button, buttonClass } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Field, Input } from "@/components/ui/Input";
import { Switch } from "@/components/ui/Switch";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { TierControl, TierNote } from "@/components/ui/TierControl";
import { Gated } from "@/components/ui/Gate";
import { toast } from "@/components/ui/Toast";

const MIN = 2;
const MAX = 4;

/**
 * Compare 2 to 4 models on one tier and metric, optionally narrowed to clip types. Every choice lives in the URL
 * (?m=a,b&tier=&metric=&tags=), so the address is the share link. Save stores it on the account.
 * ?c=<id> opens a saved public comparison.
 */
export default function Compare() {
  const state = useRun();
  const run = state.data?.run;
  const [params, setParams] = useSearchParams();
  const tier = pickTier(run, params.get("tier"));
  const published = run ? isPublished(run) : true;
  const metric = params.get("metric") || (run ? primaryMetric(run).name : "cer");
  // A focus slice from a findings deep link (?slice_kind=audio_condition&slice=music), shown first.
  const focusKind = params.get("slice_kind");
  const focus = focusKind && focusKind !== "all" && params.get("slice") ? { kind: focusKind as SliceKind, slice: params.get("slice")! } : null;
  const tagsParam = params.get("tags");
  const tags = tagsParam ? tagsParam.split(",").filter(Boolean) : [];
  const mParam = params.get("m");
  const models = useMemo(() => {
    if (!run) return [];
    const valid = new Set(run.providers.map((p) => p.id));
    const fromUrl = (mParam ?? "").split(",").filter((x) => valid.has(x));
    if (fromUrl.length >= 1) return fromUrl.slice(0, MAX);
    return overall(run, tier, metric)
      .slice(0, 2)
      .map((a) => a.provider);
  }, [run, mParam, tier, metric]);

  const update = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v == null || v === "") p.delete(k);
      else p.set(k, v);
    }
    p.delete("c");
    setParams(p, { replace: true });
  };

  useSharedComparison(params.get("c"), (c) =>
    setParams(new URLSearchParams({ m: c.providers.join(","), tier: c.tier, metric: c.metric, ...(c.tags.length ? { tags: c.tags.join(",") } : {}) }), { replace: true }),
  );

  const toggleModel = (id: string) => {
    const on = models.includes(id);
    if (on && models.length <= MIN) return toast(`Keep at least ${MIN} models to compare.`);
    if (!on && models.length >= MAX) return toast(`Compare at most ${MAX} models at a time.`);
    const next = on ? models.filter((x) => x !== id) : [...models, id];
    update({ m: (run ? providerOrder(run) : next).filter((x) => next.includes(x)).join(",") });
  };
  const toggleTag = (t: string) => update({ tags: (tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t]).join(",") });

  return (
    <>
      <PageHeader
        eyebrow="Compare"
        title="Compare models"
        description={`Compare ${words(MIN)} to ${words(MAX)} models on the same clips. Verdicts come from the paired test on the clips both models scored, not from the gap between two numbers.`}
        actions={run && published && <ShareSave run={run} models={models} tier={tier} metric={metric} tags={tags} />}
      >
        {published && <TierControl run={run} value={tier} onChange={(v) => update({ tier: v })} />}
        {run && published && <Segmented label="Metric" value={metric} onChange={(v) => update({ metric: v })} options={run.metrics.map((m) => ({ value: m.name, label: m.label }))} />}
      </PageHeader>
      <div className="frame grid gap-8 py-8">
        {state.status === "error" && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {!run && state.status !== "error" && (
          <>
            <Skeleton className="h-24 w-full" />
            <Skeleton className="h-96 w-full" />
          </>
        )}
        {run && !published && <Gated run={run} what="comparison">{null}</Gated>}
        {run && published && (
          <>
            <div className="grid gap-5 rounded-md border border-line bg-surface p-4 shadow-card sm:p-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)] lg:gap-8">
              <fieldset>
                <legend className="label mb-2.5">
                  Models <span className="normal-case">({models.length} of {MAX})</span>
                </legend>
                <div className="flex flex-wrap gap-1.5">
                  {overall(run, tier, metric).map((a) => {
                    const on = models.includes(a.provider);
                    return (
                      <Chip key={a.provider} pressed={on} onClick={() => toggleModel(a.provider)} disabled={!on && models.length >= MAX} dot={<SeriesDot color={providerColor(run, a.provider)} />}>
                        {providerLabel(run, a.provider)}
                      </Chip>
                    );
                  })}
                </div>
              </fieldset>
              <fieldset>
                <legend className="label mb-2.5">
                  Clip types <span className="normal-case">({tags.length ? `${tags.length} picked` : "all"})</span>
                </legend>
                <div className="flex flex-wrap gap-1.5">
                  {tagsInRun(run, tier).length === 0 && <span className="py-1.5 text-[13px] text-ink-3">No clip types tagged in this run yet.</span>}
                  {tagsInRun(run, tier).map((t) => (
                    <Chip key={t} pressed={tags.includes(t)} onClick={() => toggleTag(t)}>
                      {TAG_LABEL[t]}
                    </Chip>
                  ))}
                  {tags.length > 0 && (
                    <button type="button" onClick={() => update({ tags: null })} className="h-8 px-2 text-[13px] text-ink-3 underline decoration-line-strong underline-offset-4 hover:text-ink">
                      Clear
                    </button>
                  )}
                </div>
              </fieldset>
            </div>
            <TierNote tier={tier} className="-mt-3" />
            <SideBySide run={run} models={models} tier={tier} metric={metric} tags={tags} focus={focus} />
            <Verdicts run={run} models={models} tier={tier} metric={metric} tags={tags} focus={focus} />
            <PerType run={run} models={models} tier={tier} metric={metric} tags={tags} />
          </>
        )}
      </div>
    </>
  );
}

function useSharedComparison(id: string | null, apply: (c: SavedComparison) => void) {
  useEffect(() => {
    if (!id) return;
    api<{ comparison: SavedComparison }>(`/api/comparisons/${encodeURIComponent(id)}/public`).then(
      (r) => {
        apply(r.comparison);
        toast(`Opened "${r.comparison.name}"`);
      },
      () => toast.error("That shared comparison is not available", { description: "It may be private or deleted. Showing the default comparison." }),
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);
}

// ---------------------------------------------------------------- share and save

function ShareSave({ run, models, tier, metric, tags }: { run: Run; models: string[]; tier: Tier; metric: string; tags: string[] }) {
  // ACCOUNTS is a build-time constant: with accounts off there is no Save and no session request.
  const data = ACCOUNTS ? useSession().data : null;
  const loc = useLocation();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [pub, setPub] = useState(true);
  const [busy, setBusy] = useState(false);
  const share = async () => {
    const url = `${location.origin}/compare?${new URLSearchParams({ m: models.join(","), tier, metric, ...(tags.length ? { tags: tags.join(",") } : {}) })}`;
    try {
      await navigator.clipboard.writeText(url);
      toast("Link copied", { description: "Anyone can open it. No account needed." });
    } catch {
      toast.error("Could not copy. Copy the address bar instead.");
    }
  };
  const save = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      await api("/api/comparisons", { method: "POST", body: { name: name.trim() || defaultName(run, models), providers: models, tier, metric, tags, public: pub } });
      toast.success("Comparison saved", { description: "Find it under Account, Saved comparisons." });
      setOpen(false);
    } catch (err) {
      toast.error("Could not save the comparison", { description: err instanceof ApiError && !err.unavailable ? err.message : "The account service did not answer. Try again in a minute." });
    } finally {
      setBusy(false);
    }
  };
  return (
    <>
      <Button variant="secondary" size="sm" onClick={share}>
        <Link2 size={14} /> Share
      </Button>
      {!ACCOUNTS ? null : data?.user ? (
        <Dialog
          open={open}
          onOpenChange={(o) => {
            setOpen(o);
            if (o) setName(defaultName(run, models));
          }}
          title="Save this comparison"
          description="Keeps the models, tier, metric and clip types. Open it again from your account."
          trigger={
            <Button size="sm">
              <Bookmark size={14} /> Save
            </Button>
          }
        >
          <form onSubmit={save} className="grid gap-4">
            <Field label="Name" htmlFor="cmp-name">
              <Input id="cmp-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} autoFocus />
            </Field>
            <div className="flex items-start justify-between gap-4">
              <span>
                <span id="cmp-public" className="block text-[13px] font-medium text-ink">
                  Public link
                </span>
                <span className="block text-[12px] text-ink-3">Anyone with the link can open it. Your name is never shown.</span>
              </span>
              <Switch checked={pub} onChange={setPub} labelledBy="cmp-public" />
            </div>
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="ghost" onClick={() => setOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" disabled={busy}>
                {busy && <Loader2 size={14} className="animate-spin" />} Save
              </Button>
            </div>
          </form>
        </Dialog>
      ) : (
        <Link to={`/sign-in?next=${encodeURIComponent(loc.pathname + loc.search)}`} className={buttonClass("primary", "sm")}>
          <Bookmark size={14} /> Save
        </Link>
      )}
    </>
  );
}

function defaultName(run: Run, models: string[]) {
  return models.map((m) => providerLabel(run, m)).join(" vs ");
}

// ---------------------------------------------------------------- side by side

function SideBySide({ run, models, tier, metric, tags, focus }: { run: Run; models: string[]; tier: Tier; metric: string; tags: string[]; focus: Focus }) {
  const all = overall(run, tier, metric);
  const get = (kind: SliceKind, slice: string) => aggregatesFor(run, { tier, metric, kind, slice });
  const allRows = get("all", "all");
  const max = Math.max(0.01, ...allRows.filter((a) => models.includes(a.provider)).map((a) => a.ci_high)) * 1.1;
  const row = (kind: SliceKind, slice: string) => models.map((m) => get(kind, slice).find((a) => a.provider === m));
  const lowest = (vals: (number | null | undefined)[]) => {
    const xs = vals.filter((v): v is number => v != null);
    return xs.length ? Math.min(...xs) : null;
  };
  const showTags = tags.length ? tags : tagsInRun(run, tier);
  const metricLabel = run.metrics.find((m) => m.name === metric)?.label ?? metric;

  const rateRow = (label: ReactNode, kind: SliceKind, slice: string, big = false) => {
    const r = row(kind, slice);
    const lo = lowest(r.filter((a) => a && !a.too_small).map((a) => a!.error_rate));
    return (
      <Tr key={slice} label={label}>
        {r.map((a, i) =>
          a ? (
            <Td key={models[i]}>
              <span className="flex items-center justify-end gap-3">
                {big && <CIBar value={a.error_rate} low={a.ci_low} high={a.ci_high} max={max} color={providerColor(run, a.provider)} width={96} className="hidden md:inline-block" />}
                <span className={cn("num", big ? "text-[15px]" : "text-[13px]", a.error_rate === lo ? "font-semibold text-ink" : "text-ink-2", a.too_small && "text-ink-3")}>{pct(a.error_rate)}</span>
              </span>
              <span className="mt-0.5 block text-right text-[11px] text-ink-3">
                {a.too_small ? `n=${a.n_clips}, too small` : big ? `${pct(a.ci_low)} to ${pct(a.ci_high)}` : `${a.n_clips} clips`}
              </span>
            </Td>
          ) : (
            <Td key={models[i]}>
              <span className="text-ink-3">n/a</span>
            </Td>
          ),
        )}
      </Tr>
    );
  };

  const plain = (label: string, vals: (string | ReactNode)[], best?: number) => (
    <Tr key={label} label={label}>
      {vals.map((v, i) => (
        <Td key={models[i]}>
          <span className={cn("num text-[13px]", best === i ? "font-semibold text-ink" : "text-ink-2")}>{v}</span>
        </Td>
      ))}
    </Tr>
  );
  const aggs = row("all", "all");
  const argmin = (xs: (number | null | undefined)[]) => {
    let b = -1;
    xs.forEach((x, i) => {
      if (x != null && (b < 0 || x < (xs[b] as number))) b = i;
    });
    return b;
  };

  return (
    <Figure
      n={1}
      title={`Side by side, ${metricLabel.toLowerCase()}`}
      caption={
        <>
          Bold marks the lowest value in each row. Clip types with fewer than 15 clips are shown but never bolded.
          {models.length > 2 && <span className="sm:hidden"> Swipe the table sideways for every model.</span>}
        </>
      }
      source={`source: aggregates, run=${run.run_id}, tier=${tier}, metric=${metric}`}
      bodyClassName="p-0 sm:p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full border-separate border-spacing-0 text-[13.5px]" style={{ minWidth: 120 + models.length * 100 }}>
          <caption className="sr-only">Selected models side by side</caption>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 w-[120px] border-b border-line bg-surface px-4 py-3 text-left sm:w-[30%] sm:px-5">
                <span className="label">Measure</span>
              </th>
              {models.map((m) => {
                const rank = all.findIndex((a) => a.provider === m) + 1;
                return (
                  <th key={m} scope="col" className="border-b border-line bg-surface px-3 py-3 text-right align-bottom font-normal last:pr-4 sm:last:pr-5">
                    <Link to={`/providers/${m}`} className="inline-flex items-center gap-2 hover:underline">
                      <SeriesDot color={providerColor(run, m)} />
                      <span className="text-[14px] font-medium text-ink">{providerLabel(run, m)}</span>
                    </Link>
                    <span className="mt-0.5 block font-mono text-[11px] text-ink-3">
                      {run.providers.find((p) => p.id === m)?.model_id} / #{rank}
                    </span>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rateRow(<span className="font-medium text-ink">All clips</span>, "all", "all", true)}
            {focus && rateRow(<span className="font-medium text-ink">{sliceLabel(focus.kind, focus.slice)}</span>, focus.kind, focus.slice, true)}
            {showTags.map((t) => rateRow(TAG_LABEL[t] ?? t, "tag", t))}
            {plain("Hallucination", aggs.map((a) => pct(a?.hallucination_rate, 2)), argmin(aggs.map((a) => a?.hallucination_rate)))}
            {plain("Price per hour", aggs.map((a) => usdPerHour(a?.cost_per_hour)), argmin(aggs.map((a) => a?.cost_per_hour)))}
            {plain("Latency p50", aggs.map((a) => ms(a?.latency_p50)), argmin(aggs.map((a) => a?.latency_p50)))}
            {plain("Latency p95", aggs.map((a) => ms(a?.latency_p95)), argmin(aggs.map((a) => a?.latency_p95)))}
            {CAPTION_METRICS.filter((m) => aggs.some((a) => metricValue(a, m) != null)).map((m) => {
              const vals = aggs.map((a) => metricValue(a, m));
              const best = m.better === "lower" ? argmin(vals) : argmin(vals.map((v) => (v == null ? null : -v)));
              return plain(m.label, vals.map((v) => (v == null ? "n/a" : m.fmt(v))), best);
            })}
            {plain(
              "Audio leaves your machine",
              models.map((m) => (run.providers.find((p) => p.id === m)?.uploads_audio ? "Yes" : "No")),
            )}
          </tbody>
        </table>
      </div>
    </Figure>
  );
}

type Focus = { kind: SliceKind; slice: string } | null;

function Tr({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <tr className="group">
      <th scope="row" className="sticky left-0 z-[1] border-b border-line bg-surface px-4 py-2.5 text-left text-[12.5px] font-normal text-ink-2 group-last:border-b-0 sm:px-5 sm:text-[13px]">
        {label}
      </th>
      {children}
    </tr>
  );
}
function Td({ children }: { children: ReactNode }) {
  return <td className="border-b border-line px-3 py-2.5 text-right group-last:border-b-0 last:pr-4 sm:last:pr-5">{children}</td>;
}

// ---------------------------------------------------------------- verdicts

function Verdicts({ run, models, tier, metric, tags, focus }: { run: Run; models: string[]; tier: Tier; metric: string; tags: string[]; focus: Focus }) {
  const pairs: [string, string][] = [];
  for (let i = 0; i < models.length; i++) for (let j = i + 1; j < models.length; j++) pairs.push([models[i]!, models[j]!]);
  const slices: { label: string; q: SliceQuery }[] = [
    { label: "All clips", q: { tier, metric, kind: "all", slice: "all" } },
    ...(focus ? [{ label: sliceLabel(focus.kind, focus.slice), q: { tier, metric, kind: focus.kind, slice: focus.slice } }] : []),
    ...(tags.length ? tags : tagsInRun(run, tier).slice(0, 3)).map((t) => ({ label: TAG_LABEL[t] ?? t, q: { tier, metric, kind: "tag" as const, slice: t } })),
  ];
  return (
    <Figure
      n={2}
      title="Paired verdicts"
      caption={tags.length || !tagsInRun(run, tier).length ? "Each cell is a paired bootstrap on the clips both models scored in that slice." : "Each cell is a paired bootstrap on the clips both models scored. Pick clip types above to change the columns."}
      source={`source: paired, tier=${tier}, metric=${metric}, 1000 resamples`}
      bodyClassName="p-0 sm:p-0"
    >
      <div className="overflow-x-auto">
        <table className="w-full min-w-[620px] border-separate border-spacing-0">
          <caption className="sr-only">Paired verdicts</caption>
          <thead>
            <tr>
              <th scope="col" className="sticky left-0 z-10 border-b border-line bg-surface px-4 py-2.5 text-left sm:px-5">
                <span className="label">Pair</span>
              </th>
              {slices.map((s) => (
                <th key={s.label} scope="col" className="border-b border-line bg-surface px-3 py-2.5 text-left font-normal last:pr-4 sm:last:pr-5">
                  <span className="label">{s.label}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {pairs.map(([a, b]) => (
              <tr key={a + b} className="group">
                <th scope="row" className="sticky left-0 z-[1] border-b border-line bg-surface px-4 py-3 text-left font-normal whitespace-nowrap group-last:border-b-0 sm:px-5">
                  <span className="inline-flex items-center gap-1.5 text-[13.5px] text-ink">
                    <SeriesDot color={providerColor(run, a)} /> {providerLabel(run, a)}
                    <span className="px-1 text-[12px] text-ink-3">vs</span>
                    <SeriesDot color={providerColor(run, b)} /> {providerLabel(run, b)}
                  </span>
                </th>
                {slices.map((s) => {
                  const t = pair(run, s.q, a, b);
                  const small = aggregatesFor(run, s.q).find((x) => x.provider === a)?.too_small;
                  return (
                    <td key={s.label} className="border-b border-line px-3 py-3 align-top group-last:border-b-0 last:pr-4 sm:last:pr-5">
                      {!t ? (
                        <span className="text-[12.5px] text-ink-3">Not tested</span>
                      ) : t.better ? (
                        <span className="grid gap-0.5">
                          <span className="inline-flex items-center gap-1.5 text-[13px] text-ink">
                            <SeriesDot color={providerColor(run, t.better)} size={7} />
                            {providerLabel(run, t.better)} by <span className="num">{pts(t.diff)}</span>
                          </span>
                          <span className="num text-[11px] text-ink-3">
                            {pValue(t.p)}, n={t.n}
                            {small ? ", small" : ""}
                          </span>
                        </span>
                      ) : (
                        <span className="grid gap-0.5">
                          <span className="text-[13px] text-ink-2">No detectable difference</span>
                          <span className="num text-[11px] text-ink-3">
                            {pValue(t.p)}, n={t.n}
                          </span>
                        </span>
                      )}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Figure>
  );
}

// ---------------------------------------------------------------- per type chart

function PerType({ run, models, tier, metric, tags }: { run: Run; models: string[]; tier: Tier; metric: string; tags: string[] }) {
  const show = tags.length ? tags : tagsInRun(run, tier);
  const data = show.map((t) => ({ tag: t, rows: models.map((m) => aggregatesFor(run, { tier, metric, kind: "tag", slice: t }).find((a) => a.provider === m)) }));
  const max = Math.max(0.01, ...data.flatMap((d) => d.rows.map((r) => r?.ci_high ?? 0))) * 1.05;
  const w = (v: number) => `${(v / max) * 100}%`;
  return (
    <Figure
      n={3}
      title="Error rate by clip type"
      caption="One group per clip type, one bar per model, with 95% intervals. Hatched bars have fewer than 15 clips."
      source={`source: aggregates, tier=${tier}, metric=${metric}, slice_kind=tag`}
    >
      {data.length === 0 && <EmptyState title="No clip types tagged in this run yet." body="Clip types are tagged when a person verifies a clip. This chart fills in once verified clips land." />}
      <div className="grid gap-6 sm:grid-cols-2 sm:gap-x-10">
        {data.map((d) => (
          <div key={d.tag}>
            <div className="mb-2 flex items-baseline justify-between gap-2 border-b border-line pb-1.5">
              <span className="text-[13.5px] font-medium text-ink">{TAG_LABEL[d.tag]}</span>
              <span className="num text-[11px] text-ink-3">{d.rows[0]?.n_clips ?? 0} clips</span>
            </div>
            <div className="grid gap-1.5">
              {d.rows.map((r, i) =>
                r ? (
                  <div key={models[i]} className="grid grid-cols-[72px_minmax(0,1fr)_48px] items-center gap-2">
                    <span className="truncate text-[12px] text-ink-2">{providerLabel(run, r.provider)}</span>
                    <span className="relative h-5" role="img" aria-label={`${providerLabel(run, r.provider)} on ${TAG_PHRASE[d.tag]}: ${pct(r.error_rate)}, interval ${pct(r.ci_low)} to ${pct(r.ci_high)}`}>
                      <span className="absolute inset-y-0 left-0 w-px bg-line-strong" />
                      <span className={cn("absolute top-[3px] bottom-[3px] left-0 rounded-r-[4px] transition-[width] duration-500", r.too_small && "hatch border border-line")} style={{ width: w(r.error_rate), background: r.too_small ? undefined : providerColor(run, r.provider) }} />
                      <span className="absolute top-1/2 h-px -translate-y-1/2 bg-ink" style={{ left: w(r.ci_low), width: `calc(${w(r.ci_high)} - ${w(r.ci_low)})` }}>
                        <span className="absolute -top-[3px] left-0 h-[7px] w-px bg-ink" />
                        <span className="absolute -top-[3px] right-0 h-[7px] w-px bg-ink" />
                      </span>
                    </span>
                    <span className="num text-right text-[12px] text-ink">{pct(r.error_rate)}</span>
                  </div>
                ) : null,
              )}
            </div>
          </div>
        ))}
      </div>
      {data.some((d) => d.rows.some((r) => r?.too_small)) && (
        <p className="mt-5 flex items-center gap-2 text-[11.5px] text-ink-3">
          <span className="hatch inline-block h-2.5 w-5 rounded-[2px] border border-line" /> Too small to conclude
          <Badge tone="outline" className="ml-1">
            n &lt; 15
          </Badge>
        </p>
      )}
    </Figure>
  );
}
