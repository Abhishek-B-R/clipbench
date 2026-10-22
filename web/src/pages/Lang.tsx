import { useEffect } from "react";
import { useNoIndex } from "@/lib/useNoIndex";
import { Link, useParams, useSearchParams } from "react-router";
import { ArrowRight, ArrowUpRight } from "lucide-react";
import type { LanguageStatus } from "@/lib/types";
import { useRunFor, useSource } from "@/lib/data";
import { CATEGORY_KEYS, LANGUAGE_NAME, aggregatesFor, defaultTier, isPublished, languageProgress, pickMetric, pickTier, primaryMetric, runLanguageStatus } from "@/lib/analysis";
import { hasCaptionMetrics, sliceKindsIn, sliceLabel, slicesOf } from "@/lib/metrics";
import { SliceFilter, readSlice, sliceParams } from "@/components/ui/SliceFilter";
import { CaptionMetricsTable } from "@/components/charts/CaptionMetrics";
import { CATEGORIES, GITHUB_URL, LANGUAGES, RULES } from "@/lib/config";
import { date, int, pct } from "@/lib/format";
import { cn } from "@/lib/cn";
import { PageHeader } from "@/components/layout/PageHeader";
import { Figure } from "@/components/ui/Card";
import { Badge } from "@/components/ui/Badge";
import { Skeleton } from "@/components/ui/Skeleton";
import { buttonClass } from "@/components/ui/Button";
import { EmptyState, ErrorState } from "@/components/ui/State";
import { LanguageReady, minCoverage } from "@/components/ui/Gate";
import { EarlyLookView } from "@/components/early/EarlyLookView";
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
  const [params, setParams] = useSearchParams();
  const tier = run ? defaultTier(run) : "verified";
  const kinds = run && published ? sliceKindsIn(run, tier) : [];
  const sl = readSlice(params, kinds, (k) => (run ? slicesOf(run, tier, k) : []));

  useNoIndex(!known);
  // Same title as the crawler page scripts/og-routes.ts writes (PageHeader's effect runs first; this one wins).
  useEffect(() => {
    if (known) document.title = `${name} speech-to-text ${published ? "results" : "progress"} | clipbench`;
  }, [known, name, published]);
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
            Language / {code} {published ? <Badge tone="good">Ranked</Badge> : run ? <Badge tone="warn">Early look</Badge> : <Badge>Set up</Badge>}
          </span>
        }
        title={name}
        description={
          published
            ? `Ranked on clips whose captions a person corrected by ear, from the run of ${run ? date(run.created_at) : ""}.`
            : run
              ? `What the latest ${name} run measured, every system on the same clips against the creators' own captions. The ranking opens once ${int(RULES.publishMinVerified)} clips are corrected by ear and at least ${int(RULES.publishMinProviders)} systems cover ${pct(minCoverage(status), 0)} of them.`
              : `${name} is set up in the pipeline. Its results appear here after its first run.`
        }
        actions={
          published && (
            <>
              <Link to={`/findings/${code}`} className={buttonClass("secondary", "sm")}>
                Findings
              </Link>
              <Link to={`/leaderboard?lang=${code}`} className={buttonClass("primary", "sm")}>
                Full leaderboard <ArrowRight size={14} />
              </Link>
            </>
          )
        }
      >
        {published && run && <SliceFilter kinds={kinds} kind={sl.kind} slice={sl.slice} values={sl.values} onChange={(k, v) => setParams(sliceParams(params, k, v), { replace: true })} />}
      </PageHeader>
      <div className="frame grid gap-8 py-8">
        {(state.status === "error" || src.status === "error") && <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />}
        {state.status === "loading" && <Skeleton className="h-64 w-full" />}
        {state.status === "ready" && published && run && (
          <>
            <MiniLeaderboard key={`${sl.kind}:${sl.slice}`} run={run} kind={sl.kind} slice={sl.slice} />
            {hasCaptionMetrics(run) && (
              <Figure n={2} title={`Caption metrics, ${sliceLabel(sl.kind, sl.slice).toLowerCase()}`} caption="Word timing, words invented over non-speech, punctuation, casing and code-switching. n/a means a model or these clips cannot be scored on it." source={`source: aggregates, run=${run.run_id}, slice_kind=${sl.kind}, slice=${sl.slice}`} bodyClassName="p-0 sm:p-0">
                <CaptionMetricsTable run={run} rows={aggregatesFor(run, { tier, metric: primaryMetric(run).name, kind: sl.kind, slice: sl.slice })} />
              </Figure>
            )}
          </>
        )}
        {state.status === "ready" && !published && run && <EarlyLookView run={run} tier={pickTier(run, null)} metric={pickMetric(run, null)} />}
        {state.status === "ready" && !published && !run && <LanguageReady lang={code} />}
        {state.status === "ready" && published && <DatasetStatus code={code} status={status} hasRun={Boolean(run)} />}
      </div>
    </>
  );
}

function DatasetStatus({ code, status, hasRun }: { code: string; status: LanguageStatus | undefined; hasRun: boolean }) {
  // Legacy descriptive counts over every scored clip (any tier). Not used by the gate, but without them a run of
  // creator-captioned clips from many channels would read as "0 channels".
  const scoredChannels = status?.channels ?? 0;
  const scoredCats = Array.isArray(status?.categories) ? status.categories.filter((c) => c !== "other").length : typeof status?.categories === "number" ? status.categories : 0;
  // The exporter's diversity check over the verified evaluation clips (docs/V3.md). Nothing is recomputed here:
  // without it (an older export) every row reads unknown.
  const div = status?.verified_diversity;
  const cats = new Set(div?.categories ?? []);
  const rows: { k: string; v: string; ok: boolean | null }[] = [
    { k: "Channels (verified clips)", v: div ? `${int(div.channels)} of at least ${int(RULES.minChannels)}` : "unknown", ok: div ? div.channels >= RULES.minChannels : null },
    { k: "Largest channel share", v: div?.largest_channel_share != null ? `${pct(div.largest_channel_share, 0)}, limit ${pct(RULES.maxChannelShare, 0)}` : "no verified clips yet", ok: div?.largest_channel_share != null ? div.largest_channel_share <= RULES.maxChannelShare : null },
    { k: "Content categories (verified clips)", v: div ? `${int(cats.size)} of at least ${int(RULES.minCategories)}` : "unknown", ok: div ? cats.size >= RULES.minCategories : null },
    { k: "Diversity check", v: div ? (div.passes ? "passes" : "does not pass yet") : "not in this export", ok: div ? div.passes : null },
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
      source={hasRun ? `source: verified_diversity of the latest ${code} run; rules from docs/V2.md` : "source: index.json; rules from docs/V2.md"}
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
      {scoredChannels > 0 && (
        <p className="border-b border-line px-4 py-3 text-[12.5px] text-ink-3 sm:px-5">
          The clips scored so far, any tier, come from {int(scoredChannels)} {scoredChannels === 1 ? "channel" : "channels"} across {int(scoredCats)} {scoredCats === 1 ? "category" : "categories"}. The gate counts verified clips only.
        </p>
      )}
      {div && div.problems.length > 0 && (
        <ul className="grid gap-1 border-b border-line px-4 py-3 text-[12.5px] text-ink-3 sm:px-5">
          {div.problems.map((p) => (
            <li key={p}>{p}</li>
          ))}
        </ul>
      )}
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
