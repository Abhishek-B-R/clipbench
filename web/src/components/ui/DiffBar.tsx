import { cn } from "@/lib/cn";
import { Badge } from "./Badge";
import { signedPts } from "@/lib/format";

/**
 * Paired difference on a zero-centred axis: hairline track, zero tick, 95% interval band and a dot for the
 * observed difference (x minus y, error rate). Left of zero = x makes fewer errors. `span` is the shared half
 * width of the axis so rows are comparable. The dot is ink when the interval excludes zero, hollow otherwise.
 */
export function DiffBar({
  diff,
  low,
  high,
  span,
  width = 160,
  className,
  label,
}: {
  diff: number;
  low: number;
  high: number;
  span: number;
  width?: number;
  className?: string;
  label?: string;
}) {
  const x = (v: number) => `${Math.min(100, Math.max(0, 50 + (v / span) * 50))}%`;
  const clear = low > 0 || high < 0;
  const better = high < 0;
  return (
    <span
      role="img"
      aria-label={label ?? `Difference ${signedPts(diff)}, 95% interval ${signedPts(low)} to ${signedPts(high)}`}
      className={cn("relative inline-block h-4 align-middle", className)}
      style={{ width }}
    >
      <span className="absolute top-1/2 right-0 left-0 h-px -translate-y-1/2 bg-line" />
      <span className="absolute top-0 bottom-0 left-1/2 w-px bg-line-strong" />
      <span
        className={cn("absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full", clear ? (better ? "bg-good/35" : "bg-signal/35") : "bg-ink-3/30")}
        style={{ left: x(low), width: `calc(${x(high)} - ${x(low)})` }}
      />
      <span
        className={cn(
          "absolute top-1/2 size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface",
          clear ? (better ? "bg-good" : "bg-signal") : "border-[1.5px] border-ink-3 bg-surface",
        )}
        style={{ left: x(diff) }}
      />
    </span>
  );
}

/** Paired-test verdict from `id`'s side. Words, not colour alone. */
export function Verdict({ better, id }: { better: string | null; id: string }) {
  if (better === id) return <Badge tone="good">Fewer errors</Badge>;
  if (better) return <Badge tone="signal">More errors</Badge>;
  return <Badge>No detectable difference</Badge>;
}
