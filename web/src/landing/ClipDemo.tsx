import { useEffect, useMemo, useRef, useState } from "react";
import { AnimatePresence, motion, useInView, useReducedMotion } from "motion/react";
import { Pause, Play } from "lucide-react";
import { cn } from "@/lib/cn";
import { pct } from "@/lib/format";
import { Badge, SeriesDot } from "@/components/ui/Badge";
import { NOTES, SEGMENTS, WAVE, opsFor, scoreOps, segmentSpans, type Op } from "./demoClip";

/**
 * Hero clip card. Pick a model and its transcript diff lights up: substitutions in vermilion with the
 * reference as ruby text above, deletions struck through, insertions in blue. The waveform marks where the
 * errors fall. Auto-advances through the models while in view, until the user picks one.
 * The sentence and the models here are an ILLUSTRATION (see demoClip.ts), always labelled as such.
 */
const MODELS = ["model-a", "model-b", "model-c", "model-d", "model-e", "model-f"];
const LABEL = (id: string) => `Model ${id.slice(-1).toUpperCase()}`;
const COLOR = (id: string) => `var(--series-${MODELS.indexOf(id) + 1})`;

export function ClipDemo() {
  const reduce = useReducedMotion();
  const [model, setModel] = useState("model-a");
  const [auto, setAuto] = useState(!reduce);
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { amount: 0.4 });

  const scores = useMemo(() => Object.fromEntries(MODELS.map((m) => [m, scoreOps(opsFor(m))])), []);
  const ops = useMemo(() => opsFor(model), [model]);
  const s = scores[model]!;
  const spans = useMemo(segmentSpans, []);

  useEffect(() => {
    if (!auto || !inView) return;
    const t = window.setInterval(() => setModel((m) => MODELS[(MODELS.indexOf(m) + 1) % MODELS.length]!), 3600);
    return () => window.clearInterval(t);
  }, [auto, inView]);

  // Which waveform bars sit under an error, for this model.
  const errorAt = useMemo(() => {
    const marks: { from: number; to: number; kind: Op["t"] }[] = [];
    let seg = 0;
    for (const o of ops) {
      if (o.t === "ins") {
        const [, b] = spans[seg - 1] ?? [1, 1];
        marks.push({ from: b - 0.02, to: Math.min(1, b + 0.03), kind: "ins" });
        continue;
      }
      const [a, b] = spans[seg]!;
      if (o.t !== "eq") marks.push({ from: a, to: b, kind: o.t });
      seg++;
    }
    return (x: number) => marks.find((m) => x >= m.from && x <= m.to)?.kind;
  }, [ops, spans]);

  const pick = (m: string) => {
    setAuto(false);
    setModel(m);
  };

  return (
    <div ref={ref} className="relative rounded-lg border border-line bg-surface shadow-pop">
      {/* header */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 sm:px-5">
        <span className="label text-ink-2">Clip DEMO-01</span>
        <span className="label">0:14</span>
        <span className="ml-auto flex flex-wrap gap-1.5">
          <Badge tone="outline">Music under speech</Badge>
          <Badge tone="signal">Illustration</Badge>
        </span>
      </div>

      {/* model picker */}
      <div role="radiogroup" aria-label="Model" className="grid grid-cols-3 gap-px border-b border-line bg-line sm:grid-cols-6">
        {MODELS.map((m) => {
          const on = m === model;
          return (
            <button
              key={m}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => pick(m)}
              className={cn(
                "group relative flex flex-col items-start gap-1 bg-surface px-3 py-2.5 text-left transition-colors",
                on ? "bg-surface" : "bg-sunken/50 hover:bg-surface",
              )}
            >
              <span className="flex items-center gap-1.5 text-[12.5px] font-medium text-ink">
                <SeriesDot color={COLOR(m)} size={7} />
                {LABEL(m)}
              </span>
              <span className={cn("num text-[12px]", on ? "text-ink-2" : "text-ink-3")}>{pct(scores[m]!.cer)}</span>
              {on && <motion.span layoutId="clip-model" className="absolute inset-x-0 bottom-0 h-[2px] bg-ink" transition={{ type: "spring", stiffness: 500, damping: 40 }} />}
              {on && auto && inView && !reduce && (
                <motion.span
                  key={`p-${m}`}
                  className="absolute inset-x-0 bottom-0 h-[2px] origin-left bg-signal"
                  initial={{ scaleX: 0 }}
                  animate={{ scaleX: 1 }}
                  transition={{ duration: 3.6, ease: "linear" }}
                />
              )}
            </button>
          );
        })}
      </div>

      {/* waveform */}
      <div className="relative px-4 pt-5 sm:px-5">
        <div className="mb-2 flex items-center justify-between">
          <span className="label">Audio</span>
          <span className="flex items-center gap-3 text-[11px] text-ink-3">
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-[2px] bg-ink-3/60" /> speech
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-[2px] bg-line-strong" /> music
            </span>
            <span className="inline-flex items-center gap-1.5">
              <span className="h-2 w-2 rounded-[2px] bg-signal" /> error
            </span>
          </span>
        </div>
        <div aria-hidden className="flex h-16 items-center gap-[2px]">
          {WAVE.map((w, i) => {
            const kind = errorAt(i / WAVE.length);
            return (
              <span key={i} className="relative flex h-full min-w-[1px] flex-1 items-center justify-center">
                <span className="absolute w-full rounded-full bg-line-strong/70" style={{ height: `${w.music * 100}%` }} />
                <span
                  className="relative w-full rounded-full transition-[background-color,height] duration-500"
                  style={{
                    height: `${w.speech * 92}%`,
                    background: kind === "ins" ? "var(--ins)" : kind ? "var(--signal)" : "color-mix(in oklch, var(--ink-3) 70%, transparent)",
                    transitionDelay: `${i * 3}ms`,
                  }}
                />
              </span>
            );
          })}
        </div>
      </div>

      {/* transcripts */}
      <div className="grid gap-4 px-4 pt-5 pb-5 sm:px-5">
        <div>
          <div className="label mb-1.5">Reference, checked by a person</div>
          <p lang="ja" className="text-[14px] leading-7 text-ink-3">
            {SEGMENTS.join("")}
          </p>
        </div>
        <div>
          <div className="label mb-1.5 flex items-center gap-2">
            <SeriesDot color={COLOR(model)} size={6} />
            {LABEL(model)} heard
          </div>
          <AnimatePresence mode="wait" initial={false}>
            <motion.p
              key={model}
              lang="ja"
              initial={reduce ? false : { opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={reduce ? undefined : { opacity: 0, y: -4 }}
              transition={{ duration: 0.25, ease: [0.16, 1, 0.3, 1] }}
              className="min-h-[96px] text-[19px] leading-[2.15] text-ink sm:text-[21px]"
            >
              {ops.map((o, i) => (
                <Token key={i} op={o} i={i} />
              ))}
            </motion.p>
          </AnimatePresence>
        </div>
      </div>

      {/* footer stats */}
      <div className="grid grid-cols-2 border-t border-line sm:grid-cols-4 [&>*:nth-child(2)]:border-r-0 sm:[&>*:nth-child(2)]:border-r [&>*:nth-child(n+3)]:border-t sm:[&>*:nth-child(n+3)]:border-t-0">
        <Stat label="CER" value={pct(s.cer)} strong />
        <Stat label="Swapped" value={String(s.sub)} tone="signal" />
        <Stat label="Dropped" value={String(s.del)} tone="signal" />
        <Stat label="Added" value={String(s.ins)} tone="ins" />
      </div>
      <div className="flex items-start gap-3 border-t border-line px-4 py-3 sm:px-5">
        <button
          type="button"
          onClick={() => setAuto((a) => !a)}
          aria-label={auto ? "Stop cycling through models" : "Cycle through models"}
          className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full border border-line-strong text-ink-2 hover:text-ink"
        >
          {auto ? <Pause size={11} /> : <Play size={11} className="translate-x-px" />}
        </button>
        <AnimatePresence mode="wait" initial={false}>
          <motion.p key={model} initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="text-[13px] text-ink-2">
            {NOTES[model]}
          </motion.p>
        </AnimatePresence>
      </div>
    </div>
  );
}

function Token({ op, i }: { op: Op; i: number }) {
  const delay = { animationDelay: `${120 + i * 18}ms` };
  if (op.t === "eq") return <span>{op.ref}</span>;
  if (op.t === "sub")
    return (
      <ruby className="animate-fade-up rounded-[3px] bg-signal-soft px-0.5 text-signal-ink [ruby-position:over]" style={delay}>
        {op.hyp}
        <rt className="font-mono text-[10px] tracking-normal text-ink-3 not-italic">{op.ref}</rt>
      </ruby>
    );
  if (op.t === "del")
    return (
      <span
        className="animate-fade-up rounded-[3px] px-0.5 text-ink-3 line-through decoration-signal decoration-2 outline-1 outline-signal/40 outline-dashed -outline-offset-1"
        style={delay}
        title="Dropped"
      >
        {op.ref}
      </span>
    );
  return (
    <span className="animate-fade-up rounded-[3px] bg-ins-soft px-0.5 text-ins" style={delay} title="Added, never said">
      <span aria-hidden className="mr-0.5 font-mono text-[12px]">+</span>
      {op.hyp}
    </span>
  );
}

function Stat({ label, value, tone, strong }: { label: string; value: string; tone?: "signal" | "ins"; strong?: boolean }) {
  return (
    <div className="border-r border-line px-4 py-3 last:border-r-0 sm:px-5">
      <div className="label flex items-center gap-1.5">
        {tone && <span className={cn("size-1.5 rounded-full", tone === "signal" ? "bg-signal" : "bg-ins")} />}
        {label}
      </div>
      <div className={cn("num mt-1", strong ? "text-[20px] font-medium text-ink" : "text-[16px] text-ink-2")}>{value}</div>
    </div>
  );
}
