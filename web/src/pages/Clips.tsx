import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { ArrowUpRight, Flag, LayoutGrid, Rows3, Search } from "lucide-react";
import type { Run } from "@/lib/types";
import { useRun } from "@/lib/data";
import { TAG_LABEL, isPublished, pickTier, primaryMetric, providerLabel, tagsInRun, youtubeUrl } from "@/lib/analysis";
import { clipScores, earlyLook, flaggedClips, thumbUrl, type ClipScores } from "@/lib/early";
import { duration, int, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { CATEGORIES } from "@/lib/config";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Segmented } from "@/components/ui/Segmented";
import { Chip } from "@/components/ui/Chip";
import { Badge } from "@/components/ui/Badge";
import { DataTable, type Column } from "@/components/ui/Table";
import { Skeleton } from "@/components/ui/Skeleton";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { TierControl, TierNote } from "@/components/ui/TierControl";
import { ClipHeatLegend, HeatStrip } from "@/components/early/EarlyCharts";

const PAGE = { cards: 24, table: 40 };
type Sort = "hard" | "easy" | "long" | "short";
type View = "cards" | "table";

/**
 * The clip set by id: thumbnail, channel, category, duration, a per-system error strip and which systems struggled,
 * with references every system failed marked "flagged for re-review". Links open the source video (windows at their
 * start). Caption text is never shown here.
 */
