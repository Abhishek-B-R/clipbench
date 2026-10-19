import { useMemo, type ReactNode } from "react";
import { useWidth } from "@/lib/useWidth";
import { extent } from "@/lib/scale";
import { cn } from "@/lib/cn";

export interface LinePoint {
  x: number;
  y: number;
  lo?: number | null;
  hi?: number | null;
}

export interface LineSeries {
  id: string;
  label: string;
  color: string;
  points: LinePoint[];
  /** Fainter line, no end label (for context series). */
  muted?: boolean;
}

export interface LineMarker {
  series: string;
  x: number;
  label: string;
}

export interface VLine {
  x: number;
  label: string;
  /** "signal" for events (drift), "ink" for reference lines. */
  tone?: "signal" | "ink";
}

/**
 * Multi-series line chart with optional 95% bands, ring markers on a series (the "falls apart at" point), vertical
 * event lines (drift events) and direct end labels (nudged apart), plus an sr-only table with every value.
 * Design system marks: 2px lines, 8px dots with a 2px surface ring, hairline gridlines, one y axis.
 * The x domain may run high to low (SNR from loud speech to loud music reads left to right).
 */
export function LineChart({
  series,
  xDomain,
  xTicks,
  yFormat,
  xFormat,
  markers = [],
  vlines = [],
  height = 260,
  label,
  yLabel,
  className,
  tableCaption,
}: {
  series: LineSeries[];
  xDomain: [number, number];
  xTicks: { value: number; label: string }[];
  yFormat: (v: number) => string;
  xFormat: (v: number) => string;
  markers?: LineMarker[];
  vlines?: VLine[];
  height?: number;
  label: string;
  yLabel?: string;
  className?: string;
  tableCaption?: ReactNode;
}) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const narrow = w < 520;
  const pad = { l: 46, r: narrow ? 14 : 112, t: vlines.length ? 26 : 14, b: 30 };
  const ys = series.flatMap((s) => s.points.flatMap((p) => [p.y, p.hi ?? p.y, p.lo ?? p.y]));
  const [, rawMax] = extent(ys, [0, 0.1]);
  const step = niceStep(rawMax * 1.08);
  const yMax = Math.max(step, Math.ceil((rawMax * 1.08) / step) * step);
  const [d0, d1] = xDomain;
  const x = (v: number) => pad.l + ((v - d0) / (d1 - d0 || 1)) * (w - pad.l - pad.r);
  const y = (v: number) => pad.t + (1 - Math.max(0, v) / yMax) * (height - pad.t - pad.b);
  const ticks = Array.from({ length: Math.round(yMax / step) + 1 }, (_, i) => i * step);

  const ends = useMemo(() => {
    const rows = series
      .filter((s) => !s.muted && s.points.length)
      .map((s) => ({ id: s.id, label: s.label, color: s.color, y0: y(s.points[s.points.length - 1]!.y) }))
      .sort((a, b) => a.y0 - b.y0);
    const gap = 13;
    for (let i = 1; i < rows.length; i++) if (rows[i]!.y0 - rows[i - 1]!.y0 < gap) rows[i]!.y0 = rows[i - 1]!.y0 + gap;
    const over = rows.length ? rows[rows.length - 1]!.y0 - (height - pad.b) : 0;
    if (over > 0) for (const r of rows) r.y0 -= over;
    return rows;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [series, w, height, yMax]);

  const path = (pts: LinePoint[]) => pts.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.y).toFixed(1)}`).join(" ");
  const band = (pts: LinePoint[]) => {
    const b = pts.filter((p) => p.lo != null && p.hi != null);
    if (b.length < 2) return null;
    return `${b.map((p, i) => `${i ? "L" : "M"}${x(p.x).toFixed(1)},${y(p.hi!).toFixed(1)}`).join(" ")} ${[...b].reverse().map((p) => `L${x(p.x).toFixed(1)},${y(p.lo!).toFixed(1)}`).join(" ")} Z`;
  };
  const xs = [...new Set(series.flatMap((s) => s.points.map((p) => p.x)))].sort((a, b) => (d1 >= d0 ? a - b : b - a));

  return (
    <div className={cn("grid gap-3", className)}>
      <div ref={ref} className="w-full min-w-0 overflow-hidden">
        <svg width={w} height={height} role="img" aria-label={label} className="block overflow-visible">
          {ticks.map((t) => (
            <g key={t}>
              <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" />
              <text x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
                {yFormat(t)}
              </text>
            </g>
          ))}
          {yLabel && (
            <text x={pad.l} y={pad.t - 6} className="fill-[var(--ink-3)] font-mono text-[10px] uppercase" style={{ letterSpacing: "0.06em" }}>
              {yLabel}
            </text>
          )}
          {xTicks.map((t) => (
            <text key={t.value} x={x(t.value)} y={height - pad.b + 18} textAnchor="middle" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
              {t.label}
            </text>
          ))}
          {vlines.map((v, i) => (
            <g key={`v${i}`}>
              <line x1={x(v.x)} x2={x(v.x)} y1={pad.t - 4} y2={height - pad.b} stroke={v.tone === "signal" ? "var(--signal)" : "var(--line-strong)"} strokeDasharray={v.tone === "signal" ? undefined : "3 3"} />
              <text x={x(v.x) + 4} y={pad.t - 10} className={cn("font-mono text-[10px]", v.tone === "signal" ? "fill-[var(--signal-ink)]" : "fill-[var(--ink-3)]")}>
                {v.label}
              </text>
            </g>
          ))}
          {series.map((s) => {
            const bd = band(s.points);
            return (
              <g key={s.id} opacity={s.muted ? 0.35 : 1}>
                {bd && <path d={bd} fill={s.color} opacity={0.12} />}
                {s.points.length > 1 && <path d={path(s.points)} fill="none" stroke={s.color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />}
                {s.points.map((p) => (
                  <circle key={p.x} cx={x(p.x)} cy={y(p.y)} r={s.points.length > 24 ? 2.5 : 4} fill={s.color} stroke="var(--surface)" strokeWidth={s.points.length > 24 ? 1 : 2}>
                    <title>{`${s.label}, ${xFormat(p.x)}: ${yFormat(p.y)}${p.lo != null && p.hi != null ? ` (95% interval ${yFormat(p.lo)} to ${yFormat(p.hi)})` : ""}`}</title>
                  </circle>
                ))}
              </g>
            );
          })}
          {markers.map((m, i) => {
            const s = series.find((q) => q.id === m.series);
            // A marker may sit between tested points (an interpolated SNR): place it on the line.
            const p = s ? (s.points.find((q) => q.x === m.x) ?? between(s.points, m.x)) : undefined;
            if (!s || !p) return null;
            return (
              <g key={`m${i}`}>
                <circle cx={x(p.x)} cy={y(p.y)} r={8} fill="none" stroke="var(--signal)" strokeWidth={1.75} />
                {!narrow && (
                  <text x={x(p.x)} y={y(p.y) - 12} textAnchor="middle" className="fill-[var(--signal-ink)] font-mono text-[10px]">
                    {m.label}
                  </text>
                )}
              </g>
            );
          })}
          {!narrow &&
            ends.map((e) => (
              <g key={e.id}>
                <circle cx={w - pad.r + 10} cy={e.y0} r={3.5} fill={e.color} />
                <text x={w - pad.r + 18} y={e.y0} dy="0.32em" className="fill-[var(--ink)] text-[11.5px]">
                  {e.label}
                </text>
              </g>
            ))}
        </svg>
      </div>
      {narrow && (
        <ul className="flex flex-wrap gap-x-4 gap-y-1.5 text-[11.5px] text-ink-2">
          {series
            .filter((s) => !s.muted)
            .map((s) => (
              <li key={s.id} className="inline-flex items-center gap-1.5">
                <span className="size-2 rounded-full" style={{ background: s.color }} aria-hidden /> {s.label}
              </li>
            ))}
        </ul>
      )}
      {/* Tables ignore width, so the sr-only box wraps the table rather than sitting on it (no page overflow). */}
      <div className="sr-only">
      <table>
        <caption>{tableCaption ?? label}</caption>
        <thead>
          <tr>
            <th scope="col">Series</th>
            {xs.map((v) => (
              <th key={v} scope="col">
                {xFormat(v)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {series.map((s) => (
            <tr key={s.id}>
              <th scope="row">{s.label}</th>
              {xs.map((v) => {
                const p = s.points.find((q) => q.x === v);
                return <td key={v}>{p ? `${yFormat(p.y)}${p.lo != null && p.hi != null ? ` (${yFormat(p.lo)} to ${yFormat(p.hi)})` : ""}` : "n/a"}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

function between(pts: LinePoint[], x: number): LinePoint | undefined {
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1]!;
    const b = pts[i]!;
    if ((x - a.x) * (x - b.x) <= 0 && a.x !== b.x) return { x, y: a.y + ((x - a.x) / (b.x - a.x)) * (b.y - a.y) };
  }
  return undefined;
}

/** A round step giving three to five gridlines, for rates (0..1) and for milliseconds alike. */
export function niceStep(max: number) {
  if (!(max > 0)) return 0.01;
  const mag = 10 ** Math.floor(Math.log10(max));
  for (const m of [0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10]) if (max / (m * mag) <= 5) return m * mag;
  return 10 * mag;
}
