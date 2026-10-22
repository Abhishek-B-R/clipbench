import { Link, useSearchParams } from "react-router";
import { ArrowRight } from "lucide-react";
import { useRunFor } from "@/lib/data";
import { LANGUAGE_NAME, isPublished, pickMetric, pickTier } from "@/lib/analysis";
import { date } from "@/lib/format";
import { PageHeader } from "@/components/layout/PageHeader";
import { Segmented } from "@/components/ui/Segmented";
import { Skeleton } from "@/components/ui/Skeleton";
import { ErrorState } from "@/components/ui/State";
import { TierControl } from "@/components/ui/TierControl";
import { Badge } from "@/components/ui/Badge";
import { buttonClass } from "@/components/ui/Button";
import { EarlyLookView } from "@/components/early/EarlyLookView";
import { LanguageReady } from "@/components/ui/Gate";

/**
 * /early: every system of the latest run on creator captions, sorted by error, with no rank numbers and a
 * persistent "not a ranking" label. Rankings stay behind the publication gate on /leaderboard.
 */
export default function Early() {
  const [params, setParams] = useSearchParams();
  const lang = params.get("lang");
  const state = useRunFor(lang);
  const run = state.data ? state.data.run : undefined;
  const tier = pickTier(run ?? undefined, params.get("tier"));
  const metric = pickMetric(run ?? undefined, params.get("metric"));
  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v == null || v === "") p.delete(k);
    else p.set(k, v);
    setParams(p, { replace: true });
  };
  return (
    <>
      <PageHeader
        eyebrow={
          <span className="inline-flex flex-wrap items-center gap-2">
            <Badge tone="warn">Early look</Badge>
            {run ? `${LANGUAGE_NAME[run.language] ?? run.language} / run ${run.run_id} / ${date(run.created_at)}` : "Latest run"}
          </span>
        }
        title="Early look"
        description="What the first real run measured, system by system, against creator captions. Use it to see the pipeline working and where systems differ. Not a ranking: no reference here was checked by ear."
        actions={
          run &&
          isPublished(run) && (
            <Link to="/leaderboard" className={buttonClass("secondary", "sm")}>
              Published leaderboard <ArrowRight size={14} />
            </Link>
          )
        }
      >
        {run && <TierControl run={run} value={tier} onChange={(v) => set("tier", v)} />}
        {run && run.metrics.length > 1 && (
          <Segmented label="Metric" value={metric} onChange={(v) => set("metric", v)} options={run.metrics.map((m) => ({ value: m.name, label: m.label }))} />
        )}
      </PageHeader>
      <div className="frame py-8">
        {state.status === "error" ? (
          <ErrorState title="Could not load the run data." body="The results files did not load. Check your connection and try again." onRetry={() => location.reload()} />
        ) : run ? (
          <EarlyLookView run={run} tier={tier} metric={metric} />
        ) : run === null ? (
          <LanguageReady lang={lang ?? "en"} />
        ) : (
          <div className="grid gap-6">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-[480px] w-full" />
          </div>
        )}
      </div>
    </>
  );
}
