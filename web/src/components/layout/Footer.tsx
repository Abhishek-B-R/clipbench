import { Link } from "react-router";
import { useRun } from "@/lib/data";
import { date } from "@/lib/format";
import { totalClips } from "@/lib/analysis";
import { GITHUB_URL, LANGUAGES } from "@/lib/config";
import { ACCOUNTS } from "@/lib/flags";
import { LANGUAGE_NAME } from "@/lib/analysis";
import { Wordmark } from "./Logo";

const COLS = [
  {
    title: "Results",
    links: [
      { to: "/try", label: "Try it" },
      { to: "/leaderboard", label: "Leaderboard" },
      { to: "/early", label: "Early look" },
      { to: "/streaming", label: "Streaming" },
      { to: "/features", label: "Feature matrix" },
      { to: "/compare", label: "Compare models" },
      { to: "/picker", label: "Pick a provider" },
      { to: "/findings", label: "Findings" },
      { to: "/clips", label: "Clip set" },
      { to: "/changelog", label: "Changelog" },
    ],
  },
  {
    title: "Project",
    links: [
      { to: "/methodology", label: "Methodology" },
      ...(ACCOUNTS ? [{ to: "/requests", label: "Request a model" }] : []),
      { to: "/docs/api", label: "Data API" },
      { href: GITHUB_URL, label: "Source on GitHub" },
    ],
  },
  ACCOUNTS
    ? {
        title: "Account",
        links: [
          { to: "/sign-in", label: "Sign in" },
          { to: "/account#watchlist", label: "Watchlist" },
          { to: "/account#alerts", label: "Run alerts" },
          { to: "/account#keys", label: "API keys" },
        ],
      }
    : {
        title: "Languages",
        links: LANGUAGES.map((l) => ({ to: `/lang/${l}`, label: LANGUAGE_NAME[l] ?? l })),
      },
];

/** Deterministic pseudo-waveform for the footer band. */
const BARS = Array.from({ length: 96 }, (_, i) => {
  const v = Math.abs(Math.sin(i * 0.55) * 0.6 + Math.sin(i * 0.17 + 1) * 0.35 + Math.sin(i * 1.7) * 0.15);
  return 0.12 + v * 0.88;
});

export function Footer() {
  const run = useRun();
  const r = run.data?.run;
  // Findings open on the latest run's language (the one with data), not on a fixed language.
  const findingsTo = `/findings/${r?.language ?? LANGUAGES[0]}`;
  return (
    <footer className="border-t border-line">
      <div className="frame frame-lines">
        <div className="grid gap-12 py-14 md:grid-cols-[1.3fr_2fr]">
          <div className="max-w-sm">
            <Wordmark />
            <p className="mt-4 text-[14px] text-ink-2">
              An open benchmark of speech-to-text models on real short-form video. Every number on this site comes from code you can run.
            </p>
            <p className="mt-6 inline-flex items-center gap-2 rounded-full border border-line bg-surface px-3 py-1 font-mono text-[11.5px] text-ink-2">
              <span className="relative flex size-2">
                <span className="absolute inset-0 animate-ping rounded-full bg-good opacity-50" />
                <span className="relative size-2 rounded-full bg-good" />
              </span>
              {r ? `Latest run ${date(r.created_at)}, ${r.providers.length} models, ${totalClips(r)} clips` : "Loading latest run"}
            </p>
          </div>
          <div className="grid grid-cols-2 gap-8 sm:grid-cols-3">
            {COLS.map((c) => (
              <div key={c.title}>
                <h2 className="label mb-3">{c.title}</h2>
                <ul className="grid gap-2">
                  {c.links.map((l) => (
                    <li key={l.label}>
                      {"href" in l ? (
                        <a href={l.href} target="_blank" rel="noreferrer" className="text-[13.5px] text-ink-2 hover:text-ink">
                          {l.label}
                        </a>
                      ) : (
                        <Link to={l.to === "/findings" ? findingsTo : l.to} className="text-[13.5px] text-ink-2 hover:text-ink">
                          {l.label}
                        </Link>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </div>
        <div aria-hidden className="flex h-20 items-end gap-[3px] overflow-hidden">
          {BARS.map((h, i) => (
            <span
              key={i}
              className="min-w-[2px] flex-1 rounded-t-[2px]"
              style={{ height: `${h * 100}%`, background: i === 61 ? "var(--signal)" : "var(--line)" }}
            />
          ))}
        </div>
        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line py-5 text-[12px] text-ink-3">
          <span>Open source. Results are published as JSON and can be rebuilt from the repository.</span>
          <span className="font-mono">Not affiliated with any provider tested.</span>
        </div>
      </div>
    </footer>
  );
}
