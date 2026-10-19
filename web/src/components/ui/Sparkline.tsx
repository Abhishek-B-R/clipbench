import { cn } from "@/lib/cn";

/**
 * Tiny line for a value across runs. 2px line, end dot with a surface ring, no axes.
 * `lowerIsBetter` only changes the accessible description.
 */
export function Sparkline({
  values,
  width = 72,
  height = 22,
  color = "var(--ink-2)",
  className,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  className?: string;
  label?: string;
}) {
  if (values.length === 0) return null;
  const pad = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => {
    const x = values.length === 1 ? width / 2 : pad + (i / (values.length - 1)) * (width - pad * 2);
    const y = pad + (1 - (v - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });
  const d = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = pts[pts.length - 1]!;
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={label ?? `Trend over ${values.length} runs`} className={cn("overflow-visible", className)}>
      <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" opacity={0.85} />
      <circle cx={last[0]} cy={last[1]} r={3.5} fill={color} stroke="var(--surface)" strokeWidth={2} />
    </svg>
  );
}
