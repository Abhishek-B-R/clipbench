import { Link } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { TAG_LABEL, breaksOn, overall, providerColor, providerLabel } from "@/lib/analysis";
import { pct } from "@/lib/format";
import { SeriesDot } from "@/components/ui/Badge";
import { Reveal } from "@/components/layout/Section";

/**
 * "What breaks each model": one card per model with the clip type that hurts it most (relative to its own
 * average), a mini before/after bar, and its most common substitutions (reference -> output, count).
 * Snippets come from errors.json and are at most a few characters (publish-check rule).
 */
export function Breaks({ run }: { run: Run }) {
  const order = overall(run).map((a) => a.provider);
  return (
    <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line sm:grid-cols-2 lg:grid-cols-3">
      {order.map((p, i) => {
        const b = breaksOn(run, p);
        const subs = run.errors.providers[p]?.top_substitutions.slice(0, 3) ?? [];
        const color = providerColor(run, p);
        const max = b ? Math.max(b.agg.error_rate, b.base.error_rate) * 1.1 : 1;
        return (
          <Reveal key={p} delay={(i % 3) * 0.05} y={8} className="bg-surface">
            <article className="flex h-full flex-col p-5 sm:p-6">
              <header className="flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-[15px] font-medium text-ink">
                  <SeriesDot color={color} />
                  {providerLabel(run, p)}
                </h3>
                <Link to={`/providers/${p}`} aria-label={`${providerLabel(run, p)} details`} className="grid size-7 place-items-center rounded-sm text-ink-3 hover:bg-sunken hover:text-ink">
                  <ArrowUpRight size={15} />
                </Link>
              </header>
              {b ? (
                <>
                  <p className="mt-4 text-[13px] text-ink-3">Breaks most on</p>
                  <p className="display mt-1 text-[26px] text-ink">{TAG_LABEL[b.tag]}</p>
                  <div className="mt-4 grid gap-2 text-[12px]">
                    <Bar label="All clips" value={b.base.error_rate} max={max} color="var(--line-strong)" />
                    <Bar label="This type" value={b.agg.error_rate} max={max} color={color} />
                  </div>
                  <p className="mt-3 text-[12.5px] text-ink-2">
                    Errors rise <span className="num text-ink">{b.ratio.toFixed(1)}x</span> on these clips ({b.agg.n_clips} clips).
                  </p>
                </>
              ) : (
                <p className="mt-4 text-[13px] text-ink-3">No clip type has enough clips to say.</p>
              )}
              {subs.length > 0 && (
                <div className="mt-auto pt-5">
                  <div className="label mb-2">Common slips</div>
                  <ul className="flex flex-wrap gap-1.5">
                    {subs.map(([ref, hyp, n]) => (
                      <li key={ref + hyp} lang="ja" className="inline-flex h-7 items-center gap-1.5 rounded-sm border border-line bg-sunken/60 px-2 text-[12.5px]">
                        <span className="text-ink-2">{ref}</span>
                        <ArrowRight size={11} className="text-ink-3" aria-label="heard as" />
                        <span className="text-signal-ink">{hyp || <span className="font-mono text-[11px] text-ink-3">(dropped)</span>}</span>
                        <span className="num text-[11px] text-ink-3">{n}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </article>
          </Reveal>
        );
      })}
    </div>
  );
}

function Bar({ label, value, max, color }: { label: string; value: number; max: number; color: string }) {
  return (
    <div className="grid grid-cols-[64px_minmax(0,1fr)_44px] items-center gap-2">
      <span className="truncate text-ink-3">{label}</span>
      <span className="relative h-2 rounded-r-[4px] bg-sunken">
        <span className="absolute inset-y-0 left-0 rounded-r-[4px]" style={{ width: `${(value / max) * 100}%`, background: color }} />
      </span>
      <span className="num text-right text-ink-2">{pct(value)}</span>
    </div>
  );
}
