import { useMemo, useState } from "react";
import { useNavigate } from "react-router";
import type { Run } from "@/lib/types";
import type { ClipScores, EarlyLook, EarlyRow } from "@/lib/early";
import { CLIP_HEAT_MAX } from "@/lib/early";
import { providerLabel } from "@/lib/analysis";
import { pct, usdPerHour } from "@/lib/format";
import { maxOf, minOf } from "@/lib/scale";
import { useWidth } from "@/lib/useWidth";
import { cn } from "@/lib/cn";
import { heatStep } from "@/components/ui/HeatCell";

type Pt = { row: EarlyRow; cx: number; cy: number; off: "low" | "high" | null };
type Lab = { x: number; y: number; anchor: "start" | "end"; lead: boolean };

/**
 * Error rate against cost per audio hour, one ink dot per system (hollow when it covers fewer clips), with its 95%
 * interval as a whisker. Free systems sit in their own lane at the left, since a log axis cannot show zero. A system
 * far above the rest is drawn at the bottom edge with its value, so it does not squash everyone else. No frontier and
 * no "best" marks: this is an early look, not a ranking. Direct labels (with leader lines when nudged), hover/focus
 * readout, and an sr-only table.
 */
export function ErrorCostScatter({ look }: { look: EarlyLook }) {
  const [ref, w] = useWidth<HTMLDivElement>(720);
  const [hover, setHover] = useState<string | null>(null);
  const narrow = w < 560;
  const H = narrow ? 400 : 420;
  const pad = { l: 44, r: 12, t: 30, b: 44 };
  const lane = narrow ? 64 : 84;
  const rows = look.rows;

  // y: fewer errors at the top. Outliers (above 1.6x the median) are pinned to the bottom edge.
  const errs = rows.map((r) => r.error_rate).sort((a, b) => a - b);
  const med = errs.length ? errs[Math.floor(errs.length / 2)]! : 0;
  const isOut = (e: number) => e > med * 1.6 && errs.filter((x) => x <= med * 1.6).length >= 3;
  const inRows = rows.filter((r) => !isOut(r.error_rate));
  const lo = Math.max(0, minOf(inRows.map((r) => r.error_rate), 0) - 0.03);
  const hi = maxOf(inRows.map((r) => r.error_rate), 0.1) + 0.04;
  const plotB = H - pad.b - (rows.some((r) => isOut(r.error_rate)) ? 26 : 0);
  const y = (e: number) => pad.t + ((Math.min(Math.max(e, lo), hi) - lo) / (hi - lo || 1)) * (plotB - pad.t);

  // x: free lane, then log cost.
  const paid = rows.filter((r) => (r.cost_per_hour ?? 0) > 0);
  const cl = Math.log10(minOf(paid.map((r) => r.cost_per_hour!), 0.01) / 1.5);
  const ch = Math.log10(maxOf(paid.map((r) => r.cost_per_hour!), 1) * 1.5);
  const x0 = pad.l + lane;
  const x = (c: number) => x0 + ((Math.log10(c) - cl) / (ch - cl || 1)) * (w - x0 - pad.r - (narrow ? 4 : 90));
  const free = rows.filter((r) => !((r.cost_per_hour ?? 0) > 0)).sort((a, b) => a.error_rate - b.error_rate);

  const pts: Pt[] = rows.map((r) => {
    const out = isOut(r.error_rate);
    const fi = free.indexOf(r);
    const cx = fi >= 0 ? pad.l + lane / 2 + (fi - (free.length - 1) / 2) * Math.min(16, (lane - 16) / Math.max(1, free.length - 1)) : x(r.cost_per_hour!);
    return { row: r, cx, cy: out ? H - pad.b - 6 : y(r.error_rate), off: out ? "low" : null };
  });

  const labels = useMemo(() => place(pts, w, pad.l, H - pad.b + 8, narrow), [pts.map((p) => `${p.cx.toFixed(0)},${p.cy.toFixed(0)}`).join(), w]); // eslint-disable-line react-hooks/exhaustive-deps
  const xt = [0.01, 0.03, 0.1, 0.3, 1, 3].filter((t) => Math.log10(t) >= cl && Math.log10(t) <= ch);
  const yt = ticks(lo, hi);
  const hov = rows.find((r) => r.provider === hover);

  return (
    <div ref={ref} className="relative w-full min-w-0">
      <svg width={w} height={H} role="img" aria-label={`Error rate against cost per audio hour for ${rows.length} systems. Table follows.`} className="block overflow-visible">
        {yt.map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--line)" />
            <text x={pad.l - 8} y={y(t) + 4} textAnchor="end" className="fill-ink-3 font-mono text-[10.5px]">
              {pct(t, 0)}
            </text>
          </g>
        ))}
        <rect x={pad.l} y={pad.t - 6} width={lane - 6} height={H - pad.b - pad.t + 6} rx={6} fill="var(--sunken)" opacity={0.7} />
        <text x={pad.l + (lane - 6) / 2} y={H - pad.b + 18} textAnchor="middle" className="fill-ink-3 font-mono text-[10.5px]">
          free
        </text>
        {xt.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={pad.t - 6} y2={H - pad.b} stroke="var(--line)" strokeDasharray="2 3" />
            <text x={x(t)} y={H - pad.b + 18} textAnchor="middle" className="fill-ink-3 font-mono text-[10.5px]">
              {usdPerHour(t)}
            </text>
          </g>
        ))}
        <text x={w - pad.r} y={H - 6} textAnchor="end" className="fill-ink-3 font-mono text-[10.5px]">
          cost per audio hour, log scale
        </text>
        <text x={pad.l - 8} y={10} textAnchor="end" className="fill-ink-3 font-mono text-[10.5px]">
          error
        </text>
        <text x={pad.l + lane} y={10} className="fill-ink-3 font-mono text-[10.5px]">
          fewer errors at the top
        </text>
        {pts.some((p) => p.off) && <line x1={pad.l + lane} x2={w - pad.r} y1={H - pad.b - 18} y2={H - pad.b - 18} stroke="var(--line-strong)" strokeDasharray="4 4" />}

        {pts.map(({ row: r, cx, cy, off }) => {
          const l = labels[r.provider];
          const dim = hover && hover !== r.provider;
          const partial = r.n_clips < r.total;
          return (
            <g
              key={r.provider}
              tabIndex={0}
              role="button"
              aria-label={`${r.label}: ${pct(r.error_rate)} error, ${r.cost_per_hour ? usdPerHour(r.cost_per_hour) : "free"} per audio hour, ${r.n_clips} of ${r.total} clips`}
              onMouseEnter={() => setHover(r.provider)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(r.provider)}
              onBlur={() => setHover(null)}
              className="cursor-default outline-none"
              style={{ opacity: dim ? 0.3 : 1, transition: "opacity 150ms" }}
            >
              {!off && <line x1={cx} x2={cx} y1={y(r.ci_low)} y2={y(r.ci_high)} stroke="var(--ink-3)" strokeWidth={1} opacity={hover === r.provider ? 0.9 : 0.35} />}
              {off && <path d={`M${cx - 5},${cy - 4} L${cx + 5},${cy - 4} L${cx},${cy + 3} Z`} fill="var(--signal)" />}
              {!off && <circle cx={cx} cy={cy} r={5.5} fill={partial ? "var(--surface)" : "var(--ink)"} stroke="var(--ink)" strokeWidth={2} />}
              {!off && <circle cx={cx} cy={cy} r={8} fill="none" stroke="var(--surface)" strokeWidth={2} opacity={partial ? 0 : 1} />}
              {l && (
                <>
                  {l.lead && <line x1={cx + (l.anchor === "start" ? 6 : -6)} y1={cy} x2={l.x + (l.anchor === "start" ? -2 : 2)} y2={l.y - 4} stroke="var(--line-strong)" />}
                  <text x={l.x} y={l.y} textAnchor={l.anchor} className={cn("text-[11.5px]", hover === r.provider ? "fill-ink font-medium" : "fill-ink-2")}>
                    {r.label}
                    {off ? `, ${pct(r.error_rate)}` : ""}
                  </text>
                </>
              )}
            </g>
          );
        })}
      </svg>
      <div className="mt-2 flex min-h-5 flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-ink-3" aria-live="polite">
        {hov ? (
          <span className="text-ink-2">
            <span className="font-medium text-ink">{hov.label}</span>: {pct(hov.error_rate)} (95% interval {pct(hov.ci_low)} to {pct(hov.ci_high)}), {hov.cost_per_hour ? `${usdPerHour(hov.cost_per_hour)} per audio hour` : "free"}, {hov.n_clips} of {hov.total} clips
          </span>
        ) : (
          <>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block size-2.5 rounded-full bg-ink" /> every clip
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block size-2.5 rounded-full border-2 border-ink" /> fewer clips (wider interval)
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="inline-block h-3 w-px bg-ink-3" /> 95% interval
            </span>
          </>
        )}
      </div>
      <div className="sr-only">
      <table>
        <caption>Error rate and cost per audio hour</caption>
        <thead>
          <tr>
            <th>System</th>
            <th>Error rate</th>
            <th>95% interval</th>
            <th>Cost per audio hour</th>
            <th>Clips</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.provider}>
              <td>{r.label}</td>
              <td>{pct(r.error_rate)}</td>
              <td>
                {pct(r.ci_low)} to {pct(r.ci_high)}
              </td>
              <td>{r.cost_per_hour ? usdPerHour(r.cost_per_hour) : "free"}</td>
              <td>
                {r.n_clips} of {r.total}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  );
}

