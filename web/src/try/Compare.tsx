import { Fragment, useMemo } from "react";
import { SeriesDot } from "@/components/ui/Badge";
import { pct } from "@/lib/format";
import type { TryResult } from "./api";
import { diffMode, diffText } from "./diff";
import { modelColor } from "./Results";

/**
 * Where each model's transcript departs from a chosen baseline model. Words only in this model are marked
 * (blue, "ins"); baseline words it lacks are struck through. Nobody here is "right": there is no reference.
 */
export function CompareView({ results, baseline, onBaseline, language }: { results: TryResult[]; baseline: string; onBaseline: (p: string) => void; language: string | null }) {
  const ok = results.filter((r) => !r.error);
  const base = ok.find((r) => r.provider === baseline) ?? ok[0];
  const mode = diffMode(language ?? base?.detected_language);
  const rows = useMemo(() => (base ? ok.filter((r) => r !== base).map((r) => ({ r, d: diffText(base.text, r.text, mode) })) : []), [ok, base, mode]);
  if (!base) return null;
  const joiner = mode === "char" ? "" : " ";
  const unit = mode === "char" ? "characters" : "words";
  return (
    <div className="grid gap-5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <label htmlFor="baseline" className="label">
          Baseline
        </label>
        <select
          id="baseline"
          value={base.provider}
          onChange={(e) => onBaseline(e.target.value)}
          className="h-8 min-w-0 max-w-full rounded-sm border border-line-strong bg-surface px-2 text-[13.5px] text-ink"
        >
          {ok.map((r) => (
            <option key={r.provider} value={r.provider}>
              {r.label}
            </option>
          ))}
        </select>
        <span className="text-[12.5px] text-ink-3">
          Comparing {unit}, case and punctuation ignored.
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[12.5px] text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span className="rounded-xs bg-ins-soft px-1 text-ins">abc</span> only in this model
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="text-ink-3 line-through decoration-ink-3">abc</span> only in the baseline
        </span>
      </div>
      {rows.length === 0 && <p className="text-[13.5px] text-ink-2">Only one model answered, so there is nothing to compare yet.</p>}
      <ul className="grid gap-4">
        {rows.map(({ r, d }) => (
          <li key={r.provider} className="rounded-md border border-line bg-surface">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-4 py-2.5">
              <span className="flex min-w-0 items-center gap-2 text-[13.5px] font-medium text-ink">
                <SeriesDot color={modelColor(r.provider)} />
                <span className="truncate">{r.label}</span>
              </span>
              <span className="num text-[12.5px] text-ink-2">
                {d.same === d.baseTokens && d.same === d.otherTokens ? `Same ${unit} as the baseline` : `${pct(d.disagreement)} of ${unit} differ`}
              </span>
            </div>
            <p lang={language ?? undefined} className="px-4 py-3 text-[14.5px] leading-[1.9] break-words text-ink">
              {d.ops.map((o, i) => (
                <Fragment key={i}>
                  {o.op === "same" ? (
                    <span>{o.text}</span>
                  ) : o.op === "ins" ? (
                    <ins className="rounded-xs bg-ins-soft px-0.5 text-ins no-underline">{o.text}</ins>
                  ) : (
                    <del className="text-ink-3 decoration-ink-3">{o.text}</del>
                  )}
                  {i < d.ops.length - 1 ? joiner : null}
                </Fragment>
              ))}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
