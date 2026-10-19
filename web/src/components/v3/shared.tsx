import type { ReactNode } from "react";
import { Link } from "react-router";
import type { Run, SliceKind, Tier } from "@/lib/types";
import { providerColor, providerLabel } from "@/lib/analysis";
import { sliceLabel } from "@/lib/metrics";
import { inSlice, type BaseRow } from "@/lib/v3";
import { int } from "@/lib/format";
import { cn } from "@/lib/cn";
import { SeriesDot } from "@/components/ui/Badge";
import { Tooltip } from "@/components/ui/Tooltip";

/** Model name cell: series dot, label, model id; links to the model page. */
export function ModelCell({ run, id, sub }: { run: Run; id: string; sub?: ReactNode }) {
  return (
    <Link to={`/providers/${id}`} className="flex items-center gap-2.5 hover:underline">
      <SeriesDot color={providerColor(run, id)} />
      <span className="min-w-0">
        <span className="block font-medium text-ink">{providerLabel(run, id)}</span>
        {sub && <span className="block font-mono text-[11.5px] text-ink-3">{sub}</span>}
      </span>
    </Link>
  );
}

/** A value with its 95% interval and n underneath; n/a when null (never 0). Bold when best. */
export function ValueCell({ v, fmt, lo, hi, n, best, nLabel = "n" }: { v: number | null; fmt: (x: number) => string; lo?: number | null; hi?: number | null; n?: number | null; best?: boolean; nLabel?: string }) {
  if (v == null) return <span className="text-[12.5px] text-ink-3">n/a</span>;
  return (
    <span className="inline-flex flex-col items-end leading-tight">
      <span className={cn("num text-[13px]", best ? "font-semibold text-ink" : "text-ink-2")}>{fmt(v)}</span>
      {lo != null && hi != null ? (
        <span className="num text-[10.5px] text-ink-3">
          {fmt(lo)} to {fmt(hi)}
        </span>
      ) : n != null ? (
        <span className="num text-[10.5px] text-ink-3">
          {nLabel}={int(n)}
        </span>
      ) : null}
    </span>
  );
}

/** Header label with a help tooltip (keyboard reachable). */
export function Help({ label, help }: { label: string; help: string }) {
  return (
    <Tooltip content={help}>
      <span tabIndex={0} className="cursor-help">
        {label}
      </span>
    </Tooltip>
  );
}

/** Provider id with the best value of `get` (lower or higher is better); ignores nulls and too-small rows. */
export function bestBy<T extends { provider: string; too_small?: boolean }>(rows: T[], get: (r: T) => number | null, better: "lower" | "higher") {
  let best: T | undefined;
  for (const r of rows) {
    const v = get(r);
    if (v == null || r.too_small) continue;
    const bv = best ? get(best)! : null;
    if (bv == null || (better === "lower" ? v < bv : v > bv)) best = r;
  }
  return best?.provider;
}

/**
 * Rows of a v3 section for the page's tier and slice. Sections measured on all clips only fall back to "all" and
 * say so (`fellBack`), rather than showing an empty table for a condition they never sliced by.
 */
export function sectionSlice<T extends BaseRow>(rows: T[], tier: Tier, kind: SliceKind, slice: string) {
  const exact = inSlice(rows, tier, kind, slice);
  if (exact.length || kind === "all") return { rows: exact, fellBack: false, kind, slice };
  return { rows: inSlice(rows, tier, "all", "all"), fellBack: true, kind: "all" as SliceKind, slice: "all" };
}

export function FellBack({ kind, slice }: { kind: SliceKind; slice: string }) {
  return (
    <p className="border-b border-line bg-sunken/60 px-4 py-2 text-[12.5px] text-ink-2 sm:px-5">
      Not sliced by {sliceLabel(kind, slice).toLowerCase()} in this run, so this shows all clips.
    </p>
  );
}

/** Hairline-track bar for a 0..1 share, ink coloured (no meaning attached to the colour). */
export function ShareBar({ v, color = "var(--ink-2)", width = 88 }: { v: number | null; color?: string; width?: number }) {
  if (v == null) return null;
  return (
    <span aria-hidden className="relative inline-block h-1.5 rounded-full bg-sunken ring-1 ring-line ring-inset align-middle" style={{ width }}>
      <span className="absolute inset-y-0 left-0 rounded-full" style={{ width: `${Math.min(1, Math.max(0, v)) * 100}%`, background: color }} />
    </span>
  );
}