function ticks(lo: number, hi: number) {
  const span = hi - lo;
  const step = span > 0.3 ? 0.1 : span > 0.12 ? 0.05 : 0.02;
  const out: number[] = [];
  for (let t = Math.ceil(lo / step) * step; t <= hi + 1e-9; t += step) out.push(Math.round(t * 1000) / 1000);
  return out;
}

/** Greedy, collision-free label placement around each dot; nudged labels get a leader line. */
function place(pts: Pt[], w: number, left: number, bottom: number, narrow: boolean): Record<string, Lab> {
  type Box = { x0: number; x1: number; y0: number; y1: number };
  const placed: Box[] = [];
  const dots: Box[] = pts.map((p) => ({ x0: p.cx - 8, x1: p.cx + 8, y0: p.cy - 8, y1: p.cy + 8 }));
  const hit = (b: Box) => [...placed, ...dots].some((o) => b.x0 < o.x1 + 2 && b.x1 + 2 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0);
  const out: Record<string, Lab> = {};
  const order = [...pts].sort((a, b) => a.cy - b.cy || a.cx - b.cx);
  const cw = narrow ? 6.1 : 6.4;
  for (const p of order) {
    const text = p.row.label + (p.off ? `, ${pct(p.row.error_rate)}` : "");
    const tw = text.length * cw;
    const tries: Lab[] = [];
    for (const dy of [0, 14, -14, 26, -26, 38, -38, 50, -50, 62, -62]) {
      for (const dx of [11, 22]) {
        tries.push({ x: p.cx + dx, y: p.cy + 4 + dy, anchor: "start", lead: dy !== 0 || dx > 11 });
        tries.push({ x: p.cx - dx, y: p.cy + 4 + dy, anchor: "end", lead: dy !== 0 || dx > 11 });
      }
    }
    const box = (t: Lab): Box => ({ x0: t.anchor === "start" ? t.x : t.x - tw, x1: t.anchor === "start" ? t.x + tw : t.x, y0: t.y - 10, y1: t.y + 3 });
    const fits = (t: Lab) => {
      const b = box(t);
      return b.x0 >= left - 40 && b.x1 <= w - 2 && b.y0 >= 0 && b.y1 <= bottom;
    };
    const pick = tries.find((t) => fits(t) && !hit(box(t)));
    if (pick) {
      placed.push(box(pick));
      out[p.row.provider] = pick;
    }
  }
  return out;
}

