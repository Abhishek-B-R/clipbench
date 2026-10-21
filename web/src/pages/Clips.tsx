import { useMemo, useState } from "react";
import { useSearchParams } from "react-router";
import { ArrowUpRight, Search } from "lucide-react";
import type { Clip, Run } from "@/lib/types";
import { useRun } from "@/lib/data";
import { LANGUAGE_NAME, TAG_LABEL, isPublished, median, perClipMatrix, pickTier, primaryMetric, providerColor, providerLabel, providerOrder, tagsInRun, youtubeUrl } from "@/lib/analysis";
import { duration, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { CATEGORIES } from "@/lib/config";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Chip } from "@/components/ui/Chip";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { DataTable, type Column } from "@/components/ui/Table";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { heatStep } from "@/components/ui/HeatCell";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { TierControl, TierNote } from "@/components/ui/TierControl";

const PAGE = 40;
/** Top of the per-clip colour scale (error rate); everything above it is the darkest step. */
const HEAT_MAX = 0.4;
type Sort = "hard" | "easy" | "long" | "short";
type Row = Clip & { errs: Map<string, number> | undefined; med: number };

/**
 * The clip set by id: tags, duration, channel, a per-model error strip, and a link to the source video
 * (windows open at their start time). Caption text is never shown here.
 */
export default function Clips() {
  const state = useRun();
  const run = state.data?.run;
  const [params, setParams] = useSearchParams();
  const tier = pickTier(run, params.get("tier"));
  const tags = (params.get("tags") ?? "").split(",").filter(Boolean);
  const kind = (params.get("kind") as "all" | "short" | "window") || "all";
  const sort = (params.get("sort") as Sort) || "hard";
  const [q, setQ] = useState(params.get("q") ?? "");
  const [limit, setLimit] = useState(PAGE);
  const set = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v == null || v === "" ? p.delete(k) : p.set(k, v));
    setParams(p, { replace: true });
    setLimit(PAGE);
  };

  const rows = useMemo<Row[]>(() => {
    if (!run) return [];
    const m = perClipMatrix(run, tier, primaryMetric(run).name);
    const needle = q.trim().toLowerCase();
    const out = run.clips
      .filter((c) => c.tier === tier)
      .filter((c) => tags.every((t) => c.tags.includes(t)))
      .filter((c) => kind === "all" || (c.kind ?? "short") === kind)
      .filter((c) => !needle || c.video_id.toLowerCase().includes(needle) || c.channel.toLowerCase().includes(needle))
      .map((c) => {
        const errs = m.get(c.video_id);
        return { ...c, errs, med: errs ? median([...errs.values()]) : NaN };
      });
    const by: Record<Sort, (a: Row, b: Row) => number> = {
      hard: (a, b) => (b.med || 0) - (a.med || 0),
      easy: (a, b) => (a.med || 0) - (b.med || 0),
      long: (a, b) => (b.duration_s ?? 0) - (a.duration_s ?? 0),
      short: (a, b) => (a.duration_s ?? 0) - (b.duration_s ?? 0),
    };
    return out.sort(by[sort]);
  }, [run, tier, tags.join(), kind, sort, q]); // eslint-disable-line react-hooks/exhaustive-deps

  const allTier = run ? run.clips.filter((c) => c.tier === tier) : [];
  const hasWindows = allTier.some((c) => c.kind === "window");

  return (
    <>
      <PageHeader
        eyebrow={run ? `Run ${run.run_id} / ${allTier.length} clips` : "Clips"}
        title="Clip set"
        description="Every clip in the run by its YouTube id, with its tags and how hard each model found it. Captions are never published; anyone can rebuild the set from these ids."
      >
        <TierControl run={run} value={tier} onChange={(v) => set({ tier: v })} />
        {hasWindows && (
          <Segmented
            label="Clip kind"
            value={kind}
            onChange={(v) => set({ kind: v === "all" ? null : v })}
            options={[
              { value: "all", label: "All" },
              { value: "short", label: "Shorts" },
              { value: "window", label: "Windows" },
            ]}
          />
        )}
      </PageHeader>
      <div className="frame grid gap-6 py-8">
        {state.status === "error" && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {!run && state.status !== "error" && <Skeleton className="h-[600px] w-full" />}
        {run && (
          <>
            <TierNote tier={tier} />
            {!isPublished(run) && (
              <p className="rounded-md border border-line bg-sunken/60 px-4 py-2.5 text-[13px] text-ink-2">
                <span className="font-medium text-ink">Pipeline checks, not results.</span> {LANGUAGE_NAME[run.language] ?? run.language} is below the publication gate, so these per-clip scores are shown for transparency and are not a ranking.
              </p>
            )}
            <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
              <label className="relative w-full sm:w-72">
                <span className="sr-only">Search by clip id or channel</span>
                <Search size={14} className="pointer-events-none absolute top-1/2 left-3 -translate-y-1/2 text-ink-3" aria-hidden />
                <Input
                  value={q}
                  onChange={(e) => {
                    setQ(e.target.value);
                    setLimit(PAGE);
                  }}
                  placeholder="Clip id or channel"
                  className="h-9 pl-8"
                />
              </label>
              <div className="flex flex-wrap gap-1.5" role="group" aria-label="Filter by tag">
                {tagsInRun(run, tier).map((t) => (
                  <Chip key={t} pressed={tags.includes(t)} onClick={() => set({ tags: (tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t]).join(",") })}>
                    {TAG_LABEL[t]}
                  </Chip>
                ))}
              </div>
            </div>
            <Figure
              n={1}
              title={`${rows.length} ${rows.length === 1 ? "clip" : "clips"}${tags.length ? `, tagged ${tags.map((t) => TAG_LABEL[t]?.toLowerCase()).join(" and ")}` : ""}`}
              caption="Each square is one model's error rate on that clip, in model order. More colour means more errors."
              controls={
                <Segmented
                  label="Sort"
                  value={sort}
                  onChange={(v) => set({ sort: v === "hard" ? null : v })}
                  options={[
                    { value: "hard", label: "Hardest" },
                    { value: "easy", label: "Easiest" },
                    { value: "long", label: "Longest" },
                    { value: "short", label: "Shortest" },
                  ]}
                />
              }
              source={
                <span className="flex flex-wrap items-center justify-between gap-3">
                  <span>source: clips + per_clip, run={run.run_id}, tier={tier}, metric={primaryMetric(run).name}</span>
                  <span className="flex items-center gap-2">
                    <span>{pct(0, 0)}</span>
                    <span className="flex overflow-hidden rounded-xs">
                      {[0, 1, 2, 3, 4].map((k) => (
                        <span key={k} className="h-2.5 w-5" style={{ background: `var(--heat-${k})` }} />
                      ))}
                    </span>
                    <span>{pct(HEAT_MAX, 0)}+</span>
                  </span>
                </span>
              }
              bodyClassName="p-0 sm:p-0"
            >
              {rows.length === 0 ? (
                <div className="p-4 sm:p-5">
                  <EmptyState
                    title="No clips match."
                    body="Try fewer tags or clear the search."
                    action={
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          setQ("");
                          set({ tags: null, kind: null });
                        }}
                      >
                        Clear filters
                      </Button>
                    }
                  />
                </div>
              ) : (
                <>
                  <ClipTable run={run} rows={rows.slice(0, limit)} />
                  {rows.length > limit && (
                    <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-5">
                      <span className="text-[12.5px] text-ink-3">
                        Showing {limit} of {rows.length}
                      </span>
                      <Button variant="secondary" size="sm" onClick={() => setLimit((l) => l + PAGE)}>
                        Show {Math.min(PAGE, rows.length - limit)} more
                      </Button>
                    </div>
                  )}
                </>
              )}
            </Figure>
          </>
        )}
      </div>
    </>
  );
}

