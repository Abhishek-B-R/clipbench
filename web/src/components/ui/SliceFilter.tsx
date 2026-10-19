import { ChevronDown } from "lucide-react";
import type { SliceKind } from "@/lib/types";
import { SLICE_KIND_LABEL, sliceLabel } from "@/lib/metrics";
import { Segmented } from "./Segmented";

/**
 * Slice filter (A4): which kind of slice (condition, category, clip type, ...) and which value. A Segmented for the
 * kind, a native select for the value, so it stays small on phones. Renders nothing when only "all" exists.
 * Pair with `readSlice()` to read and validate the URL params.
 */
export function SliceFilter({
  kinds,
  kind,
  slice,
  values,
  onChange,
  label = "Slice",
}: {
  kinds: SliceKind[];
  kind: SliceKind;
  slice: string;
  values: string[];
  onChange: (kind: SliceKind, slice: string | null) => void;
  label?: string;
}) {
  if (kinds.length <= 1) return null;
  return (
    <>
      <Segmented label={label} value={kind} onChange={(v) => onChange(v, null)} options={kinds.map((k) => ({ value: k, label: SLICE_KIND_LABEL[k] }))} />
      {kind !== "all" && values.length > 0 && (
        <label className="relative inline-flex">
          <span className="sr-only">{SLICE_KIND_LABEL[kind]}</span>
          <select
            value={slice}
            onChange={(e) => onChange(kind, e.target.value)}
            className="h-8 max-w-[220px] appearance-none truncate rounded-sm border border-line bg-surface pr-8 pl-2.5 text-[12.5px] text-ink hover:border-line-strong focus:border-ink focus:outline-none"
          >
            {values.map((v) => (
              <option key={v} value={v}>
                {sliceLabel(kind, v)}
              </option>
            ))}
          </select>
          <ChevronDown size={14} className="pointer-events-none absolute top-1/2 right-2.5 -translate-y-1/2 text-ink-3" aria-hidden />
        </label>
      )}
    </>
  );
}

/** Validate `?slice_kind=&slice=` against what exists; an unknown kind becomes "all", an unknown value the first one. */
export function readSlice(params: URLSearchParams, kinds: SliceKind[], valuesOf: (k: SliceKind) => string[]): { kind: SliceKind; slice: string; values: string[] } {
  const wanted = params.get("slice_kind") as SliceKind | null;
  const kind = wanted && kinds.includes(wanted) ? wanted : "all";
  const values = kind === "all" ? ["all"] : valuesOf(kind);
  const v = params.get("slice") ?? "";
  return { kind, slice: kind === "all" ? "all" : values.includes(v) ? v : (values[0] ?? "all"), values };
}

/** URL update for a SliceFilter change: kind and value together, the value dropped when the kind changes. */
export function sliceParams(params: URLSearchParams, kind: SliceKind, slice: string | null) {
  const p = new URLSearchParams(params);
  if (kind === "all") p.delete("slice_kind");
  else p.set("slice_kind", kind);
  if (slice == null || kind === "all") p.delete("slice");
  else p.set("slice", slice);
  return p;
}