/** Hallucination rate per system, highest first, as horizontal bars in the error colour. Zero prints as 0.0%. */
export function HallucinationBars({ look }: { look: EarlyLook }) {
  const rows = [...look.rows].sort((a, b) => b.hallucination_rate - a.hallucination_rate || a.error_rate - b.error_rate);
  const max = Math.max(0.01, maxOf(rows.map((r) => r.hallucination_rate), 0)) * 1.08;
  return (
    <ul className="grid gap-1.5" aria-label="Hallucination rate per system">
      {rows.map((r) => (
        <li key={r.provider} className="grid grid-cols-[minmax(0,150px)_minmax(0,1fr)_56px] items-center gap-3 text-[12.5px] sm:grid-cols-[minmax(0,220px)_minmax(0,1fr)_64px]">
          <span className="truncate text-ink-2" title={r.label}>
            {r.label}
          </span>
          <span className="relative h-3.5 rounded-r-[4px] bg-sunken/70">
            <span
              className={cn("absolute inset-y-0 left-0 rounded-r-[4px]", r.hallucination_rate > 0 ? "bg-signal" : "bg-line-strong")}
              style={{ width: r.hallucination_rate > 0 ? `${(r.hallucination_rate / max) * 100}%` : "2px" }}
            />
          </span>
          <span className={cn("num text-right", r.hallucination_rate > 0 ? "font-medium text-signal-ink" : "text-ink-3")}>{pct(r.hallucination_rate)}</span>
        </li>
      ))}
    </ul>
  );
}

