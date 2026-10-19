import { useEffect, useRef, useState } from "react";
import { motion, useInView, useReducedMotion } from "motion/react";
import type { Aggregate, Run } from "@/lib/types";
import { frontier, providerColor, providerLabel } from "@/lib/analysis";
import { pct, usdPerHour } from "@/lib/format";

/**
 * Cost per audio hour (log x) against error rate (y, fewer errors at the top), one dot per model with its
 * 95% interval as a vertical whisker. Free models sit in a separate "free" lane at the left, since a log
 * axis cannot show zero. The frontier joins models no other model beats on both cost and error.
 * Direct labels on every dot (6 or fewer models) plus a hover/focus tooltip; an sr-only table carries the data.
 */
export function CostAccuracy({ run, rows, height = 360 }: { run: Run; rows: Aggregate[]; height?: number }) {
  const box = useRef<HTMLDivElement>(null);
  const [w, setW] = useState(320);
  const [hover, setHover] = useState<string | null>(null);
  const inView = useInView(box, { once: true, amount: 0.3 });
  const reduce = useReducedMotion();

  useEffect(() => {
    const el = box.current;
    if (!el) return;
    const ro = new ResizeObserver(([e]) => setW(Math.max(280, Math.round(e!.contentRect.width))));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const pad = { l: 46, r: 24, t: 18, b: 40 };
  const freeLane = 56;
  const paid = rows.filter((r) => (r.cost_per_hour ?? 0) > 0);
  const costs = paid.map((r) => r.cost_per_hour!);
  const lo = Math.log10(Math.min(...costs, 0.1) / 1.5);
  const hi = Math.log10(Math.max(...costs, 1) * 1.5);
  const x0 = pad.l + freeLane;
  const x = (c: number) => (c <= 0 ? pad.l + freeLane / 2 - 6 : x0 + ((Math.log10(c) - lo) / (hi - lo)) * (w - x0 - pad.r));
  const errMax = Math.max(...rows.map((r) => r.ci_high)) * 1.12;
  const errMin = Math.max(0, Math.min(...rows.map((r) => r.ci_low)) * 0.8);
  const y = (e: number) => pad.t + ((e - errMin) / (errMax - errMin)) * (height - pad.t - pad.b);

  const ticks = [0.01, 0.03, 0.1, 0.3, 1, 3, 10].filter((t) => Math.log10(t) >= lo && Math.log10(t) <= hi);
  const yTicks = niceTicks(errMin, errMax, 4);
  const front = frontier(rows);
  const path = front.map((r, i) => `${i ? "L" : "M"}${x(r.cost_per_hour ?? 0).toFixed(1)},${y(r.error_rate).toFixed(1)}`).join(" ");
  const hovered = rows.find((r) => r.provider === hover);

  // Greedy label placement: right of the dot, else left, else nudged down/up, so labels never overlap.
  const labels = (() => {
    type Box = { x0: number; x1: number; y0: number; y1: number };
    const placed: Box[] = [];
    const dots: Box[] = rows.map((r) => {
      const cx = x(r.cost_per_hour ?? 0);
      const cy = y(r.error_rate);
      return { x0: cx - 7, x1: cx + 7, y0: cy - 7, y1: cy + 7 };
    });
    const hit = (b: Box) => [...placed, ...dots].some((o) => b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
    const out: Record<string, { x: number; y: number; anchor: "start" | "end" }> = {};
    const order = [...rows].sort((a, b) => a.error_rate - b.error_rate);
    for (const r of order) {
      const cx = x(r.cost_per_hour ?? 0);
      const cy = y(r.error_rate);
      const tw = providerLabel(run, r.provider).length * 6.6;
      const tries: { x: number; y: number; anchor: "start" | "end" }[] = [];
      for (const dy of [0, 15, -15, 28, -28]) {
        tries.push({ x: cx + 11, y: cy + dy, anchor: "start" });
        tries.push({ x: cx - 11, y: cy + dy, anchor: "end" });
      }
      const fits = (t: { x: number; y: number; anchor: "start" | "end" }) => {
        const x0 = t.anchor === "start" ? t.x : t.x - tw;
        return x0 >= pad.l && x0 + tw <= w - 2;
      };
      const pick = tries.find((t) => fits(t) && !hit({ x0: t.anchor === "start" ? t.x : t.x - tw, x1: t.anchor === "start" ? t.x + tw : t.x, y0: t.y - 7, y1: t.y + 7 })) ?? tries.find(fits) ?? tries[0]!;
      placed.push({ x0: pick.anchor === "start" ? pick.x : pick.x - tw, x1: pick.anchor === "start" ? pick.x + tw : pick.x, y0: pick.y - 7, y1: pick.y + 7 });
      out[r.provider] = pick;
    }
    return out;
  })();

  return (
    <div ref={box} className="relative w-full min-w-0">
      <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} role="img" aria-label="Cost per hour against error rate, one point per model" className="block overflow-visible">
        {/* grid */}
        {yTicks.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" />
            <text x={pad.l - 8} y={y(t)} dy="0.32em" textAnchor="end" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
              {Math.round(t * 100)}%
            </text>
          </g>
        ))}
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={pad.t} y2={height - pad.b} stroke="var(--line)" />
            <text x={x(t)} y={height - pad.b + 18} textAnchor="middle" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
              ${t}
            </text>
          </g>
        ))}
        {/* free lane */}
        <rect x={pad.l} y={pad.t} width={freeLane - 12} height={height - pad.t - pad.b} fill="var(--sunken)" rx={4} />
        <text x={pad.l + (freeLane - 12) / 2} y={height - pad.b + 18} textAnchor="middle" className="fill-[var(--ink-3)] font-mono text-[10.5px]">
          free
        </text>
        <line x1={pad.l} x2={w - pad.r} y1={height - pad.b} y2={height - pad.b} stroke="var(--line-strong)" />
        <text x={w - pad.r} y={height - 4} textAnchor="end" className="fill-[var(--ink-3)] font-mono text-[10.5px] tracking-wider uppercase">
          Cost per audio hour, log scale
        </text>
        <text x={pad.l} y={pad.t - 6} className="fill-[var(--ink-3)] font-mono text-[10.5px] tracking-wider uppercase">
          ↑ fewer errors
        </text>

        {/* frontier */}
        <motion.path
          d={path}
          fill="none"
          stroke="var(--ink-3)"
          strokeWidth={1.5}
          strokeDasharray="1.5 5"
          strokeLinecap="round"
          initial={reduce ? false : { opacity: 0 }}
          animate={inView ? { opacity: 1 } : undefined}
          transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1], delay: 0.4 }}
        />

        {/* points */}
        {rows.map((r, i) => {
          const cx = x(r.cost_per_hour ?? 0);
          const cy = y(r.error_rate);
          const color = providerColor(run, r.provider);
          const dim = hover && hover !== r.provider;
          const lab = labels[r.provider]!;
          return (
            <motion.g
              key={r.provider}
              initial={reduce ? false : { opacity: 0, y: 8 }}
              animate={inView ? { opacity: dim ? 0.35 : 1, y: 0 } : undefined}
              transition={{ duration: 0.6, ease: [0.16, 1, 0.3, 1], delay: reduce ? 0 : 0.1 + i * 0.06 }}
              tabIndex={0}
              role="button"
              aria-label={`${providerLabel(run, r.provider)}: ${usdPerHour(r.cost_per_hour)} per hour, ${pct(r.error_rate)} error`}
              onMouseEnter={() => setHover(r.provider)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(r.provider)}
              onBlur={() => setHover(null)}
              className="cursor-default outline-none"
            >
              <line x1={cx} x2={cx} y1={y(r.ci_low)} y2={y(r.ci_high)} stroke={color} strokeWidth={2} strokeLinecap="round" opacity={0.45} />
              <circle cx={cx} cy={cy} r={14} fill="transparent" />
              <circle cx={cx} cy={cy} r={5.5} fill={color} stroke="var(--surface)" strokeWidth={2} />
              <text x={lab.x} y={lab.y} dy="0.32em" textAnchor={lab.anchor} className="fill-[var(--ink)] text-[12px] font-medium">
                {providerLabel(run, r.provider)}
              </text>
            </motion.g>
          );
        })}
      </svg>
      {hovered && (
        <div
          className="pointer-events-none absolute z-10 w-52 -translate-x-1/2 -translate-y-full rounded-sm border border-line bg-surface px-3 py-2 text-[12px] shadow-pop"
          style={{ left: Math.min(Math.max(x(hovered.cost_per_hour ?? 0), 110), w - 110), top: y(hovered.error_rate) - 14 }}
        >
          <div className="font-medium text-ink">{providerLabel(run, hovered.provider)}</div>
          <div className="mt-1 grid grid-cols-2 gap-x-3 text-ink-2">
            <span>Error</span>
            <span className="num text-right text-ink">{pct(hovered.error_rate)}</span>
            <span>95% interval</span>
            <span className="num text-right">
              {pct(hovered.ci_low)} to {pct(hovered.ci_high)}
            </span>
            <span>Cost / hour</span>
            <span className="num text-right text-ink">{usdPerHour(hovered.cost_per_hour)}</span>
          </div>
        </div>
      )}
      <table className="sr-only">
        <caption>Cost and error per model</caption>
        <thead>
          <tr>
            <th>Model</th>
            <th>Cost per hour</th>
            <th>Error rate</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.provider}>
              <td>{providerLabel(run, r.provider)}</td>
              <td>{usdPerHour(r.cost_per_hour)}</td>
              <td>{pct(r.error_rate)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function niceTicks(min: number, max: number, n: number) {
  const step0 = (max - min) / n;
  const mag = 10 ** Math.floor(Math.log10(step0));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= step0) ?? step0;
  const out: number[] = [];
  for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) out.push(Number(v.toFixed(6)));
  return out;
}