function ClipTable({ run, rows }: { run: Run; rows: Row[] }) {
  const order = providerOrder(run);
  const columns: Column<Row>[] = [
    {
      key: "id",
      header: "Clip",
      sticky: true,
      cell: (c) => (
        <span className="flex items-center gap-2">
          <a href={youtubeUrl(c)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[12.5px] text-ink hover:underline" aria-label={`Open ${c.video_id} on YouTube${c.kind === "window" ? " at the window start" : ""}`}>
            {c.video_id}
            <ArrowUpRight size={12} className="text-ink-3" />
          </a>
        </span>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      cell: (c) => (c.kind === "window" ? <Badge tone="outline">Window {c.start_s != null ? duration(c.start_s) : ""}</Badge> : <Badge>Short</Badge>),
      headerClassName: "hidden md:table-cell",
      className: "hidden md:table-cell",
    },
    { key: "dur", header: "Length", align: "right", cell: (c) => <span className="num text-ink-2">{duration(c.duration_s)}</span> },
    {
      key: "heat",
      header: (
        <span className="inline-flex items-center gap-[3px]" aria-label="Error by model">
          {order.map((p) => (
            <span key={p} className="grid w-[18px] place-items-center" title={providerLabel(run, p)}>
              <SeriesDot color={providerColor(run, p)} size={6} />
            </span>
          ))}
        </span>
      ),
      cell: (c) => <Strip run={run} order={order} errs={c.errs} />,
    },
    { key: "med", header: "Median", align: "right", cell: (c) => <span className="num text-ink-2">{Number.isNaN(c.med) ? "n/a" : pct(c.med)}</span> },
    {
      key: "tags",
      header: "Type and tags",
      cell: (c) =>
        c.tags.filter((t) => TAG_LABEL[t]).length || (c.category && CATEGORIES[c.category]) ? (
          <span className="flex max-w-[340px] flex-wrap gap-1">
            {c.category && CATEGORIES[c.category] && <Badge tone="outline" mono={false}>{CATEGORIES[c.category]}</Badge>}
            {c.tags
              .filter((t) => TAG_LABEL[t])
              .map((t) => (
                <Badge key={t} mono={false}>
                  {TAG_LABEL[t]}
                </Badge>
              ))}
          </span>
        ) : (
          <span className="text-[12px] text-ink-3">No tags</span>
        ),
      className: "hidden whitespace-normal md:table-cell",
      headerClassName: "hidden md:table-cell",
    },
    { key: "ch", header: "Channel", cell: (c) => <span className="block max-w-[220px] truncate text-[13px] text-ink-2">{c.channel}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(c) => c.video_id} caption="Clips" dense />;
}

function Strip({ run, order, errs }: { run: Run; order: string[]; errs?: Map<string, number> }) {
  return (
    <span className="inline-flex gap-[3px]">
      {order.map((p) => {
        const v = errs?.get(p);
        const step = v == null ? -1 : heatStep(v, [0, HEAT_MAX]);
        return (
          <span
            key={p}
            role="img"
            aria-label={`${providerLabel(run, p)}: ${v == null ? "no result" : pct(v)}`}
            title={`${providerLabel(run, p)}: ${v == null ? "no result" : pct(v)}`}
            className={cn("size-[18px] rounded-[3px]", v == null && "hatch border border-line")}
            style={v == null ? undefined : { background: `var(--heat-${step})`, boxShadow: step === 0 ? "inset 0 0 0 1px var(--line)" : undefined }}
          />
        );
      })}
    </span>
  );
}