/** One square per system on a clip, in the given order; hatched when a system has no result. */
export function HeatStrip({ run, order, errs, size = 16 }: { run: Run; order: string[]; errs: Map<string, number> | undefined; size?: number }) {
  return (
    <span className="inline-flex gap-[3px]">
      {order.map((p) => {
        const v = errs?.get(p);
        const step = v == null ? -1 : heatStep(v, [0, CLIP_HEAT_MAX]);
        return (
          <span
            key={p}
            role="img"
            aria-label={`${providerLabel(run, p)}: ${v == null ? "no result" : pct(v)}`}
            title={`${providerLabel(run, p)}: ${v == null ? "no result" : pct(v)}`}
            className={cn("shrink-0 rounded-[3px]", v == null && "hatch border border-line")}
            style={{ width: size, height: size, ...(v == null ? {} : { background: `var(--heat-${step})`, boxShadow: step === 0 ? "inset 0 0 0 1px var(--line)" : undefined }) }}
          />
        );
      })}
    </span>
  );
}

/** The ramp legend used by every per-clip view: 0% to CLIP_HEAT_MAX and above, plus the no-result hatch. */
export function ClipHeatLegend({ className }: { className?: string }) {
  return (
    <span className={cn("flex flex-wrap items-center gap-2 text-[11px] text-ink-3", className)}>
      <span className="num">{pct(0, 0)}</span>
      <span className="flex overflow-hidden rounded-xs">
        {[0, 1, 2, 3, 4].map((k) => (
          <span key={k} className="h-2.5 w-5" style={{ background: `var(--heat-${k})` }} />
        ))}
      </span>
      <span className="num">{pct(CLIP_HEAT_MAX, 0)}+</span>
      <span className="ml-2 inline-flex items-center gap-1.5">
        <span className="hatch inline-block h-2.5 w-4 rounded-[2px] border border-line" /> no result
      </span>
    </span>
  );
}

/**
 * Per-clip heatmap: one row per system (board order), one column per clip (hardest first). Flagged references get a
 * vermilion tick above their column. Scrolls inside its own box on small screens. Clicking a column opens the clip.
 */
