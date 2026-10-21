import { useId, useState, type KeyboardEvent } from "react";
import { m } from "motion/react";
import type { Run } from "@/lib/types";
import { TAG_LABEL, TAG_PHRASE, aggregatesFor, pair, primaryMetric, providerColor, providerLabel, winnersByTag, defaultTier } from "@/lib/analysis";
import { pct, pValue } from "@/lib/format";
import { cn } from "@/lib/cn";
import { Figure } from "@/components/ui/Card";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { EmptyState } from "@/components/ui/State";

/**
 * "Which model wins on which kind of clip" (Pangram pattern). Numbered clip types on the left act as tabs;
 * the figure on the right redraws model bars with 95% intervals for the selected type.
 */
export function ByClipType({ run }: { run: Run }) {
  const winners = winnersByTag(run);
  const [tag, setTag] = useState(winners[0]?.tag ?? "background_music");
  const id = useId();
  const metric = primaryMetric(run);
  const rows = aggregatesFor(run, { tier: defaultTier(run), metric: metric.name, kind: "tag", slice: tag });
  const max = Math.max(0.01, ...run.aggregates.filter((a) => a.tier === defaultTier(run) && a.metric === metric.name && a.slice_kind === "tag").map((a) => a.ci_high)) * 1.05;
  const cur = winners.find((w) => w.tag === tag);

  const onKey = (e: KeyboardEvent, i: number) => {
    const n = winners.length;
    let j = i;
    if (e.key === "ArrowDown" || e.key === "ArrowRight") j = (i + 1) % n;
    else if (e.key === "ArrowUp" || e.key === "ArrowLeft") j = (i - 1 + n) % n;
    else return;
    e.preventDefault();
    setTag(winners[j]!.tag);
    document.getElementById(`${id}-tab-${j}`)?.focus();
  };

  let sentence = "";
  if (cur?.first && cur.second) {
    const t = pair(run, { tier: defaultTier(run), metric: metric.name, kind: "tag", slice: tag }, cur.first.provider, cur.second.provider);
    const a = providerLabel(run, cur.first.provider);
    const b = providerLabel(run, cur.second.provider);
    if (cur.tooSmall) sentence = `Only ${cur.first.n_clips} clips have ${TAG_PHRASE[tag]}. Too few to conclude anything.`;
    else if (cur.clear && t) sentence = `${a} leads on ${TAG_PHRASE[tag]}: ${pct(cur.first.error_rate)} against ${pct(cur.second.error_rate)} for ${b}, the runner-up (adjusted ${pValue(t.p)}).`;
    else sentence = `${a} and ${b} are within each other's noise on ${TAG_PHRASE[tag]}. The paired test finds no clear leader.`;
  }

  if (winners.length === 0)
    return (
      <EmptyState
        title="No clip types tagged in this run yet."
        body="Clip types (music under speech, fast talkers, slang and more) are tagged when a person verifies a clip. Once verified clips land, this view shows which model leads on each kind."
      />
    );
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,380px)_minmax(0,1fr)] lg:gap-8">
      <div role="tablist" aria-label="Clip type" aria-orientation="vertical" className="grid content-start gap-1.5">
        {winners.map((w, i) => {
          const on = w.tag === tag;
          return (
            <button
              key={w.tag}
              id={`${id}-tab-${i}`}
              role="tab"
              type="button"
              aria-selected={on}
              aria-controls={`${id}-panel`}
              tabIndex={on ? 0 : -1}
              onClick={() => setTag(w.tag)}
              onKeyDown={(e) => onKey(e, i)}
              className={cn(
                "group relative flex items-center gap-4 rounded-md border px-4 py-3.5 text-left transition-[background-color,border-color] duration-200",
                on ? "border-line-strong bg-surface shadow-card" : "border-transparent hover:bg-surface/70",
              )}
            >
              <span className={cn("num text-[13px]", on ? "text-signal-ink" : "text-ink-3")}>{String(i + 1).padStart(2, "0")}</span>
              <span className="min-w-0 flex-1">
                <span className={cn("block text-[16px] transition-colors", on ? "text-ink" : "text-ink-2 group-hover:text-ink")} style={{ fontStretch: "96%" }}>
                  {TAG_LABEL[w.tag]}
                </span>
                <span className="mt-0.5 flex items-center gap-1.5 text-[12px] text-ink-3">
                  {w.first && <SeriesDot color={providerColor(run, w.first.provider)} size={6} />}
                  {w.tooSmall ? "Too few clips" : w.first ? `${providerLabel(run, w.first.provider)} ${w.clear ? "leads" : "ahead, within noise"}` : ""}
                  <span aria-hidden>/</span>
                  <span className="num">{w.first?.n_clips ?? 0} clips</span>
                </span>
              </span>
              {on && <m.span layoutId={`${id}-mark`} className="absolute top-3 bottom-3 left-0 w-[2px] rounded-full bg-signal" />}
            </button>
          );
        })}
      </div>

      <Figure
        n={2}
        title={`Error rate on ${TAG_PHRASE[tag] ?? tag}`}
        caption={sentence}
        controls={cur?.tooSmall ? <Badge tone="warn">Too small to conclude</Badge> : cur?.clear ? <Badge tone="good">Paired test: clear lead</Badge> : <Badge>No clear lead</Badge>}
        source={`source: aggregates + paired, tier=${defaultTier(run)}, metric=${metric.name}, slice=tag:${tag}`}
        className="self-start"
      >
        <div id={`${id}-panel`} role="tabpanel" aria-labelledby={`${id}-tab-${winners.findIndex((w) => w.tag === tag)}`} className="grid gap-3.5">
          {rows.map((r, i) => {
            const w = (v: number) => `${(v / max) * 100}%`;
            return (
              <m.div key={r.provider} layout transition={{ type: "spring", stiffness: 300, damping: 34 }} className="grid grid-cols-[88px_minmax(0,1fr)_56px] items-center gap-3 sm:grid-cols-[108px_minmax(0,1fr)_64px]">
                <span className="flex items-center gap-2 truncate text-[13px] text-ink">
                  <SeriesDot color={providerColor(run, r.provider)} />
                  {providerLabel(run, r.provider)}
                </span>
                <span className="relative h-6" role="img" aria-label={`${providerLabel(run, r.provider)}: ${pct(r.error_rate)}, 95% interval ${pct(r.ci_low)} to ${pct(r.ci_high)}`}>
                  <span className="absolute inset-y-0 left-0 w-px bg-line-strong" />
                  <m.span
                    className={cn("absolute top-1 bottom-1 left-0 rounded-r-[4px]", r.too_small && "hatch")}
                    style={{ background: r.too_small ? undefined : providerColor(run, r.provider), opacity: i === 0 ? 1 : 0.8 }}
                    initial={false}
                    animate={{ width: w(r.error_rate) }}
                    transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                  />
                  <m.span
                    className="absolute top-1/2 h-px -translate-y-1/2 bg-ink"
                    initial={false}
                    animate={{ left: w(r.ci_low), width: `calc(${w(r.ci_high)} - ${w(r.ci_low)})` }}
                    transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                  >
                    <span className="absolute -top-[4px] left-0 h-[9px] w-px bg-ink" />
                    <span className="absolute -top-[4px] right-0 h-[9px] w-px bg-ink" />
                  </m.span>
                </span>
                <span className={cn("num text-right text-[13px]", i === 0 ? "font-medium text-ink" : "text-ink-2")}>{pct(r.error_rate)}</span>
              </m.div>
            );
          })}
          <div className="mt-2 flex items-center justify-between text-[11px] text-ink-3">
            <span className="inline-flex items-center gap-2">
              <span className="relative inline-block h-[9px] w-5">
                <span className="absolute top-1/2 h-px w-full bg-ink" />
                <span className="absolute top-0 left-0 h-[9px] w-px bg-ink" />
                <span className="absolute top-0 right-0 h-[9px] w-px bg-ink" />
              </span>
              95% interval
            </span>
            <span className="num">{metric.name.toUpperCase()}, lower is better</span>
          </div>
        </div>
      </Figure>
    </div>
  );
}
