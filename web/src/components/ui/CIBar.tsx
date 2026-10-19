import { cn } from "@/lib/cn";
import { pct } from "@/lib/format";

/**
 * Confidence interval bar: a hairline track for the axis range, a band for the 95% CI and a dot for the
 * point estimate. Everything in one table cell. `max` is the shared axis maximum so rows are comparable.
 * Accessible name states the numbers in words.
 */
export function CIBar({
  value,
  low,
  high,
  max,
  color = "var(--ink)",
  muted = false,
  className,
  width = 140,
}: {
  value: number;
  low: number;
  high: number;
  max: number;
  color?: string;
  muted?: boolean;
  className?: string;
  width?: number;
}) {
  const x = (v: number) => `${Math.min(100, Math.max(0, (v / max) * 100))}%`;
  return (
    <span
      role="img"
      aria-label={`${pct(value)}, 95% interval ${pct(low)} to ${pct(high)}`}
      className={cn("relative inline-block h-3 align-middle", muted && "opacity-50", className)}
      style={{ width }}
    >
      <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line-strong" />
      <span
        className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full opacity-30"
        style={{ left: x(low), width: `calc(${x(high)} - ${x(low)})`, background: color }}
      />
      <span
        className="absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface"
        style={{ left: x(value), background: color }}
      />
    </span>
  );
}
