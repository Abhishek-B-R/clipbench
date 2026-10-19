import { Link, useParams } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { Run } from "@/lib/types";
import { useRunFor, useSource } from "@/lib/data";
import { CATEGORY_KEYS, LANGUAGE_NAME, isPublished, languageProgress, runLanguageStatus } from "@/lib/analysis";
import { CATEGORIES, GITHUB_URL, LANGUAGES, RULES } from "@/lib/config";
import { date, int, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { buttonClass } from "@/components/ui/Button";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { GateBars } from "@/components/ui/Gate";
import { MiniLeaderboard } from "@/landing/MiniLeaderboard";

/**
 * One language: publication status, the dataset behind it, and (once published) its ranking.
 * Everything here is computed from the export; nothing is typed.
 */
export default function Lang() {
  const { code = "" } = useParams();
  const known = (LANGUAGES as readonly string[]).includes(code);
  const name = LANGUAGE_NAME[code] ?? code;
  const src = useSource();
  const state = useRunFor(known ? code : null);
  const run = known ? state.data?.run ?? null : null;
  const fromIndex = src.data ? languageProgress(src.data.index).find((l) => l.lang === code) : undefined;
  const status = run ? runLanguageStatus(run) : fromIndex;
  const published = Boolean(run && isPublished(run));

  if (!known)
    return (
      <>
        <PageHeader eyebrow="Language" title="Language not covered" />
        <div className="frame py-8">
          <EmptyState
            title={`clipbench does not cover "${code}".`}
            body={`The benchmark covers ${LANGUAGES.map((l) => LANGUAGE_NAME[l]).join(", ")}.`}
            action={
              <Link to="/" className={buttonClass("secondary", "sm")}>
                Back to the overview
              </Link>
            }
          />
        </div>
      </>
    );

  return (
    <>
      <PageHeader
        eyebrow={
          <span className="inline-flex items-center gap-2">
            Language / {code} {published ? <Badge tone="good">Published</Badge> : <Badge tone="outline">Not published yet</Badge>}
          </span>
        }
        title={name}
        description={
          published
            ? `Ranked on clips whose captions a person corrected by ear, from the run of ${run ? date(run.created_at) : ""}.`
            : `No ranking yet. ${name} is published once it has ${int(RULES.publishMinVerified)} verified clips and results from ${int(RULES.publishMinProviders)} providers.`
        }
        actions={
          published && (
            <Link to={`/leaderboard?lang=${code}`} className={buttonClass("primary", "sm")}>
              Full leaderboard <ArrowRight size={14} />
            </Link>
          )
        }
      />
      <div className="frame grid gap-8 py-8">
        {(state.status === "error" || src.status === "error") && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {state.status === "loading" && <Skeleton className="h-64 w-full" />}
        {state.status === "ready" && published && run && <MiniLeaderboard run={run} />}
        {state.status === "ready" && !published && (
          <div className="grid items-start gap-8 lg:grid-cols-2">
            <Figure n={1} title="Publication gate" caption="Counts from the latest export for this language." source={run ? `source: run ${run.run_id}` : "source: index.json"}>
              {status ? <GateBars status={status} /> : <Skeleton className="h-20 w-full" />}
            </Figure>
            <Figure n={2} title="Latest run" source={run ? `source: run ${run.run_id}` : "source: index.json"} bodyClassName="p-0 sm:p-0">
              {run ? (
                <dl>
                  {[
                    ["Date", date(run.created_at)],
                    ["Clips scored", `${int(run.clips.length)} (${int(run.clips.filter((c) => c.tier === "verified").length)} verified)`],
                    ["Providers run", int(run.providers.length)],
                    ["Run id", run.run_id],
                  ].map(([k, v]) => (
                    <div key={k} className="grid grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)] gap-4 border-b border-line px-4 py-2.5 last:border-b-0 sm:px-5">
                      <dt className="text-[13px] text-ink-3">{k}</dt>
                      <dd className="num text-[13px] text-ink">{v}</dd>
                    </div>
                  ))}
                </dl>
              ) : (
                <p className="px-4 py-5 text-[13.5px] text-ink-2 sm:px-5">No run for {name} yet. The clip set is being collected.</p>
              )}
            </Figure>
          </div>
        )}
        {state.status === "ready" && <DatasetStatus code={code} run={run} />}
        {state.status === "ready" && run && !published && (
          <p className="text-[13.5px] text-ink-2">
            Per-clip scores from this run are in the{" "}
            <Link to="/clips" className="text-ink underline decoration-line-strong underline-offset-4">
              clip browser
            </Link>{" "}
            as pipeline checks, not results.
          </p>
        )}
      </div>
    </>
  );
}

function DatasetStatus({ code, run }: { code: string; run: Run | null }) {
  const clips = run?.clips ?? [];
  const channels = new Map<string, number>();
  for (const c of clips) channels.set(c.channel, (channels.get(c.channel) ?? 0) + 1);
  const top = Math.max(0, ...channels.values());
  const share = clips.length ? top / clips.length : 0;
  const cats = new Set(clips.map((c) => c.category).filter((c): c is string => Boolean(c) && c !== "other"));
  const rows: { k: string; v: string; ok: boolean | null }[] = [
    { k: "Channels", v: `${int(channels.size)} of at least ${int(RULES.minChannels)}`, ok: channels.size >= RULES.minChannels },
    { k: "Largest channel share", v: clips.length ? `${pct(share, 0)}, limit ${pct(RULES.maxChannelShare, 0)}` : "no clips yet", ok: clips.length ? share <= RULES.maxChannelShare : null },
    { k: "Content categories", v: `${int(cats.size)} of at least ${int(RULES.minCategories)}`, ok: cats.size >= RULES.minCategories },
  ];
  return (
    <Figure
      n={3}
      title="Dataset"
      caption={
        <>
          The clip list lives in <code className="font-mono text-[12px]">datasets/{code}/clips.csv</code>. Diversity is checked in code before a run.
        </>
      }
      source={`source: clips of the latest ${code} run; rules from docs/V2.md`}
      bodyClassName="p-0 sm:p-0"
    >
      <dl>
        {rows.map((r) => (
          <div key={r.k} className="flex items-center justify-between gap-4 border-b border-line px-4 py-3 sm:px-5">
            <dt className="text-[13.5px] text-ink-2">{r.k}</dt>
            <dd className="flex items-center gap-3">
              <span className="num text-[13px] text-ink">{r.v}</span>
              {r.ok == null ? null : <Badge tone={r.ok ? "good" : "outline"}>{r.ok ? "Met" : "Not yet"}</Badge>}
            </dd>
          </div>
        ))}
      </dl>
      <div className="flex flex-wrap items-center gap-1.5 px-4 py-3 sm:px-5">
        {CATEGORY_KEYS.map((k) => (
          <span key={k} className={cn("inline-flex h-7 items-center rounded-full border px-2.5 text-[12.5px]", cats.has(k) ? "border-ink text-ink" : "border-line-strong text-ink-3")}>
            {CATEGORIES[k]}
          </span>
        ))}
        <a href={`${GITHUB_URL}/tree/main/datasets/${code}`} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-[13px] text-ink-2 hover:text-ink">
          datasets/{code} on GitHub <ArrowUpRight size={13} />
        </a>
      </div>
    </Figure>
  );
}
