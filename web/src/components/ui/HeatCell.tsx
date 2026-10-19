import { cn } from "@/lib/cn";
import { pct } from "@/lib/format";

/** Map an error rate to one of five ramp steps. `domain` is [min, max] across the visible matrix. */
export function heatStep(v: number, [lo, hi]: [number, number]) {
  const t = hi > lo ? (v - lo) / (hi - lo) : 0;
  return Math.max(0, Math.min(4, Math.floor(t * 5)));
}

/**
 * Heatmap cell: a real <td> with the value printed. One-hue vermilion ramp (more colour = more errors).
 * Too-small slices are hatched grey with "n<15", never coloured. `best` draws an inset ring (lowest in column).
 */
export function HeatCell({
  value,
  n,
  domain,
  tooSmall,
  best,
  label,
  className,
}: {
  value: number | null;
  n?: number;
  domain: [number, number];
  tooSmall?: boolean;
  best?: boolean;
  label: string;
  className?: string;
}) {
  if (value == null) {
    return (
      <td className={cn("p-0.5", className)}>
        <div className="grid h-11 place-items-center rounded-xs text-[11px] text-ink-3" aria-label={`${label}: no data`}>
          n/a
        </div>
      </td>
    );
  }
  if (tooSmall) {
    return (
      <td className={cn("p-0.5", className)}>
        <div
          className="hatch grid h-11 place-items-center rounded-xs border border-line text-center"
          aria-label={`${label}: ${pct(value)}, too small to conclude (${n} clips)`}
          title={`Too small to conclude (${n} clips)`}
        >
          <span className="num text-[12px] text-ink-3">{pct(value)}</span>
        </div>
      </td>
    );
  }
  const step = heatStep(value, domain);
  return (
    <td className={cn("p-0.5", className)}>
      <div
        className={cn("relative grid h-11 place-items-center rounded-xs transition-transform duration-150", best && "ring-[1.5px] ring-ink ring-inset")}
        style={{ background: `var(--heat-${step})`, color: step >= 3 ? "var(--heat-ink-high)" : "var(--heat-ink-low)" }}
        aria-label={`${label}: ${pct(value)}${n ? `, ${n} clips` : ""}${best ? ", lowest in this column" : ""}`}
      >
        <span className="num text-[12.5px] font-medium">{pct(value)}</span>
      </div>
    </td>
  );
}

/** Legend for the ramp, with the domain ends printed. */
export function HeatLegend({ domain }: { domain: [number, number] }) {
  return (
    <div className="flex items-center gap-2 text-[11px] text-ink-3">
      <span className="num">{pct(domain[0], 0)}</span>
      <div className="flex overflow-hidden rounded-xs">
        {[0, 1, 2, 3, 4].map((s) => (
          <span key={s} className="h-2.5 w-6" style={{ background: `var(--heat-${s})` }} />
        ))}
      </div>
      <span className="num">{pct(domain[1], 0)}</span>
      <span className="ml-2 inline-flex items-center gap-1.5">
        <span className="hatch inline-block h-2.5 w-4 rounded-[2px] border border-line" /> too few clips
      </span>
    </div>
  );
}
