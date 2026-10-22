import { Link } from "react-router";
import { ArrowRight, Upload } from "lucide-react";
import { m, useReducedMotion } from "motion/react";
import { Corners } from "@/components/ui/Card";
import { buttonClass } from "@/components/ui/Button";
import { TRY_MODELS } from "@/try/api";

const ease = [0.16, 1, 0.3, 1] as const;

/** Abstract transcript lanes: grey words with a few vermilion slips. Decoration only, no names and no numbers. */
const LANES = [
  [14, 22, 9, 30, 12, 18],
  [14, 22, 9, 26, 16, 18],
  [14, 18, 13, 30, 12, 10, 8],
  [14, 22, 9, 30, 28],
];
const SLIPS: Record<number, number[]> = { 1: [3], 2: [1, 5], 3: [4] };

/** Homepage hero card: the way into the /try playground. */
export function TryCard() {
  const reduce = useReducedMotion();
  return (
    <div className="relative rounded-lg border border-line bg-surface shadow-pop">
      <Corners />
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <span className="label">Try it / playground</span>
        <span className="inline-flex items-center gap-1.5 font-mono text-[10.5px] tracking-[0.05em] text-ink-3 uppercase">
          <span className="relative inline-flex size-1.5">
            {!reduce && <span className="absolute inset-0 animate-ping rounded-full bg-good opacity-60" />}
            <span className="relative inline-flex size-1.5 rounded-full bg-good" />
          </span>
          Your own clip
        </span>
      </div>
      <Link
        to="/try"
        className="group mx-5 mt-5 grid place-items-center gap-2 rounded-md border border-dashed border-line-strong bg-sunken/50 px-4 py-7 text-center transition-colors hover:border-ink hover:bg-sunken"
      >
        <span className="grid size-10 place-items-center rounded-sm border border-line bg-surface text-ink-2 transition-colors group-hover:text-ink">
          <Upload size={18} strokeWidth={1.75} aria-hidden />
        </span>
        <span className="text-[17px] font-semibold tracking-[-0.015em] text-ink" style={{ fontStretch: "94%" }}>
          Drop a clip, compare {TRY_MODELS.length} models side by side
        </span>
        <span className="max-w-[36ch] text-[13px] text-ink-2">See how each speech-to-text system handles your audio, with the transcripts next to each other.</span>
      </Link>
      <div aria-hidden className="mx-5 mt-4 grid gap-2">
        {LANES.map((lane, i) => (
          <div key={i} className="flex items-center gap-2">
            <span className="h-2 w-2 shrink-0 rounded-full border border-line-strong" />
            <span className="flex flex-1 gap-1">
              {lane.map((wd, j) => (
                <m.span
                  key={j}
                  className={SLIPS[i]?.includes(j) ? "h-1.5 rounded-full bg-signal/70" : "h-1.5 rounded-full bg-line-strong/70"}
                  style={{ flexGrow: wd, flexBasis: 0 }}
                  initial={reduce ? false : { opacity: 0, scaleX: 0.4 }}
                  animate={{ opacity: 1, scaleX: 1 }}
                  transition={{ duration: 0.6, ease, delay: 0.5 + i * 0.08 + j * 0.03 }}
                />
              ))}
            </span>
          </div>
        ))}
      </div>
      <div className="flex flex-wrap items-center justify-between gap-3 px-5 pt-5 pb-5">
        <Link to="/try" className={buttonClass("primary", "md", "group")}>
          Open the playground
          <ArrowRight size={15} className="transition-transform duration-200 group-hover:translate-x-0.5" />
        </Link>
        <Link to="/early" className="text-[13px] text-ink-2 underline decoration-line-strong underline-offset-4 hover:text-ink">
          or see the first run
        </Link>
      </div>
    </div>
  );
}