export function ClipHeatmap({ look, clips }: { look: EarlyLook; clips: ClipScores[] }) {
  const [ref, w] = useWidth<HTMLDivElement>(900);
  const [col, setCol] = useState<number | null>(null);
  const navigate = useNavigate();
  const narrow = w < 640;
  const labelW = narrow ? Math.min(176, Math.round(w * 0.5)) : 232;
  const cols = clips.length;
  const cell = Math.max(narrow ? 5 : 3, Math.min(12, (w - labelW - 6) / Math.max(1, cols)));
  const gap = cell >= 4 ? 1 : 0.5;
  const rowH = narrow ? 18 : 20;
  const top = 14;
  const gridW = cell * cols;
  const H = top + rowH * look.rows.length + 4;
  const sel = col != null ? clips[col] : null;
  return (
    <div ref={ref} className="min-w-0">
      <div className="flex min-w-0">
        <ul className="shrink-0 pr-2.5" style={{ width: labelW, paddingTop: top }} aria-hidden>
          {look.rows.map((r) => (
            <li key={r.provider} className="truncate text-right text-[11.5px] leading-none text-ink-2" style={{ height: rowH, lineHeight: `${rowH}px` }} title={r.label}>
              {r.label}
            </li>
          ))}
        </ul>
        <div className="min-w-0 flex-1 overflow-x-auto overscroll-x-contain pb-1">
          <svg width={gridW + 2} height={H} role="img" aria-label={`Error rate per clip for ${look.rows.length} systems on ${cols} clips, hardest first.`} className="block" onMouseLeave={() => setCol(null)}>
            <defs>
              <pattern id="cb-hatch" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
                <rect width="4" height="4" fill="var(--sunken)" />
                <line x1="0" y1="0" x2="0" y2="4" stroke="var(--line-strong)" strokeWidth="1" />
              </pattern>
            </defs>
            {clips.map((c, j) => (
              <g key={c.clip.video_id} onMouseEnter={() => setCol(j)} onClick={() => navigate(`/clips?q=${encodeURIComponent(c.clip.video_id)}`)} className="cursor-pointer">
                {c.flagged && <rect x={j * cell} y={2} width={Math.max(2, cell - gap)} height={7} rx={1} fill="var(--signal)" />}
                {look.rows.map((r, i) => {
                  const v = c.errs.get(r.provider);
                  const step = v == null ? -1 : heatStep(v, [0, CLIP_HEAT_MAX]);
                  return (
                    <rect key={r.provider} x={j * cell} y={top + i * rowH + 1} width={Math.max(1, cell - gap)} height={rowH - 2} fill={v == null ? "url(#cb-hatch)" : `var(--heat-${step})`}>
                      <title>{`${r.label} on ${c.clip.video_id}: ${v == null ? "no result" : pct(v)}`}</title>
                    </rect>
                  );
                })}
                {col === j && <rect x={j * cell - 0.5} y={top} width={cell} height={rowH * look.rows.length} fill="none" stroke="var(--ink)" strokeWidth={1} />}
              </g>
            ))}
          </svg>
        </div>
      </div>
      <div className="mt-2 flex min-h-5 flex-wrap items-center justify-between gap-x-4 gap-y-2 text-[12px] text-ink-3" aria-live="polite">
        {sel ? (
          <span className="text-ink-2">
            <span className="font-mono text-ink">{sel.clip.video_id}</span>, {sel.clip.channel}: median {pct(sel.med)}
            {sel.flagged ? ", flagged for re-review" : ""}
            {sel.worst[0] ? `; hardest for ${providerLabel(look.run, sel.worst[0].provider)} (${pct(sel.worst[0].err)})` : ""}
          </span>
        ) : (
          <span>Hardest clips on the left. {narrow ? "Scroll sideways for the rest; tap a column to open the clip." : "Hover a column for the clip, click to open it."}</span>
        )}
        <span className="flex flex-wrap items-center gap-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="inline-block h-2 w-1.5 rounded-[1px] bg-signal" /> flagged for re-review
          </span>
          <ClipHeatLegend />
        </span>
      </div>
    </div>
  );
}
