import { Link } from "react-router";
import { ArrowRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { int } from "@/lib/format";
import { RULES } from "@/lib/config";
import { defaultTier, primaryMetric } from "@/lib/analysis";
import { Reveal } from "@/components/layout/Section";

/** Computed from the export, never typed: how many clips a person has actually checked. */
function verifiedSentence(run?: Run) {
  const base = "Headline numbers use only clips whose captions a person corrected while listening. Creator captions are scored separately and labelled unverified.";
  if (!run) return base;
  const v = run.clips.filter((c) => c.tier === "verified").length;
  return v > 0 ? `${base} Verified so far: ${int(v)} ${v === 1 ? "clip" : "clips"}.` : `${base} No clip in the latest run is verified yet.`;
}

/** Four trust signals, each with a small diagram drawn in the page's own language. */
export function Method({ run }: { run?: Run }) {
  const tiles = [
    {
      n: "01",
      title: "References checked by ear",
      body: verifiedSentence(run),
      art: <ArtVerified />,
    },
    {
      n: "02",
      title: "Paired tests, not leaderboard gaps",
      body: "Models are compared on the same clips. A lead counts only when the 95% interval of the per-clip difference excludes zero.",
      art: <ArtPaired />,
    },
    {
      n: "03",
      title: "Budget guarded and cached",
      body: "Every paid call is estimated first and refused past the cap. Results are cached, so a rerun of the same clips costs nothing.",
      art: <ArtBudget />,
    },
    {
      n: "04",
      title: "Open, but no captions leak",
      body: "Code, clip ids and per-clip scores are public. Audio and transcripts are not, and a publish check scans every file for caption text.",
      art: <ArtOpen run={run} />,
    },
  ];
  return (
    <div>
      <div className="grid gap-px overflow-hidden rounded-md border border-line bg-line md:grid-cols-2 xl:grid-cols-4">
        {tiles.map((t, i) => (
          <Reveal key={t.n} delay={i * 0.06} y={8} className="bg-surface">
            <div className="relative flex h-full flex-col p-5 sm:p-6">
              <div className="flex items-center justify-between">
                <span className="num text-[12px] text-signal-ink">{t.n}</span>
              </div>
              <div className="relative my-6 h-24 rounded-sm border border-line bg-paper">
                {t.art}
              </div>
              <h3 className="text-[16px] font-medium text-ink">{t.title}</h3>
              <p className="mt-2 text-[13.5px] leading-relaxed text-ink-2">{t.body}</p>
            </div>
          </Reveal>
        ))}
      </div>
      <Link to="/methodology" className="group mt-6 inline-flex items-center gap-1.5 text-[14px] text-ink-2 hover:text-ink">
        Read the full methodology <ArrowRight size={14} className="transition-transform group-hover:translate-x-0.5" />
      </Link>
    </div>
  );
}

const svg = "absolute inset-0 size-full";

function ArtVerified() {
  return (
    <svg viewBox="0 0 240 96" className={svg} aria-hidden>
      <g fontFamily="var(--font-mono)" fontSize="10" fill="var(--ink-3)">
        <text x="20" y="32">caption</text>
        <text x="20" y="66">checked</text>
      </g>
      <rect x="80" y="23" width="120" height="12" rx="3" fill="var(--sunken)" />
      <rect x="128" y="23" width="26" height="12" rx="3" fill="var(--signal-soft)" />
      <line x1="128" x2="154" y1="29" y2="29" stroke="var(--signal)" strokeWidth="1.5" />
      <rect x="80" y="57" width="120" height="12" rx="3" fill="var(--sunken)" />
      <rect x="128" y="57" width="26" height="12" rx="3" fill="var(--good-soft)" />
      <path d="M136 63 l4 4 l8 -8" fill="none" stroke="var(--good)" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ArtPaired() {
  return (
    <svg viewBox="0 0 240 96" className={svg} aria-hidden>
      <line x1="120" x2="120" y1="14" y2="82" stroke="var(--ink-3)" strokeDasharray="2 3" />
      <text x="124" y="88" fontFamily="var(--font-mono)" fontSize="9" fill="var(--ink-3)">0</text>
      <line x1="40" x2="104" y1="34" y2="34" stroke="var(--ink)" strokeWidth="1.5" />
      <line x1="40" x2="40" y1="29" y2="39" stroke="var(--ink)" strokeWidth="1.5" />
      <line x1="104" x2="104" y1="29" y2="39" stroke="var(--ink)" strokeWidth="1.5" />
      <circle cx="72" cy="34" r="4.5" fill="var(--signal)" stroke="var(--paper)" strokeWidth="2" />
      <text x="40" y="22" fontFamily="var(--font-mono)" fontSize="9" fill="var(--ink-2)">clear lead</text>
      <line x1="96" x2="168" y1="64" y2="64" stroke="var(--ink-3)" strokeWidth="1.5" />
      <line x1="96" x2="96" y1="59" y2="69" stroke="var(--ink-3)" strokeWidth="1.5" />
      <line x1="168" x2="168" y1="59" y2="69" stroke="var(--ink-3)" strokeWidth="1.5" />
      <circle cx="132" cy="64" r="4.5" fill="var(--ink-3)" stroke="var(--paper)" strokeWidth="2" />
      <text x="174" y="67" fontFamily="var(--font-mono)" fontSize="9" fill="var(--ink-3)">no call</text>
    </svg>
  );
}

function ArtBudget() {
  const bars = [10, 18, 26, 31, 37, 44, 52, 58, 63];
  return (
    <svg viewBox="0 0 240 96" className={svg} aria-hidden>
      <line x1="20" x2="220" y1="22" y2="22" stroke="var(--signal)" strokeDasharray="3 3" />
      <text x="220" y="16" textAnchor="end" fontFamily="var(--font-mono)" fontSize="9" fill="var(--signal-ink)">cap ${RULES.budgetCapUsd.toFixed(2)}</text>
      {bars.map((h, i) => (
        <rect key={i} x={24 + i * 21} y={82 - h} width="13" height={h} rx="2" fill={i === bars.length - 1 ? "var(--ink)" : "var(--line-strong)"} />
      ))}
    </svg>
  );
}

/** Two real per-clip rows from the latest export (ids and scores are public); placeholders before it loads. */
function ArtOpen({ run }: { run?: Run }) {
  const metric = run ? primaryMetric(run).name : "cer";
  const tier = run ? defaultTier(run) : undefined;
  const rows = (run?.per_clip ?? [])
    .filter((r) => r.metric === metric && r.tier === tier && Number.isFinite(r.error_rate))
    .slice(0, 2)
    .map((r) => `${r.video_id.slice(0, 7)}, ${r.error_rate.toFixed(3)}`);
  while (rows.length < 2) rows.push("...");
  return (
    <svg viewBox="0 0 240 96" className={svg} aria-hidden>
      <g fontFamily="var(--font-mono)" fontSize="10">
        <text x="24" y="30" fill="var(--ink-2)">video_id, {metric}</text>
        <text x="24" y="48" fill="var(--ink-3)">{rows[0]}</text>
        <text x="24" y="66" fill="var(--ink-3)">{rows[1]}</text>
      </g>
      <rect x="150" y="24" width="66" height="44" rx="4" fill="var(--sunken)" stroke="var(--line-strong)" />
      <rect x="160" y="36" width="46" height="5" rx="2" fill="var(--line-strong)" />
      <rect x="160" y="47" width="34" height="5" rx="2" fill="var(--line-strong)" />
      <line x1="146" x2="220" y1="20" y2="72" stroke="var(--signal)" strokeWidth="1.5" />
      <text x="183" y="84" textAnchor="middle" fontFamily="var(--font-mono)" fontSize="9" fill="var(--ink-3)">captions</text>
    </svg>
  );
}
