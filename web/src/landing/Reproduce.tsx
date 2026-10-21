import { useRef, useState } from "react";
import { m, useInView, useReducedMotion } from "motion/react";
import { Check, Copy } from "lucide-react";
import { REPO_SLUG, RULES } from "@/lib/config";
import type { Run } from "@/lib/types";
import { defaultTier, primaryMetric, totalClips } from "@/lib/analysis";
import { toast } from "@/components/ui/Toast";

const steps = (tier: string) => [
  { cmd: `git clone https://github.com/${REPO_SLUG} && cd clipbench`, what: "Get the code. Python 3.12, managed with uv." },
  { cmd: "make setup", what: "Install pinned dependencies, including local Whisper." },
  { cmd: "make build-data sample fetch-audio", what: "Build the manifest, draw the seeded sample, fetch audio." },
  { cmd: `make run PROVIDERS=all TIER=${tier}`, what: "Transcribe with every model. Cached, and stops at the budget cap." },
  { cmd: "make report", what: "Score, analyse errors, write the report and this site's JSON." },
];

/**
 * "Reproduce it yourself": a terminal that types itself once in view (lines appear in sequence), with
 * each command explained on the right (Giga "command surface").
 */
export function Reproduce({ run }: { run?: Run }) {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true, amount: 0.4 });
  const reduce = useReducedMotion();
  const [copied, setCopied] = useState(false);
  // The commands reproduce the latest run: its tier and language, so the "ok" line below matches them.
  const tier = run ? defaultTier(run) : "verified";
  const STEPS = steps(tier);
  const spent = run
    ? // One tier only: the same clip can be scored under both tiers, and summing them would double count its cost.
      run.aggregates
        .filter((a) => a.slice_kind === "all" && a.metric === primaryMetric(run).name && a.tier === defaultTier(run))
        .reduce((t, a) => t + (Number.isFinite(a.cost_usd_total) ? a.cost_usd_total : 0), 0)
    : null;
  const all = STEPS.map((s) => s.cmd).join("\n");
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(all);
      setCopied(true);
      toast("Five commands copied");
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      toast.error("Could not copy");
    }
  };
  return (
    <div ref={ref} className="grid gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:gap-10">
      <div className="overflow-hidden rounded-lg border border-[oklch(0.3_0.005_70)] bg-[oklch(0.17_0.005_70)] text-[oklch(0.93_0.005_85)] shadow-pop" data-theme="dark">
        <div className="flex h-10 items-center gap-2 border-b border-[oklch(0.27_0.005_70)] px-4">
          <span className="size-2.5 rounded-full bg-[oklch(0.35_0.005_70)]" />
          <span className="size-2.5 rounded-full bg-[oklch(0.35_0.005_70)]" />
          <span className="size-2.5 rounded-full bg-[oklch(0.35_0.005_70)]" />
          <span className="ml-3 font-mono text-[11.5px] text-[oklch(0.64_0.006_85)]">~/clipbench</span>
          <button
            type="button"
            onClick={copy}
            className="ml-auto inline-flex h-7 items-center gap-1.5 rounded-sm px-2 font-mono text-[11.5px] text-[oklch(0.72_0.006_85)] hover:bg-[oklch(0.24_0.005_70)] hover:text-white"
          >
            {copied ? <Check size={13} /> : <Copy size={13} />}
            {copied ? "Copied" : "Copy all"}
          </button>
        </div>
        <ol className="grid gap-1 p-4 font-mono text-[12.5px] leading-6 sm:p-5 sm:text-[13px]">
          {STEPS.map((s, i) => (
            <m.li
              key={s.cmd}
              initial={reduce ? false : { opacity: 0, x: -6 }}
              animate={inView ? { opacity: 1, x: 0 } : undefined}
              transition={{ duration: 0.4, delay: reduce ? 0 : 0.15 + i * 0.32 }}
              className="flex gap-3"
            >
              <span className="text-[oklch(0.5_0.006_85)] select-none">{i + 1}</span>
              <span className="min-w-0 break-words">
                <span className="text-[oklch(0.72_0.15_36)] select-none">$ </span>
                {s.cmd}
              </span>
            </m.li>
          ))}
          <m.li
            initial={reduce ? false : { opacity: 0 }}
            animate={inView ? { opacity: 1 } : undefined}
            transition={{ delay: reduce ? 0 : 0.15 + STEPS.length * 0.32 }}
            className="mt-3 grid gap-0.5 text-[oklch(0.64_0.006_85)]"
          >
            <span>
              <span className="text-[oklch(0.74_0.15_150)]">ok</span> scored {run?.providers.length ?? "all"} models on {run ? totalClips(run) : "the"} {run ? `${tier}-tier ` : ""}clips{spent != null ? `, list cost $${spent.toFixed(2)} of the $${RULES.budgetCapUsd.toFixed(2)} cap` : ""}
            </span>
            <span>
              <span className="text-[oklch(0.74_0.15_150)]">ok</span> wrote results/ and web/public/data/
            </span>
            <span className="mt-1 text-[oklch(0.93_0.005_85)]">
              $ <span className="inline-block h-4 w-2 translate-y-0.5 animate-blink bg-[oklch(0.93_0.005_85)]" />
            </span>
          </m.li>
        </ol>
      </div>
      <ol className="grid content-start border-t border-line">
        {STEPS.map((s, i) => (
          <li key={s.cmd} className="grid grid-cols-[32px_minmax(0,1fr)] gap-2 border-b border-line py-3.5">
            <span className="num text-[13px] text-signal-ink">{String(i + 1).padStart(2, "0")}</span>
            <span>
              <code className="block truncate font-mono text-[12.5px] text-ink">{s.cmd.split(" &&")[0]}</code>
              <span className="mt-0.5 block text-[13.5px] text-ink-2">{s.what}</span>
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}