export default function Clips() {
  const state = useRun();
  const run = state.data?.run;
  const [params, setParams] = useSearchParams();
  const tier = pickTier(run, params.get("tier"));
  const tags = (params.get("tags") ?? "").split(",").filter(Boolean);
  const kind = (params.get("kind") as "all" | "short" | "window") || "all";
  const sort = (params.get("sort") as Sort) || "hard";
  const view: View = params.get("view") === "table" ? "table" : "cards";
  const onlyFlagged = params.get("flagged") === "1";
  const cat = params.get("category");
  const [q, setQ] = useState(params.get("q") ?? "");
  const [limit, setLimit] = useState(view === "table" ? PAGE.table : PAGE.cards);
  const set = (patch: Record<string, string | null>) => {
    const p = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v == null || v === "" ? p.delete(k) : p.set(k, v));
    setParams(p, { replace: true });
    setLimit((patch.view ?? view) === "table" ? PAGE.table : PAGE.cards);
  };

  const all = useMemo(() => (run ? clipScores(run, tier, primaryMetric(run).name) : []), [run, tier]);
  const flagged = run ? flaggedClips(run, tier) : null;
  const order = run ? earlyLook(run, tier).rows.map((r) => r.provider) : [];
  const rows = useMemo<ClipScores[]>(() => {
    const needle = q.trim().toLowerCase();
    const out = all
      .filter((c) => tags.every((t) => c.clip.tags.includes(t)))
      .filter((c) => kind === "all" || (c.clip.kind ?? "short") === kind)
      .filter((c) => !onlyFlagged || c.flagged)
      .filter((c) => !cat || c.clip.category === cat)
      .filter((c) => !needle || c.clip.video_id.toLowerCase().includes(needle) || c.clip.channel.toLowerCase().includes(needle));
    const by: Record<Sort, (a: ClipScores, b: ClipScores) => number> = {
      hard: (a, b) => (b.med || 0) - (a.med || 0),
      easy: (a, b) => (a.med || 0) - (b.med || 0),
      long: (a, b) => (b.clip.duration_s ?? 0) - (a.clip.duration_s ?? 0),
      short: (a, b) => (a.clip.duration_s ?? 0) - (b.clip.duration_s ?? 0),
    };
    return [...out].sort(by[sort]);
  }, [all, tags.join(), kind, sort, q, onlyFlagged, cat]); // eslint-disable-line react-hooks/exhaustive-deps

  const hasWindows = all.some((c) => c.clip.kind === "window");
  const cats = [...new Set(all.map((c) => c.clip.category).filter((x): x is string => Boolean(x && CATEGORIES[x])))];
  const channels = new Set(all.map((c) => c.clip.channel)).size;

  return (
    <>
      <PageHeader
        eyebrow={run ? `Run ${run.run_id} / ${all.length} clips / ${channels} channels` : "Clips"}
        title="Clip set"
        description="Every clip in the run, by its public YouTube id: what it is, how long it runs, and which systems struggled on it. Captions are never published; anyone can rebuild the set from these ids."
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
        <Segmented
          label="View"
          value={view}
          onChange={(v) => set({ view: v === "cards" ? null : v })}
          options={[
            { value: "cards", label: <LayoutGrid size={14} aria-hidden />, aria: "Cards" },
            { value: "table", label: <Rows3 size={14} aria-hidden />, aria: "Table" },
          ]}
        />
      </PageHeader>
      <div className="frame grid gap-6 py-8">
        {state.status === "error" && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {!run && state.status !== "error" && <Skeleton className="h-[600px] w-full" />}
        {run && (
          <>
            <TierNote tier={tier} />
            {!isPublished(run) && (
              <p className="rounded-md border border-line bg-sunken/60 px-4 py-2.5 text-[13px] text-ink-2">
                <span className="font-medium text-ink">Early look.</span> Per-clip scores against creator captions, shown in full for transparency; they are not a ranking.{" "}
                <Link to="/early" className="text-ink underline decoration-line-strong underline-offset-4 hover:decoration-ink">
                  Early look
                </Link>
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
                    setLimit(view === "table" ? PAGE.table : PAGE.cards);
                  }}
                  placeholder="Clip id or channel"
                  className="h-9 pl-8"
                />
              </label>
              <div className="flex min-w-0 flex-wrap gap-1.5" role="group" aria-label="Filters">
                {flagged && flagged.ids.size > 0 && (
                  <Chip pressed={onlyFlagged} onClick={() => set({ flagged: onlyFlagged ? null : "1" })} dot={<Flag size={12} className="text-signal" aria-hidden />}>
                    Flagged for re-review <span className="num text-ink-3">{int(all.filter((c) => c.flagged).length)}</span>
                  </Chip>
                )}
                {cats.map((c) => (
                  <Chip key={c} pressed={cat === c} onClick={() => set({ category: cat === c ? null : c })}>
                    {CATEGORIES[c]}
                  </Chip>
                ))}
                {tagsInRun(run, tier).map((t) => (
                  <Chip key={t} pressed={tags.includes(t)} onClick={() => set({ tags: (tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t]).join(",") })}>
                    {TAG_LABEL[t]}
                  </Chip>
                ))}
              </div>
            </div>
            <Figure
              n={1}
              title={`${rows.length} ${rows.length === 1 ? "clip" : "clips"}${onlyFlagged ? ", flagged for re-review" : ""}${cat ? `, ${CATEGORIES[cat]?.toLowerCase()}` : ""}${tags.length ? `, tagged ${tags.map((t) => TAG_LABEL[t]?.toLowerCase()).join(" and ")}` : ""}`}
              caption={
                flagged && onlyFlagged
                  ? `Every system scored above ${pct(flagged.threshold, 0)} error on these clips, which usually means the reference is wrong. They go back to the review queue.`
                  : "Each square is one system's error rate on that clip, in the early-look order. More colour means more errors."
              }
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
                  <span>
                    source: clips + per_clip, run={run.run_id}, tier={tier}, metric={primaryMetric(run).name}
                    {flagged ? `, flags=${flagged.source === "export" ? "errors.all_fail" : "per_clip"}` : ""}
                  </span>
                  <ClipHeatLegend />
                </span>
              }
              bodyClassName="p-0 sm:p-0"
            >
              <StripKey run={run} order={order} />
              {rows.length === 0 ? (
                <div className="p-4 sm:p-5">
                  <EmptyState
                    title="No clips match."
                    body="Try fewer filters or clear the search."
                    action={
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => {
                          setQ("");
                          set({ tags: null, kind: null, flagged: null, category: null, q: null });
                        }}
                      >
                        Clear filters
                      </Button>
                    }
                  />
                </div>
              ) : (
                <>
                  {view === "cards" ? <ClipCards run={run} order={order} rows={rows.slice(0, limit)} /> : <ClipTable run={run} order={order} rows={rows.slice(0, limit)} />}
                  {rows.length > limit && (
                    <div className="flex items-center justify-between gap-3 border-t border-line px-4 py-3 sm:px-5">
                      <span className="text-[12.5px] text-ink-3">
                        Showing {limit} of {rows.length}
                      </span>
                      <Button variant="secondary" size="sm" onClick={() => setLimit((l) => l + (view === "table" ? PAGE.table : PAGE.cards))}>
                        Show {Math.min(view === "table" ? PAGE.table : PAGE.cards, rows.length - limit)} more
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

/** Which square is which system: the early-look order, printed once above the clips. */
function StripKey({ run, order }: { run: Run; order: string[] }) {
  return (
    <details className="group border-b border-line px-4 py-2.5 text-[12.5px] text-ink-3 sm:px-5">
      <summary className="cursor-pointer list-none text-ink-2 select-none hover:text-ink">
        <span className="underline decoration-line-strong underline-offset-4">Square order</span>: the {order.length} systems, lowest error first. Hover a square for its value.
      </summary>
      <ol className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
        {order.map((p, i) => (
          <li key={p} className="inline-flex items-center gap-1.5">
            <span className="num text-[11px] text-ink-3">{i + 1}</span>
            <Link to={`/providers/${encodeURIComponent(p)}`} className="text-ink-2 hover:text-ink hover:underline">
              {providerLabel(run, p)}
            </Link>
          </li>
        ))}
      </ol>
    </details>
  );
}

function struggled(run: Run, c: ClipScores) {
  const bad = c.worst.filter((w) => w.err >= 0.3);
  const top = (bad.length ? bad : c.worst).slice(0, 2);
  return top.map((w) => `${providerLabel(run, w.provider)} ${pct(w.err, 0)}`).join(", ");
}

function ClipCards({ run, order, rows }: { run: Run; order: string[]; rows: ClipScores[] }) {
  return (
    <ul className="grid grid-cols-1 gap-px bg-line sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
      {rows.map((c) => (
        <li key={c.clip.video_id} className="flex min-w-0 flex-col bg-surface">
          <a
            href={youtubeUrl(c.clip)}
            target="_blank"
            rel="noreferrer"
            className="group relative block aspect-video overflow-hidden bg-sunken"
            aria-label={`Open ${c.clip.video_id} on YouTube${c.clip.kind === "window" ? " at the window start" : ""}`}
          >
            <img src={thumbUrl(c.clip.video_id)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" className="size-full object-cover transition-transform duration-300 group-hover:scale-[1.03]" />
            <span className="absolute top-2 left-2 flex gap-1">
              {c.clip.kind === "window" ? (
                <span className="rounded-xs bg-ink/80 px-1.5 py-0.5 font-mono text-[10.5px] text-paper">from {duration(c.clip.start_s)}</span>
              ) : (
                <span className="rounded-xs bg-ink/80 px-1.5 py-0.5 font-mono text-[10.5px] text-paper uppercase">Short</span>
              )}
            </span>
            <span className="absolute right-2 bottom-2 inline-flex items-center gap-1 rounded-xs bg-ink/80 px-1.5 py-0.5 font-mono text-[10.5px] text-paper">
              {duration(c.clip.duration_s)} <ArrowUpRight size={11} aria-hidden />
            </span>
          </a>
          <div className="flex flex-1 flex-col gap-2 px-3.5 pt-3 pb-3.5">
            <div className="flex min-w-0 items-start justify-between gap-2">
              <span className="min-w-0">
                <span className="block truncate text-[13.5px] font-medium text-ink">{c.clip.channel}</span>
                <span className="block truncate font-mono text-[11px] text-ink-3">{c.clip.video_id}</span>
              </span>
              <span className="shrink-0 text-right">
                <span className={cn("num block text-[15px] font-medium", c.med >= 0.4 ? "text-signal-ink" : "text-ink")}>{Number.isNaN(c.med) ? "n/a" : pct(c.med, 0)}</span>
                <span className="block text-[10.5px] text-ink-3">median</span>
              </span>
            </div>
            <HeatStrip run={run} order={order} errs={c.errs} size={14} />
            {c.flagged ? (
              <span className="mt-auto inline-flex items-center gap-1.5 self-start rounded-xs bg-signal-soft px-1.5 py-0.5 text-[12px] font-medium text-signal-ink">
                <Flag size={11} aria-hidden /> Flagged for re-review
              </span>
            ) : c.worst.length ? (
              <span className="mt-auto text-[12px] text-ink-3">
                Hardest for <span className="text-ink-2">{struggled(run, c)}</span>
              </span>
            ) : null}
            {c.clip.category && CATEGORIES[c.clip.category] && (
              <span className="flex flex-wrap gap-1">
                <Badge tone="outline" mono={false}>
                  {CATEGORIES[c.clip.category]}
                </Badge>
                {c.clip.tags
                  .filter((t) => TAG_LABEL[t])
                  .map((t) => (
                    <Badge key={t} mono={false}>
                      {TAG_LABEL[t]}
                    </Badge>
                  ))}
              </span>
            )}
          </div>
        </li>
      ))}
    </ul>
  );
}

function ClipTable({ run, order, rows }: { run: Run; order: string[]; rows: ClipScores[] }) {
  const columns: Column<ClipScores>[] = [
    {
      key: "id",
      header: "Clip",
      sticky: true,
      cell: (c) => (
        <span className="flex items-center gap-2.5">
          <img src={thumbUrl(c.clip.video_id)} alt="" loading="lazy" decoding="async" referrerPolicy="no-referrer" className="hidden h-7 w-12 shrink-0 rounded-[3px] bg-sunken object-cover sm:block" />
          <a href={youtubeUrl(c.clip)} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 font-mono text-[12.5px] text-ink hover:underline" aria-label={`Open ${c.clip.video_id} on YouTube${c.clip.kind === "window" ? " at the window start" : ""}`}>
            {c.clip.video_id}
            <ArrowUpRight size={12} className="text-ink-3" />
          </a>
          {c.flagged && (
            <span title="Flagged for re-review" className="inline-flex text-signal">
              <Flag size={12} aria-label="Flagged for re-review" />
            </span>
          )}
        </span>
      ),
    },
    {
      key: "kind",
      header: "Kind",
      cell: (c) => (c.clip.kind === "window" ? <Badge tone="outline">Window {c.clip.start_s != null ? duration(c.clip.start_s) : ""}</Badge> : <Badge>Short</Badge>),
      headerClassName: "hidden md:table-cell",
      className: "hidden md:table-cell",
    },
    { key: "dur", header: "Length", align: "right", cell: (c) => <span className="num text-ink-2">{duration(c.clip.duration_s)}</span> },
    { key: "heat", header: "Error by system", cell: (c) => <HeatStrip run={run} order={order} errs={c.errs} size={16} /> },
    { key: "med", header: "Median", align: "right", cell: (c) => <span className="num text-ink-2">{Number.isNaN(c.med) ? "n/a" : pct(c.med)}</span> },
    {
      key: "cat",
      header: "Category",
      cell: (c) => (c.clip.category && CATEGORIES[c.clip.category] ? <Badge tone="outline" mono={false}>{CATEGORIES[c.clip.category]}</Badge> : <span className="text-[12px] text-ink-3">n/a</span>),
      className: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
    },
    { key: "ch", header: "Channel", cell: (c) => <span className="block max-w-[220px] truncate text-[13px] text-ink-2">{c.clip.channel}</span>, headerClassName: "hidden lg:table-cell", className: "hidden lg:table-cell" },
  ];
  return <DataTable columns={columns} rows={rows} rowKey={(c) => c.clip.video_id} caption="Clips" dense />;
}
