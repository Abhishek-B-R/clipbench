import { Link } from "react-router";
import { ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/layout/PageHeader";
import { buttonClass } from "@/components/ui/Button";
import { Corners } from "@/components/ui/Card";

const PAGES: Record<string, { title: string; description: string; will: string[] }> = {
  provider: { title: "Model", description: "One model in depth.", will: ["Error rate by clip type and duration, with intervals", "Paired results against every other model", "Top substitutions and worst clips", "Price, latency, RTF and run-to-run stability", "Watch button (signed in)"] },
  compare: { title: "Compare", description: "Pick models, a tier, a metric and clip types; share the URL.", will: ["Model, tier, metric and clip-type pickers synced to the URL", "Side-by-side intervals and paired verdicts", "Save a comparison (signed in), share a public link"] },
  picker: { title: "Model picker", description: "Which model for my clips?", will: ["Inputs: language, clip types, budget per hour, latency need", "Ranked recommendation with the paired-test caveat", "Why each model ranks where it does"] },
  clips: { title: "Clips", description: "The clip set, by id.", will: ["Clip ids with tags, duration and channel", "Filters by tag and duration", "Links to the source video"] },
  methodology: { title: "Methodology", description: "How clips are chosen, checked and scored.", will: ["Sampling and verification", "Normalisation and metrics per language", "Bootstrap intervals and paired tests", "Budget guard, caching and the publish check", "Limitations"] },
  requests: { title: "Requests", description: "Vote for the next model or language.", will: ["Board sorted by votes, provider and language tabs", "One vote per signed-in user", "New request form"] },
  changelog: { title: "Changelog", description: "Every published run and what changed.", will: ["Runs by date with model and clip counts", "Rank changes between runs", "Links to each run's data"] },
  account: { title: "Account", description: "Profile, watchlist, alerts, saved comparisons and API keys.", will: ["Profile", "Watchlist", "Email alerts on new runs and rank changes", "Saved comparisons", "API keys (secret shown once)"] },
  api: { title: "Data API", description: "Read run results as JSON.", will: ["GET /api/v1/runs and /api/v1/runs/:runId", "Rate limits, anonymous and with a key", "curl, JavaScript and Python examples"] },
};

/** Placeholder for pages the page author builds next. Honest about what is coming; no fake content. */
export default function Stub({ page }: { page: keyof typeof PAGES | string }) {
  const p = PAGES[page] ?? { title: "Page", description: "", will: [] };
  return (
    <>
      <PageHeader eyebrow="Coming next" title={p.title} description={p.description} />
      <div className="frame py-10">
        <div className="relative max-w-xl rounded-md border border-dashed border-line-strong bg-surface p-6">
          <Corners />
          <div className="label mb-3">This page will show</div>
          <ul className="grid gap-2">
            {p.will.map((w) => (
              <li key={w} className="flex gap-2.5 text-[14px] text-ink-2">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-line-strong" />
                {w}
              </li>
            ))}
          </ul>
          <Link to="/leaderboard" className={buttonClass("secondary", "md", "mt-6")}>
            Go to the leaderboard <ArrowRight size={14} />
          </Link>
        </div>
      </div>
    </>
  );
}
